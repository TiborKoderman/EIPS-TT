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

REJECTED_QUERY_KEYS = {
    "_wpnonce",
    "object_id",
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
        if path is None:
            raise ValueError(f"Malformed URL path: {raw_url!r}")

        query = self._normalize_query(parsed.query)
        if query is None:
            raise ValueError(f"Rejected URL query: {raw_url!r}")

        return urlunsplit((scheme, netloc, path, query, ""))

    @staticmethod
    def _normalize_path(raw_path: str) -> str | None:
        path = unquote(raw_path or "/")
        if DefaultUrlCanonicalizer._contains_embedded_absolute_url_tail(path):
            return None

        normalized = posixpath.normpath(path)
        if raw_path.endswith("/") and not normalized.endswith("/"):
            normalized = f"{normalized}/"
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        if normalized in {"/.", ""}:
            normalized = "/"
        return quote(normalized, safe="/:@!$&'()*+,;=-._~")

    @staticmethod
    def _normalize_query(raw_query: str) -> str | None:
        if not raw_query:
            return ""

        pairs = parse_qsl(raw_query, keep_blank_values=True)
        filtered_pairs = []
        for key, value in pairs:
            lowered = key.lower()
            if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
                continue
            if DefaultUrlCanonicalizer._should_reject_query_pair(lowered, value):
                return None
            filtered_pairs.append((key, value))

        filtered_pairs.sort(key=lambda kv: (kv[0], kv[1]))
        return urlencode(filtered_pairs, doseq=True)

    @staticmethod
    def _should_reject_query_pair(lowered_key: str, value: str) -> bool:
        if lowered_key in REJECTED_QUERY_KEYS:
            return True
        if lowered_key.startswith("bbp_"):
            return True

        value_lower = (value or "").strip().lower()
        return lowered_key == "action" and value_lower.startswith("bbp_")

    @staticmethod
    def _contains_embedded_absolute_url_tail(decoded_path: str) -> bool:
        lowered_path = (decoded_path or "").lower()
        if not lowered_path:
            return False

        for raw_segment in lowered_path.split("/"):
            segment = raw_segment.strip()
            if not segment:
                continue
            stripped = segment.lstrip("[]()")
            if stripped.startswith(("http:", "https:")):
                return True

        return False
