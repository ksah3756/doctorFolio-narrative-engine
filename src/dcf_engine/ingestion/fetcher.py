"""Source document fetchers for ingestion input boundaries."""

from __future__ import annotations

import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlencode, urljoin

from pydantic import BaseModel, ConfigDict

from dcf_engine.claim import SOURCE_RELIABILITY, SourceRef

type EdgarFilingType = Literal["8-K", "10-Q", "10-K"]
type FeedReader = Callable[[str], str]

_ATOM_NS: Final[dict[str, str]] = {"atom": "http://www.w3.org/2005/Atom"}
_SUPPORTED_FILINGS: Final[frozenset[str]] = frozenset({"8-K", "10-Q", "10-K"})
_SEC_BROWSE_URL: Final[str] = "https://www.sec.gov/cgi-bin/browse-edgar"
_SEC_USER_AGENT: Final[str] = "dcf-narrative-engine/0.1 contact=research@example.com"
_REUTERS_RSS_URL: Final[str] = "https://www.reuters.com/technology/rss"
_IGNORED_HTML_ELEMENTS: Final[frozenset[str]] = frozenset({"script", "style"})
_NVDA_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:NVDA|NVIDIA)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _FilingDocumentRow:
    href: str
    cells: tuple[str, ...]


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
            document = _document_from_entry(entry, filing_type, self._reader)
            if document is not None:
                documents.append(document)
            if len(documents) == count:
                break
        return documents


class ReutersRssFetcher:
    """Fetch and parse Reuters RSS entries that mention NVIDIA or NVDA."""

    def __init__(
        self,
        *,
        feed_url: str = _REUTERS_RSS_URL,
        reader: FeedReader | None = None,
    ) -> None:
        self._feed_url = feed_url
        self._reader = reader or _read_url

    def fetch(self) -> list[SourceDocument]:
        feed_text = self._reader(self._feed_url)
        documents: list[SourceDocument] = []
        for item in _rss_items(feed_text):
            document = _reuters_document_from_item(item)
            if document is not None:
                documents.append(document)
        return documents


class ManualTranscriptLoader:
    """Load a caller-provided local transcript as one earnings-call source document."""

    def __init__(
        self,
        *,
        path: Path,
        title: str,
        published_date: date,
        source_url: str | None = None,
    ) -> None:
        self._path = path
        self._title = title
        self._published_date = published_date
        self._source_url = source_url or path.as_uri()

    def fetch(self) -> list[SourceDocument]:
        title = _normalize_text(self._title)
        raw_text = _normalize_text(self._path.read_text())
        source_ref = SourceRef(
            discovery_channel="direct",
            content_source="earnings_call",
            source_reliability=SOURCE_RELIABILITY["earnings_call"],
        )
        return [
            SourceDocument(
                doc_id=_doc_id(f"{self._source_url}|{self._published_date.isoformat()}"),
                url=self._source_url,
                title=title,
                published_date=self._published_date,
                source_ref=source_ref,
                raw_text=raw_text,
            )
        ]


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


def _rss_items(feed_text: str) -> list[ET.Element]:
    try:
        root = ET.fromstring(feed_text)
    except ET.ParseError:
        return []
    return root.findall("./channel/item")


