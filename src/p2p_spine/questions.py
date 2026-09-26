"""Data-driven question register.

Each question row binds to computed metrics via `metric_keys` and renders an
`answer_template` with Python str.format placeholders (dots replaced by `__`).
Questions whose metrics are missing are reported as OPEN_GAP rather than
answered with made-up values."""

from __future__ import annotations

import math
import string
from typing import Dict, List, Mapping


def _key(k: str) -> str:
    return k.strip().replace(".", "__")


class _Fmt(string.Formatter):
    def format_field(self, value: object, format_spec: str) -> str:
        if isinstance(value, float) and math.isnan(value):
            return "n/a"
        return super().format_field(value, format_spec)


def answer_questions(questions: List[Mapping[str, str]], metrics: Mapping[str, object]) -> List[Dict[str, object]]:
    flat = {_key(k): v for k, v in metrics.items()}
    fmt = _Fmt()
    out: List[Dict[str, object]] = []
    for q in questions:
        keys = [k for k in (q.get("metric_keys") or "").split(";") if k.strip()]
        missing = [k for k in keys if _key(k) not in flat or flat[_key(k)] is None]
        template = q.get("answer_template") or ""
        if missing:
            computed = ""
            status = "OPEN_GAP"
        else:
            try:
                computed = fmt.format(template, **flat) if template else ""
                status = q.get("status_override") or "ANSWERED"
            except (KeyError, ValueError, IndexError) as exc:
                computed, status = f"TEMPLATE_ERROR: {exc}", "OPEN_GAP"
        out.append(
            {
                "question_id": q.get("question_id", ""),
                "section": q.get("section", ""),
                "topic": q.get("topic", ""),
                "question": q.get("question", ""),
                "management_response": q.get("management_response", ""),
                "computed_answer": computed,
                "evidence_assessment": q.get("evidence_assessment", ""),
                "status": status,
                "confidence": q.get("confidence", ""),
                "missing_metrics": ";".join(missing),
                "evidence_refs": q.get("evidence_refs", ""),
                "next_action": q.get("next_action", ""),
            }
        )
    return out
