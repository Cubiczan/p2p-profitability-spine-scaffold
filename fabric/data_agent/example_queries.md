# Fabric Data Agent - example queries

Add these under **Example queries** (question → SQL) so the agent learns the gold schema.

**What does the register say about capacity?**
```sql
SELECT question_id, status, confidence, computed_answer, next_action
FROM gold_question_register
WHERE topic = 'Capacity';
```

**Which questions are still open or contradicted?**
```sql
SELECT question_id, section, question, status, next_action
FROM gold_question_register
WHERE status IN ('OPEN_GAP', 'CONTRADICTED')
ORDER BY status, question_id;
```

**EBITDA per tonne of feed for every scenario, model vs actual purchase payables**
```sql
SELECT scenario_id, label, payable_case, ROUND(cost_feedstock, 0) AS feed_cost, ROUND(ebitda, 0) AS ebitda,
       ROUND(breakeven_feed_payable * 100, 1) AS breakeven_payable_pct
FROM gold_unit_economics
ORDER BY scenario_id, payable_case;
```

**How much of EBITDA depends on by-products?**
```sql
SELECT scenario_id, ROUND(ebitda, 0) AS ebitda, ROUND(byproduct_revenue, 0) AS byproduct_rev,
       ROUND(ebitda_ex_byproducts, 0) AS ebitda_ex_byproducts
FROM gold_unit_economics
WHERE payable_case = 'model';
```

**Which assumptions rest on weak evidence?**
```sql
SELECT product, metric, model_value, evidence_value, variance_pct, evidence_grade, evidence_refs
FROM gold_assumption_evidence
WHERE evidence_grade IN ('C', 'D')
ORDER BY evidence_grade DESC;
```

**Invoice control exceptions**
```sql
SELECT severity, code, subject, message, amount_usd
FROM gold_findings
WHERE severity IN ('BLOCKING', 'WARNING')
ORDER BY severity, amount_usd DESC;
```

**Highest product grade met per lot**
```sql
SELECT lot_id, grade, verdict, failing_components, tightest_component, tightest_headroom_pct
FROM gold_product_grade_verdicts
ORDER BY lot_id, grade;
```

**Capacity at target cycle times, 24/7**
```sql
SELECT cycle_case, batch_case, shift_case, bottleneck_vessel, ROUND(cycle_hours, 2) AS cycle_h,
       ROUND(batches_per_day, 2) AS batches_per_day, ROUND(tonnes_per_year, 0) AS tpa
FROM gold_capacity
ORDER BY cycle_case, shift_case, batch_case;
```

**Plan vs actual throughput**
```sql
SELECT period, plan_t, ROUND(plan_prorata_t, 1) AS plan_to_date, ROUND(actual_t, 1) AS actual_to_date, attainment_pct
FROM gold_plan_vs_actual;
```

**Look up any single metric**
```sql
SELECT metric_key, value FROM gold_metrics WHERE metric_key LIKE 'derived.%' ORDER BY metric_key;
```
