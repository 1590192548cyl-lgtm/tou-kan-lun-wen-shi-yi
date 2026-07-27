from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import ssl
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from lxml import html
from pypdf import PdfReader


PROVINCES = (
    "上海",
    "江苏",
    "浙江",
    "安徽",
    "江西",
    "湖北",
    "湖南",
    "重庆",
    "四川",
    "云南",
    "贵州",
    "西藏",
    "青海",
)
PAIR_ALIASES = {
    "苏皖": ("江苏", "安徽"),
    "沪苏": ("上海", "江苏"),
    "川渝": ("四川", "重庆"),
    "渝鄂": ("重庆", "湖北"),
    "渝黔": ("重庆", "贵州"),
    "渝湘": ("重庆", "湖南"),
}
PAIR_SOURCE_TYPES = {
    "bilateral_consultation",
    "bilateral_agreement",
    "agreement_summary",
}
AMOUNT_PATTERN = re.compile(
    r"(?<![\d.])(-?\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|万亿元|元)(?![\d])"
)
YEAR_PATTERN = re.compile(r"(?<!\d)(20(?:1[8-9]|2[0-6]))年")
SPACE_PATTERN = re.compile(r"\s+")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？；])")


def compact(text: str) -> str:
    return SPACE_PATTERN.sub(" ", text.replace("\u3000", " ")).strip()


def decode_html(payload: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([\w-]+)", content_type, re.I)
    candidates = [charset_match.group(1)] if charset_match else []
    candidates.extend(["utf-8", "gb18030"])
    for encoding in candidates:
        try:
            return payload.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return payload.decode("utf-8", errors="replace")


def extract_html(payload: bytes, content_type: str) -> tuple[str, str]:
    decoded = decode_html(payload, content_type)
    tree = html.fromstring(decoded)
    for node in tree.xpath("//script|//style|//noscript"):
        node.drop_tree()
    title = compact("".join(tree.xpath("//title//text()")))
    body_nodes = tree.xpath(
        "//article//*[self::p or self::li or self::td]//text()"
        " | //div[contains(@class,'content')]//*[self::p or self::li or self::td]//text()"
        " | //body//*[self::p or self::li or self::td]//text()"
    )
    return title, compact(" ".join(body_nodes))


def extract_pdf(payload: bytes) -> tuple[str, str]:
    reader = PdfReader(io.BytesIO(payload))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return "", compact(text)


def fetch(url: str, timeout: int) -> tuple[bytes, str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
            )
        },
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            payload = response.read()
            return payload, response.headers.get("Content-Type", ""), response.geturl()
    except Exception as urllib_error:
        marker = b"\n__CODEX_FETCH_META__"
        process = subprocess.run(
            [
                "curl.exe",
                "--location",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                str(timeout),
                "--user-agent",
                request.headers["User-agent"],
                "--write-out",
                "\n__CODEX_FETCH_META__%{content_type}\t%{url_effective}",
                url,
            ],
            check=False,
            capture_output=True,
        )
        if process.returncode != 0:
            curl_error = process.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"urllib failed ({urllib_error}); curl failed ({curl_error})"
            ) from urllib_error
        if marker not in process.stdout:
            raise RuntimeError("curl response metadata marker was not found")
        payload, metadata = process.stdout.rsplit(marker, 1)
        content_type, final_url = metadata.decode("utf-8", errors="replace").split(
            "\t", 1
        )
        return payload, content_type, final_url.strip()


def sentences(text: str) -> list[str]:
    return [compact(item) for item in SENTENCE_SPLIT_PATTERN.split(text) if compact(item)]


