{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if target.name == 'shadow' -%}
        {{ env_var('TWIN_SHADOW_SCHEMA') }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