def _document_from_entry(
    entry: ET.Element,
    requested_filing_type: str,
    reader: FeedReader,
) -> SourceDocument | None:
    title = _required_text(entry, "atom:title")
    url = _entry_href(entry)
    published_date = _entry_date(entry)
    filing_type = _entry_filing_type(entry)

    if (
        title is None
        or url is None
        or published_date is None
        or filing_type != requested_filing_type
        or filing_type not in _SUPPORTED_FILINGS
    ):
        return None

    try:
        # EDGAR 항목은 독립 입력이므로 한 문서 읽기 실패가 전체 피드를 막지 않게 한다.
        document_url, document_text = _resolve_filing_document(url, filing_type, reader)
    except (OSError, TypeError, UnicodeError):
        return None
    raw_text = _filing_body_text(document_text)
    if raw_text is None:
        return None

    source_ref = SourceRef(
        discovery_channel="edgar_api",
        content_source=filing_type,
        source_reliability=SOURCE_RELIABILITY[filing_type],
    )
    return SourceDocument(
        doc_id=_doc_id(document_url),
        url=document_url,
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


def _reuters_document_from_item(item: ET.Element) -> SourceDocument | None:
    title = _rss_required_text(item, "title")
    url = _rss_required_text(item, "link")
    published_date = _rss_date(item)
    raw_text = _rss_description(item)

    if title is None or url is None or published_date is None or raw_text is None:
        return None
    if not _is_nvda_relevant(title, raw_text):
        return None

    source_ref = SourceRef(
        discovery_channel="rss_aggregator",
        content_source="reuters",
        source_reliability=SOURCE_RELIABILITY["reuters"],
    )
    return SourceDocument(
        doc_id=_doc_id(url),
        url=url,
        title=title,
        published_date=published_date,
        source_ref=source_ref,
        raw_text=raw_text,
    )


def _rss_required_text(item: ET.Element, path: str) -> str | None:
    value = item.findtext(path)
    if value is None:
        return None
    normalized = _normalize_text(value)
    return normalized or None


def _rss_description(item: ET.Element) -> str | None:
    description = item.find("description")
    if description is None:
        return None
    value = "".join(description.itertext())
    normalized = _normalize_html_text(value)
    return normalized or None


def _rss_date(item: ET.Element) -> date | None:
    value = _rss_required_text(item, "pubDate")
    if value is None:
        return None
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def _is_nvda_relevant(title: str, raw_text: str) -> bool:
    return _NVDA_RE.search(f"{title} {raw_text}") is not None


class _SecFilingIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_FilingDocumentRow] = []
        self._inside_row = False
        self._inside_cell = False
        self._row_href: str | None = None
        self._current_cells: list[str] = []
        self._current_cell_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "tr":
            self._inside_row = True
            self._inside_cell = False
            self._row_href = None
            self._current_cells = []
            self._current_cell_chunks = []
            return
        if not self._inside_row:
            return
        if normalized_tag in {"td", "th"}:
            self._inside_cell = True
            self._current_cell_chunks = []
            return
        if normalized_tag == "a" and self._inside_cell and self._row_href is None:
            self._row_href = _attribute_value(attrs, "href")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in {"td", "th"} and self._inside_cell:
            cell = _normalize_text(" ".join(self._current_cell_chunks))
            self._current_cells.append(cell)
            self._inside_cell = False
            self._current_cell_chunks = []
            return
        if normalized_tag == "tr" and self._inside_row:
            if self._row_href is not None and self._current_cells:
                self.rows.append(
                    _FilingDocumentRow(
                        href=self._row_href,
                        cells=tuple(self._current_cells),
                    )
                )
            self._inside_row = False
            self._inside_cell = False
            self._row_href = None
            self._current_cells = []
            self._current_cell_chunks = []

    def handle_data(self, data: str) -> None:
        if self._inside_cell:
            self._current_cell_chunks.append(data)


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in _IGNORED_HTML_ELEMENTS:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _IGNORED_HTML_ELEMENTS and self._ignored_depth > 0:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return _normalize_text(" ".join(self._chunks))


def _filing_body_text(value: str) -> str | None:
    normalized = _normalize_html_text(value)
    return normalized or None


def _resolve_filing_document(
    url: str,
    filing_type: str,
    reader: FeedReader,
) -> tuple[str, str]:
    document_text = reader(url)
    if not _is_sec_filing_index_url(url):
        return url, document_text

    document_url = _primary_filing_document_url(document_text, url, filing_type)
    if document_url is None:
        return url, ""
    return document_url, reader(document_url)


def _primary_filing_document_url(
    index_html: str,
    index_url: str,
    filing_type: str,
) -> str | None:
    parser = _SecFilingIndexParser()
    parser.feed(index_html)
    parser.close()

    for row in parser.rows:
        if _row_matches_filing_type(row.cells, filing_type):
            return urljoin(index_url, row.href)
    return None


def _row_matches_filing_type(cells: tuple[str, ...], filing_type: str) -> bool:
    return any(cell.upper() == filing_type for cell in cells)


def _is_sec_filing_index_url(url: str) -> bool:
    path = url.split("?", maxsplit=1)[0].lower()
    return path.endswith(("-index.htm", "-index.html"))


def _attribute_value(attrs: list[tuple[str, str | None]], name: str) -> str | None:
    for attr_name, attr_value in attrs:
        if attr_name.lower() == name and attr_value:
            return attr_value
    return None


def _normalize_html_text(value: str) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(html.unescape(value))
    parser.close()
    return parser.text()


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _doc_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]
