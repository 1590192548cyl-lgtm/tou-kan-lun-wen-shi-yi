from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import ssl
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from lxml import html
from pypdf import PdfReader


ACCOUNT_PATTERNS = {
    "ecological_compensation_transfer_income": re.compile(
        r"生态保护补偿转移性收入\s*[|：:]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)"
    ),
    "ecological_compensation_transfer_expenditure": re.compile(
        r"生态保护补偿转移性支出\s*[|：:]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)"
    ),
}
UNIT_PATTERN = re.compile(r"单位\s*[：:]?\s*(万元|亿元)")
SPACE_PATTERN = re.compile(r"[ \t\r\f\v]+")


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
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=ssl.create_default_context()
        ) as response:
            return (
                response.read(),
                response.headers.get("Content-Type", ""),
                response.geturl(),
            )
    except Exception as urllib_error:
        marker = b"\n__FETCH_META__"
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
                "\n__FETCH_META__%{content_type}\t%{url_effective}",
                url,
            ],
            check=False,
            capture_output=True,
        )
        if process.returncode != 0 or marker not in process.stdout:
            detail = process.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"urllib failed ({urllib_error}); curl failed ({detail})"
            ) from urllib_error
        payload, metadata = process.stdout.rsplit(marker, 1)
        content_type, final_url = metadata.decode(
            "utf-8", errors="replace"
        ).split("\t", 1)
        return payload, content_type, final_url.strip()


def html_text_and_pdf_links(
    payload: bytes, base_url: str
) -> tuple[str, list[str]]:
    document = html.fromstring(payload)
    for node in document.xpath("//script|//style|//noscript"):
        node.drop_tree()
    text = "\n".join(
        SPACE_PATTERN.sub(" ", item).strip()
        for item in document.xpath("//body//text()")
        if SPACE_PATTERN.sub(" ", item).strip()
    )
    links = []
    for href in document.xpath("//a/@href"):
        absolute = urllib.parse.urljoin(base_url, href)
        if ".pdf" in absolute.lower():
            links.append(absolute)
    return text, list(dict.fromkeys(links))


def pdf_text(payload: bytes) -> str:
    reader = PdfReader(io.BytesIO(payload))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def normalize_text(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def infer_unit(text: str, configured_unit: str | None) -> str:
    if configured_unit:
        return configured_unit
    match = UNIT_PATTERN.search(text)
    return match.group(1) if match else "unknown"


def to_ten_thousand_yuan(value: str, unit: str) -> float | None:
    numeric = float(value.replace(",", ""))
    if unit == "万元":
        return numeric
    if unit == "亿元":
        return numeric * 10000
    return None


def extract_accounts(
    source_id: str,
    province: str,
    year: int,
    source_url: str,
    text: str,
    configured_unit: str | None,
) -> list[dict[str, object]]:
    compact = normalize_text(text)
    unit = infer_unit(compact, configured_unit)
    rows: list[dict[str, object]] = []
    for account, pattern in ACCOUNT_PATTERNS.items():
        match = pattern.search(compact)
        if not match:
            continue
        value = match.group(1)
        start = max(0, match.start() - 120)
        end = min(len(compact), match.end() + 120)
        rows.append(
            {
                "province": province,
                "year": year,
                "account": account,
                "reported_value": value.replace(",", ""),
                "reported_unit": unit,
                "amount_10k_cny": to_ten_thousand_yuan(value, unit),
                "value_status": "reported_numeric",
                "source_id": source_id,
                "source_url": source_url,
                "evidence_text": compact[start:end],
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sources", type=Path, default=Path(__file__).with_name("sources.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).with_name("output")
    )
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--indexed-only", action="store_true")
    args = parser.parse_args()

    sources = json.loads(args.sources.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat()
    source_rows: list[dict[str, object]] = []
    account_rows: list[dict[str, object]] = []

    for source in sources:
        source_id = source["source_id"]
        try:
            if args.indexed_only:
                raise RuntimeError("network fetch skipped by --indexed-only")
            payload, content_type, final_url = fetch(source["url"], args.timeout)
            is_pdf = "pdf" in content_type.lower() or final_url.lower().endswith(".pdf")
            if is_pdf:
                text = pdf_text(payload)
                pdf_links: list[str] = []
                selected_url = final_url
                selected_payload = payload
                selected_format = "pdf"
            else:
                text, pdf_links = html_text_and_pdf_links(payload, final_url)
                selected_url = final_url
                selected_payload = payload
                selected_format = "html"
                if source.get("follow_pdf") and pdf_links:
                    selected_url = pdf_links[0]
                    selected_payload, _, selected_url = fetch(
                        selected_url, args.timeout
                    )
                    text = pdf_text(selected_payload)
                    selected_format = "pdf_from_landing_page"
            status = "ok" if text else "empty_text"
            error = ""
        except Exception as exc:
            selected_url = source["url"]
            selected_payload = b""
            selected_format = "unknown"
            text = ""
            pdf_links = []
            status = "error"
            error = f"{type(exc).__name__}: {exc}"

        extracted = extract_accounts(
            source_id,
            source["province"],
            int(source["year"]),
            selected_url,
            text,
            source.get("unit"),
        )
        if not extracted and source.get("indexed_records"):
            extracted = [
                {
                    "province": source["province"],
                    "year": int(source["year"]),
                    "account": record["account"],
                    "reported_value": str(record["reported_value"]),
                    "reported_unit": record["reported_unit"],
                    "amount_10k_cny": to_ten_thousand_yuan(
                        str(record["reported_value"]), record["reported_unit"]
                    ),
                    "value_status": record["value_status"],
                    "source_id": source_id,
                    "source_url": source["url"],
                    "evidence_text": record["evidence_text"],
                }
                for record in source["indexed_records"]
            ]
            status = "indexed_official_text"
        account_rows.extend(extracted)
        source_rows.append(
            {
                **source,
                "final_url": selected_url,
                "format": selected_format,
                "fetch_status": status,
                "error": error,
                "fetched_at_utc": fetched_at,
                "sha256": (
                    hashlib.sha256(selected_payload).hexdigest()
                    if selected_payload
                    else ""
                ),
                "text_length": len(text),
                "pdf_links_found": "|".join(pdf_links),
                "accounts_extracted": len(extracted),
            }
        )

    write_csv(
        args.output_dir / "source_audit.csv",
        source_rows,
        [
            "source_id",
            "province",
            "year",
            "url",
            "publisher",
            "source_type",
            "unit",
            "follow_pdf",
            "final_url",
            "format",
            "fetch_status",
            "error",
            "fetched_at_utc",
            "sha256",
            "text_length",
            "pdf_links_found",
            "accounts_extracted",
        ],
    )
    write_csv(
        args.output_dir / "province_year_fiscal_accounts.csv",
        account_rows,
        [
            "province",
            "year",
            "account",
            "reported_value",
            "reported_unit",
            "amount_10k_cny",
            "value_status",
            "source_id",
            "source_url",
            "evidence_text",
        ],
    )
    print(
        json.dumps(
            {
                "sources": len(source_rows),
                "sources_ok": sum(row["fetch_status"] == "ok" for row in source_rows),
                "sources_usable": sum(
                    row["fetch_status"] in {"ok", "indexed_official_text"}
                    for row in source_rows
                ),
                "account_values": len(account_rows),
                "output_dir": str(args.output_dir.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return (
        0
        if all(
            row["fetch_status"] in {"ok", "indexed_official_text"}
            for row in source_rows
        )
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
