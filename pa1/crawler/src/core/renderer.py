"""Headless browser rendering utilities."""

from __future__ import annotations

from dataclasses import dataclass

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


@dataclass(frozen=True)
class RenderResult:
    """Rendered page content from a headless browser."""

    html: str
    current_url: str
    title: str


class SeleniumRenderer:
    """Simple Selenium renderer for JS-generated HTML."""

    def __init__(self, user_agent: str, timeout_seconds: float = 25.0) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds

    def render(self, url: str) -> RenderResult:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--user-agent={self.user_agent}")

        driver = webdriver.Chrome(options=options)
        try:
            driver.set_page_load_timeout(self.timeout_seconds)
            driver.get(url)
            return RenderResult(
                html=driver.page_source,
                current_url=driver.current_url,
                title=driver.title,
            )
        finally:
            driver.quit()

