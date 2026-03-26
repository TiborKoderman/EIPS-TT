"""Crawler scheduling loop glueing together:
- DB-backed frontier (queue)
- robots policy
- per-IP politeness (rate limiting)
- downloader
- parser + enqueue discovered URLs
- persistence into crawldb.page/link/page_data via PostgresPageStore

This is intentionally a minimal synchronous worker loop. You can run N threads,
where each thread makes its own DB connection and runs `worker_loop()`.

Important notes:
- We respect robots.txt allow/disallow via RobotFileParser.can_fetch.
- We respect crawl-delay (robots) AND global min-delay (per IP) via PerIpRateLimiter.
- We do not download binary data except optional PDF bytes.
- For non-HTML, we store BINARY page + optional page_data (metadata only for docs/ppt).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from crawler.core.config import CrawlerConfig
from crawler.core.downloader import Downloader
from crawler.core.parser import parse_outgoing_urls
from crawler.core.politeness import PerIpRateLimiter
from crawler.core.relevance import RelevancePolicy, score_url
from crawler.core.robots import RobotsPolicyManager
from db.frontier_store import PostgresFrontierStore
from db.page_store import PostgresPageStore
from utils.url_canonicalizer import DefaultUrlCanonicalizer

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CrawlWorkerDeps:
    config: CrawlerConfig
    robots: RobotsPolicyManager
    rate_limiter: PerIpRateLimiter
    relevance_policy: RelevancePolicy


def worker_loop(
    *,
    conn_factory,
    deps: CrawlWorkerDeps,
    worker_id: str,
    stop_event: threading.Event,
    idle_sleep_seconds: float = 0.5,
) -> None:
    """Run one worker loop until stop_event is set.

    conn_factory: callable -> new psycopg2 connection. Use a new connection per
                  worker thread.
    """
    canonicalizer = DefaultUrlCanonicalizer()

    conn = conn_factory()
    try:
        frontier = PostgresFrontierStore(conn)
        store = PostgresPageStore(conn)

        downloader = Downloader(
            user_agent=deps.config.user_agent,
            timeout_seconds=deps.config.download_timeout_seconds,
            render_timeout_seconds=deps.config.render_timeout_seconds,
        )

        while not stop_event.is_set():
            leased = frontier.claim_next(worker_id=worker_id)
            if leased is None:
                time.sleep(idle_sleep_seconds)
                continue

            url = leased.canonical_url
            try:
                # Pre-check: if URL was already crawled (HTML/BINARY/DUPLICATE), do not re-download.
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, page_type_code
                        FROM crawldb.page
                        WHERE canonical_url = %s
                        LIMIT 1;
                        """,
                        (url,),
                    )
                    row = cur.fetchone()
                if row is not None and row[1] in {"HTML", "BINARY", "DUPLICATE"}:
                    existing_page_id = int(row[0])
                    if leased.source_page_id is not None:
                        store.add_link(leased.source_page_id, existing_page_id)
                        conn.commit()
                    frontier.mark_done(leased.frontier_page_id)
                    continue

                # robots check
                policy = deps.robots.get_policy(url)
                if not policy.allows(deps.config.user_agent, url):
                    frontier.mark_done(leased.frontier_page_id)
                    continue

                # PerIpRateLimiter already resolves host->IP and enforces delay per IP.
                # We pass robots crawl-delay (if any); the limiter max()'s it with its base delay.
                deps.rate_limiter.wait_for_turn(url, policy.crawl_delay_seconds)

                # fetch
                parsed_final = urlsplit(url)
                host = (parsed_final.hostname or "").lower()
                allow_pdf_bytes = (
                    deps.config.download_pdf_content
                    and host
                    and any(
                        host == h or host.endswith("." + h)
                        for h in deps.config.pdf_binary_download_hosts
                    )
                )
                result = downloader.fetch(
                    url,
                    render_html=False,
                    download_pdf_content=allow_pdf_bytes,
                )

                # persist + enqueue outgoing
                if result.page_type_code == "HTML" and result.html_content is not None:
                    ingest = store.ingest_html_from_frontier(
                        raw_url=result.final_url,
                        html_content=result.html_content,
                        site_id=None,
                        http_status_code=result.status_code,
                    )

                    # parse outgoing and enqueue
                    parsed_out = parse_outgoing_urls(
                        result.html_content,
                        page_url=result.final_url,
                        canonicalizer=canonicalizer,
                    )

                    child_depth = leased.depth + 1
                    for link in parsed_out.links:
                        prio = score_url(
                            link,
                            parent_url=result.final_url,
                            depth=child_depth,
                            policy=deps.relevance_policy,
                        )
                        frontier.enqueue(
                            link,
                            source_page_id=ingest.page_id,
                            depth=child_depth,
                            priority=prio,
                        )
                        # link table: if URL already existed (known_url), ingest_html_from_frontier
                        # would only add link when called, but we don't re-crawl here.
                        # We add link when the target is discovered and already known.
                        # find_page_id_by_url expects canonical URL
                        target_page_id = store.find_page_id_by_url(canonicalizer.canonicalize(link))
                        if target_page_id is not None:
                            store.add_link(ingest.page_id, target_page_id)

                    # One commit for all link insertions for this page.
                    conn.commit()

                    # images/binaries: enqueue as frontier too, but they'll generally become BINARY.
                    for img in parsed_out.images:
                        prio = score_url(
                            img,
                            parent_url=result.final_url,
                            depth=child_depth,
                            policy=deps.relevance_policy,
                        )
                        frontier.enqueue(img, source_page_id=ingest.page_id, depth=child_depth, priority=prio)

                else:
                    # binary content: store BINARY page and page_data metadata
                    # Requirement: do NOT store bytes for DOC/DOCX/PPT/PPTX.
                    binary_bytes = result.binary_content
                    if result.data_type_code in {"DOC", "DOCX", "PPT", "PPTX"}:
                        binary_bytes = None
                    ingest_bin = store.ingest_binary_from_frontier(
                        raw_url=result.final_url,
                        site_id=None,
                        data_type_code=result.data_type_code,
                        http_status_code=result.status_code,
                        binary_content=binary_bytes,
                    )
                    _ = ingest_bin

                frontier.mark_done(leased.frontier_page_id)

            except Exception as exc:
                # keep it robust: requeue with backoff
                frontier.reschedule_with_backoff(
                    leased.frontier_page_id,
                    error=f"{type(exc).__name__}: {exc}",
                    base_delay_seconds=30,
                )

    finally:
        conn.close()




