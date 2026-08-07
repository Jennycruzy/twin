"""The connection used by the verifier.

Every statement Twin sends to the warehouse passes :func:`twin.verify.guard.assert_safe`
first. Not most statements, and not the ones a caller remembered to check — the guard lives
inside ``execute`` so that there is no unguarded path to the database in the codebase.

The guard returns how a statement must be run, and this module is what carries that out:
anything routed as read-only executes inside a PostgreSQL READ ONLY transaction, so the
read-only path is enforced by the server instead of by the guard's opinion of a leading
keyword. See :meth:`ShadowConnection._transaction`.

The connection authenticates as ``twin_shadow``, which owns nothing in the estate. That is
the layer underneath this one: even if every check in the guard were removed, PostgreSQL
would still refuse to drop or alter a real estate table, because ownership cannot be assumed
at will. See docs/SAFETY.md.

Dry-run mode records statements instead of executing them, so the exact SQL a scenario would
run can be read before it is trusted.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

import psycopg

from twin.verify.guard import READ_ONLY, assert_safe


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
        """The shadow role. Deliberately not the role dbt uses to build the estate."""
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

    The database's own error is the evidence the verifier grades itself against, so it is
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

    @contextmanager
    def _transaction(self, classification: str) -> Iterator[Any]:
        """A cursor in the transaction mode this statement's classification requires.

        Statements the guard routed as read-only run inside ``BEGIN READ ONLY``, so that
        PostgreSQL refuses any write nested anywhere inside them. This is the layer that
        makes the read-only path actually read-only: the guard classifies on a leading
        keyword, and a leading keyword is not evidence — ``WITH x AS (DELETE …)`` and
        ``EXPLAIN ANALYZE DELETE …`` are both classified read-only and both modify data if
        they are allowed to reach a writable connection.

        The two obvious shortcuts do not work and were tested rather than assumed.
        Tightening the guard's regex cannot help, because a write can be nested at any
        depth. Setting ``psycopg``'s connection-level ``read_only`` flag is silently
        ineffective here: with ``autocommit`` on, both statements above still executed and
        deleted rows. An explicit transaction is what the server honours.

        A failed statement leaves the transaction aborted, where PostgreSQL treats COMMIT
        as ROLLBACK — which is why the commit is unconditional rather than skipped on the
        error path. Nothing in a read-only transaction can have anything to commit.
        """
        read_only = classification == READ_ONLY
        with self._connection.cursor() as cursor:
            if read_only:
                cursor.execute("BEGIN READ ONLY")
            try:
                yield cursor
            finally:
                if read_only:
                    cursor.execute("COMMIT")

    def execute(self, sql: str) -> None:
        """Run a statement, or record it if this is a dry run."""
        classification = assert_safe(sql, self.schema)
        self.issued.append(sql)
        if self.dry_run:
            return
        with self._transaction(classification) as cursor:
            cursor.execute(sql)

    def try_execute(self, sql: str) -> QueryFailure | None:
        """Run a statement and return the warehouse's error rather than raising.

        Used where a failure *is* the observation — running a consumer's query against a
        broken estate is expected to fail, and the error text is the finding.
        """
        classification = assert_safe(sql, self.schema)
        self.issued.append(sql)
        if self.dry_run:
            return None
        try:
            with self._transaction(classification) as cursor:
                cursor.execute(sql)
                if cursor.description:
                    cursor.fetchall()
        except psycopg.Error as exc:
            return QueryFailure(statement=sql, error=str(exc).strip().splitlines()[0])
        return None

    def fetch(self, sql: str) -> list[tuple[Any, ...]]:
        classification = assert_safe(sql, self.schema)
        self.issued.append(sql)
        if self.dry_run:
            return []
        with self._transaction(classification) as cursor:
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
