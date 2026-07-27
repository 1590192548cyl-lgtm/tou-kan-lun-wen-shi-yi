from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "output" / "payment_ledger_candidates.csv"
OUTPUT = ROOT / "output" / "payment_ledger_audit.json"


def write_csv(name: str, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with (ROOT / "output" / name).open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    with LEDGER.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    usable = [row for row in rows if row["usable_as_bilateral_p_ijt"] == "yes"]
    keys = [
        (row["payer"], row["recipient"], row["period_start"], row["river"])
        for row in usable
    ]
    duplicates = [key for key, count in Counter(keys).items() if count > 1]
    pair_years: Counter[tuple[str, str]] = Counter(
        (row["payer"], row["recipient"]) for row in usable
    )
    years = sorted({int(row["period_start"]) for row in usable})
    yearly_amounts: Counter[int] = Counter()
    pair_amounts: Counter[tuple[str, str]] = Counter()
    for row in usable:
        amount = float(row["amount_10k_cny"])
        yearly_amounts[int(row["period_start"])] += amount
        pair_amounts[(row["payer"], row["recipient"])] += amount

    write_csv(
        "verified_payment_year_summary.csv",
        [
            {
                "performance_year": year,
                "verified_payment_count": sum(
                    int(row["period_start"]) == year for row in usable
                ),
                "verified_payment_total_10k_cny": yearly_amounts[year],
            }
            for year in years
        ],
    )
    write_csv(
        "verified_payment_pair_summary.csv",
        [
            {
                "payer": payer,
                "recipient": recipient,
                "verified_year_count": pair_years[(payer, recipient)],
                "verified_payment_total_10k_cny": pair_amounts[(payer, recipient)],
            }
            for payer, recipient in sorted(pair_years)
        ],
    )

    audit = {
        "ledger_rows": len(rows),
        "usable_bilateral_actual_rows": len(usable),
        "usable_years": years,
        "unique_directed_pairs": len(pair_years),
        "observations_by_pair": {
            f"{payer}->{recipient}": count
            for (payer, recipient), count in sorted(pair_years.items())
        },
        "duplicate_pair_year_river_keys": [list(key) for key in duplicates],
        "gate_a_passed_for_original_bilateral_panel": False,
        "gate_a_reason": (
            "真实逐笔支付仍不足以形成原稿所称2018—2025年多省对平衡面板；"
            "未公开年份必须记为缺失，不能记为零。"
        ),
        "permitted_analysis": [
            "verified payment ledger description",
            "river-specific payment chronology",
            "published theoretical-standard panel description",
        ],
        "prohibited_analysis": [
            "original 28-pair bilateral frontier estimation",
            "treating unobserved payments as zero",
            "interpreting theoretical standards as actual P_ijt",
        ],
    }
    OUTPUT.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False))
    return 1 if duplicates else 0


if __name__ == "__main__":
    raise SystemExit(main())
