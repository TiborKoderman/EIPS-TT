"""Daemon-side persistence routing.

Priority order:
1) Relay crawl results to manager/server API.
2) Optionally use daemon-local DB fallback when explicitly enabled.

Workers never connect to the database directly.
"""

from __future__ import annotations

import json
import os
from typing import Protocol
from urllib import error, request

from core.downloader import DownloadResult
from db.page_store import IngestPageResult, PostgresPageStore


class ManagerIngestRelay(Protocol):
    """Server relay contract used by daemon persistence router."""

    def relay_download_result(
        self,
        *,
        raw_url: str,
        download_result: DownloadResult,
        site_id: int | None,
        source_page_id: int | None = None,
    ) -> IngestPageResult | None:
        ...


class HttpManagerIngestRelay:
    """Best-effort HTTP relay to manager-side ingest endpoint."""

    def __init__(self, *, endpoint_url: str | None = None, bearer_token: str | None = None) -> None:
        self._endpoint_url = (endpoint_url or os.getenv("MANAGER_INGEST_API_URL", "")).strip() or None
        self._bearer_token = (bearer_token or os.getenv("MANAGER_INGEST_API_TOKEN", "")).strip() or None

    @property
    def enabled(self) -> bool:
        return self._endpoint_url is not None

    def relay_download_result(
        self,
        *,
        raw_url: str,
        download_result: DownloadResult,
        site_id: int | None,
        source_page_id: int | None = None,
    ) -> IngestPageResult | None:
        if not self._endpoint_url:
            return None

        payload = {
            "rawUrl": raw_url,
            "siteId": site_id,
            "sourcePageId": source_page_id,
            "downloadResult": {
                "requestedUrl": download_result.requested_url,
                "finalUrl": download_result.final_url,
                "statusCode": download_result.status_code,
                "contentType": download_result.content_type,
                "dataTypeCode": download_result.data_type_code,
                "pageTypeCode": download_result.page_type_code,
                "htmlContent": download_result.html_content,
                "binaryContent": None,
                "usedRenderer": download_result.used_renderer,
                "contentLength": download_result.content_length,
            },
        }

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"

        req = request.Request(self._endpoint_url, data=data, headers=headers, method="POST")

        try:
            with request.urlopen(req, timeout=4.0) as response:
                body = response.read().decode("utf-8", errors="ignore")
        except (error.URLError, TimeoutError, OSError):
            return None

        try:
            parsed = json.loads(body)
            result = parsed.get("data", parsed)
            return IngestPageResult(
                page_id=int(result["pageId"]),
                status=str(result["status"]),
                canonical_url=str(result["canonicalUrl"]),
                duplicate_of_page_id=(
                    int(result["duplicateOfPageId"])
                    if result.get("duplicateOfPageId") is not None
                    else None
                ),
                content_hash=(
                    str(result["contentHash"])
                    if result.get("contentHash") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None


class DaemonPersistenceRouter:
    """Routes persistence with API-first strategy and optional local DB fallback."""

    def __init__(
        self,
        *,
        manager_relay: ManagerIngestRelay | None = None,
        fallback_store: PostgresPageStore | None = None,
        allow_db_fallback: bool = False,
    ) -> None:
        self._manager_relay = manager_relay or HttpManagerIngestRelay()
        self._fallback_store = fallback_store
        self._allow_db_fallback = allow_db_fallback

    def ingest_download_result(
        self,
        *,
        raw_url: str,
        download_result: DownloadResult,
        site_id: int | None,
        source_page_id: int | None = None,
    ) -> IngestPageResult:
        relayed = self._manager_relay.relay_download_result(
            raw_url=raw_url,
            download_result=download_result,
            site_id=site_id,
            source_page_id=source_page_id,
        )
        if relayed is not None:
            return relayed

        if self._allow_db_fallback and self._fallback_store is not None:
            return self._fallback_store.ingest_download_result(
                raw_url=raw_url,
                download_result=download_result,
                site_id=site_id,
                source_page_id=source_page_id,
            )

        raise RuntimeError(
            "Manager ingest API relay failed and daemon DB fallback is disabled. "
            "Enable fallback only for serverless operation."
        )