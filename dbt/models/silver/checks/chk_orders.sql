-- Orders are only valid if their customer survived silver validation (cascading orphan check).
with src as (
    select * from {{ source('bronze', 'orders') }}
),
typed as (
    select
        src.*,
        {{ dedupe_rank('order_id') }}   as _dup_rank,
        {{ safe_ts('order_ts') }}       as order_ts_clean,
        lower(trim(status))             as status_clean,
        to_json(src)                    as _raw_payload
    from src
)
select
    t.*,
    case
        when t._dup_rank > 1                                                    then 'duplicate'
        when t.order_id is null or t.customer_id is null or t.status is null    then 'null_required_field'
        when t.order_ts_clean is null                                           then 'invalid_date'
        when t.status_clean not in ('placed', 'shipped', 'delivered', 'cancelled', 'returned')
                                                                                then 'invalid_status'
        when c.customer_id is null                                              then 'orphan_fk'
    end as _reject_reason
from typed t
left join {{ ref('stg_customers') }} c on t.customer_id = c.customer_id
