"""Source-independent records. No LLM inference or factual verification here."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Person:
    source_person_id: str
    name: str
    profile_url: str
    tagline: str = ""
    bio: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""


@dataclass(frozen=True)
class Story:
    source_item_id: str
    title: str
    subtitle: str
    source_url: str
    published_at: date
    raw_markdown: str
    body_markdown: str
    person_ids: tuple[str, ...]
    person_roles: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""


@dataclass(frozen=True)
class Collection:
    people: tuple[Person, ...]
    stories: tuple[Story, ...]
    revision: str
    source_key: str = "indie.md"
    source_name: str = "indie.md"
    source_url: str = "https://indie.md/"
    source_kind: str = "github_markdown"
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    monitoring: dict[str, Any] = field(default_factory=dict)

