"""Reproducible, isolated GPS index benchmark for a dedicated staging database.

Required environment variables:
    GPS_INDEX_STAGING_DATABASE_URL=postgresql://.../fieldops_index_staging
    GPS_INDEX_STAGING_CONFIRM=I_CONFIRM_DEDICATED_STAGING

Optional:
    GPS_INDEX_BENCHMARK_ROWS=100000       # initial synthetic GPS rows (default)
    GPS_INDEX_INSERT_BENCHMARK_ROWS=20000 # rows inserted per write benchmark (default)

The script accepts only the dedicated database named ``fieldops_index_staging``.
It creates and drops only a uniquely named scratch schema inside that database.
It never creates, drops, or alters a database, and it never touches application tables.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


TENANTS = 100
TECHNICIANS_PER_TENANT = 200
JOBS_PER_TENANT = 2_000
TOTAL_TECHNICIANS = TENANTS * TECHNICIANS_PER_TENANT
DEFAULT_BENCHMARK_ROWS = 100_000
DEFAULT_INSERT_BENCHMARK_ROWS = 20_000
MAX_BENCHMARK_ROWS = 10_000_000
INSERT_CHUNK_SIZE = 2_000
SEED = 110112

STAGING_DATABASE_NAME = "fieldops_index_staging"
STAGING_CONFIRMATION = "I_CONFIRM_DEDICATED_STAGING"

IDX_HISTORY = "candidate_gps_pings_tenant_technician_timestamp"
IDX_LATEST = "candidate_gps_pings_tenant_job_technician_latest"

HISTORY_SQL = """
SELECT id, technician_id, job_id, latitude, longitude, timestamp
FROM {schema}.gps_pings
WHERE tenant_id = :tenant_id AND technician_id = :technician_id
ORDER BY timestamp ASC
"""
LATEST_SQL = """
SELECT id, technician_id, job_id, latitude, longitude, timestamp, created_at
FROM {schema}.gps_pings
WHERE tenant_id = :tenant_id AND job_id = :job_id
  AND technician_id = :technician_id
