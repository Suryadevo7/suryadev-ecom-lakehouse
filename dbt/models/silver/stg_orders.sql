select
    order_id::bigint                         as order_id,
    customer_id::bigint                      as customer_id,
    order_ts_clean                           as order_ts,
    order_ts_clean::date                     as order_date,
    status_clean                             as status,
    ingest_date,
    ingest_date > order_ts_clean::date + 1   as is_late_arriving,
    _record_id
from {{ ref('chk_orders') }}
where _reject_reason is null
