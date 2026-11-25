-- Every bronze customer row, typed, with at most one reject reason (first failing rule wins).
with src as (
    select * from {{ source('bronze', 'customers') }}
),
typed as (
    select
        src.*,
        {{ dedupe_rank('customer_id') }}           as _dup_rank,
        lower(trim(email))                         as email_clean,
        {{ safe_ts('signup_date') }}::date         as signup_date_clean,
        to_json(src)                               as _raw_payload
    from src
)
select
    *,
    case
        when _dup_rank > 1                          then 'duplicate'
        when customer_id is null or email is null   then 'null_required_field'
        when signup_date_clean is null              then 'invalid_date'
    end as _reject_reason
from typed
