{#
    Use the schema named in dbt_project.yml verbatim, rather than dbt's default of
    prefixing it with the profile's target schema.

    Without this, `+schema: marts` builds into `public_marts`, and the estate's physical
    layout stops matching the layout described in the warehouse init SQL and in every
    piece of documentation. It also matters to Twin specifically: schema is one of the
    signals Stage 3 uses to group assets by layer, and `public_marts` would put every
    layer in the same apparent namespace.
#}

{#
    The shadow target is the exception. Stage 4 rebuilds the estate inside a single
    disposable schema, so every model collapses onto target.schema there: a rebuilt model
    and the passthrough view it reads resolve the same way, and teardown is one DROP SCHEMA
    rather than a sweep across six.

    This cannot widen the blast radius. The shadow target connects as twin_shadow, which
    holds no CREATE on any estate schema, so a model that resolved to `marts` under this
    target would be refused by the database rather than overwrite anything.
#}

{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if target.name == 'shadow' -%}
        {{ target.schema }}
    {%- elif custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
