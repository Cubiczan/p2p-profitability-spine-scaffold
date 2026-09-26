"""Generic product price rules: fixed, percent-of-index, index-minus-discount x content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional


@dataclass(frozen=True)
class PriceRule:
    rule_id: str
    product: str
    grade: str
    kind: str  # "fixed" | "pct_of_index" | "index_minus_discount_x_content"
    index: str = ""
    pct: float = 1.0
    discount: float = 0.0
    fixed_price: float = 0.0


def apply_rule(rule: PriceRule, indices: Mapping[str, float], content: float = 1.0) -> Optional[float]:
    """USD/t of product; None when the required index is not supplied."""
    if rule.kind == "fixed":
        return rule.fixed_price
    idx = indices.get(rule.index)
    if idx is None:
        return None
    if rule.kind == "pct_of_index":
        return rule.pct * idx
    if rule.kind == "index_minus_discount_x_content":
        return (idx - rule.discount) * content
    raise ValueError(f"Unknown price rule kind: {rule.kind}")


def index_for_target_price(rule: PriceRule, target: float, content: float = 1.0) -> Optional[float]:
    """Index level at which the rule yields `target` USD/t (break-even index)."""
    if rule.kind == "pct_of_index" and rule.pct:
        return target / rule.pct
    if rule.kind == "index_minus_discount_x_content" and content:
        return target / content + rule.discount
    return None


def price_grid(
    rules: Iterable[PriceRule],
    index_name: str,
    index_values: Iterable[float],
    other_indices: Optional[Mapping[str, float]] = None,
    content: float = 1.0,
) -> List[Dict[str, object]]:
    """Scenario grid of each rule's price as one index moves."""
    rules = list(rules)
    rows: List[Dict[str, object]] = []
    for v in index_values:
        indices: Dict[str, float] = dict(other_indices or {})
        indices[index_name] = v
        for r in rules:
            rows.append(
                {
                    "scenario_index": index_name,
                    "scenario_index_value": v,
                    "rule_id": r.rule_id,
                    "product": r.product,
                    "grade": r.grade,
                    "price_usd_per_t": apply_rule(r, indices, content),
                }
            )
    return rows
