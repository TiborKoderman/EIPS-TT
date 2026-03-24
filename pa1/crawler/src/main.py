"""Entry point for crawler utilities."""

from __future__ import annotations

import argparse
import time

from crawler.core.config import load_crawler_config
from crawler.core.downloader import Downloader
from crawler.core.politeness import PerIpRateLimiter
from crawler.core.robots import RobotsPolicyManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PA1 crawler utilities")
    parser.add_argument(
        "--check-url",
        type=str,
        help="Absolute URL to validate against robots and politeness rules.",
    )
    parser.add_argument(
        "--fetch-url",
        type=str,
        help="Absolute URL to fetch with downloader.",
    )
    parser.add_argument(
        "--render-js",
        action="store_true",
        help="When used with --fetch-url, render HTML with Selenium.",
    )
    parser.add_argument(
        "--download-pdf",
        action="store_true",
        help="When used with --fetch-url, store PDF binary content in memory.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.check_url and not args.fetch_url:
        print("No action selected. Use --check-url or --fetch-url.")
        return 0

    config = load_crawler_config()
    policy_manager = RobotsPolicyManager(
        user_agent=config.user_agent,
        timeout_seconds=config.robots_timeout_seconds,
    )
    rate_limiter = PerIpRateLimiter(min_interval_seconds=config.min_request_interval_seconds)

    if args.check_url:
        policy = policy_manager.get_policy(args.check_url)
        allowed = policy.allows(config.user_agent, args.check_url)
        effective_delay = max(config.min_request_interval_seconds, policy.crawl_delay_seconds or 0.0)

        print(f"User-Agent: {config.user_agent}")
        print(f"Robots URL: {policy.robots_url}")
        print(f"Robots fetched: {policy.fetched}")
        print(f"Allowed: {allowed}")
        print(f"Robots crawl-delay: {policy.crawl_delay_seconds}")
        print(f"Effective delay used: {effective_delay}")
        print(f"Sitemaps: {policy.sitemaps}")

        print("Applying rate limiter twice to demonstrate pacing...")
        start = time.monotonic()
        rate_limiter.wait_for_turn(args.check_url, policy.crawl_delay_seconds)
        t1 = time.monotonic() - start
        rate_limiter.wait_for_turn(args.check_url, policy.crawl_delay_seconds)
        t2 = time.monotonic() - start
        print(f"First permit at +{t1:.2f}s, second permit at +{t2:.2f}s")

    if args.fetch_url:
        policy = policy_manager.get_policy(args.fetch_url)
        allowed = policy.allows(config.user_agent, args.fetch_url)
        if not allowed:
            print("Fetch blocked by robots.txt.")
            return 2

        rate_limiter.wait_for_turn(args.fetch_url, policy.crawl_delay_seconds)
        downloader = Downloader(
            user_agent=config.user_agent,
            timeout_seconds=config.download_timeout_seconds,
            render_timeout_seconds=config.render_timeout_seconds,
        )
        result = downloader.fetch(
            args.fetch_url,
            render_html=args.render_js,
            download_pdf_content=args.download_pdf or config.download_pdf_content,
        )

        print("Fetch completed:")
        print(f"Requested URL: {result.requested_url}")
        print(f"Final URL: {result.final_url}")
        print(f"Status code: {result.status_code}")
        print(f"Content-Type: {result.content_type}")
        print(f"Page type: {result.page_type_code}")
        print(f"Data type: {result.data_type_code}")
        print(f"Used renderer: {result.used_renderer}")
        print(f"HTML chars: {len(result.html_content) if result.html_content else 0}")
        print(f"Binary bytes: {len(result.binary_content) if result.binary_content else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
