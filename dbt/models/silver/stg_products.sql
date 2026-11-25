select
    product_id::bigint           as product_id,
    trim(name)                   as product_name,
    lower(trim(category))        as category,
    price::decimal(12, 2)        as price,
    ingest_date,
    _record_id
from {{ ref('chk_products') }}
where _reject_reason is null
