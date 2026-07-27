from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "output"
DB_PATH = OUTPUT / "upstream_downstream_panel.sqlite"
PANEL_PATH = OUTPUT / "analysis_panel_2015_2024.csv"
COVERAGE_PATH = OUTPUT / "coverage_by_variable.csv"


def main() -> int:
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}")

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    variable_codes = [
        row["variable"]
        for row in connection.execute(
            """
            SELECT variable
            FROM variables
            WHERE variable <> 'actual_horizontal_payment_10k_cny'
            ORDER BY variable
            """
        )
    ]
    base_rows = connection.execute(
        """
        SELECT p.province, p.basin_position_group, y.year
        FROM provinces p
        CROSS JOIN (SELECT DISTINCT year FROM indicator_values) y
        ORDER BY y.year, p.nbs_region_code
        """
    ).fetchall()
    values = {
        (row["province"], row["year"], row["variable"]): row["value"]
        for row in connection.execute(
            """
            SELECT province, year, variable, value
            FROM indicator_values
            WHERE value IS NOT NULL
              AND value_status IN ('observed_official', 'derived_from_official')
            """
        )
    }
    payments = {
        (row["province"], row["year"]): (
            row["verified_payment_inflow"] or 0.0,
            row["verified_payment_outflow"] or 0.0,
        )
        for row in connection.execute(
            """
            SELECT obligation_year AS year, province,
                   SUM(inflow) AS verified_payment_inflow,
                   SUM(outflow) AS verified_payment_outflow
            FROM (
                SELECT obligation_year,
                       recipient AS province,
                       amount_10k_cny / 10000.0 AS inflow, 0.0 AS outflow
                FROM payment_events
                UNION ALL
                SELECT obligation_year,
                       payer AS province,
                       0.0 AS inflow, amount_10k_cny / 10000.0 AS outflow
                FROM payment_events
            )
            GROUP BY obligation_year, province
            """
        )
    }

    fields = [
        "province",
        "basin_position_group",
        "year",
        *variable_codes,
        "verified_payment_inflow_100m_yuan",
        "verified_payment_outflow_100m_yuan",
        "verified_payment_net_100m_yuan",
    ]
    with PANEL_PATH.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for base in base_rows:
            output_row = dict(base)
            for variable_code in variable_codes:
                output_row[variable_code] = values.get(
                    (base["province"], base["year"], variable_code), ""
                )
            inflow, outflow = payments.get(
                (base["province"], base["year"]), (0.0, 0.0)
            )
            output_row["verified_payment_inflow_100m_yuan"] = inflow
            output_row["verified_payment_outflow_100m_yuan"] = outflow
            output_row["verified_payment_net_100m_yuan"] = inflow - outflow
            writer.writerow(output_row)

    coverage_rows = connection.execute(
        """
        SELECT v.variable, v.conceptual_block, v.unit,
               COUNT(*) AS target_cells,
               SUM(CASE WHEN iv.value IS NOT NULL AND iv.value_status IN
                   ('observed_official', 'derived_from_official')
                   THEN 1 ELSE 0 END) AS populated_cells,
               SUM(CASE WHEN iv.value IS NULL OR iv.value_status = 'missing_not_zero'
                   THEN 1 ELSE 0 END) AS missing_cells
        FROM variables v
        JOIN indicator_values iv ON iv.variable = v.variable
        GROUP BY v.variable, v.conceptual_block, v.unit
        ORDER BY v.variable
        """
    ).fetchall()
    with COVERAGE_PATH.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=coverage_rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(row) for row in coverage_rows)

    connection.close()
    print(f"panel_rows={len(base_rows)}")
    print(f"panel_path={PANEL_PATH}")
    print(f"coverage_path={COVERAGE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
