-- The three revenue views of the business must agree to the cent.
with t as (
    select
        (select sum(revenue) from {{ ref('daily_revenue') }})          as daily,
        (select sum(lifetime_revenue) from {{ ref('customer_ltv') }})  as ltv,
        (select sum(revenue) from {{ ref('top_products') }})           as products
)
select * from t where daily <> ltv or daily <> products
