with src as (
    select * from {{ source('bronze', 'order_items') }}
),
typed as (
    select
        src.*,
        {{ dedupe_rank('order_item_id') }} as _dup_rank,
        to_json(src)                       as _raw_payload
    from src
)
select
    t.*,
    case
        when t._dup_rank > 1                                                   then 'duplicate'
        when t.order_item_id is null or t.order_id is null or t.product_id is null
             or t.quantity is null or t.unit_price is null                     then 'null_required_field'
        when t.unit_price < 0                                                  then 'negative_price'
        when t.quantity <= 0                                                   then 'invalid_quantity'
        when o.order_id is null or p.product_id is null                        then 'orphan_fk'
    end as _reject_reason
from typed t
left join {{ ref('stg_orders') }}   o on t.order_id = o.order_id
left join {{ ref('stg_products') }} p on t.product_id = p.product_id
