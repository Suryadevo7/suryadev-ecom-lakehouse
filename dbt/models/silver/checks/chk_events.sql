-- customer_id / product_id are legitimately NULL for anonymous visitors and page_views;
-- they are only orphans when present but unknown.
with src as (
    select * from {{ source('bronze', 'events') }}
),
typed as (
    select
        src.*,
        {{ dedupe_rank('event_id') }}   as _dup_rank,
        {{ safe_ts('event_ts') }}       as event_ts_clean,
        lower(trim(event_type))         as event_type_clean,
        to_json(src)                    as _raw_payload
    from src
)
select
    t.*,
    case
        when t._dup_rank > 1                                          then 'duplicate'
        when t.event_id is null or t.session_id is null
             or t.event_type is null                                  then 'null_required_field'
        when t.event_ts_clean is null                                 then 'invalid_date'
        when t.event_type_clean not in ('page_view', 'product_view', 'add_to_cart', 'checkout', 'purchase')
                                                                      then 'invalid_event_type'
        when (t.customer_id is not null and c.customer_id is null)
          or (t.product_id  is not null and p.product_id  is null)    then 'orphan_fk'
    end as _reject_reason
from typed t
left join {{ ref('stg_customers') }} c on t.customer_id = c.customer_id
left join {{ ref('stg_products') }}  p on t.product_id = p.product_id
