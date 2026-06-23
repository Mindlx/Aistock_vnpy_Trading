"""Intelligence service.

Core business logic for RSS/Atom intelligence source management and feed fetching.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests
from sqlalchemy import exc as sa_exc

from src.repositories.intelligence_repo import IntelligenceRepository
from src.storage import (
    INTELLIGENCE_ITEM_NULL_SCOPE_VALUE,
    DatabaseManager,
    IntelligenceItem,
    IntelligenceSource,
)

logger = logging.getLogger(__name__)

_BUILTIN_SOURCE_TEMPLATES = [
    {
        "template_id": "sec-company-news",
        "name": "SEC Latest Filings",
        "source_type": "rss",
        "url": "https://www.sec.gov/news/pressreleases.rss",
        "scope_type": "market",
        "market": "us",
        "description": "SEC official press release RSS feed for US market.",
    },
    {
        "template_id": "hkex-news",
        "name": "HKEX Market News",
        "source_type": "rss",
        "url": "https://www.hkex.com.hk/Services/RSS-Feeds/News-Releases?sc_lang=en",
        "scope_type": "market",
        "market": "hk",
        "description": "HKEX public news entry for Hong Kong market.",
    },
]

_FETCH_TIMEOUT_SECONDS = 30
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


class IntelligenceServiceError(ValueError):
    """Raised when intelligence service input is invalid."""

    error_code = "validation_error"


class IntelligenceSourceNotFoundError(IntelligenceServiceError):
    """Raised when an intelligence source is not found."""

    error_code = "not_found"


class IntelligenceFetchError(IntelligenceServiceError):
    """Raised when feed fetching or parsing fails."""

    error_code = "fetch_error"


class IntelligenceService:
    """Business logic for intelligence source management and feed fetching."""

    def __init__(self, db_manager: DatabaseManager | None = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.repo = IntelligenceRepository(self.db)

    # ---- URL validation ----

    @staticmethod
    def validate_source_url(url: str) -> bool:
        """Basic URL sanity check: must be http/https with a valid host."""
        try:
            parsed = urlparse(url)
            return parsed.scheme in ("http", "https") and bool(parsed.netloc)
        except Exception:
            return False

    # ---- Source CRUD ----

    def create_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload.get("url") or "").strip()
        if not self.validate_source_url(url):
            raise IntelligenceServiceError(f"Invalid source URL: {url}")

        name = str(payload.get("name") or "").strip()
        if not name:
            raise IntelligenceServiceError("name is required")

        existing = self.repo.get_source_by_name(name)
        if existing is not None:
            raise IntelligenceServiceError(f"Source with name '{name}' already exists")

        fields = self._normalize_source_fields(payload)
        row = self.repo.create_source(fields)
        return self._serialize_source(row)

    def get_source(self, source_id: int) -> dict[str, Any]:
        row = self.repo.get_source(source_id)
        if row is None:
            raise IntelligenceSourceNotFoundError(f"Intelligence source not found: {source_id}")
        return self._serialize_source(row)

    def update_source(self, source_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        row = self.repo.get_source(source_id)
        if row is None:
            raise IntelligenceSourceNotFoundError(f"Intelligence source not found: {source_id}")
        if not payload:
            raise IntelligenceServiceError("No fields provided for update")

        url = payload.get("url")
        if url is not None and not self.validate_source_url(str(url)):
            raise IntelligenceServiceError(f"Invalid source URL: {url}")

        name = payload.get("name")
        if name is not None:
            existing = self.repo.get_source_by_name(str(name))
            if existing is not None and existing.id != source_id:
                raise IntelligenceServiceError(f"Source with name '{name}' already exists")

        fields = self._normalize_source_fields(payload, partial=True)
        updated = self.repo.update_source(source_id, fields)
        if updated is None:
            raise IntelligenceSourceNotFoundError(f"Intelligence source not found: {source_id}")
        return self._serialize_source(updated)

    def delete_source(self, source_id: int) -> bool:
        return self.repo.delete_source(source_id)

    def list_sources(
        self,
        *,
        enabled: bool | None = None,
        source_type: str | None = None,
        scope_type: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        rows, total = self.repo.list_sources(
            enabled=enabled,
            source_type=source_type,
            scope_type=scope_type,
            market=market,
        )
        items = [self._serialize_source(row) for row in rows]
        return {"items": items, "total": total}

    # ---- Default sources ----

    def create_default_sources(self) -> tuple[int, list[dict[str, Any]]]:
        """Create built-in source templates that do not already exist."""
        created_count = 0
        created_sources: list[dict[str, Any]] = []

        for template in _BUILTIN_SOURCE_TEMPLATES:
            name = template["name"]
            existing = self.repo.get_source_by_name(name)
            if existing is not None:
                created_sources.append(self._serialize_source(existing))
                continue

            fields = self._normalize_source_fields(template)
            try:
                row = self.repo.create_source(fields)
                created_count += 1
                created_sources.append(self._serialize_source(row))
            except sa_exc.IntegrityError:
                logger.warning("Default source '%s' skipped (integrity error)", name)
            except Exception as exc:
                logger.error("Failed to create default source '%s': %s", name, exc)

        return created_count, created_sources

    # ---- Source testing ----

    def test_source_url(self, url: str) -> dict[str, Any]:
        """Test-fetch a URL and return parsed feed metadata without saving."""
        if not self.validate_source_url(url):
            raise IntelligenceServiceError(f"Invalid source URL: {url}")

        try:
            resp = requests.get(url, timeout=_FETCH_TIMEOUT_SECONDS, headers=_REQUEST_HEADERS)
            resp.raise_for_status()
        except requests.RequestException as exc:
            return {
                "success": False,
                "title": None,
                "description": None,
                "entries_count": 0,
                "sample_entries": [],
                "error": f"HTTP request failed: {exc}",
            }

        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            # Attempt XML fallback parse
            try:
                entries = self._parse_xml_fallback(resp.content)
                if entries:
                    feed_meta = {"title": None, "description": None, "entries": entries}
                    feed = type("Feed", (), {"feed": type("FeedMeta", (), {"title": None, "description": None})(), "entries": entries, "bozo": False})()  # noqa: E501
            except Exception:
                pass

        title = None
        description = None
        if hasattr(feed, "feed"):
            title = getattr(feed.feed, "title", None)
            description = getattr(feed.feed, "description", None)

        sample_entries = []
        for entry in feed.entries[:5]:
            sample_entries.append(
                {
                    "title": entry.get("title", "")[:200],
                    "url": entry.get("link", "")[:500],
                    "summary": (entry.get("summary") or entry.get("description") or "")[:300],
                }
            )

        return {
            "success": True,
            "title": str(title) if title else None,
            "description": str(description) if description else None,
            "entries_count": len(feed.entries),
            "sample_entries": sample_entries,
            "error": None,
        }

    # ---- Feed fetching ----

    def fetch_source(self, source_id: int) -> dict[str, Any]:
        """Fetch and parse a single intelligence source, saving new items."""
        row = self.repo.get_source(source_id)
        if row is None:
            raise IntelligenceSourceNotFoundError(f"Intelligence source not found: {source_id}")

        entries, error = self._fetch_and_parse_feed(row.url)
        if error:
            self.repo.update_source(
                source_id,
                {
                    "last_status": "error",
                    "last_error": error[:1000],
                    "last_fetched_at": datetime.now(),
                },
            )
            return {
                "source_id": source_id,
                "source_name": row.name,
                "status": "error",
                "items_fetched": 0,
                "error": error,
            }

        items_saved = 0
        for entry in entries:
            try:
                self._save_item(row, entry)
                items_saved += 1
            except sa_exc.IntegrityError:
                pass  # duplicate — skip silently
            except Exception as exc:
                logger.debug("Failed to save intelligence item: %s", exc)

        self.repo.update_source(
            source_id,
            {
                "last_status": "ok",
                "last_error": None,
                "last_fetched_at": datetime.now(),
            },
        )

        return {
            "source_id": source_id,
            "source_name": row.name,
            "status": "ok",
            "items_fetched": items_saved,
            "error": None,
        }

    def fetch_all_enabled(self) -> list[dict[str, Any]]:
        """Fetch all enabled sources and return per-source results."""
        sources = self.repo.list_enabled_sources()
        results: list[dict[str, Any]] = []
        for source in sources:
            try:
                result = self.fetch_source(source.id)
                results.append(result)
            except Exception as exc:
                results.append(
                    {
                        "source_id": source.id,
                        "source_name": source.name,
                        "status": "error",
                        "items_fetched": 0,
                        "error": str(exc),
                    }
                )
        return results

    def _fetch_and_parse_feed(
        self, url: str
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch feed content and parse entries. Returns (entries, error)."""
        try:
            resp = requests.get(url, timeout=_FETCH_TIMEOUT_SECONDS, headers=_REQUEST_HEADERS)
            resp.raise_for_status()
        except requests.RequestException as exc:
            return [], f"HTTP request failed: {exc}"

        content = resp.content

        # Primary: feedparser
        feed = feedparser.parse(content)
        if not feed.bozo or feed.entries:
            entries = self._extract_entries(feed)
            if entries:
                return entries, None

        # Fallback: manual XML parse
        try:
            xml_entries = self._parse_xml_fallback(content)
            if xml_entries:
                return xml_entries, None
        except Exception as exc:
            logger.debug("XML fallback parse failed: %s", exc)

        if feed.bozo:
            return [], f"Feed parse error: {feed.bozo_exception}"
        return [], "No entries found in feed"

    def _extract_entries(self, feed) -> list[dict[str, Any]]:
        """Extract entries from a feedparser result."""
        entries: list[dict[str, Any]] = []
        feed_source = None
        if hasattr(feed, "feed"):
            feed_source = getattr(feed.feed, "title", None)

        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title and not link:
                continue

            published_dt = self._parse_feed_date(
                entry.get("published_parsed") or entry.get("updated_parsed")
            )

            entries.append(
                {
                    "title": title[:300],
                    "summary": (entry.get("summary") or entry.get("description") or "")[:5000],
                    "url": link[:1000],
                    "source": feed_source or "",
                    "published_at": published_dt,
                }
            )
        return entries

    def _parse_xml_fallback(self, content: bytes) -> list[dict[str, Any]]:
        """Fallback XML parser using xml.etree.ElementTree for basic RSS/Atom feeds."""
        import xml.etree.ElementTree as ET

        root = ET.fromstring(content)
        entries: list[dict[str, Any]] = []

        # RSS 2.0
        items = root.findall(".//item")
        if items:
            for item in items:
                title = _xml_text(item.find("title"))
                link = _xml_text(item.find("link"))
                if not title and not link:
                    continue
                entries.append(
                    {
                        "title": (title or "")[:300],
                        "summary": (_xml_text(item.find("description")) or "")[:5000],
                        "url": (link or "")[:1000],
                        "source": _xml_text(root.find("./channel/title")) or "",
                        "published_at": self._parse_rss_date(_xml_text(item.find("pubDate"))),
                    }
                )
            return entries

        # Atom 1.0
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        atom_entries = root.findall("atom:entry", ns) or root.findall("entry", ns)
        if atom_entries:
            feed_title = _xml_text(root.find("atom:title", ns)) or _xml_text(root.find("title", ns)) or ""
            for entry in atom_entries:
                title = _xml_text(entry.find("atom:title", ns)) or _xml_text(entry.find("title", ns)) or ""
                link_el = entry.find("atom:link", ns) or entry.find("link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                if not title and not link:
                    continue
                entries.append(
                    {
                        "title": title[:300],
                        "summary": (
                            _xml_text(entry.find("atom:summary", ns))
                            or _xml_text(entry.find("atom:content", ns))
                            or _xml_text(entry.find("summary", ns))
                            or ""
                        )[:5000],
                        "url": link[:1000],
                        "source": feed_title,
                        "published_at": self._parse_atom_date(
                            _xml_text(entry.find("atom:published", ns))
                            or _xml_text(entry.find("atom:updated", ns))
                            or _xml_text(entry.find("published", ns))
                            or _xml_text(entry.find("updated", ns))
                        ),
                    }
                )
            return entries

        return entries

    # ---- Item persistence ----

    def _save_item(self, source: IntelligenceSource, entry: dict[str, Any]) -> None:
        """Save one feed entry as an IntelligenceItem (dedup by unique constraint)."""
        scope_value = source.scope_value or INTELLIGENCE_ITEM_NULL_SCOPE_VALUE

        if self.repo.item_exists(
            source_id=source.id,
            url=entry["url"],
            scope_type=source.scope_type,
            scope_value=scope_value,
            market=source.market,
        ):
            return

        fields: dict[str, Any] = {
            "source_id": source.id,
            "source_name": source.name,
            "source_type": source.source_type,
            "title": entry["title"],
            "summary": entry.get("summary"),
            "url": entry["url"],
            "source": entry.get("source"),
            "published_at": entry.get("published_at"),
            "fetched_at": datetime.now(),
            "scope_type": source.scope_type,
            "scope_value": scope_value,
            "market": source.market,
        }
        self.repo.create_item(fields)

    # ---- Serialization ----

    def _serialize_source(self, row: IntelligenceSource) -> dict[str, Any]:
        item_count = self.repo.count_source_items(row.id)
        return {
            "id": row.id,
            "name": row.name,
            "source_type": row.source_type,
            "url": row.url,
            "enabled": bool(row.enabled),
            "scope_type": row.scope_type,
            "scope_value": row.scope_value,
            "market": row.market,
            "description": row.description,
            "last_status": row.last_status,
            "last_error": row.last_error,
            "last_fetched_at": row.last_fetched_at.isoformat() if row.last_fetched_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "item_count": item_count,
        }

    @staticmethod
    def serialize_item(row: IntelligenceItem) -> dict[str, Any]:
        return {
            "id": row.id,
            "source_id": row.source_id,
            "source_name": row.source_name,
            "source_type": row.source_type,
            "title": row.title,
            "summary": row.summary,
            "url": row.url,
            "source": row.source,
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
            "scope_type": row.scope_type,
            "scope_value": row.scope_value,
            "market": row.market,
        }

    # ---- Helpers ----

    @staticmethod
    def _normalize_source_fields(payload: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for key in ("name", "source_type", "url", "scope_type", "market", "description"):
            if key in payload:
                value = payload[key]
                fields[key] = str(value).strip() if isinstance(value, str) else value

        if not partial:
            fields.setdefault("source_type", "rss")
            fields.setdefault("scope_type", "market")
            fields.setdefault("market", "cn")
            fields.setdefault("enabled", True)

        if "enabled" in payload:
            fields["enabled"] = bool(payload["enabled"])
        if "scope_value" in payload:
            v = payload["scope_value"]
            fields["scope_value"] = str(v).strip() if v else None

        return fields

    @staticmethod
    def _parse_feed_date(time_struct) -> datetime | None:
        """Convert a time.struct_time (from feedparser) to datetime."""
        if time_struct is None:
            return None
        try:
            from calendar import timegm

            return datetime.fromtimestamp(timegm(time_struct), tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return None

    @staticmethod
    def _parse_rss_date(date_str: str | None) -> datetime | None:
        """Parse RSS pubDate string."""
        if not date_str:
            return None
        try:
            from email.utils import parsedate_to_datetime

            return parsedate_to_datetime(date_str).replace(tzinfo=None)
        except Exception:
            return None

    @staticmethod
    def _parse_atom_date(date_str: str | None) -> datetime | None:
        """Parse Atom ISO 8601 date string."""
        if not date_str:
            return None
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            return None


def _xml_text(element) -> str:
    """Return element text or empty string."""
    if element is None:
        return ""
    return (element.text or "").strip()
