{#- Parse a raw string into a timestamp; NULL if unparseable or outside the sanity window. -#}
{% macro safe_ts(col) -%}
    case
        when try_cast({{ col }} as timestamp) between timestamp '{{ var("min_valid_date") }}'
                                               and timestamp '{{ var("max_valid_date") }} 23:59:59'
        then try_cast({{ col }} as timestamp)
    end
{%- endmacro %}

{#- Rank copies of the same business key: newest ingest wins, ties broken by load order. -#}
{% macro dedupe_rank(pk) -%}
    row_number() over (partition by {{ pk }} order by ingest_date desc, _record_id desc)
{%- endmacro %}
