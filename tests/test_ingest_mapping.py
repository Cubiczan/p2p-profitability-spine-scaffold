"""Offline tests for ingest/extract.py mapping (saved Document Intelligence responses, no Azure)."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingest.extract import (  # noqa: E402
    LOT_ASSAY_COLUMNS,
    NEEDS_REVIEW,
    OK,
    classify_columns,
    main,
    map_assay,
    map_invoice,
    normalise_unit,
    parse_number,
)
from p2p_spine.units import to_ppm  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_parse_number() -> None:
    assert parse_number("99.2") == (99.2, "", "")
    assert parse_number("< 0.01 %") == (0.01, "%", "<")
    assert parse_number("1,234.5") == (1234.5, "", "")
    assert parse_number("99,2") == (99.2, "", "")
    assert parse_number("800 ppm") == (800.0, "ppm", "")
    assert parse_number("n.d.")[0] is None
    assert parse_number("")[0] is None


def test_units_map_onto_engine_units() -> None:
    for raw in ["%", "wt%", "% w/w", "ppm", "mg/kg", "g/t", "µg/kg", "(ppm)"]:
        unit, ok = normalise_unit(raw)
        assert ok, raw
        to_ppm(1.0, unit)  # must not raise
    assert normalise_unit("cps")[1] is False


def test_classify_columns() -> None:
    roles = classify_columns(["Element", "Result", "Unit", "Method"])
    assert roles == {"component": 0, "value": 1, "unit": 2, "lot": None}
    roles = classify_columns(["Parameter", "Specification", "Result (ppm)"])
    assert roles["component"] == 0 and roles["value"] == 2


def test_long_table_lab_assay() -> None:
    m = map_assay(load("di_lab_assay.json"), "lab.pdf", product="Li2CO3", lab="Lab X", threshold=0.8)
    by = {r["component"]: r for r in m.rows}
    assert set(by) == {"purity", "Na", "Fe", "Mg", "Cl", "SO4"}
    assert all(r["lot_id"] == "LOT7" for r in m.rows)  # detected from "Lot No: LOT7"
    assert by["purity"]["value"] == "99.2" and by["purity"]["unit"] == "%"
    assert by["Cl"]["unit"] == "%"  # wt% normalised
    assert by["Na"]["review_status"] == OK
    assert by["Fe"]["review_status"] == NEEDS_REVIEW and "detection limit" in by["Fe"]["review_notes"]
    assert by["SO4"]["review_status"] == NEEDS_REVIEW and "confidence 0.55" in by["SO4"]["review_notes"]
    assert by["Na"]["source"].startswith("lab.pdf#p1")
    assert len(m.review) == len(m.rows)
    # staged rows are directly consumable by the engine's unit conversion
    for r in m.rows:
        if r["value"]:
            to_ppm(float(r["value"]), r["unit"])


def test_wide_table_coa() -> None:
    m = map_assay(load("di_coa_wide.json"), "coa.pdf", kind="coa", product="Mixed hydroxide")
    keyed = {(r["lot_id"], r["component"]): r for r in m.rows}
    assert keyed[("LOT-A", "Ni")]["value"] == "40.1" and keyed[("LOT-A", "Ni")]["unit"] == "%"
    assert keyed[("LOT-A", "Cu")]["unit"] == "ppm"
    assert ("LOT-A", "Spec max Cu") not in keyed  # spec columns are ignored
    assert keyed[("LOT-B", "Cu")]["review_status"] == NEEDS_REVIEW  # "n.d."
    assert "not detected" in keyed[("LOT-B", "Cu")]["review_notes"]


def test_missing_lot_and_product_flagged() -> None:
    resp = load("di_lab_assay.json")
    resp["analyzeResult"]["content"] = resp["analyzeResult"]["content"].replace("Lot No: LOT7", "")
    m = map_assay(resp, "lab.pdf")
    assert all(r["lot_id"] == "UNKNOWN_LOT" and r["review_status"] == NEEDS_REVIEW for r in m.rows)
    assert "product not set" in m.rows[0]["review_notes"]
    m = map_assay(resp, "lab.pdf", lot_id="LOT9", product="Li2CO3")
    assert {r["lot_id"] for r in m.rows} == {"LOT9"}


def test_invoice_mapping() -> None:
    m = map_invoice(load("di_invoice.json"), "inv.pdf", threshold=0.8, metals=["Ni", "Co"])
    assert len(m.rows) == 1
    row = m.rows[0]
    assert row["invoice_id"] == "INV-900" and row["invoice_date"] == "2026-06-30"
    assert row["counterparty"] == "Buyer Co"
    assert row["inv_subtotal"] == "84952.4" and row["inv_amount_due"] == "84952.4"
    assert "InvoiceTotal" in row["review_notes"]  # AmountDue fallback is disclosed
    assert row["doc_ref"] == "inv.pdf#p1" and row["settlement_id"] == "STG-INV-900"
    assert row["review_status"] == NEEDS_REVIEW  # SubTotal confidence 0.62
    assert "grade_Ni" in row and row["grade_Ni"] == ""
    sub = [r for r in m.review if r["field"].startswith("inv_subtotal")][0]
    assert sub["status"] == NEEDS_REVIEW
    assert [ln["review_status"] for ln in m.lines] == [OK, NEEDS_REVIEW]
    vendor = map_invoice(load("di_invoice.json"), "inv.pdf", counterparty_from="vendor")
    assert vendor.rows[0]["counterparty"] == "Plant Co"


def test_non_usd_currency_flagged() -> None:
    resp = load("di_invoice.json")
    resp["analyzeResult"]["documents"][0]["fields"]["InvoiceTotal"]["valueCurrency"]["currencyCode"] = "EUR"
    m = map_invoice(resp, "inv.pdf")
    assert "currency EUR" in m.rows[0]["review_notes"]


def test_cli_from_json_writes_staging_only(tmp_path: Path) -> None:
    rc = main(["--kind", "lab_assay", "--from-json", str(FIX / "di_lab_assay.json"), "--product", "Li2CO3", "--out", str(tmp_path), "--timestamp", "T1"])
    assert rc == 0
    staged = tmp_path / "staging" / "lab_assay_T1.csv"
    with open(staged, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0])[: len(LOT_ASSAY_COLUMNS)] == LOT_ASSAY_COLUMNS
    assert (tmp_path / "staging" / "lab_assay_T1_review.csv").exists()
    with pytest.raises(FileExistsError):  # never overwrite a staging file
        main(["--kind", "lab_assay", "--from-json", str(FIX / "di_lab_assay.json"), "--out", str(tmp_path), "--timestamp", "T1"])


def test_refuses_dataset_folder(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--kind", "coa", "--from-json", str(FIX / "di_coa_wide.json"), "--out", str(tmp_path)])
