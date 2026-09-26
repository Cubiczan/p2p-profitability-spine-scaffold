# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# MARKDOWN ********************

# # Spine 02 - Silver and Gold build
#
# Runs the deterministic spine engine over the data contract in `Files/bronze/<DATASET>/`
# and writes `silver_*` and `gold_*` Delta tables plus an evidence pack to
# `Files/evidence/<DATASET>/`. The Fabric Data Agent and Power BI read the gold tables.

# PARAMETERS CELL ********************

WORKSPACE = "p2p-spine"
LAKEHOUSE = "lh_p2p_spine"
DATASET = "example"
ENGINE_SPEC = "git+https://github.com/icohangar-ops/p2p-profitability-spine-scaffold@v0.1.0"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", ENGINE_SPEC], check=True)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import json
import os
import re
import tempfile

from p2p_spine.evidence import dumps
from p2p_spine.fabric import write_tables
from p2p_spine.pipeline import build
from p2p_spine.report import render

_GUID = re.compile(r"^[0-9a-fA-F-]{36}$")
ROOT = f"abfss://{WORKSPACE}@onelake.dfs.fabric.microsoft.com/" + (LAKEHOUSE if _GUID.match(LAKEHOUSE) else f"{LAKEHOUSE}.Lakehouse")
SRC = f"{ROOT}/Files/bronze/{DATASET}"

local = tempfile.mkdtemp(prefix="spine_")
for f in notebookutils.fs.ls(SRC):  # noqa: F821 - provided by Fabric runtime
    if f.name.endswith((".csv", ".json")):
        with open(os.path.join(local, f.name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(r.value for r in spark.read.text(f.path, wholetext=True).collect()))  # noqa: F821

result = build(local)
print(json.dumps(result["evidence_pack"]["row_counts"], indent=2))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

counts = write_tables(spark, result["tables"], ROOT)  # noqa: F821
out = f"{ROOT}/Files/evidence/{DATASET}"
notebookutils.fs.put(f"{out}/evidence_pack.json", dumps(result["evidence_pack"]), True)  # noqa: F821
notebookutils.fs.put(f"{out}/report.md", render(result, f"Spine report - {DATASET}"), True)  # noqa: F821
counts

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Check the question register

# CELL ********************

display(spark.read.format("delta").load(f"{ROOT}/Tables/gold_question_register").select("question_id", "status", "computed_answer"))  # noqa: F821

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
