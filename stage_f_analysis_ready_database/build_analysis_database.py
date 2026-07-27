from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
OUTPUT = BASE / "output"
DB_PATH = OUTPUT / "analysis_ready_upstream_downstream.sqlite"
PANEL_PATH = OUTPUT / "analysis_ready_panel_2010_2020.csv"
GROUP_PATH = OUTPUT / "group_year_summary.csv"
SOURCE_PANEL = ROOT / "stage_b_empirical_ready" / "published_panel_2010_2020.csv"
PAYMENT_LEDGER = (
    ROOT
    / "stage_d_provincial_fiscal_accounts"
    / "output"
    / "verified_bilateral_events.csv"
)

PROVINCE_GROUPS = {
    "上海": "lower",
    "江苏": "lower",
    "浙江": "lower",
    "安徽": "lower",
    "江西": "middle",
    "湖北": "middle",
    "湖南": "middle",
    "重庆": "upper",
    "四川": "upper",
    "贵州": "upper",
    "云南": "upper",
}
METRICS = (
    "ecological_overload_coefficient",
    "compensation_correction_coefficient",
    "theoretical_compensation_100m_cny",
)


def read_anchor_panel() -> dict[str, dict[int, dict[str, float]]]:
    anchors: dict[str, dict[int, dict[str, float]]] = {}
    with SOURCE_PANEL.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            province = row["province"]
            if province not in PROVINCE_GROUPS:
                continue
            year = int(row["year"])
            anchors.setdefault(province, {})[year] = {
                metric: float(row[metric]) for metric in METRICS
            }
    expected = set(PROVINCE_GROUPS)
    if set(anchors) != expected:
        raise ValueError(f"Province mismatch: {expected - set(anchors)}")
    for province, year_rows in anchors.items():
        if set(year_rows) != {2010, 2015, 2020}:
            raise ValueError(f"Incomplete anchors for {province}: {sorted(year_rows)}")
    return anchors


