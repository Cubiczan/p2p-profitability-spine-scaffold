from __future__ import annotations

import math

import pytest

from p2p_spine.capacity import bottleneck_capacity, count_batches, plan_vs_actual, run_rate, vessel_cycle_hours
from p2p_spine.evidence import AssumptionEvidence, grade
from p2p_spine.grading import SpecLimit, check_lot, grade_verdicts
from p2p_spine.payables import (
    InvoiceFacts,
    OfftakeTerms,
    SettlementInput,
    conversion_spread,
    offtake_realized_price,
    provisional_settlement,
    reconcile_settlement,
)
from p2p_spine.pricing import PriceRule, apply_rule, index_for_target_price
from p2p_spine.questions import answer_questions
from p2p_spine.unit_economics import FeedScenario, breakeven_feed_payable, contained_metal_value, unit_economics
from p2p_spine.units import to_ppm


def test_units() -> None:
    assert to_ppm(0.14, "%") == pytest.approx(1400)
    assert to_ppm(670, "g/t") == 670
    assert to_ppm(3610.94, "ug/kg") == pytest.approx(3.61094)


def test_provisional_settlement_and_reconciliation() -> None:
    inp = SettlementInput("S1", wet_kg=10_000, moisture=0.3, grades={"Ni": 0.4, "Co": 0.1}, prices={"Ni": 16_000, "Co": 30_000},
                          payables={"Ni": 0.95, "Co": 0.95}, provisional_pct=0.85, direct_costs=5_000, prior_payments=10_000)
    calc = provisional_settlement(inp)
    assert calc["dry_t"] == pytest.approx(7.0)
    gross = 7.0 * (0.4 * 16_000 + 0.1 * 30_000) * 0.95
    assert calc["payable_value_100pct"] == pytest.approx(gross)
    assert calc["expected_amount_due"] == pytest.approx(gross * 0.85 - 15_000)
    inv = InvoiceFacts("INV-1", "2026-01-31", subtotal=round(gross * 0.85, 2), direct_costs_deducted=0, prior_payments_deducted=10_000,
                       amount_due=round(gross * 0.85, 2) - 10_000, price_dates={"Co": "2025-01-01"})
    codes = {f["code"] for f in reconcile_settlement(calc, inv)}
    assert {"DIRECT_COSTS_NOT_DEDUCTED", "STALE_PRICE", "AMOUNT_DUE_DIFFERENCE"} <= codes


def test_offtake_floor_and_profit_share() -> None:
    terms = OfftakeTerms({"Ni": 0.78, "Co": 0.78}, {"Ni": 0.81, "Co": 0.81}, 0.08, {"Ni": 0.87, "Co": 0.71}, 0.03, 0.5, 80, 0.11, 60, 0.85, 85)
    res = offtake_realized_price(terms, {"Ni": 0.5, "Co": 0.08}, {"Ni": 15_000, "Co": 35_000})
    assert res["Co_floor_binding"] == 1.0  # 0.71 - margin < 0.70 floor
    assert res["Ni_floor_binding"] == 0.0
    assert res["Co_effective_payable"] == pytest.approx(0.70)
    assert res["realized_usd_per_dmt"] > 0
    spread = conversion_spread({"Ni": 0.75}, {"Ni": res["Ni_effective_payable"]}, {"Ni": 0.9})
    assert spread["Ni"] == pytest.approx(res["Ni_effective_payable"] * 0.9 - 0.75)


def test_grading_highest_grade_and_untested() -> None:
    lims = [
        SpecLimit("P", "LOW", "purity", "min", to_ppm(98.9, "%")),
        SpecLimit("P", "LOW", "Na", "max", 1500),
        SpecLimit("P", "HIGH", "purity", "min", to_ppm(99.5, "%")),
        SpecLimit("P", "HIGH", "Fe", "max", 10),
    ]
    rows = check_lot({"purity": to_ppm(99.0, "%"), "Na": 1400}, lims, "L1")
    v = grade_verdicts(rows, ["LOW", "HIGH"])
    assert v["highest_grade_met"] == "LOW"
    assert v["grades"]["HIGH"]["verdict"] == "FAIL"
    assert "Fe" in v["grades"]["HIGH"]["untested_components"]


