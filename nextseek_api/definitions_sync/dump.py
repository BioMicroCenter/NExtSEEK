"""Every definition table of one box as one JSON bundle, keyed by code or title (never by id: ids differ per box).

Two ways in, one function (``bundle``):
- On a box whose image lacks this package (production today), fed over stdin into the app container, which holds
  the database credentials, so no secret leaves the box. Read only: one READ ONLY transaction, 60 s per statement:

      timeout -k 10 300 docker exec -i nextseek /app/.venv/bin/python - < nextseek_api/definitions_sync/dump.py

- In process, by ``manage.py definitions_sync``, through Django's ``default`` connection.

Standard library plus mysql-connector (in the app image). Foreign ids are resolved to titles on the box itself and
dropped; fields starting with ``_`` are context (timestamps, per-box id checks, sample counts), never compared.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

# Per-box or SEEK-managed columns, never compared between boxes.
_ST_DROP = ("id", "contributor_id", "policy_id", "template_id", "created_at", "updated_at", "uuid", "first_letter",
            "deleted_contributor", "other_creators")
_PLAIN_DROP = ("id", "created_at", "updated_at", "first_letter")


def _plain(v):
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def bundle(q, dmac: str, seek: str) -> dict:
    """``q(sql, params)`` returns rows as dicts. ``dmac`` and ``seek`` are the two schema names on this box."""
    def rows(sql, params=()):
        return [{k: _plain(v) for k, v in r.items()} for r in q(sql, params)]

    def table(schema, name):
        if not rows("SELECT 1 AS x FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                    (schema, name)):
            return []   # absent on this box
        return rows(f"SELECT * FROM `{schema}`.`{name}`")

    def by_id(rs, field="title"):
        return {r["id"]: r[field] for r in rs}

    S, D = seek, dmac
    st_rows = table(S, "sample_types")
    st = by_id(st_rows)
    at = by_id(table(S, "sample_attribute_types"))
    cv_rows = table(S, "sample_controlled_vocabs")
    cv = by_id(cv_rows)
    units = table(S, "units")
    un = by_id(units)   # by title: the attribute API names a unit by its title
    tg = by_id(table(S, "isa_tags"))
    projects = by_id(rows(f"SELECT id, title FROM `{S}`.projects"))
    pst = rows(f"SELECT project_id, sample_type_id FROM `{S}`.projects_sample_types")

    T = {}
    T["seek.sample_types"] = [
        {**{k: v for k, v in r.items() if k not in _ST_DROP},
         "_created_at": r.get("created_at"), "_updated_at": r.get("updated_at"),
         "_projects": sorted(projects.get(p["project_id"], f"?{p['project_id']}") for p in pst
                             if p["sample_type_id"] == r["id"])}
        for r in st_rows]
    T["seek.sample_attribute_types"] = [{k: v for k, v in r.items() if k not in _PLAIN_DROP}
                                        for r in table(S, "sample_attribute_types")]
    T["seek.sample_controlled_vocabs"] = [{k: v for k, v in r.items() if k not in _PLAIN_DROP} for r in cv_rows]
    T["seek.sample_controlled_vocab_terms"] = [
        {"vocab": cv.get(r["sample_controlled_vocab_id"], f"?{r['sample_controlled_vocab_id']}"),
         **{k: v for k, v in r.items() if k not in _PLAIN_DROP + ("sample_controlled_vocab_id",)}}
        for r in table(S, "sample_controlled_vocab_terms")]
    fk = {"sample_type_id": ("sample_type", st), "sample_attribute_type_id": ("attribute_type", at),
          "sample_controlled_vocab_id": ("vocab", cv), "linked_sample_type_id": ("linked_sample_type", st),
          "unit_id": ("unit", un), "isa_tag_id": ("isa_tag", tg)}
    attrs = []
    for r in table(S, "sample_attributes"):
        row = {"_created_at": r.get("created_at"), "_updated_at": r.get("updated_at")}
        for k, v in r.items():
            if k in ("id", "created_at", "updated_at", "template_attribute_id"):
                continue
            if k in fk:
                name, m = fk[k]
                row[name] = None if v is None else m.get(v, f"?{v}")
            else:
                row[k] = v
        attrs.append(row)
    T["seek.sample_attributes"] = attrs
    T["seek.units"] = [{k: v for k, v in r.items() if k not in _PLAIN_DROP} for r in units]
    # What an attribute create rewrites on this box (sample_type_id is indexed).
    T["seek.sample_counts"] = [{"sample_type": st.get(r["sample_type_id"], f"?{r['sample_type_id']}"), "samples": r["n"]}
                               for r in rows(f"SELECT sample_type_id, COUNT(*) AS n FROM `{S}`.samples "
                                             "GROUP BY sample_type_id")]

    st_ids = {}
    for i, t in st.items():
        st_ids.setdefault(t, []).append(i)
    T["dmac.sample_types_context"] = [
        {**{k: v for k, v in r.items() if k not in ("id", "sampletype_id")},
         "_sampletype_id_matches_seek": r.get("sampletype_id") in st_ids.get(r.get("sample_type"), [])}
        for r in table(D, "sample_types_context")]
    ia = table(D, "internal_assays")
    ia_t = by_id(ia, "internal_assay_title")
    T["dmac.internal_assays"] = [{"internal_assay_title": r["internal_assay_title"]} for r in ia]
    T["dmac.assay_context"] = [
        {**{k: v for k, v in r.items() if k not in ("id", "internal_assay_id")},
         "_internal_assay_title": None if r.get("internal_assay_id") is None
         else ia_t.get(r["internal_assay_id"], f"?{r['internal_assay_id']}")}
        for r in table(D, "assay_context")]
    cl = table(D, "clades")
    cl_t = by_id(cl)
    T["dmac.clades"] = [{k: v for k, v in r.items() if k != "id"} for r in cl]
    T["dmac.sample_types_clades"] = [
        {"sample_type": st.get(r["sample_type_id"], f"?{r['sample_type_id']}"),
         "clade": None if r.get("clade_id") is None else cl_t.get(r["clade_id"], f"?{r['clade_id']}")}
        for r in table(D, "sample_types_clades")]
    T["dmac.sample_attributes_unique"] = [{k: v for k, v in r.items() if k != "id"}
                                          for r in table(D, "sample_attributes_unique")]
    return {"exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": os.environ.get("HOSTNAME", ""), "schemas": {"dmac": dmac, "seek": seek},
            "counts": {k: len(v) for k, v in T.items()}, "tables": T}


def main() -> int:
    import mysql.connector
    conn = mysql.connector.connect(host=os.environ["MYSQL_HOST"], user=os.environ["MYSQL_USER"],
                                   password=os.environ["MYSQL_PASSWORD"], connection_timeout=30)
    try:
        conn.start_transaction(readonly=True)
        cur = conn.cursor(dictionary=True)
        cur.execute("SET SESSION MAX_EXECUTION_TIME=60000")

        def q(sql, params=()):
            cur.execute(sql, params)
            return cur.fetchall()

        out = bundle(q, os.environ.get("NEXTSEEK_MYSQL_DATABASE") or "dmac", os.environ.get("MYSQL_DATABASE") or "seek")
        conn.rollback()
    finally:
        conn.close()
    json.dump(out, sys.stdout, ensure_ascii=False, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