ORDER BY timestamp DESC, created_at DESC
LIMIT 1
"""


def _positive_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer; received {raw!r}.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}; received {value}.")
    return value


def benchmark_row_count() -> int:
    """Return the requested initial dataset size, ensuring every tech has a ping."""
    return _positive_int_env(
        "GPS_INDEX_BENCHMARK_ROWS",
        DEFAULT_BENCHMARK_ROWS,
        minimum=TOTAL_TECHNICIANS,
        maximum=MAX_BENCHMARK_ROWS,
    )


def insert_benchmark_row_count() -> int:
    return _positive_int_env(
        "GPS_INDEX_INSERT_BENCHMARK_ROWS",
        DEFAULT_INSERT_BENCHMARK_ROWS,
        minimum=1,
        maximum=MAX_BENCHMARK_ROWS,
    )


def require_staging_url() -> URL:
    raw = os.getenv("GPS_INDEX_STAGING_DATABASE_URL", "").strip()
    if not raw:
        raise RuntimeError(
            "Set GPS_INDEX_STAGING_DATABASE_URL to the dedicated fieldops_index_staging database."
        )

    try:
        url = make_url(raw)
    except Exception as exc:
        raise RuntimeError("GPS_INDEX_STAGING_DATABASE_URL is not a valid SQLAlchemy URL.") from exc

    if url.get_backend_name() != "postgresql":
        raise RuntimeError("Only PostgreSQL staging targets are accepted.")

    # Strict allow-list: this prevents accidentally pointing the benchmark at any
    # application, baseline, temporary, or unrelated database, local or remote.
    if url.database != STAGING_DATABASE_NAME:
        raise RuntimeError(
            f"Refusing database {url.database!r}; only {STAGING_DATABASE_NAME!r} is allowed."
        )

    host = (url.host or "").lower()
    if not host:
        raise RuntimeError("The staging database URL must specify an explicit host.")

    if os.getenv("GPS_INDEX_STAGING_CONFIRM") != STAGING_CONFIRMATION:
        raise RuntimeError(
            f"Set GPS_INDEX_STAGING_CONFIRM={STAGING_CONFIRMATION} only after confirming "
            "the target is the isolated staging database."
        )
    return url


def insert_rows(conn, schema: str, rows: list[dict], table_name: str = "gps_pings") -> None:
    if table_name not in {"gps_pings", "gps_pings_write_probe"}:
        raise ValueError(f"Unsupported benchmark table: {table_name}")
    conn.execute(
        text(
            f"""INSERT INTO {schema}.{table_name}
            (id, tenant_id, technician_id, job_id, latitude, longitude, timestamp, created_at)
            VALUES (:id, :tenant_id, :technician_id, :job_id, :latitude, :longitude, :timestamp, :created_at)"""
        ),
        rows,
    )


def explain(conn, sql: str, params: dict) -> dict:
    result = conn.execute(
        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql), params
    ).scalar_one()[0]
    return {
        "execution_ms": result.get("Execution Time"),
        "planning_ms": result.get("Planning Time"),
        "plan": result["Plan"],
    }


def summarize_nodes(node: dict, out: list[dict]) -> None:
    out.append(
        {
            "node_type": node.get("Node Type"),
            "relation": node.get("Relation Name"),
            "index": node.get("Index Name"),
            "actual_rows": node.get("Actual Rows"),
            "loops": node.get("Actual Loops"),
            "shared_hit_blocks": node.get("Shared Hit Blocks", 0),
            "shared_read_blocks": node.get("Shared Read Blocks", 0),
            "temp_read_blocks": node.get("Temp Read Blocks", 0),
            "temp_written_blocks": node.get("Temp Written Blocks", 0),
            "rows_removed_by_filter": node.get("Rows Removed by Filter", 0),
        }
    )
    for child in node.get("Plans", []):
        summarize_nodes(child, out)


def measured_plan(conn, schema: str, kind: str, params: dict) -> dict:
    sql = HISTORY_SQL if kind == "history" else LATEST_SQL
    result = explain(conn, sql.format(schema=schema), params)
    nodes: list[dict] = []
    summarize_nodes(result.pop("plan"), nodes)
    result["nodes"] = nodes
    return result


def index_size(conn, schema: str, index_name: str) -> int | None:
    return conn.execute(
        text("SELECT pg_relation_size(to_regclass(:qualified))"),
        {"qualified": f"{schema}.{index_name}"},
    ).scalar_one()


def insert_benchmark(engine, schema: str, table_name: str, start_seq: int, count: int) -> dict:
    """Measure a fixed insert batch under the current candidate-index state."""
    rng = random.Random(SEED + start_seq)
    base = datetime.now(timezone.utc)

    started = time.perf_counter()
    with engine.connect() as conn:
        wal_before = conn.execute(text("SELECT pg_current_wal_lsn()::text")).scalar_one()

    with engine.begin() as conn:
        for batch_start in range(0, count, INSERT_CHUNK_SIZE):
            rows: list[dict] = []
            batch_end = min(batch_start + INSERT_CHUNK_SIZE, count)
            for offset in range(batch_start, batch_end):
                ti = rng.randrange(TENANTS)
                tech = rng.randrange(TECHNICIANS_PER_TENANT)
                job = rng.randrange(JOBS_PER_TENANT)
                rows.append(
                    {
                        "id": f"bench-{start_seq + offset}",
                        "tenant_id": f"tenant-{ti:03d}",
                        "technician_id": f"tech-{ti:03d}-{tech:04d}",
                        "job_id": f"job-{ti:03d}-{job:05d}",
                        "latitude": 12.9 + (rng.random() - 0.5) / 100,
                        "longitude": 77.6 + (rng.random() - 0.5) / 100,
                        "timestamp": base + timedelta(seconds=offset),
                        "created_at": base
                        + timedelta(seconds=offset, microseconds=rng.randrange(1_000_000)),
                    }
                )
            insert_rows(conn, schema, rows, table_name=table_name)

    elapsed = time.perf_counter() - started
    with engine.connect() as conn:
        wal_after = conn.execute(text("SELECT pg_current_wal_lsn()::text")).scalar_one()
        try:
            wal_bytes = conn.execute(
                text("SELECT pg_wal_lsn_diff(:after, :before)::bigint"),
                {"after": wal_after, "before": wal_before},
            ).scalar_one()
        except Exception:
            wal_bytes = None

    return {
        "rows": count,
        "elapsed_seconds": elapsed,
        "rows_per_second": count / elapsed if elapsed else None,
        "wal_bytes_lsn_delta": wal_bytes,
        "wal_lsn_before": wal_before,
        "wal_lsn_after": wal_after,
    }


def seed_initial_dataset(engine, schema: str, total_rows: int) -> None:
    """Seed exactly total_rows, distributed evenly across all synthetic technicians."""
    rows_per_tech, extra_rows = divmod(total_rows, TOTAL_TECHNICIANS)
    rng = random.Random(SEED)
    start_time = datetime.now(timezone.utc) - timedelta(seconds=total_rows)
    chunk: list[dict] = []
    seq = 0

    for ti in range(TENANTS):
        for tech in range(TECHNICIANS_PER_TENANT):
            tech_id = f"tech-{ti:03d}-{tech:04d}"
            technician_number = ti * TECHNICIANS_PER_TENANT + tech
            technician_rows = rows_per_tech + (1 if technician_number < extra_rows else 0)
            for ping_number in range(technician_rows):
                job = (tech * 13 + ping_number // 10) % JOBS_PER_TENANT
                timestamp = start_time + timedelta(seconds=seq)
                chunk.append(
                    {
                        "id": f"seed-{seq}",
                        "tenant_id": f"tenant-{ti:03d}",
                        "technician_id": tech_id,
                        "job_id": f"job-{ti:03d}-{job:05d}",
                        "latitude": 12.9 + (rng.random() - 0.5) / 100,
                        "longitude": 77.6 + (rng.random() - 0.5) / 100,
                        "timestamp": timestamp,
                        "created_at": timestamp + timedelta(microseconds=rng.randrange(1_000_000)),
                    }
                )
                seq += 1
                if len(chunk) >= INSERT_CHUNK_SIZE:
                    with engine.begin() as conn:
                        insert_rows(conn, schema, chunk)
                    chunk.clear()

    if chunk:
        with engine.begin() as conn:
            insert_rows(conn, schema, chunk)


def main() -> int:
    try:
        url = require_staging_url()
        total_rows = benchmark_row_count()
        insert_rows_per_state = insert_benchmark_row_count()
    except Exception as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    engine = create_engine(url, pool_pre_ping=True)
    schema = "gps_idx_bench_" + uuid.uuid4().hex[:12]
    schema_created = False
    report = {
        "status": "RUNNING",
        "operation": "gps_index_staging_validation",
        "database": {"host": url.host, "database": url.database},
        "schema": schema,
        "dataset": {
            "tenants": TENANTS,
            "technicians_per_tenant": TECHNICIANS_PER_TENANT,
            "jobs_per_tenant": JOBS_PER_TENANT,
            "total_technicians": TOTAL_TECHNICIANS,
            "initial_rows": total_rows,
            "average_pings_per_technician": total_rows / TOTAL_TECHNICIANS,
            "insert_benchmark_rows_per_state": insert_rows_per_state,
            "seed": SEED,
        },
        "candidate_results": {},
        "insert_benchmarks": {},
        "cleanup": "Only the uniquely generated scratch schema is dropped; the database is retained.",
    }

    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL statement_timeout = '15min'"))
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            schema_created = True
            conn.execute(
                text(
                    f"""CREATE TABLE {schema}.gps_pings (
                        id varchar(64) PRIMARY KEY,
                        tenant_id varchar(50) NOT NULL,
                        job_id varchar(64) NOT NULL,
                        technician_id varchar(64) NOT NULL,
                        latitude double precision NOT NULL,
                        longitude double precision NOT NULL,
                        timestamp timestamptz NOT NULL,
                        created_at timestamptz NOT NULL
                    )"""
                )
            )
            # Separate write probe prevents insert benchmarks from changing the
            # row count used for the query-plan comparisons on gps_pings.
            conn.execute(
                text(
                    f"CREATE TABLE {schema}.gps_pings_write_probe "
                    f"(LIKE {schema}.gps_pings INCLUDING ALL)"
                )
            )

        seed_initial_dataset(engine, schema, total_rows)

        with engine.begin() as conn:
            conn.execute(text(f"ANALYZE {schema}.gps_pings"))
            params = {
                "tenant_id": "tenant-042",
                "technician_id": "tech-042-0100",
                "job_id": "job-042-01300",
            }
            expected = conn.execute(
                text(
                    f"""SELECT count(*) FROM {schema}.gps_pings
                    WHERE tenant_id=:tenant_id AND technician_id=:technician_id"""
                ),
                params,
            ).scalar_one()
            cross_tenant = conn.execute(
                text(
                    f"""SELECT count(*) FROM {schema}.gps_pings
                    WHERE technician_id=:technician_id AND tenant_id <> :tenant_id"""
                ),
                params,
            ).scalar_one()
            latest_expected = conn.execute(
                text(
                    f"""SELECT id FROM {schema}.gps_pings
                    WHERE tenant_id=:tenant_id AND job_id=:job_id AND technician_id=:technician_id
                    ORDER BY timestamp DESC, created_at DESC LIMIT 1"""
                ),
                params,
            ).scalar_one_or_none()
            if expected < 1 or latest_expected is None:
                raise AssertionError("Seed distribution did not populate the selected technician/job query.")
            if cross_tenant != 0:
                raise AssertionError("Synthetic technician identifiers unexpectedly crossed tenant boundaries.")

            report["correctness"] = {
                "history_rows_expected": expected,
                "cross_tenant_rows_for_same_technician_id": cross_tenant,
                "latest_row_expected": latest_expected,
            }
            baseline_history_rows = conn.execute(
                text(HISTORY_SQL.format(schema=schema)), params
            ).mappings().all()
            baseline_latest_id = conn.execute(
                text(LATEST_SQL.format(schema=schema)), params
            ).scalar_one_or_none()
            report["candidate_results"]["baseline"] = {
                "history": measured_plan(conn, schema, "history", params),
                "latest": measured_plan(conn, schema, "latest", params),
                "history_result_rows": len(baseline_history_rows),
                "latest_result_id": baseline_latest_id,
            }

        if len(baseline_history_rows) != expected or baseline_latest_id != latest_expected:
            raise AssertionError("Baseline query results did not match independently computed expected results.")

        states = [
            ("history_only", [IDX_HISTORY]),
            ("latest_only", [IDX_LATEST]),
            ("both", [IDX_HISTORY, IDX_LATEST]),
        ]
        for state_number, (state, indexes) in enumerate(states, start=1):
            with engine.begin() as conn:
                # Reset only candidate indexes created by this run in its scratch schema.
                conn.execute(text(f"DROP INDEX IF EXISTS {schema}.{IDX_HISTORY}"))
                conn.execute(text(f"DROP INDEX IF EXISTS {schema}.{IDX_LATEST}"))
                for index_name in indexes:
                    if index_name == IDX_HISTORY:
                        conn.execute(
                            text(
                                f"CREATE INDEX {IDX_HISTORY} ON {schema}.gps_pings "
                                "(tenant_id, technician_id, timestamp)"
                            )
                        )
                    else:
                        conn.execute(
                            text(
                                f"CREATE INDEX {IDX_LATEST} ON {schema}.gps_pings "
                                "(tenant_id, job_id, technician_id, timestamp DESC, created_at DESC)"
                            )
                        )

                conn.execute(text(f"ANALYZE {schema}.gps_pings"))
                history_rows = conn.execute(
                    text(HISTORY_SQL.format(schema=schema)), params
                ).mappings().all()
                latest_id = conn.execute(
                    text(LATEST_SQL.format(schema=schema)), params
                ).scalar_one_or_none()
                report["candidate_results"][state] = {
                    "history": measured_plan(conn, schema, "history", params),
                    "latest": measured_plan(conn, schema, "latest", params),
                    "history_result_rows": len(history_rows),
                    "latest_result_id": latest_id,
                    "table_bytes": conn.execute(
                        text("SELECT pg_total_relation_size(to_regclass(:r))"),
                        {"r": f"{schema}.gps_pings"},
                    ).scalar_one(),
                    "index_bytes": {
                        index_name: index_size(conn, schema, index_name)
                        for index_name in indexes
                    },
                    "all_candidate_index_bytes": conn.execute(
                        text(
                            """SELECT coalesce(sum(pg_relation_size(i.indexrelid)),0)
                            FROM pg_index i
                            JOIN pg_class t ON t.oid=i.indrelid
                            JOIN pg_namespace n ON n.oid=t.relnamespace
                            WHERE n.nspname=:schema AND t.relname='gps_pings'
                              AND i.indexrelid::regclass::text LIKE '%candidate_gps_pings%'"""
                        ),
                        {"schema": schema},
                    ).scalar_one(),
                }
                if len(history_rows) != expected or latest_id != latest_expected or cross_tenant != 0:
                    raise AssertionError(
                        "Candidate index state changed query results or tenant isolation."
                    )

        # Benchmark writes on a separate table. Each state starts empty and gets
        # the same number of inserts, so query-plan row counts stay identical.
        probe_history_idx = IDX_HISTORY + "_probe"
        probe_latest_idx = IDX_LATEST + "_probe"
        write_states = [
            ("baseline", []),
            ("history_only", [IDX_HISTORY]),
            ("latest_only", [IDX_LATEST]),
            ("both", [IDX_HISTORY, IDX_LATEST]),
        ]
        for state_number, (state, indexes) in enumerate(write_states):
            with engine.begin() as conn:
                conn.execute(text(f"TRUNCATE TABLE {schema}.gps_pings_write_probe"))
                conn.execute(text(f"DROP INDEX IF EXISTS {schema}.{probe_history_idx}"))
                conn.execute(text(f"DROP INDEX IF EXISTS {schema}.{probe_latest_idx}"))
                for index_name in indexes:
                    if index_name == IDX_HISTORY:
                        conn.execute(
                            text(
                                f"CREATE INDEX {probe_history_idx} "
                                f"ON {schema}.gps_pings_write_probe "
                                "(tenant_id, technician_id, timestamp)"
                            )
                        )
                    else:
                        conn.execute(
                            text(
                                f"CREATE INDEX {probe_latest_idx} "
                                f"ON {schema}.gps_pings_write_probe "
                                "(tenant_id, job_id, technician_id, timestamp DESC, created_at DESC)"
                            )
                        )
            report["insert_benchmarks"][state] = insert_benchmark(
                engine,
                schema,
                "gps_pings_write_probe",
                30_000_000 + state_number * insert_rows_per_state,
                insert_rows_per_state,
            )

        # pg_stat_wal is cluster-wide. Treat it as context, not isolated per-index WAL.
        with engine.connect() as conn:
            try:
                report["wal_stats"] = conn.execute(
                    text("SELECT wal_records, wal_fpi, wal_bytes FROM pg_stat_wal")
                ).mappings().one()
                report["wal_measurement_caveat"] = (
                    "pg_stat_wal is cluster-wide; isolate the staging PostgreSQL instance "
                    "during the run to compare deltas meaningfully."
                )
            except Exception as exc:
                report["wal_stats"] = {
                    "available": False,
                    "reason": str(exc).splitlines()[0],
                }

        report["status"] = "COMPLETED"
        print(json.dumps(report, indent=2, default=str))
        return 0
    finally:
        try:
            if schema_created:
                with engine.begin() as conn:
                    conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
