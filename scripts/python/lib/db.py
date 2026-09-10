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


def amend_meta(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    script: str,
    source_urls: list[str] | None = None,
    column_updates: dict[str, str] | None = None,
) -> None:
    """Merge a later script's contribution into a table another script produced.

    record_meta is DELETE-then-INSERT, so a script that APPENDS to an
    existing table must not call it directly -- that throws away the
    producing script's provenance. This keeps what is there, appends
    `script` to the chain, unions in any new source URLs, applies
    `column_updates` (each value is appended to the existing description
    for that column, or added outright if the column is new), and recounts
    row_count from the table itself so the stamp cannot drift from reality.

    Idempotent: amending scripts get rerun, and a chain reading
    "02 -> 03d -> 03d" with the same note appended three times would be
    its own kind of stale. Re-amending refreshes the timestamp and the
    count without duplicating the chain entry or the notes.
    """
    prior = con.execute(
        "SELECT script, source_urls, column_descriptions FROM _meta WHERE table_name = ?",
        [table_name],
    ).fetchone()
    prior_script, prior_urls, prior_cols = prior if prior else ("", "[]", "{}")

    urls = json.loads(prior_urls)
    for u in source_urls or []:
        if u not in urls:
            urls.append(u)

    cols = json.loads(prior_cols)
    for col, note in (column_updates or {}).items():
        if col not in cols:
            cols[col] = note
        elif note not in cols[col]:
            cols[col] = f"{cols[col]} {note}"

    chain = [s for s in prior_script.split(" -> ") if s]
    if script not in chain:
        chain.append(script)

    row_count = con.execute(f'SELECT count(*) FROM "{table_name}"').fetchone()[0]
    record_meta(
        con,
        table_name,
        script=" -> ".join(chain),
        source_urls=urls,
        column_descriptions=cols,
        row_count=row_count,
    )
