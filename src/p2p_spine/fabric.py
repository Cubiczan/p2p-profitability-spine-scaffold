"""Microsoft Fabric helpers: write gold tables to a Lakehouse as Delta tables.

Runs inside a Fabric PySpark notebook. Uses OneLake ABFS paths so the notebook
does not need a default lakehouse attached."""

from __future__ import annotations

import json
import math
from typing import Dict, List


def onelake_root(workspace: str, lakehouse: str) -> str:
    return f"abfss://{workspace}@onelake.dfs.fabric.microsoft.com/{lakehouse}.Lakehouse"


def _normalise(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Uniform columns; values as str/float/None so Spark infers stable schemas."""
    cols: List[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    out: List[Dict[str, object]] = []
    for r in rows:
        row: Dict[str, object] = {}
        for c in cols:
            v = r.get(c)
            if isinstance(v, bool):
                v = str(v)
            elif isinstance(v, int):
                v = float(v)
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                v = None
            elif isinstance(v, (list, dict)):
                v = json.dumps(v)
            elif v is not None and not isinstance(v, (float, str)):
                v = str(v)
            row[c] = v
        out.append(row)
    return out


def write_tables(spark, tables: Dict[str, List[Dict[str, object]]], root: str) -> Dict[str, int]:  # type: ignore[no-untyped-def]
    """Overwrite each table as Delta under <root>/Tables/<name>. Returns row counts."""
    from pyspark.sql.types import DoubleType, StringType, StructField, StructType

    counts: Dict[str, int] = {}
    for name, rows in tables.items():
        norm = _normalise(rows)
        if not norm:
            continue
        fields = []
        for c in norm[0]:
            numeric = all(isinstance(r[c], float) or r[c] is None for r in norm) and any(r[c] is not None for r in norm)
            fields.append(StructField(c, DoubleType() if numeric else StringType(), True))
        schema = StructType(fields)
        data = [tuple(r[f.name] if f.dataType == DoubleType() or r[f.name] is None else str(r[f.name]) for f in fields) for r in norm]
        df = spark.createDataFrame(data, schema)
        df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(f"{root}/Tables/{name}")
        counts[name] = len(norm)
    return counts
