"""Bounded, standards-based ingestion of public Substack RSS feeds."""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from .models import Collection, Person, Story
from .network import build_direct_opener


MAX_FEED_BYTES = 2_000_000
HTTP_TIMEOUT_SECONDS = 25
USER_AGENT = "overseas-story-supabase/0.1 (+authorized RSS processing)"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"
DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"


@dataclass(frozen=True)
class SubstackSource:
    key: str
    name: str
    feed_url: str

    @property
    def site_url(self) -> str:
        parsed = urllib.parse.urlsplit(self.feed_url)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")) + "/"


SUBSTACK_SOURCES = (
    SubstackSource("substack:indieniche", "Indieniche", "https://indieniche.substack.com/feed"),
    SubstackSource("substack:behindthelaunchh", "Behind the Launch", "https://behindthelaunchh.substack.com/feed"),
    SubstackSource("substack:buildinpublichub", "Build In Public Newsletter", "https://buildinpublichub.substack.com/feed"),
    SubstackSource("substack:unstuckentrepreneur", "The Unstuck Entrepreneur", "https://unstuckentrepreneur.substack.com/feed"),
    SubstackSource("substack:thefoundingjourney", "The Founding Journey", "https://thefoundingjourney.substack.com/feed"),
)


class FeedError(RuntimeError):
    pass


