"""The connection Stage 4 executes through.

Every statement Twin sends to the warehouse passes :func:`twin.verify.guard.assert_safe`
first. Not most statements, and not the ones a caller remembered to check — the guard lives
inside ``execute`` so that there is no unguarded path to the database in the codebase.

The connection authenticates as ``twin_shadow``, which owns nothing in the estate. That is
the layer underneath this one: even if every check in the guard were removed, PostgreSQL
would still refuse to drop or alter a real estate table, because ownership cannot be assumed
at will. See docs/SAFETY.md.

Dry-run mode records statements instead of executing them, so the exact SQL a scenario would
run can be read before it is trusted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

import psycopg

from twin.verify.guard import assert_safe


@dataclass(frozen=True)
class Credentials:
    """Where the warehouse is and who Twin connects as."""

    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def shadow_role(cls) -> "Credentials":
        """The Stage 4 role. Deliberately not the role dbt builds the estate with."""
        return cls(
            host=os.environ.get("WAREHOUSE_HOST", "warehouse"),
            port=int(os.environ.get("WAREHOUSE_PORT", "5432")),
            dbname=os.environ.get("WAREHOUSE_DB", "warehouse"),
            user=os.environ.get("WAREHOUSE_SHADOW_USER", "twin_shadow"),
            password=os.environ.get("WAREHOUSE_SHADOW_PASSWORD", "twin_shadow"),
        )

    def dsn(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.dbname} "
            f"user={self.user} password={self.password}"
        )


@dataclass
class QueryFailure:
    """A statement the warehouse refused, kept verbatim.

    The database's own error is the evidence Stage 4 grades itself against, so it is
    recorded as returned rather than summarised into a category.
    """

    statement: str
    error: str


@dataclass
class ShadowConnection:
    """A guarded connection scoped to one shadow schema."""

    schema: str
    credentials: Credentials
    dry_run: bool = False
    issued: list[str] = field(default_factory=list)
    _connection: Any = field(default=None, repr=False)

    def __enter__(self) -> "ShadowConnection":
        if not self.dry_run:
            self._connection = psycopg.connect(self.credentials.dsn(), autocommit=True)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # ---------------------------------------------------------------- execution

    def execute(self, sql: str) -> None:
        """Run a statement, or record it if this is a dry run."""
        assert_safe(sql, self.schema)
        self.issued.append(sql)
        if self.dry_run:
            return
        with self._connection.cursor() as cursor:
            cursor.execute(sql)

    def try_execute(self, sql: str) -> QueryFailure | None:
        """Run a statement and return the warehouse's error rather than raising.

        Used where a failure *is* the observation — running a consumer's query against a
        broken estate is expected to fail, and the error text is the finding.
        """
        assert_safe(sql, self.schema)
        self.issued.append(sql)
        if self.dry_run:
            return None
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(sql)
                if cursor.description:
                    cursor.fetchall()
        except psycopg.Error as exc:
            return QueryFailure(statement=sql, error=str(exc).strip().splitlines()[0])
        return None

    def fetch(self, sql: str) -> list[tuple[Any, ...]]:
        assert_safe(sql, self.schema)
        self.issued.append(sql)
        if self.dry_run:
            return []
        with self._connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()


def quote(identifier: str) -> str:
    """Quote an identifier for interpolation into DDL.

    Twin builds statements naming assets that came from the catalog rather than from a
    literal in this repository, so the names are quoted rather than trusted. The guard is
    the layer that decides whether a statement may run at all; this only ensures the
    statement means what it appears to mean.
    """
    return '"' + identifier.replace('"', '""') + '"'


def qualified(schema: str, name: str) -> str:
    return f"{quote(schema)}.{quote(name)}"


def literal(value: str) -> str:
    """Quote a string literal for interpolation into a catalog lookup."""
    return "'" + value.replace("'", "''") + "'"


def columns_clause(columns: Sequence[str]) -> str:
    return ", ".join(quote(c) for c in columns)
