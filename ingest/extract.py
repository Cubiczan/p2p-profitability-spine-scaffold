"""Extract invoices, lab assays and certificates of analysis into data-contract-shaped STAGING files.

Calls Azure AI Document Intelligence (REST, api-version 2024-11-30 GA) or, with --from-json,
re-runs the mapping offline on saved analyze responses. Output never touches the dataset: it goes
to <out>/staging/ and a reviewer approves rows before they are appended to data/*.csv
(see docs/azure-ai-extensions.md).

  --kind invoice    prebuilt-invoice -> settlements.csv columns (+ line items file)
  --kind lab_assay  prebuilt-layout  -> lot_assays.csv rows from component/value/unit tables
  --kind coa        prebuilt-layout  -> lot_assays.csv rows (same heuristics as lab_assay)
  --kind generic    prebuilt-layout + keyValuePairs -> key/value rows for manual triage

Files written (never overwritten):
  <out>/staging/<kind>_<ts>.csv          staged rows in contract columns + review_* columns
  <out>/staging/<kind>_<ts>_review.csv   one line per extracted value with confidence + status
  <out>/staging/<kind>_<ts>_lines.csv    invoice line items (invoice only)
  <out>/staging/raw/<kind>_<ts>_<n>_<file>.json  raw DI responses (audit trail; re-mappable)

Rows whose confidence is below --threshold, or with missing/ambiguous values, carry
review_status = NEEDS_REVIEW.

Examples:
  python ingest/extract.py --kind lab_assay --from-json tests/fixtures/di_lab_assay.json --product Li2CO3 --lab "Buyer lab"
  uv run --no-project --with azure-identity python ingest/extract.py --kind invoice \
      --endpoint https://<resource>.cognitiveservices.azure.com --auth interactive invoices/*.pdf
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

if __package__ in (None, ""):  # allow `python ingest/extract.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.docintel import DI_API_VERSION, result_of  # noqa: E402

KINDS = ("invoice", "lab_assay", "coa", "generic")
MODEL_FOR_KIND = {"invoice": "prebuilt-invoice", "lab_assay": "prebuilt-layout", "coa": "prebuilt-layout", "generic": "prebuilt-layout"}
NEEDS_REVIEW = "NEEDS_REVIEW"
OK = "OK"

SETTLEMENT_BASE_COLUMNS = [
    "settlement_id", "invoice_id", "invoice_date", "counterparty", "product", "wet_kg", "moisture",
]
SETTLEMENT_TAIL_COLUMNS = [
    "provisional_pct", "direct_costs", "prior_payments", "inv_subtotal", "inv_direct_costs", "inv_prior_payments",
    "inv_amount_due", "inv_description_wet_t", "workbook_balance", "doc_ref",
]
LOT_ASSAY_COLUMNS = ["lot_id", "product", "lab", "component", "value", "unit", "source"]
REVIEW_COLUMNS = ["review_status", "review_confidence", "review_notes", "source_file"]
REVIEW_SHEET_COLUMNS = ["kind", "source_file", "page", "record", "field", "value", "confidence", "status", "notes", "reviewer_decision", "reviewer_value"]
LINE_COLUMNS = ["invoice_id", "line_no", "description", "product_code", "quantity", "unit", "unit_price", "amount", "confidence", "review_status", "doc_ref", "source_file"]

# Units the stdlib engine (p2p_spine.units.to_ppm) accepts, with common spellings mapped onto them.
UNIT_ALIASES = {
    "%": "%", "pct": "%", "wt%": "%", "wt.%": "%", "wt %": "%", "% w/w": "%", "%w/w": "%", "mass%": "%", "% m/m": "%",
    "ppm": "ppm", "mg/kg": "mg/kg", "g/t": "g/t", "ug/kg": "ug/kg", "µg/kg": "ug/kg", "μg/kg": "ug/kg", "ppb": "ppb",
}


def settlement_columns(metals: Sequence[str] = ()) -> List[str]:
    metal_cols = [f"{p}_{m}" for p in ("grade", "price", "price_date", "payable") for m in metals]
    return SETTLEMENT_BASE_COLUMNS + metal_cols + SETTLEMENT_TAIL_COLUMNS


@dataclass
class Mapped:
    rows: List[Dict[str, Any]] = field(default_factory=list)
    review: List[Dict[str, Any]] = field(default_factory=list)
    lines: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# value helpers
# ---------------------------------------------------------------------------------------------

_NUM_RE = re.compile(r"^\s*(?P<q>[<>≤≥]|n\.?d\.?)?\s*(?P<num>[-+]?(?:\d[\d,' ]*)?\.?\d+(?:[eE][-+]?\d+)?)\s*(?P<rest>.*)$", re.I)


def parse_number(text: str) -> Tuple[Optional[float], str, str]:
    """'< 0.01 %' -> (0.01, '%', '<'); '1,234.5' -> (1234.5, '', ''); '99,2' -> (99.2, '', '')."""
    if text is None:
        return None, "", ""
    s = str(text).strip().replace("−", "-")
    m = _NUM_RE.match(s)
    if not m:
        return None, "", ""
    num = m.group("num").replace(" ", "").replace("'", "")
    if "," in num and "." not in num and re.fullmatch(r"[-+]?\d+,\d{1,2}", num):
        num = num.replace(",", ".")  # decimal comma
    else:
        num = num.replace(",", "")
    try:
        val = float(num)
    except ValueError:
        return None, "", ""
    q = (m.group("q") or "").lower()
    q = {"≤": "<", "≥": ">"}.get(q, q)
    if q.startswith("n"):
        q = "<"
    return val, m.group("rest").strip(), q


def normalise_unit(unit: str) -> Tuple[str, bool]:
    """Return (unit, supported_by_engine)."""
    u = unit.strip().strip("()[]").strip()
    key = u.lower().replace("  ", " ")
    if key in UNIT_ALIASES:
        return UNIT_ALIASES[key], True
    return u, False


def _unit_from_header(header: str) -> str:
    m = re.search(r"[\(\[]\s*([^\)\]]+?)\s*[\)\]]", header)
    if m:
        return m.group(1)
    m = re.search(r"\b(?:in|as)\s+(%|ppm|mg/kg|g/t|ppb|ug/kg|µg/kg|wt%)\s*$", header, re.I)
    return m.group(1) if m else ""


def field_value(f: Optional[Dict[str, Any]]) -> Any:
    """Typed value from a DI v4 DocumentField."""
    if not f:
        return None
    t = f.get("type")
    if t == "currency":
        return (f.get("valueCurrency") or {}).get("amount")
    key = {"string": "valueString", "date": "valueDate", "number": "valueNumber", "integer": "valueInteger", "time": "valueTime",
           "phoneNumber": "valuePhoneNumber", "countryRegion": "valueCountryRegion", "selectionMark": "valueSelectionMark"}.get(t or "")
    if key and f.get(key) is not None:
        return f[key]
    return f.get("content")


def field_page(f: Optional[Dict[str, Any]]) -> Optional[int]:
    for br in (f or {}).get("boundingRegions", []) or []:
        if br.get("pageNumber"):
            return int(br["pageNumber"])
    return None


def status_for(conf: Optional[float], threshold: float, problems: Sequence[str] = ()) -> str:
    if problems or conf is None or conf < threshold:
        return NEEDS_REVIEW
    return OK


def low_conf_note(conf: Optional[float], threshold: float) -> List[str]:
    if conf is None:
        return ["no confidence reported"]
    return [f"confidence {conf:.2f} < {threshold:g}"] if conf < threshold else []


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.10g}"
    return str(v)


def _min_conf(values: Sequence[Optional[float]]) -> Optional[float]:
    known = [v for v in values if v is not None]
    return min(known) if known else None


# ---------------------------------------------------------------------------------------------
# invoice -> settlements.csv
# ---------------------------------------------------------------------------------------------


def map_invoice(response: Dict[str, Any], source_file: str, *, threshold: float = 0.8, counterparty_from: str = "customer", metals: Sequence[str] = ()) -> Mapped:
    res = result_of(response)
    out = Mapped()
    cols = settlement_columns(metals)
    for d_i, doc in enumerate(res.get("documents", []) or []):
        fields: Dict[str, Any] = doc.get("fields", {}) or {}
        cp_field = "CustomerName" if counterparty_from == "customer" else "VendorName"
        wanted = {
            "invoice_id": ("InvoiceId", None),
            "invoice_date": ("InvoiceDate", None),
            "counterparty": (cp_field, None),
            "inv_subtotal": ("SubTotal", None),
            "inv_amount_due": ("AmountDue", "InvoiceTotal"),
        }
        row: Dict[str, Any] = {c: "" for c in cols}
        confs: List[Optional[float]] = []
        notes: List[str] = []
        pages: List[int] = []
        record = f"doc{d_i + 1}"
        for col, (primary, fallback) in wanted.items():
            f = fields.get(primary)
            used = primary
            if field_value(f) is None and fallback and field_value(fields.get(fallback)) is not None:
                f, used = fields.get(fallback), fallback
                notes.append(f"{col} from {fallback} ({primary} missing)")
            val = field_value(f)
            conf = f.get("confidence") if f else None
            problems = low_conf_note(conf, threshold)
            if val is None:
                problems.append("missing")
                notes.append(f"{col} missing")
            if f and f.get("type") == "currency":
                ccy = (f.get("valueCurrency") or {}).get("currencyCode")
                if ccy and ccy.upper() != "USD":
                    problems.append(f"currency {ccy}")
                    notes.append(f"{col} currency {ccy} (contract is USD)")
            row[col] = _fmt(val)
            confs.append(conf)
            pg = field_page(f)
            if pg:
                pages.append(pg)
            out.review.append({
                "kind": "invoice", "source_file": source_file, "page": pg or "", "record": record, "field": f"{col} <- {used}",
                "value": _fmt(val), "confidence": _fmt(conf), "status": status_for(conf, threshold, problems), "notes": "; ".join(problems),
                "reviewer_decision": "", "reviewer_value": "",
            })
        row["settlement_id"] = f"STG-{row['invoice_id']}" if row["invoice_id"] else f"STG-{Path(source_file).stem}-{d_i + 1}"
        page = min(pages) if pages else 1
        row["doc_ref"] = f"{source_file}#p{page}"
        notes.append("complete wet_kg, moisture, grade/price/payable per metal from the settlement statement before approval")
        min_c = _min_conf(confs)
        low = [n for n in notes if "missing" in n or "currency" in n]
        row.update({
            "review_status": status_for(min_c, threshold, low) if min_c is not None else NEEDS_REVIEW,
            "review_confidence": _fmt(min_c),
            "review_notes": "; ".join(notes),
            "source_file": source_file,
        })
        out.rows.append(row)

        items = (fields.get("Items") or {}).get("valueArray", []) or []
        for l_i, it in enumerate(items):
            obj = it.get("valueObject", {}) or {}
            sub_confs = [it.get("confidence")] + [v.get("confidence") for v in obj.values() if isinstance(v, dict)]
            c = _min_conf(sub_confs)
            out.lines.append({
                "invoice_id": row["invoice_id"], "line_no": l_i + 1,
                "description": _fmt(field_value(obj.get("Description"))), "product_code": _fmt(field_value(obj.get("ProductCode"))),
                "quantity": _fmt(field_value(obj.get("Quantity"))), "unit": _fmt(field_value(obj.get("Unit"))),
                "unit_price": _fmt(field_value(obj.get("UnitPrice"))), "amount": _fmt(field_value(obj.get("Amount"))),
                "confidence": _fmt(c), "review_status": status_for(c, threshold),
                "doc_ref": f"{source_file}#p{field_page(it) or page}", "source_file": source_file,
            })
    if not out.rows:
        out.review.append({"kind": "invoice", "source_file": source_file, "page": "", "record": "", "field": "documents", "value": "",
                           "confidence": "", "status": NEEDS_REVIEW, "notes": "no invoice document recognised", "reviewer_decision": "", "reviewer_value": ""})
    return out


# ---------------------------------------------------------------------------------------------
# lab assay / CoA tables -> lot_assays.csv
# ---------------------------------------------------------------------------------------------

UNIT_HDR = re.compile(r"^\s*(unit|units|uom|u\.o\.m\.?)\s*$", re.I)
SPEC_HDR = re.compile(r"\b(spec|specification|limit|limits|min|max|minimum|maximum|requirement|method|standard|range|lod|loq|mdl)\b", re.I)
LOT_HDR = re.compile(r"\b(lot|batch|sample)\b", re.I)
COMPONENT_HDR = re.compile(r"\b(element|elements|component|analyte|parameter|determinand|test|item|species|characteristic|property|constituent|impurity|name)\b", re.I)
VALUE_HDR = re.compile(r"\b(result|results|value|conc|concentration|content|found|measured|actual|assay|reading|typical|analysis)\b", re.I)
LOT_IN_TEXT = re.compile(r"\b(?:lot|batch)\s*(?:no\.?|number|id|#)?\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-_/\.]{1,40})", re.I)


class _WordIndex:
    """Min word confidence within a set of content spans (layout cells carry no confidence)."""

    def __init__(self, res: Dict[str, Any]) -> None:
        self.words = sorted(
            ((w["span"]["offset"], w["span"]["offset"] + w["span"]["length"], w.get("confidence")) for p in res.get("pages", []) or [] for w in p.get("words", []) or [] if w.get("span")),
            key=lambda t: t[0],
        )

    def conf(self, spans: Sequence[Dict[str, int]]) -> Optional[float]:
        vals = []
        for s in spans or []:
            a, b = s["offset"], s["offset"] + s["length"]
            vals.extend(c for (wa, wb, c) in self.words if wa < b and wb > a and c is not None)
        return min(vals) if vals else None


def _grid(table: Dict[str, Any]) -> Tuple[Dict[Tuple[int, int], Dict[str, Any]], int, int]:
    cells = {(c["rowIndex"], c["columnIndex"]): c for c in table.get("cells", [])}
    return cells, int(table.get("rowCount", 0)), int(table.get("columnCount", 0))


def _header_row(cells: Dict[Tuple[int, int], Dict[str, Any]], rows: int) -> int:
    hdr = [r for (r, _), c in cells.items() if c.get("kind") == "columnHeader"]
    return max(hdr) if hdr else 0


def _text(cells: Dict[Tuple[int, int], Dict[str, Any]], r: int, c: int) -> str:
    return (cells.get((r, c), {}).get("content") or "").replace(":selected:", "").replace(":unselected:", "").strip()


def classify_columns(headers: List[str]) -> Dict[str, Optional[int]]:
    roles: Dict[str, Optional[int]] = {"component": None, "value": None, "unit": None, "lot": None}
    for i, h in enumerate(headers):
        if UNIT_HDR.match(h):
            roles["unit"] = i if roles["unit"] is None else roles["unit"]
        elif SPEC_HDR.search(h):
            continue  # specification / limit / method columns are never the measured value
        elif LOT_HDR.search(h) and roles["lot"] is None:
            roles["lot"] = i
        elif COMPONENT_HDR.search(h) and roles["component"] is None:
            roles["component"] = i
        elif VALUE_HDR.search(h) and roles["value"] is None:
            roles["value"] = i
    return roles


def lot_from_text(content: str) -> Optional[str]:
    m = LOT_IN_TEXT.search(content or "")
    return m.group(1).rstrip(".") if m else None


def map_assay(
    response: Dict[str, Any],
    source_file: str,
    *,
    kind: str = "lab_assay",
    lot_id: Optional[str] = None,
    product: str = "",
    lab: str = "",
    threshold: float = 0.8,
) -> Mapped:
    res = result_of(response)
    out = Mapped()
    words = _WordIndex(res)
    doc_lot = lot_id or lot_from_text(res.get("content", ""))

    def emit(t_i: int, r: int, lot: Optional[str], component: str, raw_value: str, unit_hint: str, spans: list, page: int) -> None:
        val, unit_in_cell, qual = parse_number(raw_value)
        unit_raw = unit_hint or unit_in_cell
        unit, supported = normalise_unit(unit_raw) if unit_raw else ("", False)
        problems: List[str] = []
        if val is None:
            nd = re.fullmatch(r"\s*(n\.?d\.?|bdl|<\s*lod|<\s*loq|not detected)\s*", raw_value, re.I)
            problems.append(f"not detected ('{raw_value}'); enter detection limit or leave blank" if nd else f"non-numeric value '{raw_value}'")
        if qual == "<":
            problems.append(f"below detection limit ({raw_value}); value is the limit")
        elif qual == ">":
            problems.append(f"above range ({raw_value})")
        if not unit:
            problems.append("unit missing")
        elif not supported:
            problems.append(f"unit '{unit}' not supported by engine")
        if not lot:
            problems.append("lot_id not found (pass --lot-id)")
        if not product:
            problems.append("product not set (pass --product)")
        conf = words.conf(spans)
        problems += low_conf_note(conf, threshold)
        status = status_for(conf, threshold, problems)
        src = f"{source_file}#p{page} table{t_i + 1} r{r}"
        row = {
            "lot_id": lot or "UNKNOWN_LOT", "product": product, "lab": lab, "component": component,
            "value": _fmt(val), "unit": unit, "source": src,
            "review_status": status, "review_confidence": _fmt(conf), "review_notes": "; ".join(problems), "source_file": source_file,
        }
        out.rows.append(row)
        out.review.append({
            "kind": kind, "source_file": source_file, "page": page, "record": f"{row['lot_id']}/{component}", "field": "value",
            "value": raw_value, "confidence": _fmt(conf), "status": status, "notes": "; ".join(problems) or "", "reviewer_decision": "", "reviewer_value": "",
        })

    for t_i, table in enumerate(res.get("tables", []) or []):
        cells, n_rows, n_cols = _grid(table)
        if n_rows < 2 or n_cols < 2:
            continue
        page = int(((table.get("boundingRegions") or [{}])[0]).get("pageNumber", 1))
        h = _header_row(cells, n_rows)
        headers = [_text(cells, h, c) for c in range(n_cols)]
        roles = classify_columns(headers)

        if roles["component"] is not None and roles["value"] is not None:
            # long layout: one component per row
            hdr_unit = _unit_from_header(headers[roles["value"]])
            for r in range(h + 1, n_rows):
                comp = _text(cells, r, roles["component"])
                raw = _text(cells, r, roles["value"])
                if not comp or not raw:
                    continue
                unit = _text(cells, r, roles["unit"]) if roles["unit"] is not None else hdr_unit
                lot = _text(cells, r, roles["lot"]) if roles["lot"] is not None else doc_lot
                cell = cells.get((r, roles["value"]), {})
                emit(t_i, r, lot or doc_lot, comp, raw, unit, cell.get("spans", []), page)
            continue

        # wide layout: header cells are components, rows are lots/samples
        id_col = roles["lot"] if roles["lot"] is not None else 0
        comp_cols = []
        for c in range(n_cols):
            if c == id_col or not headers[c] or SPEC_HDR.search(headers[c]) or UNIT_HDR.match(headers[c]):
                continue
            numeric = sum(1 for r in range(h + 1, n_rows) if parse_number(_text(cells, r, c))[0] is not None)
            if numeric:
                comp_cols.append(c)
        if len(comp_cols) < 1:
            continue
        # optional units row directly under the header: e.g. "Unit | % | ppm | ppm"
        first = h + 1
        unit_row: Dict[int, str] = {}
        if first < n_rows and UNIT_HDR.match(_text(cells, first, id_col) or ""):
            unit_row = {c: _text(cells, first, c) for c in comp_cols}
            first += 1
        for r in range(first, n_rows):
            row_id = _text(cells, r, id_col)
            # a lot/batch/sample column wins; otherwise a non-numeric first cell; otherwise the document lot
            if roles["lot"] is not None:
                lot = row_id or doc_lot
            else:
                lot = row_id if row_id and parse_number(row_id)[0] is None else doc_lot
            for c in comp_cols:
                raw = _text(cells, r, c)
                if not raw:
                    continue
                comp = re.sub(r"\s*[\(\[].*?[\)\]]\s*", "", headers[c]).strip()
                unit = unit_row.get(c) or _unit_from_header(headers[c])
                emit(t_i, r, lot, comp, raw, unit, cells.get((r, c), {}).get("spans", []), page)

    if not out.rows:
        out.review.append({"kind": kind, "source_file": source_file, "page": "", "record": "", "field": "tables", "value": "",
                           "confidence": "", "status": NEEDS_REVIEW, "notes": "no component/value table recognised", "reviewer_decision": "", "reviewer_value": ""})
    return out


# ---------------------------------------------------------------------------------------------
# generic -> key/value triage
# ---------------------------------------------------------------------------------------------


def map_generic(response: Dict[str, Any], source_file: str, *, threshold: float = 0.8) -> Mapped:
    res = result_of(response)
    out = Mapped()
    for kv in res.get("keyValuePairs", []) or []:
        k = (kv.get("key") or {}).get("content", "").strip()
        v = (kv.get("value") or {}).get("content", "").strip()
        conf = kv.get("confidence")
        page = field_page(kv.get("key")) or 1
        st = status_for(conf, threshold, [] if v else ["empty value"])
        out.rows.append({"key": k, "value": v, "page": page, "review_status": st, "review_confidence": _fmt(conf), "review_notes": "", "source_file": source_file})
        out.review.append({"kind": "generic", "source_file": source_file, "page": page, "record": k, "field": "value", "value": v,
                           "confidence": _fmt(conf), "status": st, "notes": "", "reviewer_decision": "", "reviewer_value": ""})
    return out


# ---------------------------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------------------------


def _write_csv(path: Path, columns: List[str], rows: List[Dict[str, Any]]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    with open(path, "x", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def staging_dir(out: Path) -> Path:
    """<out>/staging, refusing to write into a dataset folder (one holding config.json)."""
    for p in (out, out / "staging"):
        if (p / "config.json").exists():
            raise SystemExit(f"{p} looks like a dataset folder (config.json present); staging must be separate")
    d = out / "staging"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_staging(out: Path, kind: str, mapped: Mapped, ts: str, metals: Sequence[str] = ()) -> Dict[str, Path]:
    d = staging_dir(out)
    paths: Dict[str, Path] = {}
    if kind == "invoice":
        cols = settlement_columns(metals) + REVIEW_COLUMNS
    elif kind in ("lab_assay", "coa"):
        cols = LOT_ASSAY_COLUMNS + REVIEW_COLUMNS
    else:
        cols = ["key", "value", "page"] + REVIEW_COLUMNS
    paths["staging"] = d / f"{kind}_{ts}.csv"
    _write_csv(paths["staging"], cols, mapped.rows)
    paths["review"] = d / f"{kind}_{ts}_review.csv"
    _write_csv(paths["review"], REVIEW_SHEET_COLUMNS, mapped.review)
    if kind == "invoice":
        paths["lines"] = d / f"{kind}_{ts}_lines.csv"
        _write_csv(paths["lines"], LINE_COLUMNS, mapped.lines)
    return paths


def map_response(kind: str, response: Dict[str, Any], source_file: str, a: argparse.Namespace) -> Mapped:
    if kind == "invoice":
        return map_invoice(response, source_file, threshold=a.threshold, counterparty_from=a.counterparty_from, metals=a.metals)
    if kind in ("lab_assay", "coa"):
        return map_assay(response, source_file, kind=kind, lot_id=a.lot_id, product=a.product, lab=a.lab, threshold=a.threshold)
    return map_generic(response, source_file, threshold=a.threshold)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Document Intelligence extraction into staging (never into data/).")
    ap.add_argument("files", nargs="+", type=Path, help="documents to analyze, or saved DI JSON with --from-json")
    ap.add_argument("--kind", choices=KINDS, required=True)
    ap.add_argument("--from-json", action="store_true", help="treat files as saved analyze responses; no Azure calls")
    ap.add_argument("--out", type=Path, default=Path("out/ingest"))
    ap.add_argument("--endpoint", default=os.environ.get("AZURE_DOCINTEL_ENDPOINT", ""), help="https://<resource>.cognitiveservices.azure.com")
    ap.add_argument("--auth", choices=["default", "interactive"], default="default")
    ap.add_argument("--client-id", default=None)
    ap.add_argument("--tenant-id", default=None)
    ap.add_argument("--api-version", default=DI_API_VERSION)
    ap.add_argument("--model", default="", help="override the model id (e.g. a custom CoA model)")
    ap.add_argument("--threshold", type=float, default=0.8, help="confidence below this -> NEEDS_REVIEW")
    ap.add_argument("--lot-id", default=None, help="lot id for assay/CoA documents (else detected from text)")
    ap.add_argument("--product", default="", help="product name as used in specs.csv")
    ap.add_argument("--lab", default="", help="lab name for lot_assays.lab")
    ap.add_argument("--counterparty-from", choices=["customer", "vendor"], default="customer",
                    help="settlements.counterparty from CustomerName (sales invoice we issued) or VendorName (self-billed/buyer-issued)")
    ap.add_argument("--metals", default="", help="comma list (e.g. Ni,Co) to add empty grade_/price_/payable_ columns")
    ap.add_argument("--timestamp", default="", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    a.metals = [m.strip() for m in a.metals.split(",") if m.strip()]
    ts = a.timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    headers = None
    if not a.from_json:
        if not a.endpoint:
            ap.error("--endpoint (or AZURE_DOCINTEL_ENDPOINT) is required unless --from-json")
        from ingest.docintel import di_headers

        headers = di_headers(a.auth, a.client_id, a.tenant_id)

    merged = Mapped()
    raw_dir = staging_dir(a.out) / "raw"
    for n, f in enumerate(a.files):
        if a.from_json:
            response = json.loads(f.read_text(encoding="utf-8"))
            source = response.get("_source_file") or f.name
        else:
            from ingest.azure_common import long_path
            from ingest.docintel import analyze

            with open(long_path(f), "rb") as fh:
                data = fh.read()
            model = a.model or MODEL_FOR_KIND[a.kind]
            print(f"analyzing {f.name} with {model} ...")
            response = analyze(a.endpoint, model, data, headers, api_version=a.api_version, features=["keyValuePairs"] if a.kind == "generic" else None)
            source = f.name
            raw_dir.mkdir(parents=True, exist_ok=True)
            response["_source_file"] = source
            raw_path = raw_dir / f"{a.kind}_{ts}_{n + 1:03d}_{Path(source).stem[:60]}.json"
            with open(raw_path, "x", encoding="utf-8") as fh:
                json.dump(response, fh, ensure_ascii=False, indent=1)
        m = map_response(a.kind, response, source, a)
        merged.rows += m.rows
        merged.review += m.review
        merged.lines += m.lines

    paths = write_staging(a.out, a.kind, merged, ts, a.metals)
    flagged = sum(1 for r in merged.rows if r.get("review_status") == NEEDS_REVIEW)
    print(f"{len(merged.rows)} staged rows ({flagged} NEEDS_REVIEW)")
    for k, p in paths.items():
        print(f"  {k}: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
