"""Lab records mined from SEEK institution titles.

SEEK records every lab as an Institution whose title reads ``<CODE>-<Name> Lab
(<Affiliation>)``: ``CODE`` is the three-letter code sample UIDs carry
(``TYPE-YYMMDDCODE-n``) and ``Name`` is the lab head's surname. This module reads those
titles, with the projects each institution belongs to, into ``labs_db.json`` in the
context directory. The design is ``docs/superpowers/specs/2026-09-18-projects-labs-context.md``
(sections 4 to 6).

* **Source.** One fixed SELECT over the SEEK database (``INSTITUTIONS_SQL``), run by
  ``fetch_institution_rows`` inside a read-only transaction, so the server refuses any
  write. Moving to the SEEK API later replaces that one function.
* **Cadence.** None of its own. ``ChatConfig._fetch_context_files_from_db`` calls
  ``refresh_labs_file`` first, so the read runs exactly when the context export does: at
  most once per UTC day per starting process, never per turn.
* **Grammar.** One strict regular expression after NFC, strip and whitespace collapse,
  and nothing else repaired. A title that does not fit is reported with a reason code,
  never guessed.
* **The file** is runtime-only: gitignored, excluded from the build context and never
  baked into the cc-agent image, because it holds real lab titles.
* ``python -m chat_nextseek.labs --report`` runs the same read and prints the document
  without writing anything. It is the operator's first look at every title's fate.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

LABS_FILE_NAME = "labs_db.json"
DOCUMENT_VERSION = 1

# The schema name is spelled exactly as ChatConfig._load_name_to_id_from_db spells it. One
# statement, SELECT only, no interpolation: nothing about it comes from outside this module.
INSTITUTIONS_SQL = (
    "SELECT i.id AS institution_id, i.title AS title, wg.project_id AS project_id\n"
    "FROM seek_production.institutions AS i\n"
    "LEFT JOIN seek_production.work_groups AS wg ON wg.institution_id = i.id\n"
    "ORDER BY i.id, wg.project_id"
)
SOURCE = "seek_production.institutions LEFT JOIN seek_production.work_groups (one read-only SELECT)"

TITLE_GRAMMAR = re.compile(
    r"^(?P<code>[A-Z]{3})-(?P<name>[^\s()][^()]*?) Lab \((?P<affiliation>[^()]*[^\s()][^()]*)\)$"
)

# Reason codes for a title that does not fit, in the order they are tested. First hit wins.
REASONS = (
    "no_title",
    "non_ascii_dash",
    "code_not_three_letters",
    "no_code_prefix",
    "no_lab_word",
    "no_affiliation",
    "trailing_text",
    "bad_name_characters",
)

# Dashes that are not the ASCII hyphen-minus: hyphen, non-breaking hyphen, figure dash,
# en dash, em dash, horizontal bar, minus sign, small and fullwidth hyphen-minus.
_NON_ASCII_DASHES = "‐‑‒–—―−﹘﹣－"
# The code-prefix position: a run of letters, then a dash of any kind, spaces allowed so
# they can be reported. Only an exact "<CODE>-" with no space fits the grammar.
_PREFIX = re.compile(rf"^(?P<letters>[^\W\d_]+)(?P<pre>\s*)(?P<dash>[-{_NON_ASCII_DASHES}])(?P<post>\s*)")
# The word "Lab", capital L, as a whole word.
_LAB_WORD = re.compile(r"(?:^|(?<= ))Lab(?=$|[\s(])")
_AFFILIATION = re.compile(r"^ \((?P<affiliation>[^()]*[^\s()][^()]*)\)(?P<tail>.*)$")
# Apostrophes a surname may carry: ASCII, right single quotation mark, modifier letter.
_NAME_PUNCTUATION = frozenset(" -.'’ʼ")


def normalise_title(title) -> str | None:
    """NFC, strip, collapse runs of whitespace to one space. Nothing else is repaired.

    Bytes are decoded as UTF-8; anything that is not text is ``None``.
    """
    if isinstance(title, (bytes, bytearray)):
        title = bytes(title).decode("utf-8", errors="replace")
    if not isinstance(title, str):
        return None
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", title)).strip()


def _name_is_clean(name: str) -> bool:
    """Letters of any script, spaces, hyphens, apostrophes and periods; starts with a letter."""
    if not name or not name[0].isalpha():
        return False
    for ch in name:
        if ch.isalpha() or ch in _NAME_PUNCTUATION or unicodedata.category(ch).startswith("M"):
            continue
        return False
    return True


def parse_title(title) -> tuple[dict | None, str | None]:
    """Parse one institution title.

    Returns ``({"code", "name", "affiliation"}, None)`` for a title in the grammar, and
    ``(None, reason)`` for one that is not, ``reason`` being the first of ``REASONS`` that
    applies.
    """
    text = normalise_title(title)
    if not text:
        return None, "no_title"

    match = TITLE_GRAMMAR.match(text)
    if match:
        name = match.group("name")
        if not _name_is_clean(name):
            return None, "bad_name_characters"
        return {
            "code": match.group("code"),
            "name": name,
            "affiliation": match.group("affiliation").strip(),
        }, None

    prefix = _PREFIX.match(text)
    if prefix and prefix.group("dash") != "-":
        return None, "non_ascii_dash"
    if prefix and not re.fullmatch(r"[A-Z]{3}", prefix.group("letters")):
        return None, "code_not_three_letters"
    if not prefix or prefix.group("pre") or prefix.group("post"):
        return None, "no_code_prefix"

    rest = text[prefix.end():]
    # Split at the first "Lab" that opens an affiliation, else at the first "Lab" at all.
    opening = re.search(r"(?:^|(?<= ))Lab \(", rest)
    lab = opening or _LAB_WORD.search(rest)
    if lab is None:
        return None, "no_lab_word"
    after = rest[lab.start() + len("Lab"):]
    affiliation = _AFFILIATION.match(after)
    if affiliation is None:
        return None, "no_affiliation"
    if affiliation.group("tail"):
        return None, "trailing_text"
    return None, "bad_name_characters"


# --------------------------------------------------------------------------------------
# The read
# --------------------------------------------------------------------------------------

def _as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        text = str(value).strip()
    except Exception:
        return None
    return int(text) if text.isdigit() else None


def _row_triple(row) -> tuple[int | None, str | None, int | None]:
    if isinstance(row, dict):
        iid, title, pid = row.get("institution_id"), row.get("title"), row.get("project_id")
    else:
        iid, title, pid = (list(row) + [None, None, None])[:3]
    if isinstance(title, (bytes, bytearray)):
        title = bytes(title).decode("utf-8", errors="replace")
    return _as_int(iid), title if isinstance(title, str) else None, _as_int(pid)


def fetch_institution_rows(conn) -> list[tuple[int | None, str | None, int | None]]:
    """Run ``INSTITUTIONS_SQL`` in its own read-only transaction; rows are ``(institution_id, title, project_id)``.

    The app's MySQL user can write, so read-only is enforced by the server, not by trust:
    end any implicit transaction left open on the connection, open a read-only one, run
    the one SELECT, then end it. A failure raises; ``refresh_labs_file`` catches it.
    """
    conn.rollback()
    conn.start_transaction(readonly=True)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(INSTITUTIONS_SQL)
            rows = cursor.fetchall() or []
        finally:
            try:
                cursor.close()
            except Exception:
                pass
    finally:
        conn.rollback()
    return [_row_triple(r) for r in rows]


# --------------------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------------------

def fold_name(name: str) -> str:
    """Compare surnames as the entity agent does: NFKC, apostrophes, no accents, casefold."""
    text = unicodedata.normalize("NFKC", name)
    text = text.replace("’", "'").replace("‘", "'").replace("ʼ", "'")
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_labs_document(rows, fetched_at: str | None = None) -> dict:
    """Group the fetched rows by institution, parse every title, and report the rest.

    ``labs`` is sorted by code, then institution id; ``unparsed`` by institution id;
    ``project_ids`` are sorted, de-duplicated and ``[]`` for an institution in no project.
    Conflicting records are all kept, and the conflicts listed.
    """
    institutions: dict[int | None, dict] = {}
    for row in rows:
        iid, title, pid = _row_triple(row)
        entry = institutions.setdefault(iid, {"title": title, "project_ids": set()})
        if entry["title"] is None and title is not None:
            entry["title"] = title
        if pid is not None:
            entry["project_ids"].add(pid)

    records: list[dict] = []
    unparsed: list[dict] = []
    for iid, entry in institutions.items():
        project_ids = sorted(entry["project_ids"])
        parsed, reason = parse_title(entry["title"])
        if parsed is None:
            unparsed.append({"institution_id": iid, "title": entry["title"],
                             "project_ids": project_ids, "reason": reason})
            continue
        records.append({**parsed, "title": entry["title"], "institution_id": iid, "project_ids": project_ids})

    def _id_key(value):
        return (value is None, value if value is not None else 0)

    records.sort(key=lambda r: (r["code"], _id_key(r["institution_id"])))
    unparsed.sort(key=lambda u: _id_key(u["institution_id"]))

    return {
        "version": DOCUMENT_VERSION,
        "source": SOURCE,
        "fetched_at": fetched_at or _utc_now(),
        "labs": records,
        "unparsed": unparsed,
        "conflicts": _conflicts(records),
    }


def _conflicts(records: list[dict]) -> list[dict]:
    """``name_shared``: one surname under several codes. ``code_shared``: one code, several institutions."""
    by_name: dict[str, dict] = {}
    by_code: dict[str, set] = {}
    for record in records:
        slot = by_name.setdefault(fold_name(record["name"]), {"name": record["name"], "codes": set()})
        slot["codes"].add(record["code"])
        by_code.setdefault(record["code"], set()).add(record["institution_id"])

    conflicts = [
        {"kind": "name_shared", "name": slot["name"], "codes": sorted(slot["codes"])}
        for _, slot in sorted(by_name.items())
        if len(slot["codes"]) > 1
    ]
    conflicts += [
        {"kind": "code_shared", "code": code,
         "institution_ids": sorted(ids, key=lambda v: (v is None, v if v is not None else 0))}
        for code, ids in sorted(by_code.items())
        if len(ids) > 1
    ]
    return conflicts


# --------------------------------------------------------------------------------------
# The file
# --------------------------------------------------------------------------------------

def write_labs_file(doc: dict, context_dir) -> Path:
    """Write ``labs_db.json`` atomically: a temporary file beside it, then ``os.replace``.

    Several gunicorn workers starting on a new UTC day may each run the export, so a
    reader must never see a torn file. A failed write leaves the previous file intact.
    """
    dest = Path(context_dir) / LABS_FILE_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{LABS_FILE_NAME}.", suffix=".tmp", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_name, dest)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return dest


def load_labs_file(context_dir) -> dict | None:
    """The labs document on disk, or ``None`` when it is missing or unreadable."""
    path = Path(context_dir) / LABS_FILE_NAME
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("labs"), list):
        return None
    return doc


def _record_is_valid(record) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("code"), str)
        and re.fullmatch(r"[A-Z]{3}", record["code"]) is not None
        and isinstance(record.get("name"), str)
        and bool(record["name"].strip())
    )


def checked_labs(doc) -> list[dict] | None:
    """The document's lab records with a three-capital code and a non-empty name.

    ``None`` when there is no usable document: unavailable. ``[]`` means SEEK has no
    parseable lab.
    """
    if not isinstance(doc, dict) or not isinstance(doc.get("labs"), list):
        return None
    return [record for record in doc["labs"] if _record_is_valid(record)]


def labs_by_project(doc) -> dict[int, list[dict]]:
    """``{project_id: [{code, name, affiliation}, ...]}``, each list sorted by code.

    Accepts the document, a list of records, or ``None`` (which gives ``{}``). The same
    lab listed under two institutions appears once.
    """
    records = doc if isinstance(doc, list) else checked_labs(doc)
    by_project: dict[int, dict[tuple, dict]] = {}
    for record in records or []:
        if not _record_is_valid(record):
            continue
        lab = {"code": record["code"], "name": record["name"], "affiliation": record.get("affiliation")}
        key = (lab["code"], lab["name"], lab["affiliation"] or "")
        for pid in record.get("project_ids") or []:
            pid = _as_int(pid)
            if pid is not None:
                by_project.setdefault(pid, {})[key] = lab
    return {pid: [labs_[k] for k in sorted(labs_)] for pid, labs_ in by_project.items()}


def log_line(doc: dict) -> str:
    """The one line a refresh logs."""
    unparsed_ids = [u.get("institution_id") for u in doc.get("unparsed") or []]
    kinds: dict[str, int] = {}
    for conflict in doc.get("conflicts") or []:
        kinds[conflict.get("kind")] = kinds.get(conflict.get("kind"), 0) + 1
    conflicts = ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())) or "none"
    return (
        f"[CONFIG][LABS] {len(doc.get('labs') or [])} labs from SEEK institutions; "
        f"unparsed institution ids {unparsed_ids}; conflicts: {conflicts}"
    )


def refresh_labs_file(conn, context_dir) -> tuple[dict | None, str]:
    """Fetch, build and write the labs file; on a failed read fall back to the file on disk.

    Returns ``(document, source)`` with ``source`` one of ``fetched``, ``previous_file``
    or ``unavailable``. Never raises: a SEEK read that fails (no such schema, no such table,
    a timeout) is logged once and the export carries on. A fetched document whose write
    fails is still returned, so this process uses it.
    """
    try:
        doc = build_labs_document(fetch_institution_rows(conn))
    except Exception as exc:
        previous = load_labs_file(context_dir)
        source = "previous_file" if previous is not None else "unavailable"
        print(f"[CONFIG][LABS] SEEK institution read failed: {exc!r}; labs from: {source}")
        return previous, source

    print(log_line(doc))
    try:
        write_labs_file(doc, context_dir)
    except Exception as exc:
        print(f"[CONFIG][LABS] Could not write {LABS_FILE_NAME}: {exc!r}")
    return doc, "fetched"


# --------------------------------------------------------------------------------------
# python -m chat_nextseek.labs --report
# --------------------------------------------------------------------------------------

def connect_prod(env=None):
    """Connect exactly as ``ChatConfig._connect_db(env="prod")`` does; ``None`` on any failure."""
    env = os.environ if env is None else env
    host = env.get("MYSQL_HOST_PROD")
    user = env.get("MYSQL_USER")
    password = env.get("MYSQL_PROD_PASSWORD")
    if not host or not user or not password:
        print("[LABS] MYSQL_HOST_PROD, MYSQL_USER and MYSQL_PROD_PASSWORD must all be set.", file=sys.stderr)
        return None
    try:
        port = int(env.get("MYSQL_PORT")) if env.get("MYSQL_PORT") is not None else 3306
    except ValueError:
        port = 3306
    try:
        import mysql.connector  # type: ignore  # noqa: PLC0415
    except ImportError:
        print("[LABS] mysql-connector-python is not installed.", file=sys.stderr)
        return None
    try:
        return mysql.connector.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            use_pure=True,
        )
    except Exception as exc:
        print(f"[LABS] Connection failed: {exc!r}", file=sys.stderr)
        return None


def main(argv=None, env=None) -> int:
    """Print the labs document built from a live read. Writes nothing.

    Exit 0 when printed, 1 without ``--report`` or when the read fails, 2 when it cannot
    connect.
    """
    parser = argparse.ArgumentParser(
        prog="python -m chat_nextseek.labs",
        description="Read SEEK's institution titles (read only) and print the labs document. Writes nothing.",
    )
    parser.add_argument("--report", action="store_true", help="run the read and print the document")
    args = parser.parse_args(argv)
    if not args.report:
        parser.print_usage(sys.stderr)
        return 1

    if env is None:
        try:
            from dotenv import load_dotenv  # noqa: PLC0415

            load_dotenv()  # as ChatConfig sources its environment; never overrides a set variable
        except Exception:
            pass
    conn = connect_prod(env)
    if conn is None:
        return 2
    try:
        doc = build_labs_document(fetch_institution_rows(conn))
    except Exception as exc:
        print(f"[LABS] The read failed: {exc!r}", file=sys.stderr)
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass
    print(log_line(doc), file=sys.stderr)
    print(json.dumps(doc, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
