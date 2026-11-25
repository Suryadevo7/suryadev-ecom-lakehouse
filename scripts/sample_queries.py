"""Print the README's sample query outputs from the built warehouse (run after `make all`)."""

import duckdb

QUERIES = {
    "Where did bronze rows go? (reconciliation)": """
        with b as (
            select 'customers' as entity, count(*) as bronze from {bronze}/customers/*/*.parquet') union all
            select 'products',    count(*) from {bronze}/products/*/*.parquet')    union all
            select 'orders',      count(*) from {bronze}/orders/*/*.parquet')      union all
            select 'order_items', count(*) from {bronze}/order_items/*/*.parquet') union all
            select 'events',      count(*) from {bronze}/events/*/*.parquet')
        ), s as (
            select 'customers' as entity, count(*) as silver from silver.stg_customers union all
            select 'products',    count(*) from silver.stg_products    union all
            select 'orders',      count(*) from silver.stg_orders      union all
            select 'order_items', count(*) from silver.stg_order_items union all
            select 'events',      count(*) from silver.stg_events
        ), r as (select entity, count(*) as rejected from silver.rejected_rows group by entity)
        select entity, bronze, silver, rejected, bronze = silver + rejected as reconciled
        from b join s using (entity) join r using (entity)
        order by bronze
    """,
    "Rejected rows by reason": """
        select reject_reason, count(*) as rows,
               string_agg(distinct entity, ', ' order by entity) as entities
        from silver.rejected_rows group by 1 order by rows desc
    """,
    "gold.daily_revenue (first 7 days)": """
        select order_date, orders, units_sold, revenue, avg_order_value, late_arriving_orders
        from gold.daily_revenue order by order_date limit 7
    """,
    "gold.customer_ltv (segments)": """
        select ltv_segment, count(*) as customers,
               round(avg(lifetime_revenue), 2) as avg_ltv, round(avg(orders), 2) as avg_orders
        from gold.customer_ltv group by 1 order by avg_ltv desc
    """,
    "gold.conversion_funnel (whole window)": """
        select stage, sum(sessions) as sessions,
               round(sum(sessions) / first_value(sum(sessions)) over (order by stage_order), 4) as conversion_from_top
        from gold.conversion_funnel group by stage, stage_order order by stage_order
    """,
    "gold.top_products (top 5)": """
        select revenue_rank, product_name, category, units_sold, revenue
        from gold.top_products order by revenue_rank limit 5
    """,
}

if __name__ == "__main__":
    con = duckdb.connect("data/lakehouse.duckdb", read_only=True)
    for title, sql in QUERIES.items():
        print(f"-- {title}")
        con.sql(sql.replace("{bronze}", "read_parquet('data/bronze")).show(max_width=200)
        print()
