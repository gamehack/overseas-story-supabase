"""Deterministic ingestion of indie.md's public Markdown content."""

import hashlib
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

import yaml

from .git_checkout import GitCheckout
from .models import Collection, Person, Story


SOURCE_KEY = "indie.md"
SOURCE_NAME = "indie.md"
SOURCE_URL = "https://indie.md/"
UPSTREAM_REPO = "indie-tm/indie.md"
PEOPLE_PREFIX = "src/content/people/"
JOURNEYS_PREFIX = "src/content/journeys/"


class ContentError(ValueError):
    pass


def parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    normalized = markdown.lstrip("\ufeff")
    lines = normalized.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ContentError("Missing YAML frontmatter")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        raise ContentError("Unclosed YAML frontmatter")
    try:
        metadata = yaml.safe_load("".join(lines[1:close]))
    except yaml.YAMLError as exc:
        raise ContentError("Malformed YAML frontmatter") from exc
    if not isinstance(metadata, dict):
        raise ContentError("YAML frontmatter must be a mapping")
    return metadata, "".join(lines[close + 1 :]).lstrip("\r\n")


def _required_str(frontmatter: dict, field: str) -> str:
    value = frontmatter.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContentError(f"Missing required text field: {field}")
    return value.strip()


def _optional_str(frontmatter: dict, field: str) -> str:
    value = frontmatter.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ContentError(f"Invalid text field: {field}")
    return value.strip()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def parse_person(slug: str, raw: str) -> Person:
    fm, _ = parse_frontmatter(raw)
    return Person(
        source_person_id=slug,
        name=_required_str(fm, "name"),
        tagline=_optional_str(fm, "tagline"),
        bio=_optional_str(fm, "bio"),
        profile_url=f"https://indie.md/people/{slug}/",
        metadata=_jsonable(fm),
        content_hash=_digest(raw),
    )


def parse_story(slug: str, raw: str, revision: str) -> Story:
    fm, body = parse_frontmatter(raw)
    raw_date = fm.get("date")
    if isinstance(raw_date, datetime):
        published_at = raw_date.date()
    elif isinstance(raw_date, date):
        published_at = raw_date
    elif isinstance(raw_date, str):
        try:
            published_at = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ContentError(f"Invalid date in {slug}") from exc
    else:
        raise ContentError(f"Missing date in {slug}")

    person_id = _required_str(fm, "person")
    lessons = fm.get("lessons")
    if not isinstance(lessons, list) or not all(isinstance(x, str) for x in lessons):
        raise ContentError(f"Invalid lessons in {slug}")

    return Story(
        source_item_id=slug,
        title=_required_str(fm, "title"),
        subtitle=_required_str(fm, "subtitle"),
        source_url=f"https://indie.md/journeys/{slug}/",
        published_at=published_at,
        raw_markdown=raw,
        body_markdown=body,
        person_ids=(person_id,),
        metadata={
            **_jsonable(fm),
            "source_revision": revision,
            "content_kind": "journey",
            # Reuse the M2 -> M3 -> M4 pipeline.  The Markdown and its original
            # founder link remain authoritative; only editorial facts are AI-made.
            "ai_extraction": {"status": "pending_not_configured", "schema_version": 2},
        },
        content_hash=_digest(raw),
    )


class IndieMDAdapter:
    def __init__(self, client: GitCheckout):
        self.client = client

    def collect(self) -> Collection:
        revision, tree = self.client.list_files(UPSTREAM_REPO)
        people: list[Person] = []
        stories: list[Story] = []
        errors: list[str] = []
        for path, sha in sorted(tree.items()):
            if not path.endswith(".md"):
                continue
            slug = PurePosixPath(path).stem
            try:
                if path.startswith(PEOPLE_PREFIX):
                    people.append(parse_person(slug, self.client.read_blob(UPSTREAM_REPO, sha)))
                elif path.startswith(JOURNEYS_PREFIX):
                    stories.append(parse_story(slug, self.client.read_blob(UPSTREAM_REPO, sha), revision))
            except ContentError as exc:
                errors.append(f"{path}: {exc}")
        if not stories or not people:
            raise ContentError("No indie.md journeys or people returned; refusing empty sync")
        return Collection(
            tuple(people), tuple(stories), revision,
            source_key=SOURCE_KEY, source_name=SOURCE_NAME, source_url=SOURCE_URL,
            source_kind="github_markdown",
            errors=tuple(errors),
        )

