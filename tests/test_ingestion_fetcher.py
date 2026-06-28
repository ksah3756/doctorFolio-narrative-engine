from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date

from dcf_engine.claim import SOURCE_RELIABILITY, SourceRef
from dcf_engine.ingestion import EdgarRssFetcher, SourceDocument


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
    filing_type: str,
    updated: str = "2026-06-24T18:01:05-04:00",
    summary: str = "NVIDIA filed a current report for material corporate events.",
) -> str:
    return f"""
  <entry>
    <title>{title}</title>
    <link rel="alternate" type="text/html" href="{href}" />
    <category label="form type" scheme="https://www.sec.gov/" term="{filing_type}" />
    <updated>{updated}</updated>
    <summary type="html">{summary}</summary>
  </entry>
"""


def _reader(feed_text: str) -> Callable[[str], str]:
    def read(url: str) -> str:
        assert "CIK=NVDA" in url
        assert "output=atom" in url
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

    docs = EdgarRssFetcher(reader=_reader(feed)).fetch_recent("8-K", count=10)

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
            raw_text="NVIDIA filed a current report for material corporate events.",
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

    [doc] = EdgarRssFetcher(reader=_reader(feed)).fetch_recent("10-Q", count=1)

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

    [doc] = EdgarRssFetcher(reader=_reader(feed)).fetch_recent("10-K", count=1)

    assert doc.source_ref.content_source == "10-K"
    assert doc.source_ref.source_reliability == SOURCE_RELIABILITY["10-K"]


def test_fetch_recent_honors_count_and_preserves_fixture_order() -> None:
    feed = _edgar_feed(
        _entry(
            title="8-K - first",
            href="https://www.sec.gov/Archives/edgar/data/1045810/first.htm",
            filing_type="8-K",
            updated="2026-06-25T09:00:00-04:00",
        )
        + _entry(
            title="8-K - second",
            href="https://www.sec.gov/Archives/edgar/data/1045810/second.htm",
            filing_type="8-K",
            updated="2026-06-24T09:00:00-04:00",
        )
    )

    docs = EdgarRssFetcher(reader=_reader(feed)).fetch_recent("8-K", count=1)

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

    docs = EdgarRssFetcher(reader=_reader(feed)).fetch_recent("8-K", count=10)

    assert len(docs) == 1
    assert docs[0].url == valid_href
