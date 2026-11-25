select
    customer_id::bigint          as customer_id,
    email_clean                  as email,
    first_name,
    last_name,
    upper(trim(country))         as country,
    signup_date_clean            as signup_date,
    ingest_date,
    _record_id
from {{ ref('chk_customers') }}
where _reject_reason is null
