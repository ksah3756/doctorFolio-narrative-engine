"""EDGAR RSS source document fetchers."""

from __future__ import annotations

import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import date, datetime
from typing import Final, Literal
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict

from dcf_engine.claim import SOURCE_RELIABILITY, SourceRef

type EdgarFilingType = Literal["8-K", "10-Q", "10-K"]
type FeedReader = Callable[[str], str]

_ATOM_NS: Final[dict[str, str]] = {"atom": "http://www.w3.org/2005/Atom"}
_SUPPORTED_FILINGS: Final[frozenset[str]] = frozenset({"8-K", "10-Q", "10-K"})
_SEC_BROWSE_URL: Final[str] = "https://www.sec.gov/cgi-bin/browse-edgar"
_SEC_USER_AGENT: Final[str] = "dcf-narrative-engine/0.1 contact=research@example.com"
_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")


class SourceDocument(BaseModel):
    """Fetched source text with claim-compatible source metadata."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    url: str
    title: str
    published_date: date
    source_ref: SourceRef
    raw_text: str

    @property
    def discovery_channel(self) -> str:
        return self.source_ref.discovery_channel

    @property
    def content_source(self) -> str:
        return self.source_ref.content_source

    @property
    def source_reliability(self) -> float:
        return self.source_ref.source_reliability


class EdgarRssFetcher:
    """Fetch and parse NVDA SEC EDGAR Atom feeds for 8-K, 10-Q, and 10-K filings."""

    def __init__(self, *, ticker: str = "NVDA", reader: FeedReader | None = None) -> None:
        self._ticker = ticker
        self._reader = reader or _read_url

    def fetch_recent(self, filing_type: str, count: int = 10) -> list[SourceDocument]:
        if filing_type not in _SUPPORTED_FILINGS:
            raise ValueError("filing_type must be one of 8-K, 10-Q, or 10-K")
        if count < 1:
            raise ValueError("count must be positive")

        feed_text = self._reader(_edgar_feed_url(self._ticker, filing_type, count))
        documents: list[SourceDocument] = []
        for entry in _feed_entries(feed_text):
            document = _document_from_entry(entry, filing_type)
            if document is not None:
                documents.append(document)
            if len(documents) == count:
                break
        return documents


def _read_url(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _SEC_USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    if not isinstance(payload, bytes):
        raise TypeError("EDGAR response payload must be bytes")
    return payload.decode("utf-8")


def _edgar_feed_url(ticker: str, filing_type: str, count: int) -> str:
    query = urlencode(
        {
            "action": "getcompany",
            "CIK": ticker,
            "type": filing_type,
            "dateb": "",
            "owner": "include",
            "count": str(count),
            "search_text": "",
            "output": "atom",
        }
    )
    return f"{_SEC_BROWSE_URL}?{query}"


def _feed_entries(feed_text: str) -> list[ET.Element]:
    try:
        root = ET.fromstring(feed_text)
    except ET.ParseError:
        return []
    return root.findall("atom:entry", _ATOM_NS)


def _document_from_entry(entry: ET.Element, requested_filing_type: str) -> SourceDocument | None:
    title = _required_text(entry, "atom:title")
    url = _entry_href(entry)
    published_date = _entry_date(entry)
    filing_type = _entry_filing_type(entry)
    raw_text = _entry_summary(entry)

    if (
        title is None
        or url is None
        or published_date is None
        or filing_type != requested_filing_type
        or filing_type not in _SUPPORTED_FILINGS
        or raw_text is None
    ):
        return None

    source_ref = SourceRef(
        discovery_channel="edgar_api",
        content_source=filing_type,
        source_reliability=SOURCE_RELIABILITY[filing_type],
    )
    return SourceDocument(
        doc_id=_doc_id(url),
        url=url,
        title=title,
        published_date=published_date,
        source_ref=source_ref,
        raw_text=raw_text,
    )


def _required_text(entry: ET.Element, path: str) -> str | None:
    value = entry.findtext(path, namespaces=_ATOM_NS)
    if value is None:
        return None
    stripped = " ".join(value.split())
    return stripped or None


def _entry_href(entry: ET.Element) -> str | None:
    for link in entry.findall("atom:link", _ATOM_NS):
        href = link.attrib.get("href")
        if href:
            return href
    return None


def _entry_date(entry: ET.Element) -> date | None:
    value = _required_text(entry, "atom:updated") or _required_text(entry, "atom:published")
    if value is None:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return None


def _entry_filing_type(entry: ET.Element) -> str | None:
    for category in entry.findall("atom:category", _ATOM_NS):
        term = category.attrib.get("term")
        if term:
            return term
    title = _required_text(entry, "atom:title")
    if title is None:
        return None
    prefix = title.split(" - ", maxsplit=1)[0]
    return prefix if prefix in _SUPPORTED_FILINGS else None


def _entry_summary(entry: ET.Element) -> str | None:
    summary = _required_text(entry, "atom:summary")
    if summary is None:
        return None
    unescaped = html.unescape(summary)
    text = _TAG_RE.sub(" ", unescaped)
    stripped = " ".join(text.split())
    return stripped or None


def _doc_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]
