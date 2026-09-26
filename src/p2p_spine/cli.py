"""Command line: build gold tables, evidence pack and report from a data directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evidence import dumps
from .io import write_csv
from .pipeline import build
from .report import render


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="p2p-spine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="Build gold tables from a data-contract directory")
    b.add_argument("--data", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--title", default="Procurement-to-Profitability Diligence Report")
    q = sub.add_parser("ask", help="Print question-register answers as JSON")
    q.add_argument("--data", type=Path, required=True)
    q.add_argument("--id", default="")
    args = ap.parse_args(argv)

    result = build(args.data)
    if args.cmd == "build":
        out: Path = args.out
        for name, rows in result["tables"].items():  # type: ignore[union-attr]
            write_csv(out / "gold" / f"{name}.csv", rows)
        (out / "evidence_pack.json").write_text(dumps(result["evidence_pack"]), encoding="utf-8")
        (out / "report.md").write_text(render(result, args.title), encoding="utf-8")
        pack = result["evidence_pack"]
        print(json.dumps({"out": str(out), "status": pack["status"], "tables": pack["row_counts"], "findings": pack["finding_counts"]}, indent=2))  # type: ignore[index]
    else:
        rows = [q for q in result["tables"]["gold_question_register"] if not args.id or q["question_id"] == args.id]  # type: ignore[index]
        print(dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
