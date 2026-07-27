from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STAGE_D = Path(__file__).resolve().parent
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
)
YEARS = range(2018, 2025)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(
    path: Path, rows: list[dict[str, object]], fields: list[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    bilateral = read_csv(
        ROOT
        / "stage_a_payment_audit"
        / "output"
        / "payment_ledger_candidates.csv"
    )
    province_candidates = read_csv(
        ROOT / "stage_c_data_expansion" / "province_year_payment_candidates.csv"
    )
    fiscal = read_csv(
        STAGE_D / "output" / "province_year_fiscal_accounts.csv"
    )

    event_flows: dict[tuple[str, int, str], float] = defaultdict(float)
    event_sources: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    event_count: dict[tuple[str, int, str], int] = defaultdict(int)
    event_rows: list[dict[str, object]] = []

    for row in bilateral:
        if row["usable_as_bilateral_p_ijt"].lower() != "yes":
            continue
        year = int(row["period_start"])
        amount = float(row["amount_10k_cny"])
        for province, direction in (
            (row["payer"], "outflow"),
            (row["recipient"], "inflow"),
        ):
            key = (province, year, direction)
            event_flows[key] += amount
            event_sources[key].add(row["source_id"])
            event_count[key] += 1
        event_rows.append(
            {
                "record_id": row["record_id"],
                "obligation_year": year,
                "reported_payment_year": row["payment_year"],
                "payer": row["payer"],
                "recipient": row["recipient"],
                "river": row["river"],
                "amount_10k_cny": amount,
                "evidence_grade": row["evidence_grade"],
                "source_id": row["source_id"],
                "aggregation_role": "bilateral_actual_event",
            }
        )

    derived_flows: dict[tuple[str, int, str], float] = defaultdict(float)
    derived_sources: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    for row in province_candidates:
        if row["usable_for_province_year_model"].lower() != "yes":
            continue
        key = (row["province"], int(row["year"]), row["direction"])
        derived_flows[key] += float(row["amount_10k_cny"])
        derived_sources[key].add(row["source_id"])

    fiscal_totals: dict[tuple[str, int, str], tuple[float, str, str]] = {}
    mixed_upper_bounds: dict[tuple[str, int, str], tuple[float, str]] = {}
    for row in fiscal:
        account = row["account"]
        if account == "ecological_compensation_transfer_income":
            direction = "inflow"
        elif account == "ecological_compensation_transfer_expenditure":
            direction = "outflow"
        elif account == "regional_transfer_expenditure_mixed_upper_bound":
            direction = "outflow"
            mixed_upper_bounds[
                (row["province"], int(row["year"]), direction)
            ] = (float(row["amount_10k_cny"]), row["source_id"])
            continue
        else:
            continue
        fiscal_totals[(row["province"], int(row["year"]), direction)] = (
            float(row["amount_10k_cny"]),
            row["source_id"],
            row["value_status"],
        )

    panel_rows: list[dict[str, object]] = []
    for province in PROVINCES:
        for year in YEARS:
            selected: dict[str, float | None] = {"inflow": None, "outflow": None}
            method: dict[str, str] = {}
            sources: dict[str, str] = {}
            lower_bounds: dict[str, float] = {}
            upper_bounds: dict[str, float | None] = {}

            for direction in ("inflow", "outflow"):
                key = (province, year, direction)
                event_value = event_flows.get(key, 0.0)
                lower_bounds[direction] = event_value
                upper_bounds[direction] = None
                if key in fiscal_totals:
                    value, source_id, value_status = fiscal_totals[key]
                    selected[direction] = value
                    method[direction] = "provincial_final_account_total"
                    sources[direction] = source_id
                    if event_value > value:
                        method[direction] = (
                            "conflict_event_sum_exceeds_final_account_total"
                        )
                elif event_value > 0:
                    selected[direction] = event_value
                    method[direction] = "sum_verified_bilateral_actual_events"
                    sources[direction] = "|".join(sorted(event_sources[key]))
                elif key in derived_flows:
                    selected[direction] = derived_flows[key]
                    method[direction] = "derived_fixed_schedule_actual"
                    sources[direction] = "|".join(sorted(derived_sources[key]))
                else:
                    selected[direction] = None
                    method[direction] = "missing_not_zero"
                    sources[direction] = ""
                if key in mixed_upper_bounds:
                    upper_bounds[direction] = mixed_upper_bounds[key][0]

            inflow = selected["inflow"]
            outflow = selected["outflow"]
            if inflow is not None and outflow is not None:
                net = inflow - outflow
            else:
                net = None
            observed_directions = sum(value is not None for value in selected.values())
            panel_rows.append(
                {
                    "province": province,
                    "year": year,
                    "inflow_10k_cny": inflow,
                    "outflow_10k_cny": outflow,
                    "net_inflow_10k_cny": net,
                    "inflow_method": method["inflow"],
                    "outflow_method": method["outflow"],
                    "inflow_sources": sources["inflow"],
                    "outflow_sources": sources["outflow"],
                    "verified_inflow_lower_bound_10k_cny": lower_bounds["inflow"],
                    "verified_outflow_lower_bound_10k_cny": lower_bounds["outflow"],
                    "outflow_mixed_upper_bound_10k_cny": upper_bounds["outflow"],
                    "observed_directions": observed_directions,
                    "coverage_status": (
                        "both_directions_observed"
                        if observed_directions == 2
                        else "one_direction_observed"
                        if observed_directions == 1
                        else "missing_not_zero"
                    ),
                    "analysis_ready_complete_case": (
                        "yes" if observed_directions == 2 else "no"
                    ),
                }
            )

    output = STAGE_D / "output"
    write_csv(
        output / "verified_bilateral_events.csv",
        event_rows,
        [
            "record_id",
            "obligation_year",
            "reported_payment_year",
            "payer",
            "recipient",
            "river",
            "amount_10k_cny",
            "evidence_grade",
            "source_id",
            "aggregation_role",
        ],
    )
    write_csv(
        output / "province_year_payment_panel_2018_2024.csv",
        panel_rows,
        [
            "province",
            "year",
            "inflow_10k_cny",
            "outflow_10k_cny",
            "net_inflow_10k_cny",
            "inflow_method",
            "outflow_method",
            "inflow_sources",
            "outflow_sources",
            "verified_inflow_lower_bound_10k_cny",
            "verified_outflow_lower_bound_10k_cny",
            "outflow_mixed_upper_bound_10k_cny",
            "observed_directions",
            "coverage_status",
            "analysis_ready_complete_case",
        ],
    )
    known_2024 = {
        "四川": ("complete", "已取得生态保护补偿转移性收入和支出决算数"),
        "湖南": ("needs_detail_table", "已取得混合区域间转移性支出，需拆分救灾与生态补偿"),
        "重庆": ("render_blocked", "已定位2024年决算页面，需手动下载附件PDF"),
    }
    manual_rows = []
    for province in PROVINCES:
        status, task = known_2024.get(
            province,
            (
                "source_not_yet_located",
                "查找并下载2024年省级一般公共预算收支决算平衡表",
            ),
        )
        manual_rows.append(
            {
                "province": province,
                "year": 2024,
                "status": status,
                "manual_task": task,
                "target_income_account": "1102102 生态保护补偿转移性收入",
                "target_expenditure_account": "2302102 生态保护补偿转移性支出",
                "do_not_substitute": "重点生态功能区转移支付|节能环保支出|中央引导奖励",
            }
        )
    write_csv(
        output / "manual_source_queue_2024.csv",
        manual_rows,
        [
            "province",
            "year",
            "status",
            "manual_task",
            "target_income_account",
            "target_expenditure_account",
            "do_not_substitute",
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
