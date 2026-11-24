"""Time ranges (SniffOptions / SniffMagic): validation and the matching SQL conditions.

``start`` / ``end`` are Unix seconds and may each be None (open end). Commands match when their
start time lies in the range; sessions match when they *overlap* it.
"""


class RangeError(ValueError):
    """The range ends before it starts (shown to the user)."""


def check(start: float | None, end: float | None) -> None:
    if start is not None and end is not None and start > end:
        raise RangeError("time range: start is after end")


def commands_sql(start: float | None, end: float | None, column: str = "e.ts") -> tuple[str, list]:
    """`` AND <column> >= ? AND <column> <= ?`` for the given ends, plus the arguments."""
    sql, args = "", []
    if start is not None:
        sql += f" AND {column} >= ?"
        args.append(start)
    if end is not None:
        sql += f" AND {column} <= ?"
        args.append(end)
    return sql, args


def sessions_sql(start: float | None, end: float | None, alias: str = "") -> tuple[str, list]:
    """Condition for sessions (``blocks``) that overlap the range."""
    p = f"{alias}." if alias else ""
    sql, args = "", []
    if start is not None:
        sql += f" AND {p}end_ts >= ?"
        args.append(start)
    if end is not None:
        sql += f" AND {p}start_ts <= ?"
        args.append(end)
    return sql, args
