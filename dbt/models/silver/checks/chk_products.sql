with src as (
    select * from {{ source('bronze', 'products') }}
),
typed as (
    select
        src.*,
        {{ dedupe_rank('product_id') }} as _dup_rank,
        to_json(src)                    as _raw_payload
    from src
)
select
    *,
    case
        when _dup_rank > 1                                          then 'duplicate'
        when product_id is null or price is null
             or name is null or category is null                    then 'null_required_field'
        when price < 0                                              then 'negative_price'
    end as _reject_reason
from typed
