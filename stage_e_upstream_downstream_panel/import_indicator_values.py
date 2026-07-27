from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


BASE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        type=Path,
        default=BASE / "output" / "upstream_downstream_panel.sqlite",
    )
    parser.add_argument("csv_file", type=Path)
    args = parser.parse_args()

    with args.csv_file.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    valid_status = {
        "observed_official",
        "derived_from_official",
        "missing_not_zero",
    }
    connection = sqlite3.connect(args.database)
    for row in rows:
        if row["value_status"] not in valid_status:
            raise ValueError(f"invalid value_status: {row['value_status']}")
        value = float(row["value"]) if row["value"] else None
        connection.execute(
            """
            UPDATE indicator_values
            SET value=?, unit=?, value_status=?, source_id=?, source_url=?,
                table_locator=?, extraction_method=?, evidence_grade=?, notes=?
            WHERE province=? AND year=? AND variable=?
            """,
            (
                value,
                row["unit"],
                row["value_status"],
                row["source_id"],
                row["source_url"],
                row["table_locator"],
                row["extraction_method"],
                row["evidence_grade"],
                row["notes"],
                row["province"],
                int(row["year"]),
                row["variable"],
            ),
        )
    connection.commit()
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
