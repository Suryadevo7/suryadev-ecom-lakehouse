-- Quarantine: every bronze row that did not make it into a stg_* table, with the reason.
{% set entities = [
    ('customers',   'customer_id'),
    ('products',    'product_id'),
    ('orders',      'order_id'),
    ('order_items', 'order_item_id'),
    ('events',      'event_id'),
] %}
{% for entity, pk in entities %}
select
    '{{ entity }}'           as entity,
    _record_id               as record_id,
    {{ pk }}::varchar        as source_key,
    _reject_reason           as reject_reason,
    ingest_date,
    _raw_payload             as raw_payload
from {{ ref('chk_' ~ entity) }}
where _reject_reason is not null
{% if not loop.last %}union all{% endif %}
{% endfor %}
