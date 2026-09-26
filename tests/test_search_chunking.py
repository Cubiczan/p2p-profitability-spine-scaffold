"""Offline tests for search/build_index.py chunking, ids, schema and dry-run (no Azure)."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import search.build_index as bi  # noqa: E402
from ingest.azure_common import long_path  # noqa: E402

PARA = "The seller shall deliver the material FOB. " * 10  # ~430 chars


def test_split_text_sizes_and_overlap() -> None:
    text = "\n\n".join(f"Clause {i}. " + PARA for i in range(12))
    chunks = bi.split_text(text, size=1200, overlap=200)
    assert len(chunks) > 3
    assert all(len(c) <= 1200 for c in chunks)
    assert all(len(c) >= 400 for c in chunks[:-1])
    for a, b in zip(chunks, chunks[1:]):  # consecutive chunks share text
        assert b[:40] in a
    joined = " ".join(chunks)
    for i in range(12):
        assert f"Clause {i}." in joined


def test_split_text_short_and_empty() -> None:
    assert bi.split_text("  short \n text ", size=1200, overlap=200) == ["short \n text"]
    assert bi.split_text("   ") == []


def test_chunk_pages_keeps_page_numbers() -> None:
    pages = [(1, "Page one. " * 200), (2, ""), (3, "Clause 7.2 Payables: 93.5% of LME Ni.")]
    chunks = bi.chunk_pages(pages, source_path="contracts/offtake.pdf", folder="contracts", doc_type="pdf", sha256="ab" * 32, last_modified="2026-01-01T00:00:00Z")
    assert {c.page for c in chunks} == {1, 3}
    assert [c.chunk_no for c in chunks] == list(range(len(chunks)))
    last = chunks[-1]
    assert last.page == 3 and "Clause 7.2" in last.content and last.file_name == "offtake.pdf"
    assert len({c.id for c in chunks}) == len(chunks)
    import re

    assert all(re.fullmatch(r"[A-Za-z0-9_\-=]+", c.id) for c in chunks)  # valid Search key chars


def test_doc_id_stable_across_separators() -> None:
    assert bi.doc_id("a\\b\\c.pdf", 3) == bi.doc_id("a/b/C.pdf", 3)
    assert bi.doc_id("a/b.pdf", 0) != bi.doc_id("a/b.pdf", 1)


def test_index_schema() -> None:
    s = bi.index_schema("spine-evidence")
    fields = {f["name"]: f for f in s["fields"]}
    assert set(fields) == {"id", "content", "source_path", "file_name", "page", "doc_type", "folder", "sha256", "chunk_no", "last_modified"}
    assert fields["id"]["key"] is True
    assert fields["content"]["searchable"] is True
    assert fields["page"]["type"] == "Edm.Int32"
    cfg = s["semantic"]["configurations"][0]
    assert s["semantic"]["defaultConfiguration"] == cfg["name"]
    assert cfg["prioritizedFields"]["prioritizedContentFields"] == [{"fieldName": "content"}]


def test_long_path() -> None:
    short = long_path("x.txt")
    assert not short.startswith("\\\\?\\")
    if os.name == "nt":
        p = "C:\\" + "\\".join(["d" * 50] * 6) + "\\f.pdf"
        assert long_path(p).startswith("\\\\?\\C:\\")
        assert long_path("\\\\srv\\share\\" + "d" * 250).startswith("\\\\?\\UNC\\srv\\share")


def test_dry_run_end_to_end(tmp_path: Path) -> None:
    room = tmp_path / "room"
    (room / "contracts").mkdir(parents=True)
    (room / "contracts" / "terms.md").write_text("# Offtake\n\n" + PARA * 5, encoding="utf-8")
    (room / "notes.txt").write_text("Plant capacity note.", encoding="utf-8")
    (room / "blob.bin").write_bytes(b"\x00\x01")
    (room / "scan.png").write_bytes(b"\x89PNG")
    (room / "big.txt").write_text("x" * 3000, encoding="utf-8")
    out = tmp_path / "out"
    rc = bi.main(["--root", str(room), "--out", str(out), "--dry-run", "--max-mb", "0.0025"])
    assert rc == 0
    chunks = [json.loads(line) for line in (out / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    paths = {c["source_path"] for c in chunks}
    assert paths == {"contracts/terms.md", "notes.txt"}
    assert all(c["folder"] == "contracts" for c in chunks if c["source_path"] == "contracts/terms.md")
    assert all(len(c["sha256"]) == 64 and c["last_modified"].endswith("Z") for c in chunks)
    with open(out / "index_report.csv", newline="", encoding="utf-8") as fh:
        report = {r["source_path"]: r for r in csv.DictReader(fh)}
    assert report["blob.bin"]["status"] == "skipped"
    assert report["scan.png"]["status"] == "skipped" and "docintel" in report["scan.png"]["reason"]
    assert report["big.txt"]["status"] == "skipped" and "larger than" in report["big.txt"]["reason"]
    assert report["notes.txt"]["status"] == "indexed"


def test_ocr_fallback_for_images(tmp_path: Path) -> None:
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG")
    pages, method = bi.extract_pages(img, ocr=lambda data: [(1, "Certificate of analysis Lot 7")])
    assert method == "docintel-read" and pages[0][1].startswith("Certificate")
