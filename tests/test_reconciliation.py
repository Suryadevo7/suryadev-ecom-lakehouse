"""Row-count reconciliation: every bronze row lands in exactly one place.

    count(bronze)  ==  count(silver.stg_<entity>)  +  count(silver.rejected_rows where entity = <entity>)

and, stronger than counts alone, the sets of _record_ids are disjoint and their union
is exactly the set of bronze _record_ids. Requires `make generate run` first.
"""

import os
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
BRONZE = Path(os.environ.get("BRONZE_PATH", ROOT / "data" / "bronze"))
DB = Path(os.environ.get("LAKEHOUSE_DB", ROOT / "data" / "lakehouse.duckdb"))
ENTITIES = ["customers", "products", "orders", "order_items", "events"]

_MISSING = not (BRONZE.exists() and DB.exists())
if _MISSING and os.environ.get("RECONCILE_STRICT") == "1":  # set by `make test` / CI: never skip silently
    raise RuntimeError(f"reconciliation inputs missing: {BRONZE} / {DB}")
pytestmark = pytest.mark.skipif(_MISSING, reason="bronze data or warehouse missing; run `make generate run` first")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB), read_only=True)
    yield c
    c.close()


def _bronze(entity: str) -> str:
    return f"read_parquet('{(BRONZE / entity).as_posix()}/*/*.parquet', hive_partitioning=true)"


@pytest.mark.parametrize("entity", ENTITIES)
def test_counts_reconcile(con, entity):
    bronze = con.execute(f"select count(*) from {_bronze(entity)}").fetchone()[0]
    silver = con.execute(f"select count(*) from silver.stg_{entity}").fetchone()[0]
    rejected = con.execute("select count(*) from silver.rejected_rows where entity = ?", [entity]).fetchone()[0]
    print(f"{entity:<12} bronze={bronze:>6} silver={silver:>6} rejected={rejected:>5}")
    assert bronze > 0
    assert bronze == silver + rejected


@pytest.mark.parametrize("entity", ENTITIES)
def test_record_ids_partition_exactly(con, entity):
    """No row is lost, duplicated, or present in both silver and rejected."""
    in_both, missing, extra = con.execute(f"""
        with b as (select _record_id as id from {_bronze(entity)}),
             s as (select _record_id as id from silver.stg_{entity}),
             r as (select record_id  as id from silver.rejected_rows where entity = '{entity}'),
             landed as (select id from s union all select id from r)
        select
            (select count(*) from s join r using (id)),
            (select count(*) from b anti join landed using (id)),
            (select count(*) - count(distinct id) from landed)
                + (select count(*) from landed anti join b using (id))
    """).fetchone()
    assert (in_both, missing, extra) == (0, 0, 0)


def test_every_dirt_type_was_quarantined(con):
    """The generator injects each kind of dirt; silver must catch every kind."""
    reasons = {r for (r,) in con.execute("select distinct reject_reason from silver.rejected_rows").fetchall()}
    assert {
        "duplicate",
        "null_required_field",
        "invalid_date",
        "negative_price",
        "invalid_quantity",
        "invalid_event_type",
        "orphan_fk",
    } <= reasons


def test_late_arriving_orders_survive(con):
    """Late data is valid data: it must reach silver, not the quarantine."""
    late = con.execute("select count(*) from silver.stg_orders where is_late_arriving").fetchone()[0]
    assert late > 0
