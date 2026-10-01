"""compute_over_rows: the follow-up loop computes over rows it already has.

"Which of those have SHA in their UID?" about 731 stored records, or a breakdown by species past the five values
``column_summary`` shows, can be answered from rows already in hand, without a new query. The loop's model declares
what it filters (``where``) and what it counts by (``group_by``); both run here as plain Python, so the reviewer
(``graph_review.review_compute``) sees exactly which values a filter matched. Anything they cannot express goes in
``code``, the memory-code subset, which runs in a separate, limited process (``run_code_isolated``).

It refuses, and sends the model to run_new_query, whenever the rows cannot speak for the whole set: they are not all
of it, there are none, there are more than it takes, or they lack a column the call names. A zero from it only means
these rows do not show it.
"""
from __future__ import annotations

import json
from typing import Any

from ..helpers import strip_html_recursive
from ..helpers.tools.row_compute import run_code_isolated
from .followup import FOLLOWUP_ROWS_CHARS, _summary_value, preview_rows

#: The most rows one call computes over. Past this, count or break them down in a query.
COMPUTE_ROWS_MAX = 20_000
#: The most groups a ``group_by`` returns; ``groups_total`` says how many there were.
COMPUTE_GROUPS_MAX = 50
#: How many of the values a ``contains`` matched are listed, most common first.
MATCHED_VALUES_MAX = 10
#: How many column names a payload lists.
COLUMNS_MAX = 60
GROUP_BY_MAX = 2
OPS = ("equals", "contains", "in", "present", "absent")

RESULT_TOO_LARGE = ("The result is too large to show here. Compute a smaller answer: a count, the top values, "
                    "or the first few.")


class _CallError(ValueError):
    """A malformed call: reported to the model as an error, not as a reason to query."""


class _Missing(LookupError):
    """A column the rows do not hold."""


def _text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _present(value: Any) -> bool:
    return value is not None and bool(_text(value).strip())


