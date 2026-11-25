-- Daily session funnel. A session "reaches" a stage if its deepest valid event is at or
-- beyond that stage, so the funnel is monotonic even when an intermediate event was
-- quarantined in silver.
{% set stages = ['page_view', 'product_view', 'add_to_cart', 'checkout', 'purchase'] %}
with stage_map as (
    {% for s in stages %}
    select '{{ s }}' as stage, {{ loop.index }} as stage_order
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
),
sessions as (
    select
        e.session_id,
        min(e.event_date)       as session_date,
        max(m.stage_order)      as max_stage
    from {{ ref('stg_events') }} e
    join stage_map m on e.event_type = m.stage
    group by e.session_id
),
counts as (
    select
        s.session_date as event_date,
        m.stage,
        m.stage_order,
        count(*) filter (where s.max_stage >= m.stage_order) as sessions
    from sessions s
    cross join stage_map m
    group by all
)
select
    event_date,
    stage,
    stage_order,
    sessions,
    round(sessions / nullif(lag(sessions) over w, 0), 4)            as conversion_from_previous,
    round(sessions / nullif(first_value(sessions) over w, 0), 4)    as conversion_from_top
from counts
window w as (partition by event_date order by stage_order)
order by event_date, stage_order
