-- Sessions can only shrink as you go deeper into the funnel.
select *
from {{ ref('conversion_funnel') }}
where conversion_from_previous > 1