def interpolate(
    anchors: dict[str, dict[int, dict[str, float]]]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for province, group in PROVINCE_GROUPS.items():
        for year in range(2010, 2021):
            if year in anchors[province]:
                values = anchors[province][year]
                status = "observed_published_anchor"
                left_year = right_year = year
            else:
                left_year, right_year = (2010, 2015) if year < 2015 else (2015, 2020)
                weight = (year - left_year) / (right_year - left_year)
                values = {
                    metric: anchors[province][left_year][metric]
                    + weight
                    * (
                        anchors[province][right_year][metric]
                        - anchors[province][left_year][metric]
                    )
                    for metric in METRICS
                }
                status = "linear_interpolation"
            compensation = values["theoretical_compensation_100m_cny"]
            rows.append(
                {
                    "province": province,
                    "year": year,
                    "basin_position_group": group,
                    **{metric: round(values[metric], 6) for metric in METRICS},
                    "payer_dummy": int(compensation < 0),
                    "recipient_dummy": int(compensation >= 0),
                    "absolute_compensation_100m_cny": round(abs(compensation), 6),
                    "value_status": status,
                    "interpolation_left_year": left_year,
                    "interpolation_right_year": right_year,
                    "source_id": "paper_2025_yangtze_water_compensation_table5",
                }
            )
    return rows


def create_database(rows: list[dict[str, object]]) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE compensation_panel (
            province TEXT NOT NULL,
            year INTEGER NOT NULL,
            basin_position_group TEXT NOT NULL,
            ecological_overload_coefficient REAL NOT NULL,
            compensation_correction_coefficient REAL NOT NULL,
            theoretical_compensation_100m_cny REAL NOT NULL,
            payer_dummy INTEGER NOT NULL,
            recipient_dummy INTEGER NOT NULL,
            absolute_compensation_100m_cny REAL NOT NULL,
            value_status TEXT NOT NULL,
            interpolation_left_year INTEGER NOT NULL,
            interpolation_right_year INTEGER NOT NULL,
            source_id TEXT NOT NULL,
            PRIMARY KEY (province, year)
        );
        CREATE TABLE verified_payment_events (
            record_id TEXT PRIMARY KEY,
            obligation_year INTEGER NOT NULL,
            reported_payment_year INTEGER,
            payer TEXT NOT NULL,
            recipient TEXT NOT NULL,
            river TEXT NOT NULL,
            amount_10k_cny REAL NOT NULL,
            evidence_grade TEXT NOT NULL,
            source_id TEXT NOT NULL
        );
        CREATE VIEW analysis_panel AS
        SELECT * FROM compensation_panel;
        CREATE VIEW observed_anchor_panel AS
        SELECT * FROM compensation_panel
        WHERE value_status = 'observed_published_anchor';
        CREATE VIEW verified_province_year_payment AS
        SELECT obligation_year AS year, province,
               SUM(inflow_10k_cny) AS verified_inflow_10k_cny,
               SUM(outflow_10k_cny) AS verified_outflow_10k_cny,
               SUM(inflow_10k_cny - outflow_10k_cny) AS verified_net_10k_cny
        FROM (
            SELECT obligation_year, recipient AS province,
                   amount_10k_cny AS inflow_10k_cny, 0.0 AS outflow_10k_cny
            FROM verified_payment_events
            UNION ALL
            SELECT obligation_year, payer AS province,
                   0.0 AS inflow_10k_cny, amount_10k_cny AS outflow_10k_cny
            FROM verified_payment_events
        )
        GROUP BY obligation_year, province;
        CREATE VIEW panel_with_payment_validation AS
        SELECT p.*, v.verified_inflow_10k_cny, v.verified_outflow_10k_cny,
               v.verified_net_10k_cny
        FROM compensation_panel p
        LEFT JOIN verified_province_year_payment v
          ON p.province = v.province AND p.year = v.year;
        CREATE INDEX idx_panel_group_year
            ON compensation_panel (basin_position_group, year);
        CREATE INDEX idx_payment_year
            ON verified_payment_events (obligation_year);
        """
    )
    connection.executemany(
        """
        INSERT INTO compensation_panel VALUES (
            :province, :year, :basin_position_group,
            :ecological_overload_coefficient,
            :compensation_correction_coefficient,
            :theoretical_compensation_100m_cny,
            :payer_dummy, :recipient_dummy,
            :absolute_compensation_100m_cny,
            :value_status, :interpolation_left_year,
            :interpolation_right_year, :source_id
        )
        """,
        rows,
    )
    metadata = {
        "analysis_unit": "province-year",
        "coverage": "11 Yangtze-related provinces, 2010-2020",
        "observed_anchor_years": "2010|2015|2020",
        "interpolation_method": "within-province linear interpolation",
        "interpretation": "scenario panel for upstream-middle-downstream difference analysis",
        "causal_claim_allowed": "no",
        "actual_bilateral_payment_claim_allowed": "no",
    }
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items()
    )
    with PAYMENT_LEDGER.open(encoding="utf-8-sig", newline="") as handle:
        payment_rows = list(csv.DictReader(handle))
    connection.executemany(
        """
        INSERT INTO verified_payment_events (
            record_id, obligation_year, reported_payment_year, payer, recipient,
            river, amount_10k_cny, evidence_grade, source_id
        ) VALUES (
            :record_id, :obligation_year, :reported_payment_year, :payer, :recipient,
            :river, :amount_10k_cny, :evidence_grade, :source_id
        )
        """,
        payment_rows,
    )
    connection.commit()

    with PANEL_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summaries = connection.execute(
        """
        SELECT basin_position_group, year, COUNT(*) AS province_count,
               AVG(theoretical_compensation_100m_cny) AS mean_compensation_100m_cny,
               AVG(absolute_compensation_100m_cny) AS mean_absolute_compensation_100m_cny,
               AVG(ecological_overload_coefficient) AS mean_overload_coefficient,
               AVG(compensation_correction_coefficient) AS mean_correction_coefficient,
               AVG(payer_dummy) AS payer_share
        FROM compensation_panel
        GROUP BY basin_position_group, year
        ORDER BY year, basin_position_group
        """
    ).fetchall()
    summary_fields = [
        "basin_position_group",
        "year",
        "province_count",
        "mean_compensation_100m_cny",
        "mean_absolute_compensation_100m_cny",
        "mean_overload_coefficient",
        "mean_correction_coefficient",
        "payer_share",
    ]
    with GROUP_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(summary_fields)
        writer.writerows(summaries)
    connection.close()


def validate() -> None:
    connection = sqlite3.connect(DB_PATH)
    total = connection.execute("SELECT COUNT(*) FROM compensation_panel").fetchone()[0]
    provinces = connection.execute(
        "SELECT COUNT(DISTINCT province) FROM compensation_panel"
    ).fetchone()[0]
    years = connection.execute(
        "SELECT COUNT(DISTINCT year) FROM compensation_panel"
    ).fetchone()[0]
    missing = connection.execute(
        """
        SELECT COUNT(*) FROM compensation_panel
        WHERE ecological_overload_coefficient IS NULL
           OR compensation_correction_coefficient IS NULL
           OR theoretical_compensation_100m_cny IS NULL
        """
    ).fetchone()[0]
    anchors = connection.execute(
        "SELECT COUNT(*) FROM compensation_panel WHERE value_status='observed_published_anchor'"
    ).fetchone()[0]
    interpolated = connection.execute(
        "SELECT COUNT(*) FROM compensation_panel WHERE value_status='linear_interpolation'"
    ).fetchone()[0]
    payments = connection.execute(
        "SELECT COUNT(*) FROM verified_payment_events"
    ).fetchone()[0]
    connection.close()
    assert (total, provinces, years, missing) == (121, 11, 11, 0)
    assert anchors == 33 and interpolated == 88
    print(
        f"rows={total} provinces={provinces} years={years} missing={missing} "
        f"anchors={anchors} interpolated={interpolated} payment_events={payments}"
    )


def main() -> int:
    create_database(interpolate(read_anchor_panel()))
    validate()
    print(DB_PATH)
    print(PANEL_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
