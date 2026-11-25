select
    order_item_id::bigint                         as order_item_id,
    order_id::bigint                              as order_id,
    product_id::bigint                            as product_id,
    quantity::integer                             as quantity,
    unit_price::decimal(12, 2)                    as unit_price,
    (quantity * unit_price)::decimal(14, 2)       as line_total,
    ingest_date,
    _record_id
from {{ ref('chk_order_items') }}
where _reject_reason is null
