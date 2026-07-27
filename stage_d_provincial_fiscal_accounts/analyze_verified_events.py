from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path


BASE = Path(__file__).resolve().parent


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(
    path: Path, rows: list[dict[str, object]], fields: list[str]
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    output = BASE / "output"
    events = read_csv(output / "verified_bilateral_events.csv")

    pair = defaultdict(lambda: {"amount": 0.0, "events": 0, "years": set(), "rivers": set()})
    annual = defaultdict(lambda: {"amount": 0.0, "events": 0, "pairs": set()})
    province = defaultdict(
        lambda: {"inflow": 0.0, "outflow": 0.0, "in_events": 0, "out_events": 0}
    )

    for row in events:
        amount = float(row["amount_10k_cny"])
        year = int(row["obligation_year"])
        pair_key = (row["payer"], row["recipient"])
        pair[pair_key]["amount"] += amount
        pair[pair_key]["events"] += 1
        pair[pair_key]["years"].add(year)
        pair[pair_key]["rivers"].add(row["river"])
        annual[year]["amount"] += amount
        annual[year]["events"] += 1
        annual[year]["pairs"].add(pair_key)
        province[row["payer"]]["outflow"] += amount
        province[row["payer"]]["out_events"] += 1
        province[row["recipient"]]["inflow"] += amount
        province[row["recipient"]]["in_events"] += 1

    pair_rows = [
        {
            "payer": payer,
            "recipient": recipient,
            "event_count": values["events"],
            "year_count": len(values["years"]),
            "first_year": min(values["years"]),
            "last_year": max(values["years"]),
            "total_amount_10k_cny": values["amount"],
            "mean_event_amount_10k_cny": values["amount"] / values["events"],
            "rivers": "|".join(sorted(values["rivers"])),
        }
        for (payer, recipient), values in sorted(pair.items())
    ]
    annual_rows = [
        {
            "year": year,
            "event_count": values["events"],
            "active_pair_count": len(values["pairs"]),
            "total_amount_10k_cny": values["amount"],
        }
        for year, values in sorted(annual.items())
    ]
    province_rows = [
        {
            "province": name,
            "verified_inflow_10k_cny": values["inflow"],
            "verified_outflow_10k_cny": values["outflow"],
            "verified_net_inflow_10k_cny": values["inflow"] - values["outflow"],
            "inflow_event_count": values["in_events"],
            "outflow_event_count": values["out_events"],
        }
        for name, values in sorted(province.items())
    ]

    write_csv(
        output / "event_pair_summary.csv",
        pair_rows,
        [
            "payer",
            "recipient",
            "event_count",
            "year_count",
            "first_year",
            "last_year",
            "total_amount_10k_cny",
            "mean_event_amount_10k_cny",
            "rivers",
        ],
    )
    write_csv(
        output / "event_year_summary.csv",
        annual_rows,
        ["year", "event_count", "active_pair_count", "total_amount_10k_cny"],
    )
    write_csv(
        output / "event_province_summary.csv",
        province_rows,
        [
            "province",
            "verified_inflow_10k_cny",
            "verified_outflow_10k_cny",
            "verified_net_inflow_10k_cny",
            "inflow_event_count",
            "outflow_event_count",
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
