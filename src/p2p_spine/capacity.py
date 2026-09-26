"""Batch-plant capacity from cycle times, and actual run-rate from production logs."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Mapping, Optional, Tuple


def vessel_cycle_hours(steps: Iterable[Tuple[str, float]]) -> Dict[str, float]:
    """Sum step durations per vessel. `steps` = (vessel, hours)."""
    out: Dict[str, float] = defaultdict(float)
    for vessel, hours in steps:
        out[vessel] += hours
    return dict(out)


def bottleneck_capacity(
    vessel_hours: Mapping[str, float],
    batch_t: float,
    operating_days: float,
    hours_per_day: float = 24.0,
) -> Dict[str, float | str]:
    """Pipelined batch train: throughput is set by the slowest vessel."""
    bottleneck = max(vessel_hours, key=vessel_hours.get)
    cycle = vessel_hours[bottleneck]
    batches_per_day = hours_per_day / cycle
    return {
        "bottleneck_vessel": bottleneck,
        "cycle_hours": cycle,
        "batches_per_day": batches_per_day,
        "batch_t": batch_t,
        "operating_days": operating_days,
        "hours_per_day": hours_per_day,
        "tonnes_per_year": batches_per_day * batch_t * operating_days,
    }


_RANGE = re.compile(r"(\d+)\s*-\s*(\d+)")
_NUM = re.compile(r"\d+")


def count_batches(batch_ref: str) -> int:
    """Parse batch references like '10-25', '30, 31', '42', '50-60 (trial excluded)'.

    Only the leading batch list is counted; parenthesised notes are ignored."""
    if not batch_ref or batch_ref.strip().upper() in {"NA", "N/A"}:
        return 0
    head = batch_ref.split("(")[0]
    if "excluded" in batch_ref.lower() and not _RANGE.search(head) and "," not in head:
        return 0
    total = 0
    for part in head.split(","):
        part = part.strip()
        m = _RANGE.search(part)
        if m:
            total += int(m.group(2)) - int(m.group(1)) + 1
        elif _NUM.search(part):
            total += 1
    return total


def run_rate(monthly: List[Dict[str, object]], trailing_months: int = 12) -> Dict[str, float]:
    """monthly rows: {'month': 'YYYY-MM', 'feed_kg': float, 'batches': int}; sorted ascending."""
    rows = sorted(monthly, key=lambda r: str(r["month"]))
    total_kg = sum(float(r["feed_kg"]) for r in rows)
    trailing = rows[-trailing_months:]
    trailing_kg = sum(float(r["feed_kg"]) for r in trailing)
    peak = max(rows, key=lambda r: float(r["feed_kg"])) if rows else None
    batches = sum(int(r["batches"]) for r in rows)
    active = [r for r in rows if float(r["feed_kg"]) > 0]
    return {
        "months_logged": len(rows),
        "total_feed_t": total_kg / 1000,
        "trailing_months": len(trailing),
        "trailing_feed_t": trailing_kg / 1000,
        "trailing_annualised_t": trailing_kg / 1000 * 12 / max(len(trailing), 1),
        "peak_month": str(peak["month"]) if peak else "",
        "peak_month_t": float(peak["feed_kg"]) / 1000 if peak else 0.0,
        "peak_month_annualised_t": float(peak["feed_kg"]) / 1000 * 12 if peak else 0.0,
        "batches_logged": batches,
        "avg_batch_t": total_kg / 1000 / batches if batches else 0.0,
        "active_months": len(active),
        "idle_months": len(rows) - len(active),
    }


def plan_vs_actual(
    plan: List[Dict[str, object]], monthly: List[Dict[str, object]], as_of_month: Optional[str] = None
) -> List[Dict[str, object]]:
    """Compare plan periods {'period','start_month','end_month','plan_t'} with actual monthly feed.

    Periods still in progress are pro-rated on elapsed logged months."""
    out: List[Dict[str, object]] = []
    for p in plan:
        start, end = str(p["start_month"]), str(p["end_month"])
        in_period = [r for r in monthly if start <= str(r["month"]) <= end and (as_of_month is None or str(r["month"]) <= as_of_month)]
        months_total = _months_between(start, end)
        months_elapsed = len(in_period)
        actual_t = sum(float(r["feed_kg"]) for r in in_period) / 1000
        plan_t = float(p["plan_t"])
        plan_prorata = plan_t * months_elapsed / months_total if months_total else 0.0
        out.append(
            {
                "period": p["period"],
                "plan_t": plan_t,
                "months_in_period": months_total,
                "months_with_actuals": months_elapsed,
                "plan_prorata_t": plan_prorata,
                "actual_t": actual_t,
                "attainment_pct": round(actual_t / plan_prorata * 100, 2) if plan_prorata else None,
            }
        )
    return out


def _months_between(start: str, end: str) -> int:
    y1, m1 = map(int, start.split("-"))
    y2, m2 = map(int, end.split("-"))
    return (y2 - y1) * 12 + (m2 - m1) + 1
