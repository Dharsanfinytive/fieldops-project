"""Safety and configuration tests for the GPS index staging benchmark."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_gps_indexes_staging.py"
SPEC = importlib.util.spec_from_file_location("gps_index_benchmark", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def set_target(monkeypatch, *, database="fieldops_index_staging", host="localhost", scheme="postgresql"):
    formatted_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    monkeypatch.setenv(
        "GPS_INDEX_STAGING_DATABASE_URL",
        f"{scheme}://postgres:example@{formatted_host}:5432/{database}",
    )
    monkeypatch.setenv("GPS_INDEX_STAGING_CONFIRM", "I_CONFIRM_DEDICATED_STAGING")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_allows_dedicated_staging_database_on_localhost(monkeypatch, host):
    set_target(monkeypatch, host=host)
    url = benchmark.require_staging_url()
    assert url.database == "fieldops_index_staging"


def test_allows_dedicated_staging_database_on_remote_host(monkeypatch):
    set_target(monkeypatch, host="staging-db.internal")
    assert benchmark.require_staging_url().database == "fieldops_index_staging"


@pytest.mark.parametrize(
    "database",
    ["fieldops", "fieldops_baseline", "fieldops_temp", "postgres", "other_database"],
)
def test_rejects_every_database_except_dedicated_staging(monkeypatch, database):
    set_target(monkeypatch, database=database)
    with pytest.raises(RuntimeError, match="only 'fieldops_index_staging' is allowed"):
        benchmark.require_staging_url()


def test_rejects_missing_confirmation(monkeypatch):
    monkeypatch.setenv(
        "GPS_INDEX_STAGING_DATABASE_URL",
        "postgresql://postgres:example@localhost:5432/fieldops_index_staging",
    )
    monkeypatch.delenv("GPS_INDEX_STAGING_CONFIRM", raising=False)
    with pytest.raises(RuntimeError, match="GPS_INDEX_STAGING_CONFIRM"):
        benchmark.require_staging_url()


def test_rejects_non_postgresql_target(monkeypatch):
    set_target(monkeypatch, scheme="sqlite")
    with pytest.raises(RuntimeError, match="Only PostgreSQL"):
        benchmark.require_staging_url()


def test_rejects_missing_database_url(monkeypatch):
    monkeypatch.delenv("GPS_INDEX_STAGING_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="GPS_INDEX_STAGING_DATABASE_URL"):
        benchmark.require_staging_url()


def test_default_dataset_is_100k_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("GPS_INDEX_BENCHMARK_ROWS", raising=False)
    assert benchmark.benchmark_row_count() == 100_000
    monkeypatch.setenv("GPS_INDEX_BENCHMARK_ROWS", "500000")
    assert benchmark.benchmark_row_count() == 500_000


def test_dataset_requires_at_least_one_ping_per_synthetic_technician(monkeypatch):
    monkeypatch.setenv("GPS_INDEX_BENCHMARK_ROWS", "19999")
    with pytest.raises(RuntimeError, match="must be between 20000"):
        benchmark.benchmark_row_count()


def test_rejects_invalid_dataset_size(monkeypatch):
    monkeypatch.setenv("GPS_INDEX_BENCHMARK_ROWS", "not-a-number")
    with pytest.raises(RuntimeError, match="must be an integer"):
        benchmark.benchmark_row_count()
