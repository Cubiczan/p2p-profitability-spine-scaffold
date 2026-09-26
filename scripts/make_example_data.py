"""Write the synthetic `data/example` dataset.

All values are illustrative round numbers for a fictional plant ("ExampleCo Plant A").
They are not derived from any real company's contracts, invoices or models.
Run: python scripts/make_example_data.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "example"
SRC = "synthetic example"


def write(name: str, rows: list[dict]) -> None:
    with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write("price_deck.csv", [
        {"deck_id": d, "commodity": c, "price_usd_per_t": p, "as_of": a, "basis": "illustrative", "source": SRC}
        for d, a, prices in [
            ("MODEL", "2026-01-01", {"Ni": 16000, "Co": 33000, "Li2CO3": 11000, "Graphite": 600, "Na2SO4": 150}),
            ("CURRENT", "2026-09-01", {"Ni": 15500, "Co": 31000, "Li2CO3": 12000, "Graphite": 600, "Na2SO4": 150}),
        ]
        for c, p in prices.items()
    ])
    write("settlements.csv", [{
        "settlement_id": "SALE-1", "invoice_id": "INV-100", "invoice_date": "2026-06-30", "counterparty": "Offtaker A", "product": "Mixed hydroxide",
        "wet_kg": 20000, "moisture": 0.35, "grade_Ni": 0.40, "grade_Co": 0.08, "price_Ni": 15500, "price_Co": 31000,
        "price_date_Ni": "2026-06-25", "price_date_Co": "2025-12-01", "payable_Ni": 0.90, "payable_Co": 0.85, "provisional_pct": 0.85,
        "direct_costs": 4000, "prior_payments": 0, "inv_subtotal": 84952.40, "inv_direct_costs": 0, "inv_prior_payments": 0,
        "inv_amount_due": 84952.40, "inv_description_wet_t": 20.0, "workbook_balance": "", "doc_ref": SRC,
    }])
    specs = [("STD", "purity", "min", 99.0, "%"), ("STD", "Na", "max", 1000, "ppm"), ("STD", "SO4", "max", 3000, "ppm"),
             ("PREMIUM", "purity", "min", 99.5, "%"), ("PREMIUM", "Na", "max", 250, "ppm"), ("PREMIUM", "SO4", "max", 800, "ppm"),
             ("PREMIUM", "Fe", "max", 10, "ppm")]
    write("specs.csv", [{"product": "Li2CO3", "grade": g, "component": c, "limit_type": t, "value": v, "unit": u, "source": SRC} for g, c, t, v, u in specs])
    write("lot_assays.csv", [
        {"lot_id": lot, "product": "Li2CO3", "lab": "Buyer lab", "component": c, "value": v, "unit": u, "source": SRC}
        for lot, vals in [("LOT1", [("purity", 99.2, "%"), ("Na", 800, "ppm"), ("SO4", 2500, "ppm"), ("Fe", 6, "ppm")]),
                          ("LOT2", [("purity", 99.6, "%"), ("Na", 200, "ppm"), ("SO4", 600, "ppm"), ("Fe", 5, "ppm")])]
        for c, v, u in vals
    ])
    write("price_rules.csv", [
        {"rule_id": "STD_FLOOR", "product": "Li2CO3", "grade": "STD", "kind": "index_minus_discount_x_content", "index": "INDEX_PREMIUM", "pct": "", "discount": 2500, "fixed_price": "", "source": SRC},
        {"rule_id": "PREMIUM_FLOOR", "product": "Li2CO3", "grade": "PREMIUM", "kind": "pct_of_index", "index": "INDEX_PREMIUM", "pct": 0.95, "discount": "", "fixed_price": "", "source": SRC},
        {"rule_id": "MODEL", "product": "Li2CO3", "grade": "MODEL", "kind": "fixed", "index": "", "pct": "", "discount": "", "fixed_price": 11000, "source": SRC},
    ])
    write("stream_assays.csv", [{"stream_id": "graphite_byproduct", "element": e, "grade_frac": g, "source": SRC} for e, g in [("Ni", 0.01), ("Co", 0.002), ("Li", 0.002)]])
    terms = [("benchmark_payable_mid", "Ni", 0.78), ("benchmark_payable_mid", "Co", 0.78), ("benchmark_payable_high", "Ni", 0.82), ("benchmark_payable_high", "Co", 0.82),
             ("end_buyer_payable", "Ni", 0.88), ("end_buyer_payable", "Co", 0.75), ("floor_payable_adjustment", "", 0.08), ("base_margin", "", 0.03),
             ("profit_share", "", 0.5), ("direct_cost_fixed", "", 75), ("financing_rate", "", 0.10), ("financing_days", "", 60),
             ("provisional_pct", "", 0.85), ("provisional_direct_cost", "", 75)]
    write("offtake_terms.csv", [{"terms_id": "OFFTAKE_A", "key": k, "metal": m, "value": v, "source": SRC} for k, m, v in terms])
    fs = []
    for sid, label, grades, yields, pay, chem, opex in [
        ("BM", "Black mass (graphite-bearing)", {"Ni": 0.22, "Co": 0.04, "Li": 0.035, "Graphite": 0.25}, {"NCM_hydroxide": 0.40, "Li2CO3": 0.15, "Graphite": 0.25, "Na2SO4": 2.3}, 0.75, 900, 500),
        ("CP", "Cathode powder (no graphite)", {"Ni": 0.45, "Co": 0.03, "Li": 0.06, "Graphite": 0.0}, {"NCM_hydroxide": 0.75, "Li2CO3": 0.25, "Graphite": 0.0, "Na2SO4": 3.6}, 0.80, 1400, 500),
    ]:
        rows = [("meta", "price_deck", "", "MODEL"), ("meta", "opex_per_t", opex, "")]
        rows += [("feed_grade", k, v, "") for k, v in grades.items()]
        rows += [("feed_payable", m, pay, "") for m in ("Ni", "Co")]
        rows += [("yield", k, v, "") for k, v in yields.items()]
        rows += [("product_price", "Li2CO3", 11000, ""), ("product_price", "Graphite", 600, ""), ("product_price", "Na2SO4", 150, "")]
        rows += [("product_offtake", "NCM_hydroxide", "", "OFFTAKE_A"), ("product_grade", "Ni", 0.50, ""), ("product_grade", "Co", 0.07, "")]
        rows += [("conversion_cost", "chemicals", chem, ""), ("conversion_cost", "utility", 300, ""), ("conversion_cost", "shipping", 150, ""), ("conversion_cost", "waste_disposal", 50, "")]
        fs += [{"scenario_id": sid, "label": label, "section": s, "key": k, "value": v, "ref": r, "source": SRC} for s, k, v, r in rows]
    write("feed_scenarios.csv", fs)
    write("feed_purchases.csv", [
        {"purchase_id": "SUPPLIER_X", "supplier": "Supplier X", "material": "Black mass", "date": "2026-03-01", "payable_Ni": 0.78, "payable_Co": 0.78, "doc_ref": SRC},
        {"purchase_id": "SUPPLIER_Y", "supplier": "Supplier Y", "material": "Black mass", "date": "2026-05-01", "payable_Ni": 0.95, "payable_Co": 0.95, "doc_ref": SRC},
    ])
    cyc = []
    for case, steps in [("current", {"R1": [1, 2, 1.5], "R2": [0.5, 2.5, 2], "R3": [0.5, 1, 4]}), ("target", {"R1": [0.5, 2, 1], "R2": [0.5, 1.5, 1], "R3": [0.5, 1, 2.5]})]:
        for vessel, hrs in steps.items():
            cyc += [{"case": case, "vessel": vessel, "step": f"step{i + 1}", "hours": h, "source": SRC} for i, h in enumerate(hrs)]
    write("cycle_times.csv", cyc)
    months = [("2026-01", 12000, "1-12"), ("2026-02", 15000, "13-27"), ("2026-03", 0, "NA"), ("2026-04", 22000, "28-47"), ("2026-05", 18000, "48-63"), ("2026-06", 20000, "64-81")]
    write("throughput_monthly.csv", [{"month": m, "feed_kg": kg, "batch_ref": b, "feed_source": "Supplier X", "source": SRC} for m, kg, b in months])
    write("plan.csv", [{"period": "FY26", "start_month": "2026-01", "end_month": "2026-12", "plan_t": 1200, "source": SRC}])
    write("facts.csv", [
        {"key": "capacity_claim_tpa", "value": 1500, "unit": "t/yr", "source": SRC},
        {"key": "byproduct_sold_to_date_t", "value": 20, "unit": "t", "source": SRC},
    ])
    write("assumption_evidence.csv", [
        {"assumption_id": "BYPRODUCT_PRICE", "product": "Na2SO4", "metric": "price", "model_value": 150, "evidence_value": 140, "unit": "USD/t", "n_transactions": 1,
         "last_transaction_date": "2025-06-01", "contract_status": "none", "evidence_refs": SRC, "model_ref": SRC},
        {"assumption_id": "MHP_PAYABLE", "product": "Mixed hydroxide", "metric": "Ni payable", "model_value": 0.82, "evidence_value": 0.90, "unit": "fraction", "n_transactions": 2,
         "last_transaction_date": "2026-06-30", "contract_status": "executed_in_force", "evidence_refs": SRC, "model_ref": SRC},
    ])
    write("findings_manual.csv", [{"code": "EXAMPLE", "severity": "INFO", "subject": "Example", "message": "Analyst findings from document review go here.", "amount_usd": "", "refs": SRC}])
    write("questions.csv", [
        {"question_id": "Q-CAP", "section": "Operations", "topic": "Capacity", "question": "Is the claimed capacity achievable?", "management_response": "Yes.",
         "answer_template": "Target cycles 24/7 give {capacity__target__b100__h24__tpa:,.0f} t/yr at 1 t batches; trailing run-rate is {runrate__trailing_annualised_t:,.0f} t/yr vs a {facts__capacity_claim_tpa:,.0f} t/yr claim.",
         "metric_keys": "capacity.target.b100.h24.tpa;runrate.trailing_annualised_t;facts.capacity_claim_tpa", "status_override": "ANSWERED", "confidence": "MEDIUM",
         "evidence_assessment": "", "evidence_refs": SRC, "next_action": ""},
        {"question_id": "Q-FEED", "section": "Feedstock", "topic": "Feed pricing", "question": "Are feed purchase payables below break-even?", "management_response": "",
         "answer_template": "Break-even feed payable: {ue__BM__breakeven_feed_payable:.1%} (black mass) and {ue__CP__breakeven_feed_payable:.1%} (cathode powder). Supplier Y at 95% gives EBITDA ${ue_pay__BM__SUPPLIER_Y__ebitda:,.0f}/t.",
         "metric_keys": "ue.BM.breakeven_feed_payable;ue.CP.breakeven_feed_payable;ue_pay.BM.SUPPLIER_Y.ebitda", "status_override": "ANSWERED", "confidence": "HIGH",
         "evidence_assessment": "", "evidence_refs": SRC, "next_action": ""},
        {"question_id": "Q-GRADE", "section": "Commercial", "topic": "Product grade", "question": "What grade does product meet?", "management_response": "",
         "answer_template": "LOT1 meets {grading__LOT1__highest_grade_met}; LOT2 meets {grading__LOT2__highest_grade_met}.",
         "metric_keys": "grading.LOT1.highest_grade_met;grading.LOT2.highest_grade_met", "status_override": "ANSWERED", "confidence": "HIGH",
         "evidence_assessment": "", "evidence_refs": SRC, "next_action": ""},
        {"question_id": "Q-GAP", "section": "Commercial", "topic": "By-product", "question": "What is the by-product CoA?", "management_response": "",
         "answer_template": "{facts__byproduct_coa}", "metric_keys": "facts.byproduct_coa", "status_override": "", "confidence": "",
         "evidence_assessment": "", "evidence_refs": "", "next_action": "Load the CoA."},
    ])
    cfg = {
        "company": "ExampleCo Plant A", "as_of": "2026-09-01", "owner": "", "last_actual_month": "2026-06",
        "core_products": ["NCM_hydroxide", "Li2CO3"], "grade_order": {"Li2CO3": ["STD", "PREMIUM"]}, "recovery": {"Ni": 0.9, "Co": 0.9},
        "capacity": {"operating_days": 330, "trailing_months": 12, "batch_t": {"b100": 1.0, "b150": 1.5}, "hours_per_day": {"h24": 24, "h8": 8}},
        "index_scenarios": [{"product": "Li2CO3", "index": "INDEX_PREMIUM", "values": [10000, 12000, 14000, 16000], "other_indices": {}, "content": 0.992,
                             "reference_value": 13000, "benchmarks": {"model_11000": 11000}}],
        "stream_valuation_decks": ["CURRENT"],
        "derived_metrics": {"runrate_vs_claim_pct": {"op": "div", "a": "runrate.trailing_annualised_t", "b": "facts.capacity_claim_tpa", "scale": 100}},
        "assumptions": ["Synthetic data for demonstration only."], "unknowns": ["Everything - this is synthetic."],
    }
    (OUT / "config.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"wrote example dataset to {OUT}")


if __name__ == "__main__":
    main()
