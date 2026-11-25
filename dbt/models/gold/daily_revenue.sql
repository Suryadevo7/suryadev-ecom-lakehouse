-- One row per calendar day in the order window (days with no orders show zeros).
with days as (
    select range::date as order_date
    from range(
        (select min(order_date) from {{ ref('stg_orders') }}),
        (select max(order_date) from {{ ref('stg_orders') }}) + interval 1 day,
        interval 1 day
    )
),
agg as (
    select
        order_date,
        count(*) filter (where is_revenue)                    as orders,
        count(*) filter (where not is_revenue)                as cancelled_or_returned,
        sum(units) filter (where is_revenue)                  as units_sold,
        sum(order_total) filter (where is_revenue)            as revenue,
        count(distinct customer_id) filter (where is_revenue) as customers,
        count(*) filter (where is_late_arriving)              as late_arriving_orders
    from {{ ref('int_order_totals') }}
    group by order_date
)
select
    d.order_date,
    coalesce(a.orders, 0)                                     as orders,
    coalesce(a.cancelled_or_returned, 0)                      as cancelled_or_returned,
    coalesce(a.units_sold, 0)::bigint                         as units_sold,
    coalesce(a.revenue, 0)::decimal(14, 2)                    as revenue,
    coalesce(round(a.revenue / nullif(a.orders, 0), 2), 0)::decimal(14, 2) as avg_order_value,
    coalesce(a.customers, 0)                                  as customers,
    coalesce(a.late_arriving_orders, 0)                       as late_arriving_orders
from days d
left join agg a using (order_date)
order by d.order_date
