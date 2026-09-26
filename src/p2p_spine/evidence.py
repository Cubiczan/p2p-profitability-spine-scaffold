"""Assumption evidence grading, reason-coded findings and evidence packs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

# Evidence grades, strongest first.
#  A  executed, in-force contract AND >=2 settled transactions at/near the assumed value
#  B  executed, in-force contract OR >=2 transactions
#  C  single transaction, expired contract, term sheet, LOI or third-party quote
#  D  management assertion only / no documentary evidence
GRADE_ORDER = ["A", "B", "C", "D"]


@dataclass(frozen=True)
class AssumptionEvidence:
    assumption_id: str
    product: str
    metric: str
    model_value: float
    evidence_value: Optional[float]
    unit: str
    n_transactions: int
    last_transaction_date: Optional[str]
    contract_status: str  # executed_in_force | executed_expired | term_sheet | loi | quote | none
    evidence_refs: str
    model_ref: str = ""


def grade(e: AssumptionEvidence, as_of: str, stale_days: int = 365) -> Dict[str, object]:
    in_force = e.contract_status == "executed_in_force"
    age: Optional[int] = None
    if e.last_transaction_date:
        age = (date.fromisoformat(as_of) - date.fromisoformat(e.last_transaction_date)).days
    fresh_tx = e.n_transactions if (age is not None and age <= stale_days) else 0
    if in_force and fresh_tx >= 2:
        g = "A"
    elif in_force or fresh_tx >= 2:
        g = "B"
    elif e.n_transactions >= 1 or e.contract_status in {"executed_expired", "term_sheet", "loi", "quote"}:
        g = "C"
    else:
        g = "D"
    variance = None
    if e.evidence_value is not None and e.model_value:
        variance = (e.evidence_value - e.model_value) / e.model_value * 100
    return {
        "assumption_id": e.assumption_id,
        "product": e.product,
        "metric": e.metric,
        "unit": e.unit,
        "model_value": e.model_value,
        "evidence_value": e.evidence_value,
        "variance_pct": None if variance is None else round(variance, 2),
        "n_transactions": e.n_transactions,
        "last_transaction_date": e.last_transaction_date,
        "days_since_last_transaction": age,
        "contract_status": e.contract_status,
        "evidence_grade": g,
        "evidence_refs": e.evidence_refs,
        "model_ref": e.model_ref,
    }


def finding(code: str, severity: str, subject: str, message: str, amount_usd: float = 0.0, refs: str = "") -> Dict[str, object]:
    return {"code": code, "severity": severity, "subject": subject, "message": message, "amount_usd": round(amount_usd, 2), "refs": refs}


def hash_inputs(paths: Iterable[Path]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    digest = hashlib.sha256()
    for p in sorted(paths):
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        out[p.name] = h
        digest.update(f"{p.name}:{h}".encode())
    out["__combined__"] = digest.hexdigest()
    return out


def evidence_pack(
    input_hashes: Mapping[str, str],
    row_counts: Mapping[str, int],
    findings: List[Dict[str, object]],
    assumptions: List[str],
    unknowns: List[str],
    owner: str,
    as_of: str,
) -> Dict[str, object]:
    blocking = [f for f in findings if f["severity"] == "BLOCKING"]
    status = "HALT" if blocking else ("ADVISORY" if not owner else "PROVISIONAL")
    return {
        "as_of": as_of,
        "input_sha256": input_hashes.get("__combined__", ""),
        "input_files": {k: v for k, v in input_hashes.items() if k != "__combined__"},
        "row_counts": dict(row_counts),
        "finding_counts": {s: sum(1 for f in findings if f["severity"] == s) for s in ("BLOCKING", "WARNING", "INFO")},
        "assumptions": assumptions,
        "unknowns": unknowns,
        "owner": owner,
        "status": status,
        "note": "Unsigned evidence pack. Not final evidence until a named reviewer locks it.",
    }


def dumps(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)
