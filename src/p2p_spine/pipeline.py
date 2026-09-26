"""Bronze (data-contract CSVs) -> Silver (typed records) -> Gold (analytical tables).

`build(data_dir)` returns {"tables": {name: rows}, "metrics": {key: value},
"evidence_pack": {...}}. Nothing here is company-specific: all specifics live in
the data directory (see docs/data-contract.md)."""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from . import capacity as cap
from .evidence import AssumptionEvidence, evidence_pack, finding, grade, hash_inputs
from .grading import SpecLimit, check_lot, grade_verdicts
from .io import fnum, read_csv, read_json
from .payables import (
    InvoiceFacts,
    OfftakeTerms,
    SettlementInput,
    conversion_spread,
    offtake_realized_price,
    provisional_settlement,
    reconcile_settlement,
)
from .pricing import PriceRule, apply_rule, index_for_target_price, price_grid
from .questions import answer_questions
from .unit_economics import FeedScenario, contained_metal_value, unit_economics
from .units import to_ppm

Rows = List[Dict[str, object]]


def _price_decks(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, float]]:
    decks: Dict[str, Dict[str, float]] = defaultdict(dict)
    for r in rows:
        decks[r["deck_id"]][r["commodity"]] = float(r["price_usd_per_t"])
    return decks


def _offtake_terms(rows: List[Dict[str, str]]) -> Dict[str, OfftakeTerms]:
    raw: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        metal = r.get("metal") or "_"
        raw[r["terms_id"]][r["key"]][metal] = float(r["value"])
    out: Dict[str, OfftakeTerms] = {}
    for tid, kv in raw.items():
        scalar = {k: v["_"] for k, v in kv.items() if "_" in v}
        out[tid] = OfftakeTerms(
            benchmark_payable_mid=dict(kv["benchmark_payable_mid"]),
            benchmark_payable_high=dict(kv["benchmark_payable_high"]),
            floor_payable_adjustment=scalar["floor_payable_adjustment"],
            end_buyer_payable=dict(kv["end_buyer_payable"]),
            base_margin=scalar["base_margin"],
            profit_share=scalar["profit_share"],
            direct_cost_fixed=scalar["direct_cost_fixed"],
            financing_rate=scalar["financing_rate"],
            financing_days=scalar["financing_days"],
            provisional_pct=scalar["provisional_pct"],
            provisional_direct_cost=scalar.get("provisional_direct_cost", 0.0),
        )
    return out


def _feed_scenarios(
    rows: List[Dict[str, str]],
    decks: Mapping[str, Mapping[str, float]],
    terms: Mapping[str, OfftakeTerms],
    core_products: List[str],
) -> tuple[Dict[str, FeedScenario], Rows]:
    raw: Dict[str, Dict[str, Dict[str, object]]] = defaultdict(lambda: defaultdict(dict))
    labels: Dict[str, str] = {}
    for r in rows:
        sid = r["scenario_id"]
        labels[sid] = r.get("label") or sid
        raw[sid][r["section"]][r["key"]] = r.get("ref") or fnum(r.get("value"))
    scenarios: Dict[str, FeedScenario] = {}
    offtake_rows: Rows = []
    for sid, sec in raw.items():
        deck = decks[str(sec["meta"]["price_deck"])]
        metal_prices = {m: deck[m] for m in sec["feed_payable"]}
        prices = {k: float(v) for k, v in sec.get("product_price", {}).items()}
        for product, terms_id in sec.get("product_offtake", {}).items():
            res = offtake_realized_price(terms[str(terms_id)], {k: float(v) for k, v in sec["product_grade"].items()}, deck)
            prices[product] = res["realized_usd_per_dmt"]
            offtake_rows.append({"scenario_id": sid, "product": product, "terms_id": terms_id, **res})
        scenarios[sid] = FeedScenario(
            scenario_id=sid,
            label=labels[sid],
            feed_grades={k: float(v) for k, v in sec["feed_grade"].items()},
            yields={k: float(v) for k, v in sec["yield"].items()},
            product_prices=prices,
            feed_metal_prices=metal_prices,
            feed_payables={k: float(v) for k, v in sec["feed_payable"].items()},
            conversion_costs={k: float(v) for k, v in sec.get("conversion_cost", {}).items()},
            opex_per_t=float(sec["meta"].get("opex_per_t", 0.0)),
            core_products=tuple(core_products),
        )
    return scenarios, offtake_rows


