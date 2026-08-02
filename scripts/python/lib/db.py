"""Shared DuckDB connection + _meta bookkeeping.

Every pipeline table gets one row in _meta recording provenance: which
script produced it, when, from which source URL(s), what each column
means, and how many rows it had at generation time. Query it directly:
`select * from _meta where table_name = 'missing_records'`.
"""
import datetime
import json
import pathlib

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "data" / "sp500_10k.duckdb"


def connect() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS _meta (
            table_name VARCHAR PRIMARY KEY,
            script VARCHAR,
            generated_at TIMESTAMP,
            source_urls VARCHAR,
            column_descriptions VARCHAR,
            row_count BIGINT
        )
    """)
    return con


def record_meta(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    script: str,
    source_urls: list[str],
    column_descriptions: dict[str, str],
    row_count: int,
) -> None:
    con.execute("DELETE FROM _meta WHERE table_name = ?", [table_name])
    con.execute(
        """
        INSERT INTO _meta
            (table_name, script, generated_at, source_urls, column_descriptions, row_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            table_name,
            script,
            datetime.datetime.now(),
            json.dumps(source_urls),
            json.dumps(column_descriptions),
            row_count,
        ],
    )
