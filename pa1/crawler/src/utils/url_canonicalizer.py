"""Utilities for canonical URL normalization."""

from __future__ import annotations

from dataclasses import dataclass
import posixpath
from typing import Protocol
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit


TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}


class UrlCanonicalizer(Protocol):
    """Interface for URL canonicalization strategies."""

    def canonicalize(self, raw_url: str, base_url: str | None = None) -> str:
        """Return a canonical absolute URL."""


@dataclass(frozen=True)
class DefaultUrlCanonicalizer:
    """Canonicalize URLs for crawler storage and dedup checks."""

    def canonicalize(self, raw_url: str, base_url: str | None = None) -> str:
        candidate = urljoin(base_url, raw_url) if base_url else raw_url
        parsed = urlsplit(candidate)

        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
        if not parsed.hostname:
            raise ValueError(f"URL must include hostname: {raw_url!r}")

        scheme = parsed.scheme.lower()
        hostname = parsed.hostname.lower()
        port = parsed.port
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            netloc = f"{hostname}:{port}"
        else:
            netloc = hostname

        path = self._normalize_path(parsed.path)
        query = self._normalize_query(parsed.query)
        return urlunsplit((scheme, netloc, path, query, ""))

    @staticmethod
    def _normalize_path(raw_path: str) -> str:
        path = unquote(raw_path or "/")
        normalized = posixpath.normpath(path)
        if raw_path.endswith("/") and not normalized.endswith("/"):
            normalized = f"{normalized}/"
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        if normalized in {"/.", ""}:
            normalized = "/"
        return quote(normalized, safe="/:@!$&'()*+,;=-._~")

    @staticmethod
    def _normalize_query(raw_query: str) -> str:
        if not raw_query:
            return ""

        pairs = parse_qsl(raw_query, keep_blank_values=True)
        filtered_pairs = []
        for key, value in pairs:
            lowered = key.lower()
            if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
                continue
            filtered_pairs.append((key, value))

        filtered_pairs.sort(key=lambda kv: (kv[0], kv[1]))
        return urlencode(filtered_pairs, doseq=True)
