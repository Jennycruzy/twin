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


# ---------------------------------------------------------------- the server-enforced layer
#
# Everything above this line is a unit test: it asserts that the guard refuses a statement.
# The cases below cannot be written that way, because the guard does *not* refuse them and is
# not supposed to. `WITH x AS (DELETE ...)` and `EXPLAIN ANALYZE DELETE ...` are both routed
# read-only on their leading keyword, and what stops them is PostgreSQL, not this codebase.
#
# So these tests need a warehouse. They are skipped when there is not one, which is a real
# cost and worth naming: the property they cover is the most important one in the project,
# and `make test` on a machine with the stack down will report success without checking it.
# The nightly runs with the stack up. The alternative — standing the warehouse up in the
# tests workflow — turns a 30-second CI job into a multi-minute one.

import psycopg

from twin.verify.warehouse import Credentials, ShadowConnection

LIVE = "twin_shadow_guard_live"
VICTIM = "twin_shadow_guard_victim"


def _warehouse_reachable() -> bool:
    try:
        psycopg.connect(Credentials.shadow_role().dsn(), connect_timeout=2).close()
        return True
    except psycopg.Error:
        return False


needs_warehouse = pytest.mark.skipif(
    not _warehouse_reachable(), reason="no warehouse reachable; run `make up` first"
)


@pytest.fixture()
def live():
    """A guarded connection, plus a second run's shadow schema holding one row.

    The victim schema is what makes this worth testing. `twin_shadow` owns nothing in the
    estate, so PostgreSQL already refuses estate writes whatever the guard does — but it
    *owns* every shadow schema, so a modifying statement naming a concurrent run's workspace
    is permitted by the role model and has to be stopped here.
    """
    credentials = Credentials.shadow_role()
    with ShadowConnection(schema=VICTIM, credentials=credentials) as victim:
        victim.execute(f"DROP SCHEMA IF EXISTS {VICTIM} CASCADE")
        victim.execute(f"CREATE SCHEMA {VICTIM}")
        victim.execute(f"CREATE TABLE {VICTIM}.evidence AS SELECT 1 AS verdict")
        with ShadowConnection(schema=LIVE, credentials=credentials) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {LIVE} CASCADE")
            connection.execute(f"CREATE SCHEMA {LIVE}")
            try:
                yield connection, victim
            finally:
                connection.execute(f"DROP SCHEMA IF EXISTS {LIVE} CASCADE")
                victim.execute(f"DROP SCHEMA IF EXISTS {VICTIM} CASCADE")


def _victim_rows(victim: ShadowConnection) -> list:
    return victim.fetch(f"SELECT verdict FROM {VICTIM}.evidence")


@needs_warehouse
def test_a_modifying_cte_cannot_reach_another_runs_shadow_schema(live):
    """The failure the role model does not cover, and the reason this layer exists."""
    connection, victim = live
    failure = connection.try_execute(
        f"WITH u AS (UPDATE {VICTIM}.evidence SET verdict = 666 RETURNING *) SELECT * FROM u"
    )
    assert failure is not None
    assert "read-only transaction" in failure.error
    assert _victim_rows(victim) == [(1,)]


@needs_warehouse
def test_explain_analyze_cannot_execute_dml(live):
    """EXPLAIN ANALYZE runs what it explains. Without this layer, the row disappears."""
    connection, victim = live
    failure = connection.try_execute(f"EXPLAIN ANALYZE DELETE FROM {VICTIM}.evidence")
    assert failure is not None
    assert "read-only transaction" in failure.error
    assert _victim_rows(victim) == [(1,)]


@needs_warehouse
def test_a_modifying_cte_cannot_reach_the_estate(live):
    """Belt and braces: the role model refuses this too, and both layers are checked."""
    connection, _ = live
    failure = connection.try_execute(
        "WITH d AS (DELETE FROM marts.mart_revenue_daily RETURNING *) SELECT count(*) FROM d"
    )
    assert failure is not None
    assert "read-only transaction" in failure.error or "permission denied" in failure.error


@needs_warehouse
def test_a_real_consumer_query_using_a_cte_still_runs(live):
    """The reason the guard cannot simply refuse WITH: the dashboards are full of them."""
    connection, _ = live
    rows = connection.fetch(
        "WITH recent AS (SELECT 1 AS n UNION ALL SELECT 2) SELECT sum(n) FROM recent"
    )
    assert rows == [(3,)]


@needs_warehouse
def test_the_destructive_path_still_writes_inside_this_runs_schema(live):
    """The read-only transaction must not have made Twin unable to do its job."""
    connection, _ = live
    connection.execute(f"CREATE TABLE {LIVE}.probe AS SELECT 42 AS answer")
    assert connection.fetch(f"SELECT answer FROM {LIVE}.probe") == [(42,)]
