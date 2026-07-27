from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
OUTPUT = BASE / "output"
DB_PATH = OUTPUT / "upstream_downstream_panel.sqlite"
YEARS = range(2015, 2025)
PROVINCES = {
    "上海": ("lower", 310000),
    "江苏": ("lower", 320000),
    "浙江": ("lower", 330000),
    "安徽": ("lower", 340000),
    "江西": ("middle", 360000),
    "湖北": ("middle", 420000),
    "湖南": ("middle", 430000),
    "重庆": ("upper", 500000),
    "四川": ("upper", 510000),
    "贵州": ("upper", 520000),
    "云南": ("upper", 530000),
}
VARIABLES = {
    "population_10k": ("benefit", "万人", "positive"),
    "gdp_100m_cny": ("payment_capacity", "亿元", "positive"),
    "secondary_industry_share_pct": ("pressure", "%", "positive"),
    "general_budget_revenue_100m_cny": (
        "payment_capacity",
        "亿元",
        "positive",
    ),
    "water_resources_100m_m3": ("contribution", "亿立方米", "positive"),
    "water_supply_100m_m3": ("benefit", "亿立方米", "positive"),
    "water_use_100m_m3": ("benefit", "亿立方米", "positive"),
    "wastewater_discharge_10k_t": ("pressure", "万吨", "positive"),
    "cod_discharge_t": ("pressure", "吨", "positive"),
    "ammonia_nitrogen_discharge_t": ("pressure", "吨", "positive"),
    "forest_coverage_pct": ("contribution", "%", "positive"),
    "environmental_investment_100m_cny": (
        "protection_cost",
        "亿元",
        "positive",
    ),
    "surface_water_good_section_pct": (
        "contribution",
        "%",
        "positive",
    ),
    "actual_horizontal_payment_10k_cny": (
        "validation",
        "万元",
        "signed_directional",
    ),
}
SOURCES = [
    (
        "nbs_yearbook",
        "国家统计局",
        "中国统计年鉴各年度",
        "https://www.stats.gov.cn/sj/ndsj/",
        "population_10k|gdp_100m_cny|secondary_industry_share_pct|general_budget_revenue_100m_cny|water_resources_100m_m3|water_supply_100m_m3|water_use_100m_m3|wastewater_discharge_10k_t|cod_discharge_t|ammonia_nitrogen_discharge_t|forest_coverage_pct|environmental_investment_100m_cny",
    ),
    (
        "mee_environment_yearbook",
        "生态环境部",
        "中国生态环境统计年报",
        "https://www.mee.gov.cn/hjzl/sthjzk/sthjtjnb/",
        "wastewater_discharge_10k_t|cod_discharge_t|ammonia_nitrogen_discharge_t|environmental_investment_100m_cny",
    ),
    (
        "mee_surface_water",
        "生态环境部",
        "全国地表水环境质量状况及月报",
        "https://www.mee.gov.cn/hjzl/shj/",
        "surface_water_good_section_pct",
    ),
    (
        "mwr_water_bulletin",
        "水利部",
        "中国水资源公报及各省水资源公报",
        "https://www.mwr.gov.cn/sj/tjgb/szygb/",
        "water_resources_100m_m3|water_supply_100m_m3|water_use_100m_m3",
    ),
    (
        "verified_payment_ledger",
        "财政部及省级政府部门",
        "已核验横向生态补偿支付记录",
        "local:stage_d_provincial_fiscal_accounts/output/verified_bilateral_events.csv",
        "actual_horizontal_payment_10k_cny",
    ),
]