class _MarkdownParser(HTMLParser):
    """Small allow-list-style HTML-to-text converter; scripts/styles are discarded."""

    BLOCKS = {"p", "div", "section", "article", "header", "footer", "blockquote", "pre", "table", "tr"}
    SKIP = {"script", "style", "noscript", "svg", "form", "button"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.links: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        values = dict(attrs)
        if tag in self.BLOCKS:
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in {"ul", "ol"}:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "a":
            href = values.get("href")
            self.links.append(href if href and href.startswith(("http://", "https://")) else None)
            self.parts.append("[")
        elif tag == "img":
            src, alt = values.get("src"), values.get("alt") or "image"
            if src and src.startswith(("http://", "https://")):
                self.parts.append(f"![{alt}]({src})")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag in self.BLOCKS or tag in {"li", "ul", "ol"} or re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "a" and self.links:
            href = self.links.pop()
            self.parts.append(f"]({href})" if href else "]")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def markdown(self) -> str:
        text = "".join(self.parts).replace("\r", "")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_markdown(value: str) -> str:
    parser = _MarkdownParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception as exc:
        raise FeedError(f"invalid item HTML: {type(exc).__name__}") from exc
    return parser.markdown()


def canonical_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FeedError("item has no valid canonical link")
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", urllib.parse.urlencode(query), ""))


def _author_id(name: str) -> str:
    normalized = " ".join(name.casefold().split())
    return "author:" + hashlib.sha256(normalized.encode()).hexdigest()[:20]


def _is_excerpt(body: str, html: str) -> tuple[bool, str | None]:
    lowered = (body + " " + html).casefold()
    markers = ("continue reading", "read the full post", "this post is for paid subscribers", "subscribe to read")
    marker = next((item for item in markers if item in lowered), None)
    if marker:
        return True, marker
    if len(re.sub(r"\W+", "", body)) < 500:
        return True, "short_rss_body"
    return False, None


_STRONG_STORY = re.compile(
    r"\b(founder story|meet (?:the |an? )?founder|how .{0,70} (?:built|started|turned|launched)|"
    r"(?:he|she|they|i) (?:built|started|launched|quit|turned)|built .{0,80}\$|started a new company|launching [\w-]+|"
    r"\b(?:founder|co-founder)\b.{0,45}\b(?:building|built|launched)\b|"
    r"\bturned .{0,90} into\b|walked away to build|first paying customer)",
    re.IGNORECASE,
)
_INTERVIEW_STORY = re.compile(
    # A line-leading Q. / Q: (often preceded by a Markdown heading) is a common
    # interview format.  The old `\b(Q\.|Q:)\b` could never match `Q. `: there
    # is no word boundary between punctuation and the following whitespace.
    r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?Q[.:][ \t]+|"
    r"\b(?:in this interview|(?:we|i) sat down with|today.s featured interview|"
    r"tell (?:me|us) about|what inspired you|your journey|"
    r"his backstory and how he started|founder behind (?:it|the product)|"
    r"that.s the story of|my .{0,40}startup|my co-founder and i)\b",
    re.IGNORECASE,
)
_GENERIC = re.compile(
    r"\b(guide|checklist|step.by.step|how to get|how to build|what to do|dear founder|things investors|"
    r"metrics to track|observations on|your landing page|your growth|playbook|why your|the price of|"
    r"customers without|founder.led growth)\b",
    re.IGNORECASE,
)


def relevance_prefilter(title: str, body: str) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if _STRONG_STORY.search(title):
        reasons.append("narrative_title_pattern")
    if _INTERVIEW_STORY.search(body[:20_000]):
        reasons.append("interview_or_origin_language")
    if _GENERIC.search(title) and "narrative_title_pattern" not in reasons:
        return False, ("generic_advice_title",)
    return bool(reasons), tuple(reasons or ["no_strong_founder_story_signal"])


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise FeedError("refusing non-HTTPS feed redirect")
        redirects = int(req.headers.get("X-QJAI-Redirects", "0")) + 1
        if redirects > 5:
            raise FeedError("too many feed redirects")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.add_header("X-Overseas-Story-Redirects", str(redirects))
        return redirected


def fetch_feed(url: str) -> tuple[bytes, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml;q=0.9"})
    opener = build_direct_opener(_SafeRedirectHandler())
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            data = response.read(MAX_FEED_BYTES + 1)
            resolved = response.geturl()
            content_type = response.headers.get("Content-Type", "")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise FeedError(f"feed request failed: {type(exc).__name__}") from exc
    if len(data) > MAX_FEED_BYTES:
        raise FeedError(f"feed exceeds {MAX_FEED_BYTES} byte limit")
    if not data.strip():
        raise FeedError("feed response is empty")
    return data, resolved, content_type


def parse_feed(source: SubstackSource, xml: bytes, resolved_url: str | None = None) -> Collection:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise FeedError("feed is not valid XML") from exc
    channel = root.find("channel") if root.tag == "rss" else None
    if channel is None:
        raise FeedError("expected an RSS channel")
    publication = (channel.findtext("title") or source.name).strip()
    items = channel.findall("item")
    if not items:
        raise FeedError("feed contains no items; refusing empty sync")

    people: dict[str, Person] = {}
    stories: list[Story] = []
    errors: list[str] = []
    for position, item in enumerate(items, 1):
        try:
            title = (item.findtext("title") or "").strip()
            if not title:
                raise FeedError("missing title")
            link = canonical_url(item.findtext("link") or "")
            guid = (item.findtext("guid") or "").strip()
            source_item_id = guid if guid and len(guid) <= 500 else link
            raw_date = (item.findtext("pubDate") or "").strip()
            published = parsedate_to_datetime(raw_date)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            content_html = item.findtext(CONTENT_NS) or item.findtext("description") or ""
            if not content_html.strip():
                raise FeedError("missing RSS content")
            body = html_to_markdown(content_html)
            if not body:
                raise FeedError("RSS content became empty after sanitizing")
            author = (item.findtext(DC_CREATOR) or "").strip()
            person_ids: tuple[str, ...] = ()
            roles: dict[str, str] = {}
            if author:
                author_id = _author_id(author)
                people.setdefault(author_id, Person(
                    source_person_id=author_id,
                    name=author,
                    profile_url=source.site_url,
                    tagline=f"Author at {publication}",
                    metadata={"identity_basis": "rss_dc_creator", "publication": publication},
                    content_hash=hashlib.sha256((author + "\n" + publication).encode()).hexdigest(),
                ))
                person_ids = (author_id,)
                roles[author_id] = "author"
            excerpt_only, excerpt_reason = _is_excerpt(body, content_html)
            relevant, relevance_reasons = relevance_prefilter(title, body)
            substantive = f"{title.strip()}\n{body.strip()}"
            digest = hashlib.sha256(substantive.encode("utf-8")).hexdigest()
            categories = [x.text.strip() for x in item.findall("category") if x.text and x.text.strip()]
            description = html_to_markdown(item.findtext("description") or "")
            metadata = {
                "content_kind": "substack_rss_item",
                "source_attribution": {
                    "publication": publication,
                    "author": author or None,
                    "requested_feed_url": source.feed_url,
                    "resolved_feed_url": resolved_url or source.feed_url,
                    "canonical_permalink": link,
                    "guid": guid or None,
                },
                "rss": {"published_at": published.isoformat(), "categories": categories, "window_position": position},
                "content_access": {"excerpt_only": excerpt_only, "reason": excerpt_reason, "paywall_bypassed": False},
                "relevance": {"founder_story_candidate": relevant, "method": "conservative_prefilter_v2", "reasons": list(relevance_reasons)},
                "ai_extraction": {"status": "pending_not_configured" if relevant else "skipped_irrelevant", "schema_version": 1},
                "coverage": {"scope": "rss_window", "complete_archive": False},
                "raw_html_sha256": hashlib.sha256(content_html.encode("utf-8")).hexdigest(),
            }
            stories.append(Story(
                source_item_id=source_item_id,
                title=title,
                subtitle=description[:500],
                source_url=link,
                published_at=published.date(),
                raw_markdown=body,
                body_markdown=body,
                person_ids=person_ids,
                person_roles=roles,
                metadata=metadata,
                content_hash=digest,
            ))
        except (FeedError, ValueError, OverflowError) as exc:
            errors.append(f"item {position}: {exc}")
    if not stories:
        raise FeedError("no valid RSS items; refusing empty sync")
    dates = sorted(story.published_at for story in stories)
    warnings: list[str] = []
    if len(items) >= 20:
        warnings.append("feed returned 20 items; older public posts may exist outside the RSS window")
    revision_data = "\n".join(f"{story.source_item_id}:{story.content_hash}" for story in stories)
    revision = hashlib.sha256(revision_data.encode()).hexdigest()
    return Collection(
        tuple(people.values()), tuple(stories), revision,
        source_key=source.key, source_name=publication or source.name,
        source_url=source.feed_url, source_kind="substack_rss",
        errors=tuple(errors), warnings=tuple(warnings),
        monitoring={"item_count": len(stories), "feed_item_count": len(items), "oldest_date": dates[0].isoformat(), "newest_date": dates[-1].isoformat(), "coverage": "rss_window"},
    )


class SubstackRSSAdapter:
    def __init__(self, source: SubstackSource, content_authorized: bool = False):
        self.source = source
        self.content_authorized = content_authorized

    def collect(self) -> Collection:
        try:
            if not self.content_authorized:
                raise FeedError("Substack content collection requires explicit authorization")
            xml, resolved, _content_type = fetch_feed(self.source.feed_url)
            return parse_feed(self.source, xml, resolved)
        except Exception as exc:
            return Collection(
                (), (), "",
                source_key=self.source.key, source_name=self.source.name, source_url=self.source.feed_url,
                source_kind="substack_rss",
                errors=(f"feed collection failed: {type(exc).__name__}: {exc}",),
            )