def build(data_dir: Path) -> Dict[str, object]:
    d = Path(data_dir)
    cfg = read_json(d / "config.json")
    as_of = str(cfg.get("as_of", ""))
    tables: Dict[str, Rows] = {}
    metrics: Dict[str, object] = {}
    findings: Rows = []

    # ---- facts --------------------------------------------------------------
    facts = read_csv(d / "facts.csv")
    tables["silver_facts"] = facts
    for f in facts:
        v = fnum(f["value"]) if _is_num(f["value"]) else f["value"]
        metrics[f"facts.{f['key']}"] = v

    decks = _price_decks(read_csv(d / "price_deck.csv"))
    tables["silver_price_deck"] = read_csv(d / "price_deck.csv")

    # ---- settlements & invoice controls -------------------------------------
    settle_rows: Rows = []
    for r in read_csv(d / "settlements.csv"):
        metals = [k[len("grade_"):] for k in r if k.startswith("grade_") and r[k]]
        inp = SettlementInput(
            settlement_id=r["settlement_id"],
            wet_kg=float(r["wet_kg"]),
            moisture=float(r["moisture"]),
            grades={m: float(r[f"grade_{m}"]) for m in metals},
            prices={m: float(r[f"price_{m}"]) for m in metals},
            payables={m: float(r[f"payable_{m}"]) for m in metals},
            provisional_pct=float(r["provisional_pct"]),
            direct_costs=fnum(r.get("direct_costs")) or 0.0,
            prior_payments=fnum(r.get("prior_payments")) or 0.0,
        )
        calc = provisional_settlement(inp)
        inv = InvoiceFacts(
            invoice_id=r["invoice_id"],
            invoice_date=r["invoice_date"],
            subtotal=float(r["inv_subtotal"]),
            direct_costs_deducted=fnum(r.get("inv_direct_costs")) or 0.0,
            prior_payments_deducted=fnum(r.get("inv_prior_payments")) or 0.0,
            amount_due=float(r["inv_amount_due"]),
            description_wet_t=fnum(r.get("inv_description_wet_t")),
            price_dates={m: r[f"price_date_{m}"] for m in metals if r.get(f"price_date_{m}")},
        )
        recon = reconcile_settlement(calc, inv, workbook_balance=fnum(r.get("workbook_balance")))
        for f in recon:
            findings.append(finding(str(f["code"]), str(f["severity"]), str(f["invoice_id"]), str(f["message"]), float(f["amount_usd"]), r.get("doc_ref", "")))
        row = {"settlement_id": r["settlement_id"], "invoice_id": r["invoice_id"], "invoice_date": r["invoice_date"], "counterparty": r.get("counterparty", ""), "product": r.get("product", ""), **calc, "invoice_amount_due": inv.amount_due, "findings": len(recon), "doc_ref": r.get("doc_ref", "")}
        for m in metals:
            row[f"{m}_payable"] = inp.payables[m]
            row[f"{m}_price"] = inp.prices[m]
        settle_rows.append(row)
        for k, v in row.items():
            if isinstance(v, (int, float)):
                metrics[f"settle.{r['settlement_id']}.{k}"] = v
    tables["gold_settlement_recon"] = settle_rows
    if settle_rows:
        metrics["settle.total_provisional_value"] = sum(float(r["provisional_value"]) for r in settle_rows)
        metrics["settle.total_payable_value_100pct"] = sum(float(r["payable_value_100pct"]) for r in settle_rows)
        metrics["settle.total_final_balance_outstanding"] = sum(float(r["final_balance_outstanding"]) for r in settle_rows)
        metrics["settle.total_dry_t"] = sum(float(r["dry_t"]) for r in settle_rows)
        metrics["settle.total_wet_t"] = sum(float(r["wet_t"]) for r in settle_rows)

    # ---- product grading ------------------------------------------------------
    specs = [
        SpecLimit(s["product"], s["grade"], s["component"], s["limit_type"], to_ppm(float(s["value"]), s["unit"]), s.get("source", ""))
        for s in read_csv(d / "specs.csv")
    ]
    assays: Dict[tuple, Dict[str, float]] = defaultdict(dict)
    for a in read_csv(d / "lot_assays.csv"):
        if a.get("value") == "":
            continue
        assays[(a["lot_id"], a["product"], a.get("lab", ""))][a["component"]] = to_ppm(float(a["value"]), a["unit"])
    grade_rows: Rows = []
    verdict_rows: Rows = []
    grade_order: Dict[str, List[str]] = dict(cfg.get("grade_order", {}))  # type: ignore[arg-type]
    for (lot, product, lab), assay in assays.items():
        lims = [s for s in specs if s.product == product]
        if not lims:
            continue
        rows = check_lot(assay, lims, lot_id=lot)
        for r in rows:
            r["lab"] = lab
        grade_rows.extend(rows)
        v = grade_verdicts(rows, grade_order.get(product, sorted({s.grade for s in lims})))
        for g, gv in v["grades"].items():  # type: ignore[union-attr]
            verdict_rows.append({"lot_id": lot, "product": product, "lab": lab, "grade": g, **gv})
            metrics[f"grading.{lot}.{g}.verdict"] = gv["verdict"]
            metrics[f"grading.{lot}.{g}.failing"] = ", ".join(gv["failing_components"]) or "none"
            metrics[f"grading.{lot}.{g}.tightest"] = gv["tightest_component"]
            metrics[f"grading.{lot}.{g}.tightest_headroom_pct"] = gv["tightest_headroom_pct"]
        metrics[f"grading.{lot}.highest_grade_met"] = v["highest_grade_met"] or "none"
    tables["gold_product_grading"] = grade_rows
    tables["gold_product_grade_verdicts"] = verdict_rows

    # ---- index-linked price scenarios ---------------------------------------
    rules = [
        PriceRule(r["rule_id"], r["product"], r["grade"], r["kind"], r.get("index", ""), fnum(r.get("pct")) or 1.0, fnum(r.get("discount")) or 0.0, fnum(r.get("fixed_price")) or 0.0)
        for r in read_csv(d / "price_rules.csv")
    ]
    grid_rows: Rows = []
    for sc in cfg.get("index_scenarios", []):  # type: ignore[union-attr]
        prod_rules = [r for r in rules if r.product == sc["product"]]
        content = float(sc.get("content", 1.0))
        grid_rows.extend(price_grid(prod_rules, sc["index"], sc["values"], sc.get("other_indices"), content))
        ref = {**sc.get("other_indices", {}), sc["index"]: sc.get("reference_value")}
        for r in prod_rules:
            if sc.get("reference_value") is not None:
                metrics[f"price.{r.rule_id}.at_reference"] = apply_rule(r, ref, content)
            for bname, bval in sc.get("benchmarks", {}).items():
                metrics[f"price.{r.rule_id}.index_for_{bname}"] = index_for_target_price(r, float(bval), content)
    tables["gold_price_scenarios"] = grid_rows

    # ---- feed scenarios / unit economics ------------------------------------
    terms = _offtake_terms(read_csv(d / "offtake_terms.csv"))
    core = list(cfg.get("core_products", []))  # type: ignore[arg-type]
    scenarios, offtake_rows = _feed_scenarios(read_csv(d / "feed_scenarios.csv"), decks, terms, core)
    tables["gold_offtake_realization"] = offtake_rows
    for r in offtake_rows:
        for k, v in r.items():
            if isinstance(v, float):
                metrics[f"offtake.{r['scenario_id']}.{k}"] = v
    ue_rows: Rows = []
    for sid, s in scenarios.items():
        ue = unit_economics(s)
        ue_rows.append({"scenario_id": sid, "label": s.label, "payable_case": "model", **{k: v for k, v in s.feed_payables.items()}, **ue})
        for k, v in ue.items():
            metrics[f"ue.{sid}.{k}"] = v
    # actual purchase payables re-run through each scenario
    purchases = read_csv(d / "feed_purchases.csv")
    for p in purchases:
        pay = {k[len("payable_"):]: float(v) for k, v in p.items() if k.startswith("payable_") and v}
        for sid, s in scenarios.items():
            if set(pay) != set(s.feed_payables):
                continue
            from dataclasses import replace

            ue = unit_economics(replace(s, feed_payables=pay))
            ue_rows.append({"scenario_id": sid, "label": s.label, "payable_case": p["purchase_id"], **pay, **ue})
            metrics[f"ue_pay.{sid}.{p['purchase_id']}.ebitda"] = ue["ebitda"]
            metrics[f"ue_pay.{sid}.{p['purchase_id']}.cost_feedstock"] = ue["cost_feedstock"]
    tables["gold_unit_economics"] = ue_rows
    if scenarios:
        be = [unit_economics(s)["breakeven_feed_payable"] for s in scenarios.values()]
        metrics["ue.min_breakeven_feed_payable"] = min(b for b in be if b is not None)
        metrics["ue.max_breakeven_feed_payable"] = max(b for b in be if b is not None)
        model_pay = [v for s in scenarios.values() for v in s.feed_payables.values()]
        metrics["ue.model_feed_payable_min"] = min(model_pay)
        metrics["ue.model_feed_payable_max"] = max(model_pay)
    for p in purchases:
        for k, v in p.items():
            if k.startswith("payable_") and v:
                metrics[f"purchase.{p['purchase_id']}.{k}"] = float(v)

    # ---- conversion spread (feed payable vs product payable x recovery) ----
    spread_rows: Rows = []
    recovery = {k: float(v) for k, v in dict(cfg.get("recovery", {})).items()}  # type: ignore[arg-type]
    for r in offtake_rows:
        s = scenarios[str(r["scenario_id"])]
        prod_pay = {m: float(r[f"{m}_effective_payable"]) for m in s.feed_payables if f"{m}_effective_payable" in r}
        cases = [("model", s.feed_payables)] + [
            (p["purchase_id"], {k[len("payable_"):]: float(v) for k, v in p.items() if k.startswith("payable_") and v}) for p in purchases
        ]
        for case, pay in cases:
            if set(pay) != set(prod_pay):
                continue
            sp = conversion_spread(pay, prod_pay, {m: recovery.get(m, 1.0) for m in pay})
            for m, v in sp.items():
                spread_rows.append({"scenario_id": s.scenario_id, "feed_payable_case": case, "metal": m, "feed_payable": pay[m], "product_payable": prod_pay[m], "recovery": recovery.get(m, 1.0), "spread_pts": v * 100})
                metrics[f"spread.{s.scenario_id}.{case}.{m}"] = v * 100
    tables["gold_conversion_spread"] = spread_rows

    # ---- by-product streams: contained metal value ---------------------------
    stream_rows: Rows = []
    streams: Dict[str, Dict[str, float]] = defaultdict(dict)
    for r in read_csv(d / "stream_assays.csv"):
        streams[r["stream_id"]][r["element"]] = float(r["grade_frac"])
    for deck_id in cfg.get("stream_valuation_decks", []):  # type: ignore[union-attr]
        deck = decks[deck_id]
        for sid, g in streams.items():
            cv = contained_metal_value(g, deck, deck.get("Li2CO3"))
            stream_rows.append({"stream_id": sid, "price_deck": deck_id, **{f"{k}_usd_per_t": v for k, v in cv.items()}})
            metrics[f"stream.{sid}.{deck_id}.total_usd_per_t"] = cv["total"]
            for k, v in g.items():
                metrics[f"stream.{sid}.{k}_pct"] = v * 100
    tables["gold_stream_metal_value"] = stream_rows

    # ---- capacity -------------------------------------------------------------
    cyc = read_csv(d / "cycle_times.csv")
    cap_rows: Rows = []
    cap_cfg = dict(cfg.get("capacity", {}))  # type: ignore[arg-type]
    for case in sorted({c["case"] for c in cyc}):
        vh = cap.vessel_cycle_hours((c["vessel"], float(c["hours"])) for c in cyc if c["case"] == case)
        for label, bt in dict(cap_cfg.get("batch_t", {"base": 1.0})).items():
            for hlabel, hpd in dict(cap_cfg.get("hours_per_day", {"24x7": 24})).items():
                res = cap.bottleneck_capacity(vh, float(bt), float(cap_cfg.get("operating_days", 330)), float(hpd))
                cap_rows.append({"cycle_case": case, "batch_case": label, "shift_case": hlabel, **{f"vessel_{k}_h": v for k, v in vh.items()}, **res})
                metrics[f"capacity.{case}.{label}.{hlabel}.tpa"] = res["tonnes_per_year"]
                metrics[f"capacity.{case}.{label}.{hlabel}.batches_per_day"] = res["batches_per_day"]
        metrics[f"capacity.{case}.bottleneck"] = max(vh, key=vh.get)
        metrics[f"capacity.{case}.cycle_hours"] = max(vh.values())
    tables["gold_capacity"] = cap_rows

    monthly = [
        {"month": r["month"], "feed_kg": float(r["feed_kg"] or 0), "batches": cap.count_batches(r.get("batch_ref", "")), "batch_ref": r.get("batch_ref", ""), "feed_source": r.get("feed_source", "")}
        for r in read_csv(d / "throughput_monthly.csv")
    ]
    for m in monthly:
        m["t_per_batch"] = m["feed_kg"] / 1000 / m["batches"] if m["batches"] else None
    tables["silver_throughput_monthly"] = monthly
    if monthly:
        rr = cap.run_rate(monthly, int(cap_cfg.get("trailing_months", 12)))
        tables["gold_run_rate"] = [rr]
        for k, v in rr.items():
            metrics[f"runrate.{k}"] = v
    pva = cap.plan_vs_actual(read_csv(d / "plan.csv"), monthly, cfg.get("last_actual_month"))  # type: ignore[arg-type]
    tables["gold_plan_vs_actual"] = pva
    for r in pva:
        for k in ("plan_t", "plan_prorata_t", "actual_t", "attainment_pct", "months_with_actuals"):
            metrics[f"plan.{r['period']}.{k}"] = r[k]

    # ---- assumption evidence -------------------------------------------------
    ev_rows: Rows = []
    for r in read_csv(d / "assumption_evidence.csv"):
        e = AssumptionEvidence(
            assumption_id=r["assumption_id"], product=r["product"], metric=r["metric"], model_value=float(r["model_value"]),
            evidence_value=fnum(r.get("evidence_value")), unit=r["unit"], n_transactions=int(r.get("n_transactions") or 0),
            last_transaction_date=r.get("last_transaction_date") or None, contract_status=r["contract_status"],
            evidence_refs=r.get("evidence_refs", ""), model_ref=r.get("model_ref", ""),
        )
        g = grade(e, as_of)
        ev_rows.append(g)
        for k in ("evidence_grade", "variance_pct", "evidence_value", "model_value", "n_transactions", "days_since_last_transaction"):
            metrics[f"evidence.{e.assumption_id}.{k}"] = g[k]
        if g["evidence_grade"] in {"C", "D"}:
            findings.append(finding("WEAK_EVIDENCE", "WARNING", e.assumption_id, f"{e.product} {e.metric} assumption rests on grade {g['evidence_grade']} evidence.", refs=e.evidence_refs))
    tables["gold_assumption_evidence"] = ev_rows

    # ---- analyst findings from documents -------------------------------------
    for r in read_csv(d / "findings_manual.csv"):
        findings.append(finding(r["code"], r["severity"], r["subject"], r["message"], fnum(r.get("amount_usd")) or 0.0, r.get("refs", "")))
    tables["gold_findings"] = findings

    # ---- derived cross-table metrics (config-declared ratios) -----------------
    for name, spec in dict(cfg.get("derived_metrics", {})).items():  # type: ignore[arg-type]
        metrics[f"derived.{name}"] = _derive(spec, metrics)

    # ---- question register ---------------------------------------------------
    qs = answer_questions(read_csv(d / "questions.csv"), metrics)
    tables["gold_question_register"] = qs

    tables["gold_metrics"] = [{"metric_key": k, "value": v if not isinstance(v, float) else round(v, 6)} for k, v in sorted(metrics.items())]

    hashes = hash_inputs([p for p in d.glob("*") if p.suffix in {".csv", ".json"}])
    pack = evidence_pack(
        hashes,
        {k: len(v) for k, v in tables.items()},
        findings,
        list(cfg.get("assumptions", [])),  # type: ignore[arg-type]
        list(cfg.get("unknowns", [])),  # type: ignore[arg-type]
        str(cfg.get("owner", "")),
        as_of,
    )
    return {"tables": tables, "metrics": metrics, "evidence_pack": pack}


def _derive(spec: Mapping[str, object], metrics: Mapping[str, object]) -> Optional[float]:
    """{'op': 'div'|'mul'|'sub'|'add', 'a': key_or_number, 'b': key_or_number, 'scale': 1}"""

    def val(x: object) -> Optional[float]:
        if isinstance(x, (int, float)):
            return float(x)
        v = metrics.get(str(x))
        return float(v) if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)) else None

    a, b = val(spec.get("a")), val(spec.get("b"))
    if a is None or b is None:
        return None
    op = spec.get("op")
    scale = float(spec.get("scale", 1))  # type: ignore[arg-type]
    if op == "div":
        return a / b * scale if b else None
    if op == "mul":
        return a * b * scale
    if op == "sub":
        return (a - b) * scale
    if op == "add":
        return (a + b) * scale
    raise ValueError(f"Unknown op {op}")


def _is_num(s: str) -> bool:
    try:
        float(s.replace(",", ""))
        return True
    except (ValueError, AttributeError):
        return False
