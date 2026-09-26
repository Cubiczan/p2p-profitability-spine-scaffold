"""Unit conversions used across the spine."""

from __future__ import annotations

LB_PER_TONNE = 2204.62
# Mass of Li2CO3 per unit mass of contained Li (73.89 / (2 * 6.941)).
LI_TO_LI2CO3 = 5.323
PPM_PER_PCT = 10_000.0
PPM_PER_UG_PER_KG = 0.001


def pct_to_ppm(pct: float) -> float:
    return pct * PPM_PER_PCT


def ug_per_kg_to_ppm(value: float) -> float:
    return value * PPM_PER_UG_PER_KG


def usd_per_lb_to_usd_per_t(price: float) -> float:
    return price * LB_PER_TONNE


def to_ppm(value: float, unit: str) -> float:
    """Normalise an impurity assay to ppm (mg/kg)."""
    unit = unit.strip().lower()
    if unit in {"ppm", "g/t", "mg/kg"}:
        return value
    if unit in {"%", "pct", "wt%", "wt %"}:
        return pct_to_ppm(value)
    if unit in {"ug/kg", "µg/kg", "ppb"}:
        return ug_per_kg_to_ppm(value)
    raise ValueError(f"Unsupported assay unit: {unit}")
