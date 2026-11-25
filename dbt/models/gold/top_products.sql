-- Product leaderboard on recognised revenue (cancelled/returned orders excluded).
with sales as (
    select
        i.product_id,
        sum(i.quantity)                 as units_sold,
        sum(i.line_total)               as revenue,
        count(distinct i.order_id)      as orders
    from {{ ref('stg_order_items') }} i
    join {{ ref('int_order_totals') }} o using (order_id)
    where o.is_revenue
    group by i.product_id
)
select
    p.product_id,
    p.product_name,
    p.category,
    p.price,
    coalesce(s.units_sold, 0)::bigint          as units_sold,
    coalesce(s.orders, 0)                      as orders,
    coalesce(s.revenue, 0)::decimal(14, 2)     as revenue,
    row_number() over (order by coalesce(s.revenue, 0) desc, p.product_id)                       as revenue_rank,
    row_number() over (partition by p.category order by coalesce(s.revenue, 0) desc, p.product_id) as category_rank
from {{ ref('stg_products') }} p
left join sales s using (product_id)
order by revenue_rank
