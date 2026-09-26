# Data contract

A dataset is a folder of CSV files plus `config.json`. Every table carries a `source` or
`doc_ref` column that points at the document the value came from. Missing files are allowed:
the related gold tables are empty and dependent questions return `OPEN_GAP`.

| File | Grain | Key columns |
|---|---|---|
| `config.json` | dataset | `as_of`, `owner`, `core_products`, `grade_order`, `recovery`, `capacity`, `index_scenarios`, `stream_valuation_decks`, `derived_metrics`, `assumptions`, `unknowns` |
| `facts.csv` | scalar fact | `key, value, unit, source`: anything a question needs that is not computed |
| `price_deck.csv` | deck x commodity | `deck_id, commodity, price_usd_per_t, as_of, basis, source` |
| `feed_scenarios.csv` | scenario x section x key (long) | `scenario_id, label, section, key, value, ref, source`. Sections: `meta` (`price_deck` via `ref`, `opex_per_t`), `feed_grade`, `feed_payable`, `yield`, `product_price`, `product_offtake` (`ref` = terms_id), `product_grade`, `conversion_cost`, `model_output` (ignored by the engine; use it for regression tests) |
| `offtake_terms.csv` | terms x key [x metal] | `benchmark_payable_mid/high`, `end_buyer_payable` (per metal); `floor_payable_adjustment`, `base_margin`, `profit_share`, `direct_cost_fixed`, `financing_rate`, `financing_days`, `provisional_pct`, `provisional_direct_cost` |
| `feed_purchases.csv` | purchase | `purchase_id, supplier, material, date, payable_<metal>...`: actual payables, re-run through every scenario |
| `settlements.csv` | sale settlement | `wet_kg, moisture, grade_<m>, price_<m>, price_date_<m>, payable_<m>, provisional_pct, direct_costs, prior_payments, inv_subtotal, inv_direct_costs, inv_prior_payments, inv_amount_due, inv_description_wet_t, workbook_balance` |
| `specs.csv` | product x grade x component | `limit_type` min/max, `value`, `unit` (%, ppm, g/t, ug/kg) |
| `lot_assays.csv` | lot x component | `lot_id, product, lab, component, value, unit` |
| `price_rules.csv` | rule | `kind` = `fixed` / `pct_of_index` / `index_minus_discount_x_content`; `index, pct, discount, fixed_price` |
| `stream_assays.csv` | by-product stream x element | `grade_frac`: metal lost to by-products |
| `cycle_times.csv` | case x vessel x step | `hours`; cases such as `current`, `target` |
| `throughput_monthly.csv` | month | `feed_kg, batch_ref` (e.g. `44-60`, `66, 67`, `179`) |
| `plan.csv` | plan period | `period, start_month, end_month, plan_t` |
| `assumption_evidence.csv` | model assumption | `model_value, evidence_value, n_transactions, last_transaction_date, contract_status` (`executed_in_force`, `executed_expired`, `term_sheet`, `loi`, `quote`, `none`) |
| `findings_manual.csv` | analyst finding | `code, severity, subject, message, amount_usd, refs` |
| `questions.csv` | question | `question_id, section, topic, question, management_response, answer_template, metric_keys, status_override, confidence, evidence_assessment, evidence_refs, next_action` |

## Metric keys

`gold_metrics` lists every key. Families:

- `facts.<key>`, `derived.<name>` (declared in `config.derived_metrics` as `div|mul|add|sub` of two keys or numbers)
- `ue.<scenario>.<field>`, `ue_pay.<scenario>.<purchase>.<field>`, `offtake.<scenario>.<field>`, `spread.<scenario>.<case>.<metal>`
- `settle.<id>.<field>`, `settle.total_*`
- `grading.<lot>.<grade>.verdict|failing|tightest|tightest_headroom_pct`, `grading.<lot>.highest_grade_met`
- `price.<rule>.at_reference`, `price.<rule>.index_for_<benchmark>`
- `capacity.<case>.<batch>.<shift>.tpa|batches_per_day`, `capacity.<case>.bottleneck|cycle_hours`
- `runrate.*`, `plan.<period>.*`, `evidence.<assumption>.*`, `stream.<stream>.*`, `purchase.<id>.*`

In `answer_template`, write keys with `.` replaced by `__` and use Python format specs, e.g.
`{ue__BM__ebitda:,.0f}`. Avoid `.` in ids (lots, periods, scenarios) to keep keys readable.
