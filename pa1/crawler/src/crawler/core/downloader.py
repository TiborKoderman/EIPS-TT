"""HTTP downloader and content classification logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

import requests

from crawler.core.renderer import SeleniumRenderer


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
    ) -> DownloadResult:
        response = self._session.get(url, timeout=self.timeout_seconds, allow_redirects=True)
        final_url = response.url
        normalized_content_type = _normalize_content_type(response.headers)
        data_type = _detect_data_type(final_url, normalized_content_type)
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
                used_renderer=used_renderer,
            )

        binary_content = None
        if data_type == "PDF" and download_pdf_content:
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
            used_renderer=False,
        )


def _normalize_content_type(headers: Mapping[str, str]) -> str | None:
    value = headers.get("Content-Type")
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower()


def _detect_data_type(url: str, content_type: str | None) -> str | None:
    if content_type in DATA_TYPE_BY_CONTENT_TYPE:
        return DATA_TYPE_BY_CONTENT_TYPE[content_type]

    path = urlparse(url).path.lower()
    for extension, data_type in DATA_TYPE_BY_EXTENSION.items():
        if path.endswith(extension):
            return data_type
    return None


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

