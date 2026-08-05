{#
    Use the schema named in dbt_project.yml verbatim, rather than dbt's default of
    prefixing it with the profile's target schema.

    Without this, `+schema: marts` builds into `public_marts`, and the estate's physical
    layout stops matching the layout described in the warehouse init SQL and in every
    piece of documentation. It also matters to Twin specifically: schema is one of the
    signals Stage 3 uses to group assets by layer, and `public_marts` would put every
    layer in the same apparent namespace.
#}

{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
