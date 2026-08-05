"""Tests for the execution boundary.

Twin exists to execute destructive statements against a database, so this is the test file
that matters most. It is written to be read by someone deciding whether to trust the tool:
the cases below are the ones a reviewer would think to try, including the statement that
would drop a real estate table.

The guard is the second of two layers. The first is PostgreSQL itself — Stage 4 connects as
a role that owns nothing in the estate, so a dropped guard would still not permit any of the
statements refused here. Both exist because they fail differently. See docs/SAFETY.md.
"""

from __future__ import annotations

import pytest

from twin.verify.guard import SHADOW_PREFIX, UnsafeStatement, assert_safe, normalise

SHADOW = "twin_shadow_fx_rate_column_drop"
OTHER_SHADOW = "twin_shadow_someone_elses_run"


def refused(sql: str, schema: str = SHADOW) -> str:
    with pytest.raises(UnsafeStatement) as caught:
        assert_safe(sql, schema)
    return str(caught.value)


# ---------------------------------------------------------------- the statements that matter


def test_it_refuses_to_drop_a_real_estate_table():
    """The statement this whole layer exists to stop."""
    assert "outside this run's shadow schema" in refused("DROP TABLE marts.mart_revenue_daily")


def test_it_refuses_to_alter_a_real_estate_table():
    assert "outside this run's shadow schema" in refused(
        "ALTER TABLE staging.stg_fx_rates DROP COLUMN rate"
    )


def test_it_refuses_to_truncate_or_delete_from_the_estate():
    assert refused("TRUNCATE TABLE raw_pg.orders")
    assert refused("DELETE FROM raw_pg.orders WHERE 1=1")


def test_it_refuses_to_drop_a_schema_that_is_not_a_shadow_schema():
    assert refused("DROP SCHEMA marts CASCADE")


def test_it_refuses_another_runs_shadow_schema():
    """A shadow prefix is not a shared licence.

    Two scenarios can run at once, and one reaching into the other's workspace would corrupt
    a verification rather than the estate — a quieter failure, and a worse one, because the
    result would still look like evidence.
    """
    assert refused(f"DROP SCHEMA {OTHER_SHADOW} CASCADE")


def test_it_refuses_to_operate_against_a_schema_without_the_prefix():
    with pytest.raises(UnsafeStatement) as caught:
        assert_safe("CREATE SCHEMA marts", "marts")
    assert SHADOW_PREFIX in str(caught.value)


# ---------------------------------------------------------------- evasion


def test_it_refuses_a_destructive_statement_hidden_behind_a_comment():
    assert refused("/* harmless */ DROP TABLE marts.mart_revenue_daily")
    assert refused("-- just looking\nDROP TABLE marts.mart_revenue_daily")


def test_it_refuses_multiple_statements_in_one_string():
    """Checking only the first verb is the classic way past a guard like this."""
    assert "multi-statement" in refused(
        f"CREATE SCHEMA {SHADOW}; DROP TABLE marts.mart_revenue_daily"
    )


def test_a_semicolon_inside_a_literal_is_not_a_second_statement():
    assert_safe(f"SELECT 'a;b' FROM {SHADOW}.stg_fx_rates", SHADOW)


def test_it_refuses_an_unqualified_target():
    """Bare names resolve through search_path, which Twin does not control."""
    assert refused("DROP TABLE mart_revenue_daily")


def test_it_refuses_anything_it_does_not_recognise():
    assert "does not recognise" in refused("GRANT ALL ON SCHEMA marts TO twin_shadow")
    assert "does not recognise" in refused("COPY marts.mart_revenue_daily TO '/tmp/out.csv'")
    assert "does not recognise" in refused("VACUUM FULL")


def test_it_refuses_an_empty_statement():
    assert refused("   ")


# ---------------------------------------------------------------- what it must allow


def test_it_allows_work_inside_this_runs_shadow_schema():
    assert_safe(f"CREATE SCHEMA {SHADOW}", SHADOW)
    assert_safe(f"DROP SCHEMA IF EXISTS {SHADOW} CASCADE", SHADOW)
    assert_safe(f'CREATE VIEW "{SHADOW}"."stg_orders" AS SELECT * FROM "staging"."stg_orders"', SHADOW)
    assert_safe(f'DROP VIEW IF EXISTS "{SHADOW}"."stg_orders" CASCADE', SHADOW)
    assert_safe(f'CREATE TABLE "{SHADOW}"."probe" AS SELECT 1', SHADOW)


def test_it_allows_reads_of_the_real_estate():
    """Cloning a slice means reading production. Reads cannot destroy anything."""
    assert_safe("SELECT * FROM marts.mart_revenue_daily", SHADOW)
    assert_safe("WITH x AS (SELECT 1) SELECT * FROM x", SHADOW)


def test_quoted_identifiers_are_compared_by_name_not_by_spelling():
    assert_safe(f'DROP TABLE "{SHADOW}"."stg_orders"', SHADOW)
    assert refused(f'DROP TABLE "marts"."mart_revenue_daily"')


def test_normalise_leaves_string_literals_alone():
    assert normalise("SELECT 'a -- b' /* c */ FROM t") == "SELECT 'a -- b' FROM t"
