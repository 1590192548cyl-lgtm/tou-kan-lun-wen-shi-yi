from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig


AMOUNT = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>亿元|万元|元)")


async def crawl(
    urls: list[str], browser_channel: str, cdp_url: str | None
) -> list[dict[str, object]]:
    if cdp_url:
        browser = BrowserConfig(
            browser_type="chromium",
            browser_mode="cdp",
            cdp_url=cdp_url,
            verbose=False,
        )
    else:
        browser = BrowserConfig(
            browser_type="chromium",
            headless=True,
            chrome_channel=browser_channel,
            channel=browser_channel,
            use_persistent_context=True,
            user_data_dir=str(
                Path(os.environ["LOCALAPPDATA"])
                / "Temp"
                / "crawl4ai_yangtze_profile"
            ),
            verbose=False,
        )
    run = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
    rows: list[dict[str, object]] = []
    async with AsyncWebCrawler(config=browser) as crawler:
        for url in urls:
            result = await crawler.arun(url=url, config=run)
            markdown = result.markdown.raw_markdown if result.success else ""
            rows.append(
                {
                    "url": url,
                    "final_url": result.url,
                    "success": result.success,
                    "status_code": result.status_code,
                    "error": result.error_message,
                    "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                    "content_length": len(markdown),
                    "content_sha256": hashlib.sha256(markdown.encode()).hexdigest()
                    if markdown
                    else "",
                    "amount_mentions": [
                        match.group(0) for match in AMOUNT.finditer(markdown)
                    ],
                    "text_excerpt": markdown[:3000],
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--browser-channel", default="msedge")
    parser.add_argument("--cdp-url")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("dynamic_crawl_results.json"),
    )
    args = parser.parse_args()
    rows = asyncio.run(crawl(args.urls, args.browser_channel, args.cdp_url))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "total": len(rows),
                "successful": sum(bool(row["success"]) for row in rows),
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if all(row["success"] for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
