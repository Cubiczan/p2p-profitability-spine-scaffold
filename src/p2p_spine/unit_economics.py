"""Per-tonne-of-feed unit economics, by-product dependency and break-evens."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from .payables import payable_value
from .units import LI_TO_LI2CO3


@dataclass(frozen=True)
class FeedScenario:
    scenario_id: str
    label: str
    feed_grades: Dict[str, float]  # element -> mass fraction of feed
    yields: Dict[str, float]  # product -> t product per t feed
    product_prices: Dict[str, float]  # product -> USD/t product (net of payables)
    feed_metal_prices: Dict[str, float]  # payable metal -> USD/t metal
    feed_payables: Dict[str, float]  # payable metal -> fraction
    conversion_costs: Dict[str, float]  # cost line -> USD/t feed
    opex_per_t: float
    core_products: Sequence[str] = field(default_factory=tuple)  # products with executed offtake


def feed_cost(s: FeedScenario) -> float:
    return payable_value(s.feed_grades, s.feed_metal_prices, s.feed_payables)


def unit_economics(s: FeedScenario) -> Dict[str, float]:
    revenue = {p: s.yields.get(p, 0.0) * price for p, price in s.product_prices.items()}
    total_rev = sum(revenue.values())
    fc = feed_cost(s)
    conv = sum(s.conversion_costs.values())
    gp = total_rev - fc - conv
    ebitda = gp - s.opex_per_t
    byproduct_rev = sum(v for p, v in revenue.items() if p not in s.core_products)
    out: Dict[str, float] = {f"revenue_{p}": v for p, v in revenue.items()}
    out.update({f"cost_{k}": v for k, v in s.conversion_costs.items()})
    out.update(
        {
            "revenue_total": total_rev,
            "cost_feedstock": fc,
            "cost_conversion_total": conv,
            "gross_profit": gp,
            "gross_margin_pct": gp / total_rev * 100 if total_rev else 0.0,
            "opex": s.opex_per_t,
            "ebitda": ebitda,
            "ebitda_margin_pct": ebitda / total_rev * 100 if total_rev else 0.0,
            "byproduct_revenue": byproduct_rev,
            "byproduct_share_of_ebitda_pct": byproduct_rev / ebitda * 100 if ebitda > 0 else float("nan"),
            "ebitda_ex_byproducts": ebitda - byproduct_rev,
            "breakeven_feed_payable": breakeven_feed_payable(s),
        }
    )
    return out


def breakeven_feed_payable(s: FeedScenario) -> Optional[float]:
    """Uniform feed payable (applied to every payable metal) at which EBITDA = 0."""
    unit = payable_value(s.feed_grades, s.feed_metal_prices, {m: 1.0 for m in s.feed_payables})
    if not unit:
        return None
    rev = sum(s.yields.get(p, 0.0) * price for p, price in s.product_prices.items())
    return (rev - sum(s.conversion_costs.values()) - s.opex_per_t) / unit


def implied_recovery(
    feed_grade: float, product_yield: float, product_grade: float
) -> Optional[float]:
    """Recovery implied by a yield: (yield x product grade) / feed grade."""
    return product_yield * product_grade / feed_grade if feed_grade else None


def implied_li_recovery(feed_li: float, li2co3_yield: float, li2co3_purity: float = 1.0) -> Optional[float]:
    return li2co3_yield * li2co3_purity / (feed_li * LI_TO_LI2CO3) if feed_li else None


def sensitivity(
    s: FeedScenario,
    shocks: Mapping[str, Iterable[float]],
) -> List[Dict[str, object]]:
    """One-at-a-time sensitivity on EBITDA/t.

    shock keys: 'price:<product>' (absolute USD/t), 'payable' (uniform feed payable),
    'yield:<product>' (t/t), 'opex' (USD/t)."""
    base = unit_economics(s)["ebitda"]
    rows: List[Dict[str, object]] = []
    for key, values in shocks.items():
        for v in values:
            if key.startswith("price:"):
                p = key.split(":", 1)[1]
                t = replace(s, product_prices={**s.product_prices, p: v})
            elif key.startswith("yield:"):
                p = key.split(":", 1)[1]
                t = replace(s, yields={**s.yields, p: v})
            elif key == "payable":
                t = replace(s, feed_payables={m: v for m in s.feed_payables})
            elif key == "opex":
                t = replace(s, opex_per_t=v)
            else:
                raise ValueError(f"Unknown shock {key}")
            e = unit_economics(t)["ebitda"]
            rows.append(
                {"scenario_id": s.scenario_id, "driver": key, "value": v, "ebitda": e, "delta_vs_base": e - base}
            )
    return rows


def contained_metal_value(
    grades: Mapping[str, float], prices: Mapping[str, float], li_as_carbonate_price: Optional[float] = None
) -> Dict[str, float]:
    """USD of contained metal per t of a stream (e.g. metal lost to a by-product).

    Li is valued as Li2CO3 equivalent when `li_as_carbonate_price` is supplied."""
    out: Dict[str, float] = {}
    for el, g in grades.items():
        if el == "Li" and li_as_carbonate_price is not None:
            out[el] = g * LI_TO_LI2CO3 * li_as_carbonate_price
        elif el in prices:
            out[el] = g * prices[el]
    out["total"] = sum(out.values())
    return out
