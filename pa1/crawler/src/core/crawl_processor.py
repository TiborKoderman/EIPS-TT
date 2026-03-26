"""High-level crawl pipeline: download, extract links, and persist page data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.downloader import DownloadResult, Downloader
from core.link_extractor import ExtractedPageAssets, LinkExtractor
from db.page_store import IngestPageResult


class PageIngestSink(Protocol):
    """Daemon-provided persistence sink used by workers.

    Workers must not know whether persistence is relayed to manager API or
    handled via daemon-local database fallback.
    """

    def ingest_download_result(
        self,
        *,
        raw_url: str,
        download_result: DownloadResult,
        site_id: int | None,
        source_page_id: int | None = None,
    ) -> IngestPageResult:
        ...


@dataclass(frozen=True)
class CrawlProcessResult:
    """Outcome of crawling one URL through the pipeline."""

    download: DownloadResult
    ingest: IngestPageResult
    extracted_assets: ExtractedPageAssets


class CrawlerPageProcessor:
    """Orchestrates one-page crawl: fetch, parse links/images, and persist output."""

    def __init__(
        self,
        *,
        downloader: Downloader,
        page_store: PageIngestSink,
        link_extractor: LinkExtractor | None = None,
    ) -> None:
        self._downloader = downloader
        self._page_store = page_store
        self._link_extractor = link_extractor or LinkExtractor()

    def crawl_and_store(
        self,
        *,
        url: str,
        site_id: int | None,
        source_page_id: int | None = None,
        render_html: bool = False,
        download_pdf_content: bool = False,
        download_binary_content: bool = False,
        store_large_binary_content: bool = False,
        large_binary_threshold_bytes: int = 5_000_000,
    ) -> CrawlProcessResult:
        download = self._downloader.fetch(
            url,
            render_html=render_html,
            download_pdf_content=download_pdf_content,
            download_binary_content=download_binary_content,
            store_large_binary_content=store_large_binary_content,
            large_binary_threshold_bytes=large_binary_threshold_bytes,
        )

        ingest = self._page_store.ingest_download_result(
            raw_url=url,
            download_result=download,
            site_id=site_id,
            source_page_id=source_page_id,
        )

        if download.html_content:
            assets = self._link_extractor.extract(download.html_content, download.final_url)
        else:
            assets = ExtractedPageAssets(links=[], js_links=[], images=[])

        return CrawlProcessResult(
            download=download,
            ingest=ingest,
            extracted_assets=assets,
        )