def find_amounts(source_id: str, text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for sentence in sentences(text):
        for match in AMOUNT_PATTERN.finditer(sentence):
            amount, unit = match.groups()
            rows.append(
                {
                    "source_id": source_id,
                    "amount_text": match.group(0),
                    "amount_value": amount.replace(",", ""),
                    "amount_unit": unit,
                    "year_mentions": "|".join(sorted(set(YEAR_PATTERN.findall(sentence)))),
                    "province_mentions": "|".join(
                        province for province in PROVINCES if province in sentence
                    ),
                    "evidence_text": sentence[:1000],
                    "verification_status": "candidate_requires_manual_review",
                }
            )
    return rows


def find_pairs(source_id: str, text: str, source_type: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if source_type not in PAIR_SOURCE_TYPES:
        return rows
    for sentence in sentences(text):
        explicit_pairs: set[tuple[str, str]] = set()
        for alias, pair in PAIR_ALIASES.items():
            if alias in sentence:
                explicit_pairs.add(pair)
        for left, right in explicit_pairs:
            key = tuple(sorted((left, right)))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "source_id": source_id,
                    "province_a": left,
                    "province_b": right,
                    "year_mentions": "|".join(
                        sorted(set(YEAR_PATTERN.findall(sentence)))
                    ),
                    "evidence_text": sentence[:1000],
                    "relationship_status": "relationship_mentioned_direction_unverified",
                }
            )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, default=Path(__file__).with_name("sources.json"))
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("output"))
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    source_config = json.loads(args.sources.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    source_rows: list[dict[str, object]] = []
    amount_rows: list[dict[str, str]] = []
    pair_rows: list[dict[str, str]] = []
    fetched_at = datetime.now(timezone.utc).isoformat()

    for item in source_config:
        source_id = item["source_id"]
        try:
            payload, content_type, final_url = fetch(item["url"], args.timeout)
            if "pdf" in content_type.lower() or final_url.lower().endswith(".pdf"):
                title, text = extract_pdf(payload)
                format_name = "pdf"
            else:
                title, text = extract_html(payload, content_type)
                format_name = "html"
            status = "ok" if text else "empty_text"
            error = ""
        except Exception as exc:
            payload = b""
            final_url = item["url"]
            content_type = ""
            title = ""
            text = ""
            format_name = "unknown"
            status = "error"
            error = f"{type(exc).__name__}: {exc}"

        source_rows.append(
            {
                **item,
                "final_url": final_url,
                "format": format_name,
                "content_type": content_type,
                "fetch_status": status,
                "error": error,
                "fetched_at_utc": fetched_at,
                "title": title,
                "text_length": len(text),
                "sha256": hashlib.sha256(payload).hexdigest() if payload else "",
                "text_excerpt": text[:1500],
            }
        )
        if text:
            amount_rows.extend(find_amounts(source_id, text))
            pair_rows.extend(find_pairs(source_id, text, item["source_type"]))

    write_csv(
        args.output_dir / "source_records.csv",
        source_rows,
        [
            "source_id",
            "url",
            "source_type",
            "publisher",
            "scope",
            "expected_year",
            "final_url",
            "format",
            "content_type",
            "fetch_status",
            "error",
            "fetched_at_utc",
            "title",
            "text_length",
            "sha256",
            "text_excerpt",
        ],
    )
    write_csv(
        args.output_dir / "amount_candidates.csv",
        amount_rows,
        [
            "source_id",
            "amount_text",
            "amount_value",
            "amount_unit",
            "year_mentions",
            "province_mentions",
            "evidence_text",
            "verification_status",
        ],
    )
    write_csv(
        args.output_dir / "pair_candidates.csv",
        pair_rows,
        [
            "source_id",
            "province_a",
            "province_b",
            "year_mentions",
            "evidence_text",
            "relationship_status",
        ],
    )
    ok_count = sum(row["fetch_status"] == "ok" for row in source_rows)
    print(
        json.dumps(
            {
                "sources_total": len(source_rows),
                "sources_ok": ok_count,
                "amount_candidates": len(amount_rows),
                "pair_candidates": len(pair_rows),
                "output_dir": str(args.output_dir.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok_count == len(source_rows) else 2


if __name__ == "__main__":
    sys.exit(main())
