{{ config(materialized='ephemeral') }}
-- One row per valid order with its valid line items rolled up.
-- Recognised revenue excludes cancelled and returned orders.
select
    o.order_id,
    o.customer_id,
    o.order_date,
    o.status,
    o.is_late_arriving,
    o.status in ('placed', 'shipped', 'delivered')        as is_revenue,
    count(i.order_item_id)                                as line_count,
    coalesce(sum(i.quantity), 0)::bigint                  as units,
    coalesce(sum(i.line_total), 0)::decimal(14, 2)        as order_total
from {{ ref('stg_orders') }} o
left join {{ ref('stg_order_items') }} i using (order_id)
group by all
