"""Deterministic synthetic e-commerce data generator -> bronze Parquet.

Writes raw (dirty) data for five entities to
    <out>/<entity>/ingest_date=YYYY-MM-DD/part-0.parquet

Bronze is partitioned by *ingestion* date (when the record landed), not by
business date. Late-arriving records therefore sit in a later partition than
their business timestamp, exactly as they would in a real landing zone.

Dirt injected on purpose (rates configurable via DirtConfig):
  * duplicates       - re-delivered rows (same primary key, later/same batch)
  * nulls            - missing required fields
  * bad dates        - unparseable / impossible / far-future timestamps
  * negative prices  - products.price, order_items.unit_price
  * bad quantities   - zero / negative order_items.quantity
  * orphans          - foreign keys pointing at nothing
  * invalid events   - unknown event_type values
  * messy casing     - " DELIVERED", "Page_View" (cleaned, not rejected)
  * late arrivals    - ingest_date days/weeks after the business date

Same --rows and --seed => byte-identical output. No wall-clock time is used.

Usage:
    python src/generate.py --rows 10000 --seed 42 --out data/bronze
"""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ENTITIES = ("customers", "products", "orders", "order_items", "events")

START = np.datetime64("2025-01-01")  # first business day of the simulated window
DAYS = 90  # length of the order/event window
SECONDS_PER_DAY = 86_400

COUNTRIES = np.array(["US", "GB", "DE", "FR", "IN", "CA", "AU", "NL", "ES", "BR"])
CATEGORIES = np.array(["electronics", "home", "apparel", "beauty", "sports", "books", "toys"])
ORDER_STATUSES = np.array(["placed", "shipped", "delivered", "cancelled", "returned"])
ORDER_STATUS_P = np.array([0.10, 0.15, 0.62, 0.08, 0.05])
FUNNEL = ("page_view", "product_view", "add_to_cart", "checkout", "purchase")
# probability of advancing from stage i to stage i+1
FUNNEL_ADVANCE = (0.60, 0.50, 0.60, 0.70)

BAD_DATES = np.array(["2025-02-30", "not-a-date", "31/12/2025", "", "2099-01-01T00:00:00", "0000-00-00", "2025-13-01"])
BAD_EVENT_TYPES = np.array(["unknown", "click??", "page-vue", "null"])


@dataclass(frozen=True)
class DirtConfig:
    duplicate_rate: float = 0.03
    null_rate: float = 0.02
    bad_date_rate: float = 0.02
    negative_price_rate: float = 0.02
    bad_quantity_rate: float = 0.01
    orphan_rate: float = 0.01
    invalid_event_rate: float = 0.01
    messy_case_rate: float = 0.03
    late_rate: float = 0.05


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _mask(rng: np.random.Generator, n: int, rate: float) -> np.ndarray:
    """Bernoulli(rate) mask; guarantees >= 1 hit when rate > 0 so small runs still carry dirt."""
    m = rng.random(n) < rate
    if rate > 0 and n > 0 and not m.any():
        m[rng.integers(n)] = True
    return m


def _ts_strings(day_offsets: np.ndarray, seconds: np.ndarray, base=START) -> np.ndarray:
    ts = base.astype("datetime64[s]") + day_offsets.astype("timedelta64[D]") + seconds.astype("timedelta64[s]")
    return np.datetime_as_string(ts, unit="s").astype(object)


def _date_strings(day_offsets: np.ndarray, base=START) -> np.ndarray:
    return np.datetime_as_string(base + day_offsets.astype("timedelta64[D]"), unit="D").astype(object)


