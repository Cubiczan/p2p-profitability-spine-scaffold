"""Grade product lots against specification sets and report headroom."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional


@dataclass(frozen=True)
class SpecLimit:
    product: str
    grade: str
    component: str
    limit_type: str  # "min" | "max"
    limit_ppm: float  # normalised: ppm for impurities, percent*10_000 for assays
    source: str = ""


def check_lot(
    lot_assay_ppm: Mapping[str, float], limits: Iterable[SpecLimit], lot_id: str = ""
) -> List[Dict[str, object]]:
    """One row per (grade, component): value, limit, pass/fail, headroom %.

    Components absent from the lot assay are reported as UNTESTED, which blocks a
    PASS verdict for that grade (a spec is only met when every limit is evidenced)."""
    rows: List[Dict[str, object]] = []
    for lim in limits:
        value: Optional[float] = lot_assay_ppm.get(lim.component)
        if value is None:
            status, headroom = "UNTESTED", None
        elif lim.limit_type == "min":
            status = "PASS" if value >= lim.limit_ppm else "FAIL"
            headroom = (value - lim.limit_ppm) / lim.limit_ppm if lim.limit_ppm else None
        else:
            status = "PASS" if value <= lim.limit_ppm else "FAIL"
            headroom = (lim.limit_ppm - value) / lim.limit_ppm if lim.limit_ppm else None
        rows.append(
            {
                "lot_id": lot_id,
                "product": lim.product,
                "grade": lim.grade,
                "component": lim.component,
                "limit_type": lim.limit_type,
                "limit_ppm": lim.limit_ppm,
                "value_ppm": value,
                "status": status,
                "headroom_pct": None if headroom is None else round(headroom * 100, 3),
                "source": lim.source,
            }
        )
    return rows


def grade_verdicts(rows: List[Dict[str, object]], grade_order: List[str]) -> Dict[str, object]:
    """Per grade: PASS / FAIL / INCOMPLETE, failing and tightest components, highest grade met."""
    verdicts: Dict[str, Dict[str, object]] = {}
    for grade in grade_order:
        g = [r for r in rows if r["grade"] == grade]
        fails = [r["component"] for r in g if r["status"] == "FAIL"]
        untested = [r["component"] for r in g if r["status"] == "UNTESTED"]
        passing = [r for r in g if r["status"] == "PASS" and r["headroom_pct"] is not None]
        tightest = min(passing, key=lambda r: r["headroom_pct"]) if passing else None
        verdicts[grade] = {
            "verdict": "FAIL" if fails else ("INCOMPLETE" if untested else "PASS"),
            "failing_components": fails,
            "untested_components": untested,
            "tightest_component": tightest["component"] if tightest else None,
            "tightest_headroom_pct": tightest["headroom_pct"] if tightest else None,
        }
    met = [g for g in grade_order if verdicts[g]["verdict"] == "PASS"]
    return {"grades": verdicts, "highest_grade_met": met[-1] if met else None}
