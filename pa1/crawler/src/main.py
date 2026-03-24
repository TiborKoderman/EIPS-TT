"""Entry point for crawler utilities."""

from __future__ import annotations

import argparse
import time

from crawler.core.config import load_crawler_config
from crawler.core.politeness import PerIpRateLimiter
from crawler.core.robots import RobotsPolicyManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PA1 crawler utilities")
    parser.add_argument(
        "--check-url",
        type=str,
        help="Absolute URL to validate against robots and politeness rules.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.check_url:
        print("No action selected. Use --check-url <absolute_url>.")
        return 0

    config = load_crawler_config()
    policy_manager = RobotsPolicyManager(
        user_agent=config.user_agent,
        timeout_seconds=config.robots_timeout_seconds,
    )
    rate_limiter = PerIpRateLimiter(min_interval_seconds=config.min_request_interval_seconds)

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