def _late_lag(rng: np.random.Generator, n: int, rate: float, lo: int, hi: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (lag_days, is_late). Most rows land same day; `rate` land lo..hi days late."""
    is_late = _mask(rng, n, rate)
    lag = np.where(is_late, rng.integers(lo, hi + 1, n), 0)
    return lag, is_late


def _corrupt_dates(rng, values: np.ndarray, rate: float) -> tuple[np.ndarray, int]:
    m = _mask(rng, len(values), rate)
    values = values.copy()
    values[m] = rng.choice(BAD_DATES, m.sum())
    return values, int(m.sum())


def _messy_case(rng, values: np.ndarray, rate: float) -> tuple[np.ndarray, int]:
    m = _mask(rng, len(values), rate)
    values = values.astype(object).copy()
    styles = rng.integers(0, 3, len(values))
    for i in np.flatnonzero(m):
        v = values[i]
        values[i] = {0: v.upper(), 1: f" {v} ", 2: v.title()}[styles[i]]
    return values, int(m.sum())


def _nullable_int(values: np.ndarray, null_mask: np.ndarray) -> pa.Array:
    return pa.array(values.astype("int64"), type=pa.int64(), mask=null_mask)


def _nullable_float(values: np.ndarray, null_mask: np.ndarray) -> pa.Array:
    return pa.array(np.round(values.astype("float64"), 2), type=pa.float64(), mask=null_mask)


def _nullable_str(values: np.ndarray, null_mask: np.ndarray | None = None) -> pa.Array:
    vals = [None if (null_mask is not None and null_mask[i]) else values[i] for i in range(len(values))]
    return pa.array(vals, type=pa.string())


def _add_duplicates(rng, cols: dict[str, np.ndarray], ingest_day: np.ndarray, rate: float):
    """Append re-delivered copies of `rate` of the rows, landing 0-3 days after the original."""
    n = len(ingest_day)
    idx = np.flatnonzero(_mask(rng, n, rate))
    out = {k: np.concatenate([v, v[idx]]) for k, v in cols.items()}
    dup_ingest = ingest_day[idx] + rng.integers(0, 4, len(idx))
    return out, np.concatenate([ingest_day, dup_ingest]), idx


def _finalize(cols: dict[str, np.ndarray], ingest_day: np.ndarray, builders: dict) -> tuple[pa.Table, np.ndarray]:
    """Stable-sort by ingest day (load order), assign _record_id, build Arrow table."""
    order = np.argsort(ingest_day, kind="stable")
    arrays = {"_record_id": pa.array(np.arange(1, len(order) + 1, dtype="int64"))}
    for name, build in builders.items():
        arrays[name] = build({k: v[order] for k, v in cols.items()})
    return pa.table(arrays), ingest_day[order]


# --------------------------------------------------------------------------- #
# entity generators
# --------------------------------------------------------------------------- #
def gen_customers(rng, n: int, dirt: DirtConfig, stats: dict):
    ids = np.arange(1, n + 1)
    signup_offset = rng.integers(-365, 0, n)  # signed up during 2024
    signup = _date_strings(signup_offset)
    signup, stats["bad_dates"] = _corrupt_dates(rng, signup, dirt.bad_date_rate)
    email = np.array([f"user{i}@example.com" for i in ids], dtype=object)
    email_null = _mask(rng, n, dirt.null_rate)
    stats["nulls"] = int(email_null.sum())
    first = rng.choice(np.array(["Ava", "Liam", "Noah", "Mia", "Zoe", "Ravi", "Yuki", "Omar", "Lena", "Ivan"]), n)
    last = rng.choice(np.array(["Smith", "Garcia", "Chen", "Patel", "Müller", "Rossi", "Kim", "Silva"]), n)
    country = rng.choice(COUNTRIES, n)
    # initial snapshot lands on day 0; late-arriving dimension rows land 5-60 days later,
    # i.e. after some of their orders have already been ingested.
    ingest, late = _late_lag(rng, n, dirt.late_rate, 5, 60)
    stats["late"] = int(late.sum())
    cols = dict(
        customer_id=ids,
        email=email,
        email_null=email_null,
        first_name=first,
        last_name=last,
        country=country,
        signup_date=signup,
    )
    cols, ingest, dup_idx = _add_duplicates(rng, cols, ingest, dirt.duplicate_rate)
    # half of the re-delivered customers carry an updated email (CDC-style change)
    changed = np.arange(n, n + len(dup_idx))[rng.random(len(dup_idx)) < 0.5]
    cols["email"] = cols["email"].copy()
    for i in changed:
        cols["email"][i] = f"user{cols['customer_id'][i]}+new@example.com"
    stats["duplicates"] = len(dup_idx)
    return _finalize(
        cols,
        ingest,
        {
            "customer_id": lambda c: pa.array(c["customer_id"].astype("int64")),
            "email": lambda c: _nullable_str(c["email"], c["email_null"]),
            "first_name": lambda c: _nullable_str(c["first_name"]),
            "last_name": lambda c: _nullable_str(c["last_name"]),
            "country": lambda c: _nullable_str(c["country"]),
            "signup_date": lambda c: _nullable_str(c["signup_date"]),
        },
    )


def gen_products(rng, n: int, dirt: DirtConfig, stats: dict):
    ids = np.arange(1, n + 1)
    category = rng.choice(CATEGORIES, n)
    name = np.array([f"{c.title()} Item {i:04d}" for c, i in zip(category, ids, strict=True)], dtype=object)
    price = np.round(rng.lognormal(3.3, 0.8, n), 2)  # median ~ $27
    neg = _mask(rng, n, dirt.negative_price_rate)
    price[neg] = -price[neg]
    price_null = _mask(rng, n, dirt.null_rate) & ~neg
    stats.update(negative_prices=int(neg.sum()), nulls=int(price_null.sum()))
    ingest = np.zeros(n, dtype=int)  # catalogue snapshot on day 0
    cols = dict(product_id=ids, name=name, category=category, price=price, price_null=price_null)
    cols, ingest, dup_idx = _add_duplicates(rng, cols, ingest, dirt.duplicate_rate)
    stats["duplicates"] = len(dup_idx)
    return _finalize(
        cols,
        ingest,
        {
            "product_id": lambda c: pa.array(c["product_id"].astype("int64")),
            "name": lambda c: _nullable_str(c["name"]),
            "category": lambda c: _nullable_str(c["category"]),
            "price": lambda c: _nullable_float(c["price"], c["price_null"]),
        },
    ), np.where(price_null, np.nan, np.abs(price))


def gen_orders(rng, n: int, n_customers: int, dirt: DirtConfig, stats: dict):
    ids = np.arange(1, n + 1)
    day = rng.integers(0, DAYS, n)
    secs = rng.integers(0, SECONDS_PER_DAY, n)
    ts = _ts_strings(day, secs)
    ts, stats["bad_dates"] = _corrupt_dates(rng, ts, dirt.bad_date_rate)
    customer = rng.integers(1, n_customers + 1, n)
    orphan = _mask(rng, n, dirt.orphan_rate)
    customer[orphan] = n_customers + rng.integers(1, 1000, orphan.sum())
    cust_null = _mask(rng, n, dirt.null_rate) & ~orphan
    status = rng.choice(ORDER_STATUSES, n, p=ORDER_STATUS_P)
    status, stats["messy_case"] = _messy_case(rng, status, dirt.messy_case_rate)
    lag, late = _late_lag(rng, n, dirt.late_rate, 2, 14)
    stats.update(orphans=int(orphan.sum()), nulls=int(cust_null.sum()), late=int(late.sum()))
    ingest = day + lag
    cols = dict(order_id=ids, customer_id=customer, cust_null=cust_null, order_ts=ts, status=status)
    cols, ingest_all, dup_idx = _add_duplicates(rng, cols, ingest, dirt.duplicate_rate)
    stats["duplicates"] = len(dup_idx)
    table = _finalize(
        cols,
        ingest_all,
        {
            "order_id": lambda c: pa.array(c["order_id"].astype("int64")),
            "customer_id": lambda c: _nullable_int(c["customer_id"], c["cust_null"]),
            "order_ts": lambda c: _nullable_str(c["order_ts"]),
            "status": lambda c: _nullable_str(c["status"]),
        },
    )
    return table, ingest  # per-order ingest day (pre-duplication), used for items


def gen_order_items(
    rng, order_ingest: np.ndarray, n_products: int, product_price: np.ndarray, dirt: DirtConfig, stats: dict
):
    n_orders = len(order_ingest)
    per_order = rng.integers(1, 5, n_orders)  # 1-4 lines per order
    order_id = np.repeat(np.arange(1, n_orders + 1), per_order)
    n = len(order_id)
    ids = np.arange(1, n + 1)
    product = rng.integers(1, n_products + 1, n)
    base_price = product_price[product - 1]
    unit_price = np.where(np.isnan(base_price), rng.lognormal(3.3, 0.8, n), base_price)
    quantity = rng.integers(1, 6, n)
    neg = _mask(rng, n, dirt.negative_price_rate)
    unit_price[neg] = -unit_price[neg]
    badq = _mask(rng, n, dirt.bad_quantity_rate)
    quantity[badq] = rng.integers(-3, 1, badq.sum())  # -3..0
    orphan = _mask(rng, n, dirt.orphan_rate)
    product[orphan] = n_products + rng.integers(1, 1000, orphan.sum())
    oid_null = _mask(rng, n, dirt.null_rate)
    # items land with their order, some arrive late on their own
    lag, late = _late_lag(rng, n, dirt.late_rate, 1, 10)
    ingest = np.repeat(order_ingest, per_order) + lag
    stats.update(
        negative_prices=int(neg.sum()),
        bad_quantities=int(badq.sum()),
        orphans=int(orphan.sum()),
        nulls=int(oid_null.sum()),
        late=int(late.sum()),
    )
    cols = dict(
        order_item_id=ids,
        order_id=order_id,
        oid_null=oid_null,
        product_id=product,
        quantity=quantity,
        unit_price=unit_price,
    )
    cols, ingest, dup_idx = _add_duplicates(rng, cols, ingest, dirt.duplicate_rate)
    stats["duplicates"] = len(dup_idx)
    return _finalize(
        cols,
        ingest,
        {
            "order_item_id": lambda c: pa.array(c["order_item_id"].astype("int64")),
            "order_id": lambda c: _nullable_int(c["order_id"], c["oid_null"]),
            "product_id": lambda c: pa.array(c["product_id"].astype("int64")),
            "quantity": lambda c: pa.array(c["quantity"].astype("int64")),
            "unit_price": lambda c: _nullable_float(c["unit_price"], np.zeros(len(c["unit_price"]), bool)),
        },
    )


def gen_events(rng, n_sessions: int, n_customers: int, n_products: int, dirt: DirtConfig, stats: dict):
    # simulate a funnel per session: each stage reached with FUNNEL_ADVANCE prob.
    depth = np.ones(n_sessions, dtype=int)
    alive = np.ones(n_sessions, dtype=bool)
    for p in FUNNEL_ADVANCE:
        alive &= rng.random(n_sessions) < p
        depth += alive
    session_idx = np.repeat(np.arange(n_sessions), depth)
    stage = np.concatenate([np.arange(d) for d in depth])
    n = len(session_idx)
    s_day = rng.integers(0, DAYS, n_sessions)
    s_sec = rng.integers(0, SECONDS_PER_DAY - 3600, n_sessions)
    s_customer = np.where(rng.random(n_sessions) < 0.7, rng.integers(1, n_customers + 1, n_sessions), 0)
    s_product = rng.integers(1, n_products + 1, n_sessions)

    day = s_day[session_idx]
    secs = s_sec[session_idx] + stage * 60 + rng.integers(0, 60, n)
    ts = _ts_strings(day, secs)
    ts, stats["bad_dates"] = _corrupt_dates(rng, ts, dirt.bad_date_rate)
    event_type = np.array(FUNNEL, dtype=object)[stage]
    event_type, stats["messy_case"] = _messy_case(rng, event_type, dirt.messy_case_rate)
    invalid = _mask(rng, n, dirt.invalid_event_rate)
    event_type[invalid] = rng.choice(BAD_EVENT_TYPES, invalid.sum())
    customer = s_customer[session_idx]
    cust_null = customer == 0  # anonymous visitors: legitimately null
    product = s_product[session_idx]
    prod_null = stage == 0  # page_view has no product
    orphan = _mask(rng, n, dirt.orphan_rate) & ~prod_null
    product[orphan] = n_products + rng.integers(1, 1000, orphan.sum())
    session_id = np.array([f"s{i:08d}" for i in session_idx], dtype=object)
    sess_null = _mask(rng, n, dirt.null_rate)
    lag, late = _late_lag(rng, n, dirt.late_rate, 1, 7)
    stats.update(
        invalid_event_types=int(invalid.sum()),
        orphans=int(orphan.sum()),
        nulls=int(sess_null.sum()),
        late=int(late.sum()),
    )
    cols = dict(
        event_id=np.arange(1, n + 1),
        session_id=session_id,
        sess_null=sess_null,
        customer_id=customer,
        cust_null=cust_null,
        product_id=product,
        prod_null=prod_null,
        event_type=event_type,
        event_ts=ts,
    )
    cols, ingest, dup_idx = _add_duplicates(rng, cols, day + lag, dirt.duplicate_rate)
    stats["duplicates"] = len(dup_idx)
    return _finalize(
        cols,
        ingest,
        {
            "event_id": lambda c: pa.array(c["event_id"].astype("int64")),
            "session_id": lambda c: _nullable_str(c["session_id"], c["sess_null"]),
            "customer_id": lambda c: _nullable_int(c["customer_id"], c["cust_null"]),
            "product_id": lambda c: _nullable_int(c["product_id"], c["prod_null"]),
            "event_type": lambda c: _nullable_str(c["event_type"]),
            "event_ts": lambda c: _nullable_str(c["event_ts"]),
        },
    )


# --------------------------------------------------------------------------- #
# writer
# --------------------------------------------------------------------------- #
def write_partitioned(table: pa.Table, ingest_day: np.ndarray, root: Path) -> int:
    """Write one Parquet file per ingest_date partition (hive layout). Returns #partitions."""
    if root.exists():
        shutil.rmtree(root)
    days = np.unique(ingest_day)
    for d in days:
        part = table.filter(pa.array(ingest_day == d))
        ingest_date = str(START + np.timedelta64(int(d), "D"))
        path = root / f"ingest_date={ingest_date}"
        path.mkdir(parents=True)
        pq.write_table(part, path / "part-0.parquet", compression="snappy")
    return len(days)


def scale(rows: int) -> dict[str, int]:
    """Derive entity sizes from the order count."""
    return {
        "customers": max(20, rows // 5),
        "products": max(20, rows // 50),
        "orders": rows,
        "sessions": rows * 2,
    }


def generate(rows: int, seed: int, out: Path, dirt: DirtConfig = DirtConfig()) -> dict[str, dict]:
    if rows < 1:
        raise ValueError("--rows must be >= 1")
    rng = np.random.default_rng(seed)
    sizes = scale(rows)
    stats: dict[str, dict] = {e: {} for e in ENTITIES}

    customers = gen_customers(rng, sizes["customers"], dirt, stats["customers"])
    products, product_price = gen_products(rng, sizes["products"], dirt, stats["products"])
    orders, order_ingest = gen_orders(rng, sizes["orders"], sizes["customers"], dirt, stats["orders"])
    items = gen_order_items(rng, order_ingest, sizes["products"], product_price, dirt, stats["order_items"])
    events = gen_events(rng, sizes["sessions"], sizes["customers"], sizes["products"], dirt, stats["events"])

    out.mkdir(parents=True, exist_ok=True)
    for name, (table, ingest) in zip(ENTITIES, (customers, products, orders, items, events), strict=True):
        stats[name]["partitions"] = write_partitioned(table, ingest, out / name)
        stats[name]["rows"] = table.num_rows
    return stats


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--rows", type=int, default=10_000, help="number of orders (other entities scale from it)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=Path("data/bronze"))
    args = p.parse_args(argv)

    stats = generate(args.rows, args.seed, args.out)
    keys = [
        "rows",
        "partitions",
        "duplicates",
        "nulls",
        "bad_dates",
        "negative_prices",
        "bad_quantities",
        "orphans",
        "invalid_event_types",
        "messy_case",
        "late",
    ]
    print(f"bronze written to {args.out}  (rows={args.rows}, seed={args.seed})")
    print(
        f"{'entity':<12}"
        + "".join(
            f"{k:>11}"
            for k in [
                "rows",
                "parts",
                "dups",
                "nulls",
                "bad_dt",
                "neg_px",
                "bad_qty",
                "orphans",
                "bad_evt",
                "messy",
                "late",
            ]
        )
    )
    for e in ENTITIES:
        print(f"{e:<12}" + "".join(f"{stats[e].get(k, '-'):>11}" for k in keys))


if __name__ == "__main__":
    main()