def test_price_rules() -> None:
    tg = PriceRule("TG", "X", "TG", "index_minus_discount_x_content", "IDX", discount=3000)
    assert apply_rule(tg, {"IDX": 15_000}, 0.99) == pytest.approx(12_000 * 0.99)
    assert index_for_target_price(tg, 9_750, 0.99) == pytest.approx(9_750 / 0.99 + 3000)
    pct = PriceRule("IG", "X", "IG", "pct_of_index", "IDX2", pct=0.96)
    assert apply_rule(pct, {}) is None


def test_unit_economics_and_breakeven() -> None:
    s = FeedScenario("S", "s", {"Ni": 0.2, "Co": 0.04, "Li": 0.035}, {"MHP": 0.4, "LC": 0.15, "BY": 2.0}, {"MHP": 7_800, "LC": 10_000, "BY": 200},
                     {"Ni": 15_000, "Co": 35_000}, {"Ni": 0.75, "Co": 0.75}, {"chem": 900, "other": 400}, 500, core_products=("MHP", "LC"))
    ue = unit_economics(s)
    rev = 0.4 * 7_800 + 0.15 * 10_000 + 2.0 * 200
    assert ue["revenue_total"] == pytest.approx(rev)
    assert ue["byproduct_revenue"] == pytest.approx(400)
    be = breakeven_feed_payable(s)
    from dataclasses import replace

    assert unit_economics(replace(s, feed_payables={"Ni": be, "Co": be}))["ebitda"] == pytest.approx(0, abs=1e-6)


def test_contained_metal_value_li_as_carbonate() -> None:
    cv = contained_metal_value({"Ni": 0.01, "Li": 0.001}, {"Ni": 16_000}, li_as_carbonate_price=10_000)
    assert cv["Ni"] == pytest.approx(160)
    assert cv["Li"] == pytest.approx(0.001 * 5.323 * 10_000)


def test_capacity_and_run_rate() -> None:
    vh = vessel_cycle_hours([("A", 2), ("A", 2), ("B", 6)])
    res = bottleneck_capacity(vh, batch_t=1.0, operating_days=330)
    assert res["bottleneck_vessel"] == "B"
    assert res["tonnes_per_year"] == pytest.approx(4 * 330)
    assert count_batches("10-26") == 17
    assert count_batches("30, 31") == 2
    assert count_batches("40 (trial, excluded)") == 0
    assert count_batches("50-68 (trial excluded)") == 19
    assert count_batches("NA") == 0
    monthly = [{"month": "2026-01", "feed_kg": 10_000, "batches": 10}, {"month": "2026-02", "feed_kg": 0, "batches": 0}]
    rr = run_rate(monthly)
    assert rr["avg_batch_t"] == pytest.approx(1.0)
    pva = plan_vs_actual([{"period": "P", "start_month": "2026-01", "end_month": "2026-12", "plan_t": 120}], monthly)
    assert pva[0]["plan_prorata_t"] == pytest.approx(20)
    assert pva[0]["attainment_pct"] == pytest.approx(50)


def test_evidence_grades() -> None:
    base = dict(assumption_id="A", product="p", metric="m", model_value=1, evidence_value=1, unit="u", evidence_refs="")
    assert grade(AssumptionEvidence(**base, n_transactions=2, last_transaction_date="2026-06-01", contract_status="executed_in_force"), "2026-09-01")["evidence_grade"] == "A"
    assert grade(AssumptionEvidence(**base, n_transactions=1, last_transaction_date="2024-06-01", contract_status="none"), "2026-09-01")["evidence_grade"] == "C"
    assert grade(AssumptionEvidence(**base, n_transactions=0, last_transaction_date=None, contract_status="none"), "2026-09-01")["evidence_grade"] == "D"


def test_question_register_open_gap_and_nan() -> None:
    qs = [
        {"question_id": "Q1", "answer_template": "x={a__b:.1f}", "metric_keys": "a.b"},
        {"question_id": "Q2", "answer_template": "y={c}", "metric_keys": "c"},
        {"question_id": "Q3", "answer_template": "z={d:.1f}", "metric_keys": "d"},
    ]
    out = answer_questions(qs, {"a.b": 1.25, "d": math.nan})
    assert out[0]["computed_answer"] == "x=1.2" and out[0]["status"] == "ANSWERED"
    assert out[1]["status"] == "OPEN_GAP" and out[1]["missing_metrics"] == "c"
    assert out[2]["computed_answer"] == "z=n/a"
