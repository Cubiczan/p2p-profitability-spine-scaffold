# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# MARKDOWN ********************

# # Spine 01 - Bronze ingest
#
# Loads the data-contract files uploaded to `Files/bronze/<DATASET>/` into immutable
# `bronze_*` Delta tables. Every row keeps its source file, file SHA-256 and load time.
# Nothing is typed or coerced here: Bronze preserves source meaning.

# PARAMETERS CELL ********************

WORKSPACE = "p2p-spine"
LAKEHOUSE = "lh_p2p_spine"
DATASET = "example"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import hashlib
import re
from datetime import datetime, timezone

from pyspark.sql import functions as F

_GUID = re.compile(r"^[0-9a-fA-F-]{36}$")
ROOT = f"abfss://{WORKSPACE}@onelake.dfs.fabric.microsoft.com/" + (LAKEHOUSE if _GUID.match(LAKEHOUSE) else f"{LAKEHOUSE}.Lakehouse")
SRC = f"{ROOT}/Files/bronze/{DATASET}"
LOADED_AT = datetime.now(timezone.utc).isoformat()

files = [f for f in notebookutils.fs.ls(SRC) if f.name.endswith(".csv")]  # noqa: F821 - provided by Fabric runtime
print(f"{len(files)} CSV files in {SRC}")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

counts = {}
for f in files:
    raw = spark.sparkContext.binaryFiles(f.path).collect()[0][1]  # noqa: F821
    sha = hashlib.sha256(raw).hexdigest()
    df = (
        spark.read.option("header", True).option("multiLine", True).option("escape", '"').csv(f.path)  # noqa: F821
        .withColumn("_source_file", F.lit(f.name))
        .withColumn("_source_sha256", F.lit(sha))
        .withColumn("_dataset", F.lit(DATASET))
        .withColumn("_loaded_at", F.lit(LOADED_AT))
    )
    table = "bronze_" + f.name[:-4]
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(f"{ROOT}/Tables/{table}")
    counts[table] = df.count()
counts

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
