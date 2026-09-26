"""Markdown diligence report from a build result."""

from __future__ import annotations

from typing import Dict, List

STATUS_ORDER = {"CONTRADICTED": 0, "OPEN_GAP": 1, "PARTIAL": 2, "ANSWERED": 3}


def render(result: Dict[str, object], title: str) -> str:
    tables: Dict[str, List[Dict[str, object]]] = result["tables"]  # type: ignore[assignment]
    pack: Dict[str, object] = result["evidence_pack"]  # type: ignore[assignment]
    lines: List[str] = [f"# {title}", "", f"As of {pack['as_of']} · evidence pack status **{pack['status']}** · input sha256 `{str(pack['input_sha256'])[:16]}`", ""]

    lines += ["## Question register", ""]
    by_section: Dict[str, List[Dict[str, object]]] = {}
    for q in tables.get("gold_question_register", []):
        by_section.setdefault(str(q["section"]), []).append(q)
    for section, qs in by_section.items():
        lines += [f"### {section}", ""]
        for q in qs:
            lines += [
                f"**{q['question_id']} — {q['question']}**",
                "",
                f"- Status: `{q['status']}` · confidence: {q['confidence'] or 'n/a'}",
            ]
            if q["management_response"]:
                lines.append(f"- Management response: {q['management_response']}")
            if q["computed_answer"]:
                lines.append(f"- Spine answer: {q['computed_answer']}")
            if q["evidence_assessment"]:
                lines.append(f"- Evidence assessment: {q['evidence_assessment']}")
            if q["missing_metrics"]:
                lines.append(f"- Missing data: `{q['missing_metrics']}`")
            if q["next_action"]:
                lines.append(f"- Next action: {q['next_action']}")
            if q["evidence_refs"]:
                lines.append(f"- Sources: {q['evidence_refs']}")
            lines.append("")

    lines += ["## Assumption evidence", "", "| Assumption | Model | Evidence | Var % | Tx | Contract | Grade |", "|---|---:|---:|---:|---:|---|:-:|"]
    for e in tables.get("gold_assumption_evidence", []):
        lines.append(
            f"| {e['product']} {e['metric']} | {_n(e['model_value'])} | {_n(e['evidence_value'])} | {_n(e['variance_pct'])} | {e['n_transactions']} | {e['contract_status']} | {e['evidence_grade']} |"
        )
    lines += ["", "## Findings", "", "| Severity | Code | Subject | Message | USD |", "|---|---|---|---|---:|"]
    sev = {"BLOCKING": 0, "WARNING": 1, "INFO": 2}
    for f in sorted(tables.get("gold_findings", []), key=lambda f: sev.get(str(f["severity"]), 3)):
        lines.append(f"| {f['severity']} | {f['code']} | {f['subject']} | {f['message']} | {_n(f['amount_usd'])} |")
    lines += ["", "## Evidence pack", "", "```json", _json(pack), "```", ""]
    return "\n".join(lines)


def _n(v: object) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _json(obj: object) -> str:
    from .evidence import dumps

    return dumps(obj)
