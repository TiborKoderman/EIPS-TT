"""HTTP downloader and content classification logic."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping
from urllib.parse import unquote, urlparse

import requests

from core.renderer import SeleniumRenderer


HTML_PAGE_TYPE = "HTML"
BINARY_PAGE_TYPE = "BINARY"

DATA_TYPE_BY_EXTENSION = {
    ".pdf": "PDF",
    ".doc": "DOC",
    ".docx": "DOCX",
    ".ppt": "PPT",
    ".pptx": "PPTX",
}

DATA_TYPE_BY_CONTENT_TYPE = {
    "application/pdf": "PDF",
    "application/msword": "DOC",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
    "application/vnd.ms-powerpoint": "PPT",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
}


@dataclass(frozen=True)
class DownloadResult:
    """Single URL fetch output with detected content metadata."""

    requested_url: str
    final_url: str
    status_code: int
    page_type_code: str
    data_type_code: str | None
    content_type: str | None
    html_content: str | None
    binary_content: bytes | None
    content_length: int | None
    used_renderer: bool


class Downloader:
    """Downloader that supports plain HTTP fetch and optional JS rendering."""

    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float = 20.0,
        render_timeout_seconds: float = 25.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.render_timeout_seconds = render_timeout_seconds
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})

    def fetch(
        self,
        url: str,
        *,
        render_html: bool = False,
        download_pdf_content: bool = False,
        download_binary_content: bool = False,
        store_large_binary_content: bool = False,
        large_binary_threshold_bytes: int = 5_000_000,
    ) -> DownloadResult:
        response = self._session.get(url, timeout=self.timeout_seconds, allow_redirects=True)
        final_url = response.url
        normalized_content_type = _normalize_content_type(response.headers)
        data_type = _detect_data_type(final_url, normalized_content_type, response.headers)
        content_length = _detect_content_length(response)
        is_html = _is_html_response(final_url, normalized_content_type, response.text)

        if is_html:
            html_content = response.text
            used_renderer = False
            if render_html:
                renderer = SeleniumRenderer(
                    user_agent=self.user_agent,
                    timeout_seconds=self.render_timeout_seconds,
                )
                rendered = renderer.render(final_url)
                html_content = rendered.html
                final_url = rendered.current_url
                used_renderer = True

            return DownloadResult(
                requested_url=url,
                final_url=final_url,
                status_code=response.status_code,
                page_type_code=HTML_PAGE_TYPE,
                data_type_code=None,
                content_type=normalized_content_type,
                html_content=html_content,
                binary_content=None,
                content_length=content_length,
                used_renderer=used_renderer,
            )

        binary_content = None
        should_download_binary = download_binary_content or (data_type == "PDF" and download_pdf_content)
        can_store_large = store_large_binary_content or (content_length is not None and content_length <= large_binary_threshold_bytes)
        if should_download_binary and can_store_large:
            binary_content = response.content

        return DownloadResult(
            requested_url=url,
            final_url=final_url,
            status_code=response.status_code,
            page_type_code=BINARY_PAGE_TYPE,
            data_type_code=data_type,
            content_type=normalized_content_type,
            html_content=None,
            binary_content=binary_content,
            content_length=content_length,
            used_renderer=False,
        )


def _detect_content_length(response: requests.Response) -> int | None:
    content_length_raw = response.headers.get("Content-Length")
    if content_length_raw and content_length_raw.isdigit():
        return int(content_length_raw)
    if response.content:
        return len(response.content)
    return None


def _normalize_content_type(headers: Mapping[str, str]) -> str | None:
    value = headers.get("Content-Type")
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower()


def _detect_data_type(
    url: str,
    content_type: str | None,
    headers: Mapping[str, str] | None = None,
) -> str | None:
    if content_type in DATA_TYPE_BY_CONTENT_TYPE:
        return DATA_TYPE_BY_CONTENT_TYPE[content_type]

    content_disposition = ""
    if headers:
        content_disposition = headers.get("Content-Disposition") or headers.get("content-disposition") or ""

    for candidate in _extract_type_candidates(url, content_disposition):
        normalized = candidate.strip().strip("\"'()[]{}<>").lower()
        normalized = normalized.split("#", 1)[0].split("?", 1)[0]
        for extension, data_type in sorted(
            DATA_TYPE_BY_EXTENSION.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if normalized.endswith(extension):
                return data_type

    return None


def _extract_type_candidates(url: str, content_disposition: str) -> list[str]:
    candidates: list[str] = []

    parsed = urlparse(url)
    if parsed.path:
        candidates.append(parsed.path)
        candidates.append(parsed.path.rsplit("/", 1)[-1])

    if parsed.query:
        for token in parsed.query.split("&"):
            if not token:
                continue

            _, _, value = token.partition("=")
            raw = value or token
            decoded = unquote(raw.replace("+", " ")).strip()
            if not decoded:
                continue

            candidates.append(decoded)
            candidates.append(decoded.rsplit("/", 1)[-1])

    disposition_filename = _extract_filename_from_content_disposition(content_disposition)
    if disposition_filename:
        candidates.append(disposition_filename)

    return candidates


def _extract_filename_from_content_disposition(content_disposition: str) -> str | None:
    if not content_disposition:
        return None

    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", content_disposition, re.IGNORECASE)
    if not match:
        return None

    filename = unquote(match.group(1).strip().strip("\"'"))
    return filename or None


def _is_html_response(url: str, content_type: str | None, text_body: str | None) -> bool:
    if content_type in {"text/html", "application/xhtml+xml"}:
        return True
    path = urlparse(url).path.lower()
    if path.endswith(".html") or path.endswith(".htm") or path == "":
        return True
    if text_body:
        probe = text_body.lstrip()[:64].lower()
        if probe.startswith("<!doctype html") or probe.startswith("<html"):
            return True
    return False

