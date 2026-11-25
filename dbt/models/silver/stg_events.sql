select
    event_id::bigint                         as event_id,
    session_id,
    customer_id::bigint                      as customer_id,
    product_id::bigint                       as product_id,
    event_type_clean                         as event_type,
    event_ts_clean                           as event_ts,
    event_ts_clean::date                     as event_date,
    ingest_date,
    ingest_date > event_ts_clean::date + 1   as is_late_arriving,
    _record_id
from {{ ref('chk_events') }}
where _reject_reason is null
