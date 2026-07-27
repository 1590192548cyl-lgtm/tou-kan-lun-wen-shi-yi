from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "published_panel_2010_2020.csv"
OUTPUT = ROOT / "output"


def read_panel() -> list[dict[str, object]]:
    with INPUT.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    numeric = (
        "year",
        "ecological_overload_coefficient",
        "compensation_correction_coefficient",
        "theoretical_compensation_100m_cny",
    )
    for row in rows:
        for key in numeric:
            row[key] = int(row[key]) if key == "year" else float(row[key])
    return rows


def write_csv(name: str, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with (OUTPUT / name).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def hhi(values: list[float]) -> float:
    total = sum(values)
    return sum((value / total) ** 2 for value in values) if total else 0.0


def rank(values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: item[1])
    result: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2
        for name, _ in ordered[index:end]:
            result[name] = average_rank
        index = end
    return result


def pearson(left: list[float], right: list[float]) -> float:
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_ss = sum((x - left_mean) ** 2 for x in left)
    right_ss = sum((y - right_mean) ** 2 for y in right)
    return numerator / (left_ss * right_ss) ** 0.5 if left_ss and right_ss else 0.0


def main() -> int:
    rows = read_panel()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    years = sorted({int(row["year"]) for row in rows})
    provinces = sorted({str(row["province"]) for row in rows})
    if len(rows) != len(years) * len(provinces):
        raise ValueError("Panel is not balanced")

    by_year: dict[int, list[dict[str, object]]] = defaultdict(list)
    by_province: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_year[int(row["year"])].append(row)
        by_province[str(row["province"])].append(row)

    year_summary = []
    for year in years:
        values = [float(row["theoretical_compensation_100m_cny"]) for row in by_year[year]]
        payments = [-value for value in values if value < 0]
        receipts = [value for value in values if value > 0]
        year_summary.append(
            {
                "year": year,
                "province_count": len(values),
                "payer_count": len(payments),
                "recipient_count": len(receipts),
                "gross_theoretical_payment_100m_cny": round(sum(payments), 3),
                "gross_theoretical_receipt_100m_cny": round(sum(receipts), 3),
                "net_sum_100m_cny": round(sum(values), 3),
                "payer_hhi": round(hhi(payments), 6),
                "recipient_hhi": round(hhi(receipts), 6),
            }
        )

    province_summary = []
    for province in provinces:
        province_rows = sorted(by_province[province], key=lambda row: int(row["year"]))
        values = [float(row["theoretical_compensation_100m_cny"]) for row in province_rows]
        roles = [str(row["role"]) for row in province_rows]
        province_summary.append(
            {
                "province": province,
                "payer_years": roles.count("payer"),
                "recipient_years": roles.count("recipient"),
                "sign_changes": sum(a != b for a, b in zip(roles, roles[1:])),
                "value_2010": values[0],
                "value_2015": values[1],
                "value_2020": values[2],
                "change_2010_2020": round(values[-1] - values[0], 3),
                "mean_value": round(mean(values), 3),
            }
        )

    transition_rows = []
    transition_counter: Counter[tuple[int, int, str, str]] = Counter()
    for province in provinces:
        province_rows = sorted(by_province[province], key=lambda row: int(row["year"]))
        for left, right in zip(province_rows, province_rows[1:]):
            transition_counter[
                (int(left["year"]), int(right["year"]), str(left["role"]), str(right["role"]))
            ] += 1
    for key, count in sorted(transition_counter.items()):
        transition_rows.append(
            {
                "from_year": key[0],
                "to_year": key[1],
                "from_role": key[2],
                "to_role": key[3],
                "province_count": count,
            }
        )

    rank_rows = []
    year_values = {
        year: {
            str(row["province"]): float(row["theoretical_compensation_100m_cny"])
            for row in by_year[year]
        }
        for year in years
    }
    year_ranks = {year: rank(values) for year, values in year_values.items()}
    for left_index, left_year in enumerate(years):
        for right_year in years[left_index + 1 :]:
            rank_rows.append(
                {
                    "year_a": left_year,
                    "year_b": right_year,
                    "spearman_rank_correlation": round(
                        pearson(
                            [year_ranks[left_year][province] for province in provinces],
                            [year_ranks[right_year][province] for province in provinces],
                        ),
                        6,
                    ),
                    "province_count": len(provinces),
                }
            )

    write_csv("year_summary.csv", year_summary)
    write_csv("province_summary.csv", province_summary)
    write_csv("role_transitions.csv", transition_rows)
    write_csv("rank_persistence.csv", rank_rows)
    summary = {
        "observation_count": len(rows),
        "province_count": len(provinces),
        "years": years,
        "balanced_panel": True,
        "left_censored_values_encoded_as_midpoint_0_005": sum(
            bool(row["censor_flag"]) for row in rows
        ),
        "analysis_scope": [
            "descriptive trend",
            "payer-recipient role transition",
            "payer and recipient concentration",
            "province rank persistence",
        ],
        "not_identified": [
            "causal effect",
            "actual bilateral P_ijt",
            "fixed-effects regression with adequate time dimension",
        ],
    }
    (OUTPUT / "analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
