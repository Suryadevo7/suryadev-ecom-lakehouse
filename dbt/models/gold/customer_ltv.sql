-- One row per valid customer, including those who never ordered.
with per_customer as (
    select
        customer_id,
        count(*) filter (where is_revenue)            as orders,
        sum(order_total) filter (where is_revenue)    as lifetime_revenue,
        min(order_date) filter (where is_revenue)     as first_order_date,
        max(order_date) filter (where is_revenue)     as last_order_date
    from {{ ref('int_order_totals') }}
    group by customer_id
)
select
    c.customer_id,
    c.country,
    c.signup_date,
    coalesce(p.orders, 0)                                       as orders,
    coalesce(p.lifetime_revenue, 0)::decimal(14, 2)             as lifetime_revenue,
    coalesce(round(p.lifetime_revenue / nullif(p.orders, 0), 2), 0)::decimal(14, 2)
                                                                as avg_order_value,
    p.first_order_date,
    p.last_order_date,
    case
        when coalesce(p.lifetime_revenue, 0) = 0   then 'none'
        when p.lifetime_revenue >= 1000            then 'high'
        when p.lifetime_revenue >= 400             then 'mid'
        else 'low'
    end                                                         as ltv_segment
from {{ ref('stg_customers') }} c
left join per_customer p using (customer_id)
