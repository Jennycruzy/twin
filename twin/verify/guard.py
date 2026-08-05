"""The execution boundary: what Twin is allowed to send to the warehouse.

This is the second of the two guards described in docs/SAFETY.md, and the two exist because
they fail differently. The database guard cannot be bypassed by a bug in Twin — ``twin_shadow``
owns nothing in the estate, so PostgreSQL will refuse to drop or alter a real table whatever
statement it receives. But the database has no idea what Twin *meant* to do, so it cannot
stop Twin from destroying the wrong thing inside a shadow schema. This guard understands
intent and is only as correct as the code below, which is why it is not the only layer.

The rule it enforces is narrow and mechanical. Every statement Twin issues is either

* read-only, and may read anything the role can see, including the real estate; or
* destructive, in which case the object it acts on must be inside this run's shadow schema.

Anything the guard cannot confidently classify is refused. A guard that guesses is worse
than no guard, because it produces confidence rather than safety. The consequence is that
adding a new kind of fault means teaching the guard about it deliberately, which is the
intended cost.
"""

from __future__ import annotations

import re

# Non-configurable. A prefix that can be overridden by a scenario file, an environment
# variable or a command-line flag is not a guarantee, it is a default, and the difference
# is the whole point of this module.
SHADOW_PREFIX = "twin_shadow_"

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_QUOTED_IDENTIFIER = re.compile(r'"(?:[^"]|"")*"')
_WHITESPACE = re.compile(r"\s+")

_IDENTIFIER = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'

# Statements that cannot modify anything. They may name estate objects freely: cloning a
# slice into a shadow schema means reading the real tables, and forbidding that would
# forbid the feature.
_READ_ONLY = re.compile(r"^(select|with|explain|show)\b", re.IGNORECASE)

# Destructive forms Twin is allowed to issue, each paired with the object it acts on. The
# list is short on purpose: it grows only when a stage genuinely needs a new form, and each
# addition is a decision someone made rather than a pattern that happened to match.
_DESTRUCTIVE = (
    re.compile(rf"^create\s+schema\s+(?:if\s+not\s+exists\s+)?({_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^drop\s+schema\s+(?:if\s+exists\s+)?({_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^create\s+(?:or\s+replace\s+)?(?:temp\s+|temporary\s+)?table\s+(?:if\s+not\s+exists\s+)?({_IDENTIFIER}\.{_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^create\s+(?:or\s+replace\s+)?view\s+({_IDENTIFIER}\.{_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^drop\s+table\s+(?:if\s+exists\s+)?({_IDENTIFIER}\.{_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^drop\s+view\s+(?:if\s+exists\s+)?({_IDENTIFIER}\.{_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^alter\s+table\s+(?:if\s+exists\s+)?({_IDENTIFIER}\.{_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^truncate\s+(?:table\s+)?({_IDENTIFIER}\.{_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^insert\s+into\s+({_IDENTIFIER}\.{_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^update\s+({_IDENTIFIER}\.{_IDENTIFIER})", re.IGNORECASE),
    re.compile(rf"^delete\s+from\s+({_IDENTIFIER}\.{_IDENTIFIER})", re.IGNORECASE),
)


class UnsafeStatement(RuntimeError):
    """A statement was refused before it reached the warehouse."""


def is_shadow_schema(name: str) -> bool:
    """Whether a schema name is a shadow schema at all."""
    return name.startswith(SHADOW_PREFIX)


def normalise(sql: str) -> str:
    """Strip comments and collapse whitespace, leaving quoted text untouched.

    Comments are removed before a statement is classified so that a destructive verb cannot
    be hidden behind one. They cannot be removed with a regex, because ``--`` and ``/*``
    inside a string literal or a quoted identifier are data rather than comment markers, and
    treating them as markers would silently rewrite the statement the guard then reasons
    about. So the text is scanned once, and quoted regions are copied through verbatim.
    """
    out: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]

        if char in ("'", '"'):
            end = _end_of_quoted(sql, index, char)
            out.append(sql[index:end])
            index = end
            continue

        if sql.startswith("--", index):
            newline = sql.find("\n", index)
            index = length if newline == -1 else newline
            out.append(" ")
            continue

        if sql.startswith("/*", index):
            close = sql.find("*/", index + 2)
            index = length if close == -1 else close + 2
            out.append(" ")
            continue

        out.append(char)
        index += 1

    return _WHITESPACE.sub(" ", "".join(out)).strip().rstrip(";").strip()


def _end_of_quoted(sql: str, start: int, quote: str) -> int:
    """Index just past the quoted region beginning at ``start``.

    A doubled quote is an escaped quote rather than the end of the region, in both string
    literals and quoted identifiers. An unterminated region runs to the end of the input,
    which leaves the statement unrecognisable and therefore refused.
    """
    index = start + 1
    length = len(sql)
    while index < length:
        if sql[index] == quote:
            if index + 1 < length and sql[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return length


def _statement_count(sql: str) -> int:
    """How many statements this text contains, ignoring semicolons inside quoted text.

    Multi-statement strings are the classic way past a guard that inspects only the first
    verb it finds, so they are counted rather than assumed to be one.
    """
    masked = _QUOTED_IDENTIFIER.sub('""', _STRING_LITERAL.sub("''", sql))
    return len([part for part in masked.split(";") if part.strip()])


def _unquote(identifier: str) -> str:
    return identifier.strip('"')


def _schema_of(target: str) -> str:
    """The schema half of ``schema.object``, or the whole thing for a bare schema name."""
    return _unquote(target.split(".", 1)[0])


def assert_safe(sql: str, shadow_schema: str) -> None:
    """Raise :class:`UnsafeStatement` unless this statement may be executed.

    ``shadow_schema`` is the schema this run owns. Naming *any other* schema in a
    destructive statement is refused even if that schema is itself a shadow schema, so a
    run cannot reach into a concurrent run's workspace.
    """
    if not is_shadow_schema(shadow_schema):
        raise UnsafeStatement(
            f"refusing to operate against {shadow_schema!r}: not a {SHADOW_PREFIX}* schema"
        )

    statement = normalise(sql)
    if not statement:
        raise UnsafeStatement("refusing an empty statement")

    if _statement_count(statement) > 1:
        raise UnsafeStatement(
            "refusing a multi-statement string: each statement must be issued and checked "
            f"on its own — {statement[:120]!r}"
        )

    if _READ_ONLY.match(statement):
        return

    for pattern in _DESTRUCTIVE:
        match = pattern.match(statement)
        if not match:
            continue
        target = match.group(1)
        schema = _schema_of(target)
        if schema == shadow_schema:
            return
        raise UnsafeStatement(
            f"refusing to modify {target!r}: outside this run's shadow schema "
            f"{shadow_schema!r}"
        )

    raise UnsafeStatement(
        f"refusing a statement the execution boundary does not recognise: {statement[:120]!r}"
    )
