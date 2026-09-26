"""Payable-based pricing: feedstock purchases, provisional settlements and
benchmark-payable offtakes with floors and profit share."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional


def payable_value(
    grades: Mapping[str, float], prices: Mapping[str, float], payables: Mapping[str, float]
) -> float:
    """USD per tonne of material: sum(grade * metal price * payable) over payable metals."""
    return sum(grades.get(el, 0.0) * prices[el] * payables[el] for el in payables)


def contained_value(grades: Mapping[str, float], prices: Mapping[str, float], metals: List[str]) -> float:
    return sum(grades.get(el, 0.0) * prices[el] for el in metals)


# ---------------------------------------------------------------------------
# Provisional settlement (seller invoice on wet delivery with assay + payable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettlementInput:
    settlement_id: str
    wet_kg: float
    moisture: float
    grades: Dict[str, float]
    prices: Dict[str, float]
    payables: Dict[str, float]
    provisional_pct: float
    direct_costs: float = 0.0
    prior_payments: float = 0.0


def provisional_settlement(inp: SettlementInput) -> Dict[str, float]:
    dry_t = inp.wet_kg * (1.0 - inp.moisture) / 1000.0
    out: Dict[str, float] = {"dry_t": dry_t, "wet_t": inp.wet_kg / 1000.0}
    gross = 0.0
    for el, payable in inp.payables.items():
        contained = inp.grades[el] * dry_t
        value = contained * inp.prices[el] * payable
        out[f"{el}_contained_t"] = contained
        out[f"{el}_payable_value"] = value
        gross += value
    provisional = gross * inp.provisional_pct
    out.update(
        {
            "payable_value_100pct": gross,
            "provisional_value": provisional,
            "direct_costs": inp.direct_costs,
            "prior_payments": inp.prior_payments,
            "expected_amount_due": provisional - inp.direct_costs - inp.prior_payments,
            "final_balance_outstanding": gross - provisional,
            "provisional_usd_per_dry_t": provisional / dry_t if dry_t else 0.0,
            "payable_value_usd_per_dry_t": gross / dry_t if dry_t else 0.0,
        }
    )
    return out


@dataclass(frozen=True)
class InvoiceFacts:
    invoice_id: str
    invoice_date: str
    subtotal: float
    direct_costs_deducted: float
    prior_payments_deducted: float
    amount_due: float
    stated_wet_t: Optional[float] = None
    description_wet_t: Optional[float] = None
    price_dates: Dict[str, str] = field(default_factory=dict)


def reconcile_settlement(
    calc: Dict[str, float],
    invoice: InvoiceFacts,
    workbook_balance: Optional[float] = None,
    tolerance: float = 1.0,
    stale_price_days: int = 90,
) -> List[Dict[str, object]]:
    """Reason-coded findings comparing a recomputed settlement to the issued invoice."""
    from datetime import date

    findings: List[Dict[str, object]] = []

    def add(code: str, severity: str, message: str, amount: float = 0.0) -> None:
        findings.append(
            {"invoice_id": invoice.invoice_id, "code": code, "severity": severity, "amount_usd": round(amount, 2), "message": message}
        )

    diff_sub = invoice.subtotal - calc["provisional_value"]
    if abs(diff_sub) > tolerance:
        add("SUBTOTAL_MISMATCH", "BLOCKING", f"Invoice subtotal differs from recomputed provisional value by {diff_sub:,.2f}.", diff_sub)
    elif abs(diff_sub) > 0.005:
        add("ROUNDING", "INFO", f"Subtotal rounding difference {diff_sub:,.2f}.", diff_sub)

    missing_dc = calc["direct_costs"] - invoice.direct_costs_deducted
    if abs(missing_dc) > tolerance:
        add(
            "DIRECT_COSTS_NOT_DEDUCTED",
            "WARNING",
            f"Contract direct costs of {calc['direct_costs']:,.2f} vs {invoice.direct_costs_deducted:,.2f} deducted on invoice; "
            "confirm whether deferred to final settlement.",
            missing_dc,
        )
    elif abs(missing_dc) > 0.005:
        add("DIRECT_COST_ROUNDING", "INFO", f"Direct costs rounded on invoice by {missing_dc:,.2f}.", missing_dc)

    missing_prior = calc["prior_payments"] - invoice.prior_payments_deducted
    if abs(missing_prior) > tolerance:
        add("PRIOR_PAYMENT_MISMATCH", "WARNING", f"Prior payments differ by {missing_prior:,.2f}.", missing_prior)

    if workbook_balance is not None and abs(workbook_balance - calc["expected_amount_due"]) > tolerance:
        add(
            "WORKBOOK_FORMULA_INCONSISTENT",
            "WARNING",
            f"Source workbook balance {workbook_balance:,.2f} differs from contract-basis amount due "
            f"{calc['expected_amount_due']:,.2f}; workbook omits a deduction line.",
            workbook_balance - calc["expected_amount_due"],
        )

    if invoice.stated_wet_t is not None and abs(invoice.stated_wet_t - calc["wet_t"]) > 0.001:
        add("QUANTITY_MISMATCH", "WARNING", f"Invoice quantity {invoice.stated_wet_t} t vs weighbridge {calc['wet_t']:.3f} t.")
    if invoice.description_wet_t is not None and abs(invoice.description_wet_t - calc["wet_t"]) > 0.05:
        add(
            "DESCRIPTION_MISMATCH",
            "WARNING",
            f"Invoice description states {invoice.description_wet_t} t but quantity basis is {calc['wet_t']:.3f} t.",
        )

    inv_date = date.fromisoformat(invoice.invoice_date)
    for el, d in invoice.price_dates.items():
        age = (inv_date - date.fromisoformat(d)).days
        if age > stale_price_days:
            add("STALE_PRICE", "WARNING", f"{el} price dated {d} is {age} days older than the invoice.")

    diff_due = invoice.amount_due - calc["expected_amount_due"]
    if abs(diff_due) > tolerance:
        add("AMOUNT_DUE_DIFFERENCE", "WARNING", f"Invoice amount due differs from contract-basis amount by {diff_due:,.2f}.", diff_due)
    return findings


# ---------------------------------------------------------------------------
# Benchmark-payable offtake with floor, base margin and profit share
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OfftakeTerms:
    """Offtaker buys an intermediate (e.g. mixed hydroxide) on metal payables.

    Seller receives max(floor, end-buyer payable - offtaker margin) per metal,
    less direct costs (fixed + financing of the provisional payment)."""

    benchmark_payable_mid: Dict[str, float]
    benchmark_payable_high: Dict[str, float]
    floor_payable_adjustment: float
    end_buyer_payable: Dict[str, float]
    base_margin: float
    profit_share: float
    direct_cost_fixed: float
    financing_rate: float
    financing_days: float
    provisional_pct: float
    provisional_direct_cost: float = 0.0


def _weighted(values: Mapping[str, float], weights: Mapping[str, float]) -> float:
    total = sum(weights[k] for k in values)
    return sum(values[k] * weights[k] for k in values) / total if total else 0.0


def offtake_realized_price(
    terms: OfftakeTerms, product_grades: Mapping[str, float], metal_prices: Mapping[str, float]
) -> Dict[str, float]:
    """USD per dry tonne of product and the effective payable per metal."""
    metals = list(terms.end_buyer_payable)
    value = {m: metal_prices[m] * product_grades[m] for m in metals}
    floor_payable = {m: terms.benchmark_payable_mid[m] - terms.floor_payable_adjustment for m in metals}
    prov_price = sum(value[m] * floor_payable[m] for m in metals) - terms.provisional_direct_cost
    prov_payment = prov_price * terms.provisional_pct
    target = {m: (terms.benchmark_payable_mid[m] + terms.benchmark_payable_high[m]) / 2 for m in metals}
    target_w = _weighted(target, product_grades)
    end_w = _weighted(terms.end_buyer_payable, product_grades)
    margin = terms.base_margin + max(0.0, end_w - target_w) * terms.profit_share
    direct = terms.financing_rate * (terms.financing_days / 360.0) * prov_payment + terms.direct_cost_fixed
    out: Dict[str, float] = {"offtaker_margin": margin, "direct_costs_per_dmt": direct, "provisional_payment_per_dmt": prov_payment}
    realized = 0.0
    for m in metals:
        floor_price = metal_prices[m] * floor_payable[m]
        received = (terms.end_buyer_payable[m] - margin) * metal_prices[m]
        eff = max(floor_price, received)
        out[f"{m}_realized_usd_per_t_metal"] = eff
        out[f"{m}_effective_payable"] = eff / metal_prices[m]
        out[f"{m}_floor_binding"] = float(floor_price >= received)
        realized += eff * product_grades[m]
    out["realized_usd_per_dmt"] = realized - direct
    return out


def conversion_spread(
    feed_payable: Mapping[str, float], product_payable: Mapping[str, float], recovery: Mapping[str, float]
) -> Dict[str, float]:
    """Payable points gained (+) or lost (-) converting feed metal into product metal.

    spread = product payable * recovery - feed payable, per metal."""
    return {m: product_payable[m] * recovery[m] - feed_payable[m] for m in feed_payable}
