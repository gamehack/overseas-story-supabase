"""One-shot CLI for manual backfill and weekly incremental synchronization."""

from __future__ import annotations

import argparse
import json
import logging
import os

from .git_checkout import GitCheckout
from .indie_md import IndieMDAdapter
from .models import Collection
from .repository import PgRepository
from .substack_rss import SUBSTACK_SOURCES, SubstackRSSAdapter
from .sync import AiCallBudget, sync_collection

try:  # Optional AI extraction client; disabled unless all settings are present.
    from .extraction import StructuredExtractor
except Exception:  # pragma: no cover - extraction module import should not fail collection
    StructuredExtractor = None  # type: ignore[assignment,misc]


def _error_collection(source_key: str, source_name: str, source_url: str, source_kind: str, message: str) -> Collection:
    return Collection(
        (), (), "",
        source_key=source_key, source_name=source_name, source_url=source_url,
        source_kind=source_kind, errors=(message,),
    )


def _collect_indie_md() -> Collection:
    with GitCheckout("indie-tm/indie.md") as source:
        return IndieMDAdapter(source).collect()


def _collect_substack(source) -> Collection:
    return SubstackRSSAdapter(source, content_authorized=True).collect()


def _substack_content_authorized() -> bool:
    return os.getenv("SUBSTACK_CONTENT_AUTHORIZED", "").strip().lower() in {"1", "true", "yes"}


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from None
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _run_collection(
    collection: Collection, dry_run: bool, extractor, ai_budget: AiCallBudget
) -> dict:
    people = {person.source_person_id for person in collection.people}
    errors = list(collection.errors)
    for story in collection.stories:
        missing = [pid for pid in story.person_ids if pid not in people]
        for pid in missing:
            errors.append(f"story {story.source_item_id}: missing person {pid}")
    if dry_run:
        return {
            "source_key": collection.source_key,
            "source_name": collection.source_name,
            "source_kind": collection.source_kind,
            "mode": "dry-run",
            "revision": collection.revision,
            "people": len(collection.people),
            "stories": len(collection.stories),
            "errors": errors,
            "warnings": list(collection.warnings),
            "monitoring": collection.monitoring,
        }
    if errors and not collection.people and not collection.stories:
        return {
            "source_key": collection.source_key,
            "source_name": collection.source_name,
            "source_kind": collection.source_kind,
            "mode": "sync",
            "revision": collection.revision,
            "people": {},
            "stories": {},
            "errors": errors,
            "warnings": list(collection.warnings),
            "monitoring": collection.monitoring,
        }
    with PgRepository(os.getenv("DATABASE_URL", "")) as repository:
        report = sync_collection(collection, repository, extractor, ai_budget)
    return {
        "source_key": collection.source_key,
        "source_name": collection.source_name,
        "source_kind": collection.source_kind,
        "mode": "sync",
        "revision": report.revision,
        "people": report.people,
        "stories": report.stories,
        "errors": list(report.errors),
        "warnings": list(report.warnings) + list(collection.warnings),
        "monitoring": collection.monitoring,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect overseas founder stories")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="Sync stories to Supabase")
    sync.add_argument("--dry-run", action="store_true", help="Read/validate without DB writes")
    sync.add_argument(
        "--source",
        choices=["all", "indie-md", "substack"],
        default="all",
        help="Which curated source(s) to sync (default: all)",
    )
    sync.add_argument(
        "--ai-limit",
        type=_nonnegative_int,
        default=5,
        metavar="N",
        help="Maximum AI model calls across all sources in this run (default: 5)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        ai_budget = AiCallBudget(args.ai_limit)
        extractor = None
        if StructuredExtractor is not None:
            try:
                extractor = StructuredExtractor()
            except ValueError:
                # Misconfigured AI endpoint (e.g. not HTTPS): treat as unconfigured.
                extractor = None
        collections: list[Collection] = []
        skipped_sources: list[str] = []
        # Substack is deliberately opt-in: RSS availability does not itself
        # grant permission to archive or process full publication content.
        if args.source in ("all", "substack"):
            if _substack_content_authorized():
                for source in SUBSTACK_SOURCES:
                    try:
                        collections.append(_collect_substack(source))
                    except Exception as exc:
                        collections.append(_error_collection(source.key, source.name, source.feed_url, "substack_rss", f"collection failed: {type(exc).__name__}: {exc}"))
            else:
                skipped_sources.append("substack:disabled_without_explicit_authorization")
                logging.warning("Substack sources skipped: set SUBSTACK_CONTENT_AUTHORIZED=true only when content use is authorized")
        if args.source in ("all", "indie-md"):
            try:
                collections.append(_collect_indie_md())
            except Exception as exc:
                collections.append(_error_collection("indie.md", "indie.md", "https://indie.md/", "github_markdown", f"collection failed: {type(exc).__name__}: {exc}"))

        results = []
        for collection in collections:
            try:
                results.append(_run_collection(collection, args.dry_run, extractor, ai_budget))
            except Exception as exc:
                # A database or source-specific failure must not suppress later sources.
                results.append({
                    "source_key": collection.source_key,
                    "source_name": collection.source_name,
                    "source_kind": collection.source_kind,
                    "mode": "dry-run" if args.dry_run else "sync",
                    "revision": collection.revision,
                    "people": 0 if args.dry_run else {},
                    "stories": 0 if args.dry_run else {},
                    "errors": [f"sync failed: {type(exc).__name__}: {exc}"],
                    "warnings": list(collection.warnings),
                    "monitoring": collection.monitoring,
                })
        all_errors = [error for result in results for error in result["errors"]]
        print(json.dumps({
            "mode": "dry-run" if args.dry_run else "sync",
            "source": args.source,
            "ai_limit": args.ai_limit,
            "ai_calls_used": ai_budget.used,
            "skipped_sources": skipped_sources,
            "results": results,
            "errors": all_errors,
        }, ensure_ascii=False))
        return 2 if all_errors else 0
    except Exception as exc:
        logging.error("Collection failed: %s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