def _as_mapping(value: Any) -> dict | None:
    """A cell holding a dict, or a JSON string of one (``json_metadata`` comes both ways)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _key_in(mapping: dict, name: str) -> str | None:
    """``name`` as a key of ``mapping``: exactly, else the one key equal to it ignoring case."""
    if name in mapping:
        return name
    folded = [k for k in mapping if isinstance(k, str) and k.lower() == name.lower()]
    return folded[0] if len(folded) == 1 else None


class _Columns:
    """Resolves a column name the model wrote to the cells of every row, once per name."""

    def __init__(self, rows: list):
        self.rows = rows
        self.keys = list(dict.fromkeys(k for row in rows if isinstance(row, dict) for k in row if isinstance(k, str)))
        self.shown = sorted(self.keys, key=str.lower)[:COLUMNS_MAX]
        self._cells: dict[str, tuple[str, list]] = {}

    def _plain(self, name: str) -> str | None:
        if name in self.keys:
            return name
        folded = [k for k in self.keys if k.lower() == name.lower()]
        if len(folded) > 1:
            raise _CallError(f"The column name {name!r} matches several columns ({', '.join(folded)}); "
                             "name one exactly.")
        return folded[0] if folded else None

    def cells(self, name: Any) -> tuple[str, list]:
        """``(the column's name in the rows, one cell per row)``. Raises ``_Missing`` when no column resolves."""
        if not isinstance(name, str) or not name.strip():
            raise _CallError("Each column must be named with a non-empty string.")
        name = name.strip()
        if name in self._cells:
            return self._cells[name]
        column = self._plain(name)
        if column is not None:
            got = (column, [row.get(column) if isinstance(row, dict) else None for row in self.rows])
        else:
            got = self._field(name)
        self._cells[name] = got
        return got

    def _field(self, name: str) -> tuple[str, list]:
        """``head.Field``: a field of a dict or JSON-string column, such as ``json_metadata.Type``."""
        head, dot, field = name.partition(".")
        column = self._plain(head) if dot and field else None
        if column is None:
            raise _Missing(name)
        mappings = [_as_mapping(row.get(column)) if isinstance(row, dict) else None for row in self.rows]
        key = next((k for m in mappings if m for k in [_key_in(m, field)] if k is not None), None)
        if key is None:
            raise _Missing(name)
        cells = []
        for m in mappings:
            own = _key_in(m, key) if m else None
            cells.append(m.get(own) if own is not None else None)
        return f"{column}.{key}", cells


def _refusal(error: str, columns: list[str]) -> dict:
    return {"ok": False, "needs_query": True, "error": error, "columns": columns}


def _failure(error: str, columns: list[str], where_log: list | None = None) -> dict:
    return {"ok": False, "error": error, "columns": columns, "where": where_log or []}


def _check_entry(entry: Any) -> tuple[Any, str, Any]:
    if not isinstance(entry, dict):
        raise _CallError("Each `where` entry must be an object with column and op.")
    op = entry.get("op")
    if op not in OPS:
        raise _CallError(f"`op` must be one of {', '.join(OPS)}; got {op!r}.")
    value = entry.get("value")
    if op in ("equals", "contains"):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise _CallError(f"`{op}` needs a string `value`.")
        if op == "contains" and not str(value).strip():
            raise _CallError("`contains` needs a non-empty `value`.")
    elif op == "in":
        if (not isinstance(value, list) or not value
                or any(isinstance(v, bool) or not isinstance(v, (str, int, float)) for v in value)):
            raise _CallError("`in` needs a non-empty list of strings as `value`.")
    return entry.get("column"), op, value


def _matcher(op: str, value: Any):
    if op == "present":
        return _present
    if op == "absent":
        return lambda cell: not _present(cell)
    if op == "contains":
        needle = str(value).lower()
        return lambda cell: cell is not None and needle in _text(cell).lower()
    wanted = {str(v).strip().lower() for v in (value if op == "in" else [value])}
    return lambda cell: cell is not None and _text(cell).strip().lower() in wanted


def _ranked(counts: dict, shown: dict) -> list:
    return sorted(counts, key=lambda k: (-counts[k], str(shown[k])))


def compute_over_rows(*, rows: list, total: int | None, complete: bool, where: list[dict] | None,
                      group_by: list[str] | None, code: str | None) -> dict:
    """Filter, group and compute over ``rows``, all of them, and say what each filter matched.

    ``total`` is the size of the set the rows came from (None when it is not known) and ``complete`` whether the rows
    are all of it. Returns ``{ok, needs_query?, error?, columns, where, count, groups?, groups_total?, group_nulls?,
    result?, result_truncated?, result_chars?, note?, rows?, rows_shown?, scope_note?}``:

    * ``where``: each filter as applied, with ``rows_after``; a ``contains`` also lists the values it matched
      (``matched_values``, ``[value, rows]``, most common first) and how many distinct ones (``distinct_matched``).
      Filters are AND-ed and case-insensitive; a null cell never matches a value.
    * ``groups``: rows per value of the ``group_by`` columns, largest first; a row with a null or missing value in one
      of them is in no group and counted in ``group_nulls``.
    * ``result``: what ``code`` assigned to ``result``, run over the rows left after ``where``.
    * ``scope_note``: set when the total is not known, because the answer then covers the rows in hand only.

    ``ok`` false with ``needs_query`` true means these rows cannot answer (not the whole set, none, too many, or a
    column they do not hold); ``ok`` false without it means the call itself failed. ``count`` is present only when
    ``ok``. Never raises.
    """
    rows = list(rows or [])
    n = len(rows)
    columns = _Columns(rows[:COMPUTE_ROWS_MAX])
    shown_columns = columns.shown
    if not rows:
        return _refusal("This result kept no rows, so there is nothing here to compute over. Use run_new_query.",
                        shown_columns)
    if not complete:
        part = f"{n:,} of {total:,}" if isinstance(total, int) and not isinstance(total, bool) else f"{n:,}"
        return _refusal(f"The rows in hand are {part}, not the whole set, so a computation over them would describe "
                        "only part of it. Use run_new_query.", shown_columns)
    if n > COMPUTE_ROWS_MAX:
        return _refusal(f"There are {n:,} rows, more than this tool takes ({COMPUTE_ROWS_MAX:,}). Use run_new_query "
                        "and count or break them down in the query itself.", shown_columns)
    # A copy with markup stripped: the model's filters read the values a user sees, and the caller's rows stay as
    # they are.
    columns = _Columns(strip_html_recursive(rows))
    where_log: list[dict] = []
    try:
        entries = [where] if isinstance(where, dict) else (where if where is not None else [])
        if not isinstance(entries, list):
            raise _CallError("`where` must be a list of filters.")
        keep = list(range(n))
        for entry in entries:
            column, op, value = _check_entry(entry)
            name, cells = columns.cells(column)
            match = _matcher(op, value)
            keep = [i for i in keep if match(cells[i])]
            logged: dict[str, Any] = {"column": name, "op": op}
            if op not in ("present", "absent"):
                logged["value"] = value
            logged["rows_after"] = len(keep)
            if op == "contains":
                counts: dict = {}
                shown: dict = {}
                for i in keep:
                    key, val = _summary_value(cells[i])
                    counts[key] = counts.get(key, 0) + 1
                    shown.setdefault(key, val)
                logged["matched_values"] = [[shown[k], counts[k]] for k in _ranked(counts, shown)[:MATCHED_VALUES_MAX]]
                logged["distinct_matched"] = len(counts)
            where_log.append(logged)

        groups_by = [group_by] if isinstance(group_by, str) else (group_by if group_by is not None else [])
        if not isinstance(groups_by, list) or len(groups_by) > GROUP_BY_MAX:
            raise _CallError("`group_by` must be a list of one or two columns.")
        grouped = [columns.cells(name) for name in groups_by]
        if code is not None and not isinstance(code, str):
            raise _CallError("`code` must be a string.")
    except _Missing as missing:
        return _refusal(f"These rows do not hold {missing.args[0]}; they hold: {', '.join(shown_columns)}. A property "
                        "they do not hold needs run_new_query; do not report a zero.", shown_columns)
    except _CallError as bad:
        return _failure(str(bad), shown_columns, where_log)

    kept = [columns.rows[i] for i in keep]
    out: dict[str, Any] = {"ok": True, "columns": shown_columns, "where": where_log, "count": len(kept)}

    if grouped:
        names = [name for name, _cells in grouped]
        count_key = "n" if "n" not in names else "rows"
        counts = {}
        shown = {}
        nulls = 0
        for i in keep:
            cells = [col_cells[i] for _name, col_cells in grouped]
            if any(c is None for c in cells):
                nulls += 1
                continue
            parts = [_summary_value(c) for c in cells]
            key = tuple(k for k, _v in parts)
            counts[key] = counts.get(key, 0) + 1
            shown.setdefault(key, [v for _k, v in parts])
        out["groups"] = [{**dict(zip(names, shown[k])), count_key: counts[k]}
                         for k in _ranked(counts, shown)[:COMPUTE_GROUPS_MAX]]
        out["groups_total"] = len(counts)
        if nulls:
            out["group_nulls"] = nulls

    if code is not None:
        run = run_code_isolated(code, {"data": {"rows": kept}})
        too_large = not run["ok"] and isinstance(run.get("result_bytes"), int)  # it ran; its result could not come back
        if not run["ok"] and not too_large:
            failed = _failure(run["error"], shown_columns, where_log)
            if str(run["error"]).startswith("the data is too large"):
                failed["needs_query"] = True
            return failed
        size = (run["result_bytes"] if too_large
                else len(json.dumps(run["result"], separators=(",", ":"), default=str)))
        if size > FOLLOWUP_ROWS_CHARS:
            out.update(result=None, result_truncated=True, result_chars=size, note=RESULT_TOO_LARGE)
        else:
            out["result"] = run["result"]
    elif not grouped:
        preview = preview_rows(kept)
        out["rows"] = preview
        out["rows_shown"] = len(preview)

    if total is None:
        out["scope_note"] = (f"No total was stored for the earlier result, so this covers the {n:,} rows in hand, "
                             "which may not be the whole set: say so.")
    return out