def write_csv(
    path: Path, rows: list[dict[str, object]], fields: list[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild the database even if it already exists.",
    )
    args = parser.parse_args()

    OUTPUT.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        if not args.force:
            raise SystemExit(
                f"Database already exists: {DB_PATH}. "
                "Use --force only for an intentional rebuild."
            )
        DB_PATH.unlink()
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(
        """
        CREATE TABLE provinces (
            province TEXT PRIMARY KEY,
            nbs_region_code INTEGER NOT NULL,
            basin_position_group TEXT NOT NULL,
            position_note TEXT NOT NULL
        );
        CREATE TABLE variables (
            variable TEXT PRIMARY KEY,
            conceptual_block TEXT NOT NULL,
            unit TEXT NOT NULL,
            expected_direction TEXT NOT NULL
        );
        CREATE TABLE sources (
            source_id TEXT PRIMARY KEY,
            publisher TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            variables TEXT NOT NULL
        );
        CREATE TABLE indicator_values (
            province TEXT NOT NULL,
            year INTEGER NOT NULL,
            variable TEXT NOT NULL,
            value REAL,
            unit TEXT NOT NULL,
            value_status TEXT NOT NULL,
            source_id TEXT,
            source_url TEXT,
            table_locator TEXT,
            extraction_method TEXT,
            evidence_grade TEXT,
            notes TEXT,
            PRIMARY KEY (province, year, variable),
            FOREIGN KEY (province) REFERENCES provinces(province),
            FOREIGN KEY (variable) REFERENCES variables(variable)
        );
        CREATE TABLE payment_events (
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
        """
    )
    connection.executemany(
        "INSERT INTO provinces VALUES (?, ?, ?, ?)",
        [
            (
                province,
                code,
                position,
                "分析分组，不代表省内所有河段均属于同一上下游位置",
            )
            for province, (position, code) in PROVINCES.items()
        ],
    )
    connection.executemany(
        "INSERT INTO variables VALUES (?, ?, ?, ?)",
        [
            (name, block, unit, direction)
            for name, (block, unit, direction) in VARIABLES.items()
        ],
    )
    connection.executemany("INSERT INTO sources VALUES (?, ?, ?, ?, ?)", SOURCES)

    template_rows = []
    for province in PROVINCES:
        for year in YEARS:
            for variable, (_, unit, _) in VARIABLES.items():
                if variable == "actual_horizontal_payment_10k_cny":
                    continue
                template_rows.append(
                    {
                        "province": province,
                        "year": year,
                        "variable": variable,
                        "value": None,
                        "unit": unit,
                        "value_status": "missing_not_zero",
                        "source_id": "",
                        "source_url": "",
                        "table_locator": "",
                        "extraction_method": "",
                        "evidence_grade": "",
                        "notes": "",
                    }
                )
    connection.executemany(
        """
        INSERT INTO indicator_values VALUES (
            :province, :year, :variable, :value, :unit, :value_status,
            :source_id, :source_url, :table_locator, :extraction_method,
            :evidence_grade, :notes
        )
        """,
        template_rows,
    )

    events = read_csv(
        ROOT
        / "stage_d_provincial_fiscal_accounts"
        / "output"
        / "verified_bilateral_events.csv"
    )
    connection.executemany(
        "INSERT INTO payment_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                row["record_id"],
                int(row["obligation_year"]),
                int(row["reported_payment_year"])
                if row["reported_payment_year"]
                else None,
                row["payer"],
                row["recipient"],
                row["river"],
                float(row["amount_10k_cny"]),
                row["evidence_grade"],
                row["source_id"],
            )
            for row in events
        ],
    )
    connection.commit()

    gap_rows = []
    for variable, (block, unit, _) in VARIABLES.items():
        if variable == "actual_horizontal_payment_10k_cny":
            continue
        source_ids = [
            source[0] for source in SOURCES if variable in source[4].split("|")
        ]
        gap_rows.append(
            {
                "variable": variable,
                "conceptual_block": block,
                "unit": unit,
                "target_observations": len(PROVINCES) * len(YEARS),
                "observed_values": 0,
                "missing_values": len(PROVINCES) * len(YEARS),
                "preferred_sources": "|".join(source_ids),
                "collection_status": "source_located_values_pending",
            }
        )
    write_csv(
        OUTPUT / "indicator_input_template.csv",
        template_rows,
        [
            "province",
            "year",
            "variable",
            "value",
            "unit",
            "value_status",
            "source_id",
            "source_url",
            "table_locator",
            "extraction_method",
            "evidence_grade",
            "notes",
        ],
    )
    write_csv(
        OUTPUT / "collection_gap_audit.csv",
        gap_rows,
        [
            "variable",
            "conceptual_block",
            "unit",
            "target_observations",
            "observed_values",
            "missing_values",
            "preferred_sources",
            "collection_status",
        ],
    )
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
