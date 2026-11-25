"""Unit tests for the synthetic data generator (src/generate.py)."""

import hashlib
from pathlib import Path

import duckdb
import pytest

import generate

ROWS = 500


def _scan(root: Path, entity: str) -> str:
    return f"read_parquet('{(root / entity).as_posix()}/*/*.parquet', hive_partitioning=true)"


def _q(root: Path, sql: str):
    """Run SQL where {customers}, {orders}, ... are replaced by Parquet scans."""
    return duckdb.sql(sql.format(**{e: _scan(root, e) for e in generate.ENTITIES})).fetchall()


def _digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*.parquet"))
    }


@pytest.fixture(scope="module")
def bronze(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("bronze")
    generate.generate(ROWS, seed=7, out=out)
    return out


# ---- determinism ------------------------------------------------------------
def test_same_seed_is_byte_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate.generate(ROWS, seed=123, out=a)
    generate.generate(ROWS, seed=123, out=b)
    assert _digest(a) == _digest(b)
    assert len(_digest(a)) > 5


def test_different_seed_differs(tmp_path):
    generate.generate(ROWS, seed=1, out=tmp_path / "a")
    generate.generate(ROWS, seed=2, out=tmp_path / "b")
    assert _digest(tmp_path / "a") != _digest(tmp_path / "b")


def test_rerun_overwrites_instead_of_appending(tmp_path):
    generate.generate(ROWS, seed=5, out=tmp_path)
    first = _digest(tmp_path)
    generate.generate(ROWS, seed=5, out=tmp_path)
    assert _digest(tmp_path) == first


# ---- row counts / CLI -------------------------------------------------------
@pytest.mark.parametrize("rows", [50, 500, 2000])
def test_row_count_arg_controls_orders(tmp_path, rows):
    generate.generate(rows, seed=1, out=tmp_path)
    (distinct_orders,) = _q(tmp_path, "select count(distinct order_id) from {orders}")[0]
    assert distinct_orders == rows


def test_cli_entrypoint(tmp_path, capsys):
    generate.main(["--rows", "100", "--seed", "3", "--out", str(tmp_path)])
    assert "bronze written" in capsys.readouterr().out
    for e in generate.ENTITIES:
        assert any((tmp_path / e).rglob("*.parquet")), e


def test_rejects_non_positive_rows(tmp_path):
    with pytest.raises(ValueError):
        generate.generate(0, seed=1, out=tmp_path)


# ---- layout / schema --------------------------------------------------------
EXPECTED_COLUMNS = {
    "customers": ["_record_id", "customer_id", "email", "first_name", "last_name", "country", "signup_date"],
    "products": ["_record_id", "product_id", "name", "category", "price"],
    "orders": ["_record_id", "order_id", "customer_id", "order_ts", "status"],
    "order_items": ["_record_id", "order_item_id", "order_id", "product_id", "quantity", "unit_price"],
    "events": ["_record_id", "event_id", "session_id", "customer_id", "product_id", "event_type", "event_ts"],
}


@pytest.mark.parametrize("entity", generate.ENTITIES)
def test_schema(bronze, entity):
    cols = [r[0] for r in duckdb.sql(f"describe select * from {_scan(bronze, entity)}").fetchall()]
    assert cols == EXPECTED_COLUMNS[entity] + ["ingest_date"]


@pytest.mark.parametrize("entity", generate.ENTITIES)
def test_hive_partitioned_by_date(bronze, entity):
    parts = [p for p in (bronze / entity).iterdir() if p.is_dir()]
    assert parts and all(p.name.startswith("ingest_date=") for p in parts)
    for p in parts:
        generate.np.datetime64(p.name.split("=", 1)[1])  # parses as a date


@pytest.mark.parametrize("entity", generate.ENTITIES)
def test_record_id_unique(bronze, entity):
    n, d = _q(bronze, f"select count(*), count(distinct _record_id) from {{{entity}}}")[0]
    assert n == d


# ---- injected dirt ----------------------------------------------------------
@pytest.mark.parametrize("entity,pk", [
    ("customers", "customer_id"), ("products", "product_id"), ("orders", "order_id"),
    ("order_items", "order_item_id"), ("events", "event_id"),
])
def test_has_duplicates(bronze, entity, pk):
    n, d = _q(bronze, f"select count(*), count(distinct {pk}) from {{{entity}}}")[0]
    assert n > d


def test_has_nulls(bronze):
    assert _q(bronze, "select count(*) from {customers} where email is null")[0][0] > 0
    assert _q(bronze, "select count(*) from {orders} where customer_id is null")[0][0] > 0
    assert _q(bronze, "select count(*) from {events} where session_id is null")[0][0] > 0


def test_has_bad_dates(bronze):
    for entity, col in [("customers", "signup_date"), ("orders", "order_ts"), ("events", "event_ts")]:
        bad = _q(bronze, f"select count(*) from {{{entity}}} "
                         f"where try_cast({col} as timestamp) is null or {col} >= '2026'")[0][0]
        assert bad > 0, entity


def test_has_negative_prices_and_bad_quantities(bronze):
    assert _q(bronze, "select count(*) from {products} where price < 0")[0][0] > 0
    assert _q(bronze, "select count(*) from {order_items} where unit_price < 0")[0][0] > 0
    assert _q(bronze, "select count(*) from {order_items} where quantity <= 0")[0][0] > 0


def test_has_orphan_foreign_keys(bronze):
    assert _q(bronze, "select count(*) from {orders} o where o.customer_id is not null "
                      "and o.customer_id not in (select customer_id from {customers})")[0][0] > 0
    assert _q(bronze, "select count(*) from {order_items} i "
                      "where i.product_id not in (select product_id from {products})")[0][0] > 0


def test_has_invalid_and_messy_event_types(bronze):
    funnel = "('page_view','product_view','add_to_cart','checkout','purchase')"
    assert _q(bronze, f"select count(*) from {{events}} where lower(trim(event_type)) not in {funnel}")[0][0] > 0
    assert _q(bronze, f"select count(*) from {{events}} where event_type not in {funnel} "
                      f"and lower(trim(event_type)) in {funnel}")[0][0] > 0


def test_has_late_arriving_records(bronze):
    # business date earlier than the partition it landed in by more than a day
    late = _q(bronze, "select count(*) from {orders} "
                      "where try_cast(order_ts as timestamp)::date < ingest_date - 1")[0][0]
    assert late > 0
    late_dim = _q(bronze, "select count(*) from {customers} where ingest_date > date '2025-01-04'")[0][0]
    assert late_dim > 0
