"""PostgreSQL persistence in a private Supabase schema."""

from dataclasses import dataclass

from .models import Person, Story


@dataclass(frozen=True)
class Outcome:
    id: str
    action: str  # inserted, updated, or unchanged


class PgRepository:
    def __init__(self, dsn: str):
        if not dsn or "CHANGE_ME" in dsn:
            raise ValueError("Set DATABASE_URL to the selected Supabase Postgres URL")
        try:
            import psycopg
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install project dependencies before using PostgreSQL") from exc
        self.Jsonb = Jsonb
        self.conn = psycopg.connect(dsn, autocommit=True, connect_timeout=15)
        self.conn.execute("SET search_path TO overseas_stories, public")
        exists = self.conn.execute("SELECT to_regclass('overseas_stories.stories')").fetchone()[0]
        if exists is None:
            self.close()
            raise RuntimeError("Database schema missing; apply db/schema.sql first")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PgRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ensure_source(self, key: str, name: str, url: str, kind: str) -> int:
        with self.conn.transaction():
            row = self.conn.execute(
                """
                INSERT INTO sources (key, name, url, kind)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, url = EXCLUDED.url, kind = EXCLUDED.kind
                RETURNING id
                """,
                (key, name, url, kind),
            ).fetchone()
            return int(row[0])

    def upsert_person(self, source_id: int, person: Person) -> Outcome:
        with self.conn.transaction():
            current = self.conn.execute(
                "SELECT id, content_hash FROM people WHERE source_id = %s AND source_person_id = %s FOR UPDATE",
                (source_id, person.source_person_id),
            ).fetchone()
            if current and current[1] == person.content_hash:
                return Outcome(str(current[0]), "unchanged")
            params = (
                person.name,
                person.tagline,
                person.bio,
                person.profile_url,
                self.Jsonb(person.metadata),
                person.content_hash,
            )
            if current:
                self.conn.execute(
                    """
                    UPDATE people SET name=%s, tagline=%s, bio=%s, profile_url=%s,
                      metadata=%s, content_hash=%s, updated_at=now()
                    WHERE id=%s
                    """,
                    (*params, current[0]),
                )
                return Outcome(str(current[0]), "updated")
            row = self.conn.execute(
                """
                INSERT INTO people (source_id, source_person_id, name, tagline, bio,
                                    profile_url, metadata, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (source_id, person.source_person_id, *params),
            ).fetchone()
            return Outcome(str(row[0]), "inserted")

    def read_story(self, source_id: int, source_item_id: str):
        """Read-only lookup of persisted story id, content_hash and metadata."""
        row = self.conn.execute(
            """
            SELECT id, content_hash, metadata FROM stories
            WHERE source_id=%s AND source_item_id=%s
            """,
            (source_id, source_item_id),
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], row[2] if isinstance(row[2], dict) else {}

    def upsert_story(
        self,
        source_id: int,
        story: Story,
        person_ids: list[str],
        person_roles: dict[str, str],
        replace_relations: bool = True,
    ) -> Outcome:
        """person_ids / person_roles are keyed by database person id (not source slug)."""
        with self.conn.transaction():
            current = self.read_story(source_id, story.source_item_id)
            story_id = None
            action = "unchanged"
            content_changed = current is None or current[1] != story.content_hash
            old_ai = current[2].get("ai_extraction") if current else None
            new_ai = story.metadata.get("ai_extraction")
            ai_changed = current is None or old_ai != new_ai
            old_relevance = current[2].get("relevance") if current else None
            new_relevance = story.metadata.get("relevance")
            relevance_changed = current is None or old_relevance != new_relevance
            params = (
                story.title,
                story.subtitle,
                story.source_url,
                story.published_at,
                story.raw_markdown,
                story.body_markdown,
                self.Jsonb(story.metadata),
                story.content_hash,
            )
            if current is None:
                row = self.conn.execute(
                    """
                    INSERT INTO stories (source_id, source_item_id, title, subtitle,
                      source_url, published_at, raw_markdown, body_markdown, metadata, content_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (source_id, story.source_item_id, *params),
                ).fetchone()
                story_id = row[0]
                action = "inserted"
            elif content_changed or ai_changed or relevance_changed:
                story_id = current[0]
                self.conn.execute(
                    """
                    UPDATE stories SET title=%s, subtitle=%s, source_url=%s,
                      published_at=%s, raw_markdown=%s, body_markdown=%s,
                      metadata=%s, content_hash=%s, fetched_at=now(), updated_at=now()
                    WHERE id=%s
                    """,
                    (*params, story_id),
                )
                action = "updated"
            else:
                story_id = current[0]

            if story_id is not None:
                if replace_relations:
                    # Avoid a blanket DELETE for new/additive updates. The least-
                    # privilege production role intentionally has no DELETE grant,
                    # and no deletion is needed unless a persisted relation is
                    # actually obsolete.
                    existing_ids = {
                        str(row[0])
                        for row in self.conn.execute(
                            "SELECT person_id FROM story_people WHERE story_id=%s",
                            (story_id,),
                        ).fetchall()
                    }
                    obsolete_ids = existing_ids - set(person_ids)
                    for person_id in obsolete_ids:
                        self.conn.execute(
                            "DELETE FROM story_people WHERE story_id=%s AND person_id=%s",
                            (story_id, person_id),
                        )
                # Insert new relations and update changed roles. When relations
                # are being preserved, this also keeps persisted AI founders.
                for person_id in person_ids:
                    role = person_roles.get(person_id, "founder")
                    self.conn.execute(
                        """
                        INSERT INTO story_people (story_id, person_id, role)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (story_id, person_id) DO UPDATE SET role = EXCLUDED.role
                        """,
                        (story_id, person_id, role),
                    )
            return Outcome(str(story_id), action)

    def mark_checked(self, source_id: int, revision: str) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE sources SET last_checked_at=now(), last_revision=%s WHERE id=%s",
                (revision, source_id),
            )

