from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date
from pathlib import Path

from dcf_engine.claim import SOURCE_RELIABILITY, SourceRef
from dcf_engine.ingestion import (
    EdgarRssFetcher,
    ManualTranscriptLoader,
    ReutersRssFetcher,
    SourceDocument,
)


def _edgar_feed(entries: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>EDGAR Search Results</title>
  {entries}
</feed>
"""


def _entry(
    *,
    title: str,
    href: str,
    filing_type: str | None,
    updated: str = "2026-06-24T18:01:05-04:00",
    summary: str = "NVIDIA filed a current report for material corporate events.",
) -> str:
    category = (
        f'<category label="form type" scheme="https://www.sec.gov/" term="{filing_type}" />'
        if filing_type is not None
        else ""
    )
    return f"""
  <entry>
    <title>{title}</title>
    <link rel="alternate" type="text/html" href="{href}" />
    {category}
    <updated>{updated}</updated>
    <summary type="html">{summary}</summary>
  </entry>
"""


def _filing_html(body: str = "NVIDIA filed a full current report body.") -> str:
    return f"""<!doctype html>
<html>
  <head><title>Filing document</title></head>
  <body>{body}</body>
</html>
"""


def _filing_index_html(primary_document_href: str) -> str:
    return f"""<!doctype html>
<html>
  <head><title>SEC Filing Detail</title></head>
  <body>
    <table class="tableFile" summary="Document Format Files">
      <tr>
        <th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th>
      </tr>
      <tr>
        <td>1</td>
        <td>FORM 8-K</td>
        <td><a href="{primary_document_href}">nvda-20260624.htm</a></td>
        <td>8-K</td>
        <td>51234</td>
      </tr>
      <tr>
        <td>2</td>
        <td>EX-99.1</td>
        <td><a href="/Archives/edgar/data/1045810/exhibit991.htm">exhibit991.htm</a></td>
        <td>EX-99.1</td>
        <td>12345</td>
      </tr>
    </table>
  </body>
</html>
"""


def _reader(
    feed_text: str,
    filing_texts: dict[str, str] | None = None,
    calls: list[str] | None = None,
) -> Callable[[str], str]:
    def read(url: str) -> str:
        if calls is not None:
            calls.append(url)
        if "output=atom" in url:
            assert "CIK=NVDA" in url
            return feed_text
        if filing_texts is not None and url in filing_texts:
            return filing_texts[url]
        raise AssertionError(f"unexpected URL read: {url}")

    return read


def _rss_feed(items: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Reuters Business News</title>
    {items}
  </channel>
</rss>
"""


def _rss_item(
    *,
    title: str,
    link: str,
    pub_date: str = "Tue, 24 Jun 2026 18:01:05 GMT",
    description: str = "NVIDIA shares rose after analysts cited Blackwell demand.",
) -> str:
    return f"""
    <item>
      <title>{title}</title>
      <link>{link}</link>
      <pubDate>{pub_date}</pubDate>
      <description>{description}</description>
    </item>
"""


def _rss_reader(feed_text: str) -> Callable[[str], str]:
    def read(url: str) -> str:
        assert "reuters" in url.lower()
        return feed_text

    return read


def test_nvda_8k_rss_entry_parses_into_source_document() -> None:
    href = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123/nvda-8k.htm"
    feed = _edgar_feed(
        _entry(
            title="8-K - NVIDIA CORP (0001045810) (Filer)",
            href=href,
            filing_type="8-K",
        )
    )

    docs = EdgarRssFetcher(
        reader=_reader(feed, {href: _filing_html("NVIDIA filed a full current report body.")})
    ).fetch_recent("8-K", count=10)

    assert docs == [
        SourceDocument(
            doc_id=hashlib.sha256(href.encode()).hexdigest()[:12],
            url=href,
            title="8-K - NVIDIA CORP (0001045810) (Filer)",
            published_date=date(2026, 6, 24),
            source_ref=SourceRef(
                discovery_channel="edgar_api",
                content_source="8-K",
                source_reliability=SOURCE_RELIABILITY["8-K"],
            ),
            raw_text="Filing document NVIDIA filed a full current report body.",
        )
    ]


def test_doc_id_is_deterministic_sha256_url_prefix() -> None:
    href = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000124/nvda-10q.htm"
    feed = _edgar_feed(
        _entry(
            title="10-Q - NVIDIA CORP (0001045810) (Filer)",
            href=href,
            filing_type="10-Q",
        )
    )

    [doc] = EdgarRssFetcher(
        reader=_reader(feed, {href: _filing_html("NVIDIA filed a full quarterly report body.")})
    ).fetch_recent("10-Q", count=1)

    assert doc.doc_id == hashlib.sha256(href.encode()).hexdigest()[:12]


def test_content_source_and_reliability_follow_claim_source_table() -> None:
    href = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000125/nvda-10k.htm"
    feed = _edgar_feed(
        _entry(
            title="10-K - NVIDIA CORP (0001045810) (Filer)",
            href=href,
            filing_type="10-K",
        )
    )

    [doc] = EdgarRssFetcher(
        reader=_reader(feed, {href: _filing_html("NVIDIA filed a full annual report body.")})
    ).fetch_recent("10-K", count=1)

    assert doc.source_ref.content_source == "10-K"
    assert doc.source_ref.source_reliability == SOURCE_RELIABILITY["10-K"]


def test_fetch_recent_honors_count_and_preserves_fixture_order() -> None:
    first_href = "https://www.sec.gov/Archives/edgar/data/1045810/first.htm"
    second_href = "https://www.sec.gov/Archives/edgar/data/1045810/second.htm"
    feed = _edgar_feed(
        _entry(
            title="8-K - first",
            href=first_href,
            filing_type="8-K",
            updated="2026-06-25T09:00:00-04:00",
        )
        + _entry(
            title="8-K - second",
            href=second_href,
            filing_type="8-K",
            updated="2026-06-24T09:00:00-04:00",
        )
    )

    docs = EdgarRssFetcher(
        reader=_reader(
            feed,
            {
                first_href: _filing_html("First filing body."),
                second_href: _filing_html("Second filing body."),
            },
        )
    ).fetch_recent("8-K", count=1)

    assert [doc.title for doc in docs] == ["8-K - first"]


def test_malformed_entries_do_not_produce_invalid_documents() -> None:
    valid_href = "https://www.sec.gov/Archives/edgar/data/1045810/valid.htm"
    feed = _edgar_feed(
        """
  <entry>
    <title>8-K - missing link</title>
    <category label="form type" scheme="https://www.sec.gov/" term="8-K" />
    <updated>2026-06-24T18:01:05-04:00</updated>
    <summary type="html">No link should skip this entry.</summary>
  </entry>
"""
        + _entry(
            title="8-K - valid",
            href=valid_href,
            filing_type="8-K",
        )
    )

    docs = EdgarRssFetcher(
        reader=_reader(feed, {valid_href: _filing_html("Valid filing body.")})
    ).fetch_recent("8-K", count=10)

    assert len(docs) == 1
    assert docs[0].url == valid_href


def test_entry_without_explicit_filing_type_is_skipped() -> None:
    feed = _edgar_feed(
        _entry(
            title="Current report - NVIDIA CORP (0001045810) (Filer)",
            href="https://www.sec.gov/Archives/edgar/data/1045810/unreadable.htm",
            filing_type=None,
        )
    )

    docs = EdgarRssFetcher(reader=_reader(feed)).fetch_recent("8-K", count=10)

    assert docs == []


def test_entry_with_mismatched_filing_type_is_skipped() -> None:
    feed = _edgar_feed(
        _entry(
            title="10-K - NVIDIA CORP (0001045810) (Filer)",
            href="https://www.sec.gov/Archives/edgar/data/1045810/10k.htm",
            filing_type="10-K",
        )
    )

    docs = EdgarRssFetcher(reader=_reader(feed)).fetch_recent("8-K", count=10)

    assert docs == []


def test_entry_with_unsupported_filing_type_is_skipped() -> None:
    feed = _edgar_feed(
        _entry(
            title="8-K/A - NVIDIA CORP (0001045810) (Filer)",
            href="https://www.sec.gov/Archives/edgar/data/1045810/8ka.htm",
            filing_type="8-K/A",
        )
    )

    docs = EdgarRssFetcher(reader=_reader(feed)).fetch_recent("8-K", count=10)

    assert docs == []


def test_edgar_fetches_entry_link_after_feed_and_uses_filing_body_text() -> None:
    href = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000126/nvda-8k.htm"
    calls: list[str] = []
    feed = _edgar_feed(
        _entry(
            title="8-K - NVIDIA CORP (0001045810) (Filer)",
            href=href,
            filing_type="8-K",
            summary="Short Atom summary.",
        )
    )
    filing_body = _filing_html(
        "<main><h1>Item 8.01 Other Events</h1>"
        "<p>NVIDIA disclosed Blackwell supply commitments in the full filing body.</p></main>"
    )

    [doc] = EdgarRssFetcher(reader=_reader(feed, {href: filing_body}, calls)).fetch_recent(
        "8-K", count=1
    )

    assert calls == [
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=NVDA&type=8-K&dateb=&owner=include&count=1&search_text=&output=atom",
        href,
    ]
    assert doc.raw_text == (
        "Filing document Item 8.01 Other Events NVIDIA disclosed Blackwell supply "
        "commitments in the full filing body."
    )


def test_edgar_index_entry_resolves_primary_document_before_extracting_text() -> None:
    index_href = (
        "https://www.sec.gov/Archives/edgar/data/1045810/"
        "000104581026000129/0001045810-26-000129-index.htm"
    )
    document_href = (
        "https://www.sec.gov/Archives/edgar/data/1045810/"
        "000104581026000129/nvda-20260624.htm"
    )
    calls: list[str] = []
    feed = _edgar_feed(
        _entry(
            title="8-K - NVIDIA CORP (0001045810) (Filer)",
            href=index_href,
            filing_type="8-K",
            summary="Short Atom summary.",
        )
    )
    filing_body = _filing_html(
        "<main><p>NVIDIA disclosed Blackwell platform supply terms in the primary 8-K.</p></main>"
    )

    [doc] = EdgarRssFetcher(
        reader=_reader(
            feed,
            {
                index_href: _filing_index_html(
                    "/Archives/edgar/data/1045810/000104581026000129/nvda-20260624.htm"
                ),
                document_href: filing_body,
            },
            calls,
        )
    ).fetch_recent("8-K", count=1)

    assert calls == [
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=NVDA&type=8-K&dateb=&owner=include&count=1&search_text=&output=atom",
        index_href,
        document_href,
    ]
    assert doc.url == document_href
    assert doc.doc_id == hashlib.sha256(document_href.encode()).hexdigest()[:12]
    assert "Document Format Files" not in doc.raw_text
    assert doc.raw_text == (
        "Filing document NVIDIA disclosed Blackwell platform supply terms in the primary 8-K."
    )


def test_edgar_filing_html_xbrl_scripts_styles_entities_and_whitespace_are_normalized() -> None:
    href = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000127/nvda-10q.htm"
    feed = _edgar_feed(
        _entry(
            title="10-Q - NVIDIA CORP (0001045810) (Filer)",
            href=href,
            filing_type="10-Q",
        )
    )
    filing_body = """<!doctype html>
<html>
  <head>
    <title>NVIDIA 10-Q</title>
    <style>.hidden { display: none; } Hidden Style Text</style>
    <script>window.secret = "remove script text";</script>
  </head>
  <body>
    <ix:nonNumeric name="dei:DocumentType">FORM 10-Q</ix:nonNumeric>
    <p>Revenue&nbsp;&amp;&nbsp;gross margin
       increased with Blackwell&nbsp;systems.</p>
  </body>
</html>
"""

    [doc] = EdgarRssFetcher(reader=_reader(feed, {href: filing_body})).fetch_recent(
        "10-Q", count=1
    )

    assert doc.raw_text == (
        "NVIDIA 10-Q FORM 10-Q Revenue & gross margin increased with Blackwell systems."
    )
    assert "Hidden Style Text" not in doc.raw_text
    assert "remove script text" not in doc.raw_text
    assert "<ix:nonNumeric" not in doc.raw_text


def test_edgar_filing_body_is_materially_longer_than_atom_summary_and_keeps_body_keywords() -> None:
    href = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000128/nvda-8k.htm"
    summary = "Short Atom blurb."
    body_only_phrase = "contractual Blackwell supply allocations and liquid cooling readiness"
    feed = _edgar_feed(
        _entry(
            title="8-K - NVIDIA CORP (0001045810) (Filer)",
            href=href,
            filing_type="8-K",
            summary=summary,
        )
    )
    filing_body = _filing_html(
        " ".join(
            [
                "<article><p>NVIDIA filed this full Form 8-K to describe operational updates.",
                "Management discussed data center demand, customer concentration,",
                body_only_phrase,
                "across several paragraphs that do not appear in the Atom summary.</p></article>",
            ]
        )
    )

    [doc] = EdgarRssFetcher(reader=_reader(feed, {href: filing_body})).fetch_recent(
        "8-K", count=1
    )

    assert len(doc.raw_text) > len(summary) * 5
    assert body_only_phrase in doc.raw_text


def test_edgar_entry_with_empty_or_unusable_filing_body_is_skipped() -> None:
    empty_href = "https://www.sec.gov/Archives/edgar/data/1045810/empty.htm"
    valid_href = "https://www.sec.gov/Archives/edgar/data/1045810/valid-after-empty.htm"
    feed = _edgar_feed(
        _entry(
            title="8-K - empty",
            href=empty_href,
            filing_type="8-K",
        )
        + _entry(
            title="8-K - valid",
            href=valid_href,
            filing_type="8-K",
        )
    )

    docs = EdgarRssFetcher(
        reader=_reader(
            feed,
            {
                empty_href: "<html><script>discard()</script><style>discard</style></html>",
                valid_href: _filing_html("Valid filing body after unusable filing."),
            },
        )
    ).fetch_recent("8-K", count=10)

    assert [doc.url for doc in docs] == [valid_href]
    assert docs[0].raw_text == "Filing document Valid filing body after unusable filing."


def test_edgar_reader_errors_and_malformed_indexes_are_skipped() -> None:
    direct_error_href = "https://www.sec.gov/Archives/edgar/data/1045810/direct-error.htm"
    malformed_index_href = (
        "https://www.sec.gov/Archives/edgar/data/1045810/"
        "000104581026000130/0001045810-26-000130-index.htm"
    )
    primary_error_index_href = (
        "https://www.sec.gov/Archives/edgar/data/1045810/"
        "000104581026000131/0001045810-26-000131-index.htm"
    )
    primary_error_href = (
        "https://www.sec.gov/Archives/edgar/data/1045810/"
        "000104581026000131/nvda-20260625.htm"
    )
    valid_href = "https://www.sec.gov/Archives/edgar/data/1045810/valid-after-errors.htm"
    feed = _edgar_feed(
        _entry(title="8-K - read error", href=direct_error_href, filing_type="8-K")
        + _entry(title="8-K - malformed index", href=malformed_index_href, filing_type="8-K")
        + _entry(title="8-K - primary read error", href=primary_error_index_href, filing_type="8-K")
        + _entry(title="8-K - valid", href=valid_href, filing_type="8-K")
    )

    def read(url: str) -> str:
        if "output=atom" in url:
            return feed
        if url in {direct_error_href, primary_error_href}:
            raise OSError("network read failed")
        if url == malformed_index_href:
            return _filing_index_html("/Archives/edgar/data/1045810/exhibit991.htm")
        if url == primary_error_index_href:
            return _filing_index_html(
                "/Archives/edgar/data/1045810/000104581026000131/nvda-20260625.htm"
            )
        if url == valid_href:
            return _filing_html("Valid filing body after reader errors.")
        raise AssertionError(f"unexpected URL read: {url}")

    docs = EdgarRssFetcher(reader=read).fetch_recent("8-K", count=10)

    assert [doc.url for doc in docs] == [valid_href]
    assert docs[0].raw_text == "Filing document Valid filing body after reader errors."


def test_reuters_rss_returns_only_nvda_relevant_source_documents() -> None:
    nvda_href = "https://www.reuters.com/technology/nvidia-blackwell-demand-2026-06-24/"
    feed = _rss_feed(
        _rss_item(
            title="NVIDIA shares rise on Blackwell demand",
            link=nvda_href,
            description="<p>NVIDIA shares rose after analysts cited Blackwell demand.</p>",
        )
        + _rss_item(
            title="Autos supplier cuts outlook",
            link="https://www.reuters.com/business/autos-supplier-outlook-2026-06-24/",
            description="Supplier shares fell after a weak guidance update.",
        )
    )

    docs = ReutersRssFetcher(reader=_rss_reader(feed)).fetch()

    assert docs == [
        SourceDocument(
            doc_id=hashlib.sha256(nvda_href.encode()).hexdigest()[:12],
            url=nvda_href,
            title="NVIDIA shares rise on Blackwell demand",
            published_date=date(2026, 6, 24),
            source_ref=SourceRef(
                discovery_channel="rss_aggregator",
                content_source="reuters",
                source_reliability=SOURCE_RELIABILITY["reuters"],
            ),
            raw_text="NVIDIA shares rose after analysts cited Blackwell demand.",
        )
    ]


def test_reuters_rss_skips_empty_malformed_non_nvda_and_incomplete_entries() -> None:
    valid_href = "https://www.reuters.com/technology/nvda-valid-2026-06-24/"
    feed = _rss_feed(
        """
    <item>
      <title>NVIDIA item missing link</title>
      <pubDate>Tue, 24 Jun 2026 18:01:05 GMT</pubDate>
      <description>NVIDIA should be skipped without a URL.</description>
    </item>
    <item>
      <title>NVIDIA item with invalid date</title>
      <link>https://www.reuters.com/technology/nvda-invalid-date-2026-06-24/</link>
      <pubDate>not a date</pubDate>
      <description>NVIDIA should be skipped without a valid date.</description>
    </item>
    <item>
      <title>NVIDIA item with empty text</title>
      <link>https://www.reuters.com/technology/nvda-empty-2026-06-24/</link>
      <pubDate>Tue, 24 Jun 2026 18:01:05 GMT</pubDate>
      <description>   </description>
    </item>
"""
        + _rss_item(
            title="Chip equipment maker raises forecast",
            link="https://www.reuters.com/technology/chip-equipment-2026-06-24/",
            description="Semiconductor equipment demand improved.",
        )
        + _rss_item(
            title="NVDA expands data center supply",
            link=valid_href,
            description="NVDA expanded data center supply after Blackwell demand increased.",
        )
    )

    docs = ReutersRssFetcher(reader=_rss_reader(feed)).fetch()

    assert [doc.url for doc in docs] == [valid_href]


def test_reuters_malformed_feed_returns_no_documents() -> None:
    docs = ReutersRssFetcher(reader=_rss_reader("<rss><channel>")).fetch()

    assert docs == []


def test_manual_transcript_loader_builds_earnings_call_source_document(tmp_path: Path) -> None:
    transcript_path = tmp_path / "nvda-fy2027-q1.txt"
    transcript_path.write_text(
        "  Operator: Welcome to NVIDIA's call.\n\n"
        " Jensen Huang: Blackwell demand is very strong.  \n",
    )
    source_url = "file://nvda-fy2027-q1-transcript"

    docs = ManualTranscriptLoader(
        path=transcript_path,
        title=" NVIDIA FY2027 Q1 earnings call transcript ",
        published_date=date(2026, 5, 27),
        source_url=source_url,
    ).fetch()

    assert docs == [
        SourceDocument(
            doc_id=hashlib.sha256(f"{source_url}|2026-05-27".encode()).hexdigest()[:12],
            url=source_url,
            title="NVIDIA FY2027 Q1 earnings call transcript",
            published_date=date(2026, 5, 27),
            source_ref=SourceRef(
                discovery_channel="direct",
                content_source="earnings_call",
                source_reliability=SOURCE_RELIABILITY["earnings_call"],
            ),
            raw_text=(
                "Operator: Welcome to NVIDIA's call. "
                "Jensen Huang: Blackwell demand is very strong."
            ),
        )
    ]
