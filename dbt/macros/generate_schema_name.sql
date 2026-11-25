{#- Use the custom schema name verbatim (silver / gold) instead of <target>_<custom>. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name else target.schema }}
{%- endmacro %}
