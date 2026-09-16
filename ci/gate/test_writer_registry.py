"""The writer registry gate: the scan and ci/writers.py must agree, in both directions.

This is a blocking test, like the route registry beside it. The graph is a
projection of MySQL, so a write to a graph source that nothing tells graph_sync
about leaves the graph wrong until somebody notices; here, it turns the gate red
in the commit that adds it, with the entry to paste.

Four claims:

  * every site the scan finds is declared, and every declared site is still found;
  * every site the scan can see writing but cannot clear is listed as unresolved;
  * every declared hook really is called in the function that declares it (AST),
    and a hook that goes through a helper reaches hooks.enqueue;
  * a proxy's tables are declared rather than inferred from its method name.

Standard library and pytest only: no Django, no database, no network.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ci import writers  # noqa: E402
from ci.gate import writer_scan  # noqa: E402

SKELETON = '''    Writer(id="WR-NN",
           sites="{site}",
           tables=({tables}),
           how=({how}),
           reconcile="RECONCILE_...",   # or hook=..., hook_site=...
           note="what this writer is"),'''


def _skeleton(site: writer_scan.Site) -> str:
    return SKELETON.format(site=site.site,
                           tables="".join(f'"{t}", ' for t in site.tables),
                           how="".join(f'"{k}", ' for k in site.kinds))


def test_every_writer_site_is_declared():
    found = writer_scan.scan(ROOT)
    missing = sorted(set(found) - set(writers.DECLARED_SITES))
    assert not missing, (
        f"\n\n  {len(missing)} writer site(s) are not declared in ci/writers.py:\n\n"
        + "\n".join(f"    {s}  [{','.join(found[s].kinds)}]" for s in missing)
        + "\n\n  Add each one to a Writer, saying what tells graph_sync about the write:\n\n"
        + "\n\n".join(_skeleton(found[s]) for s in missing[:3])
        + "\n"
    )


def test_no_stale_writer_declarations():
    found = writer_scan.scan(ROOT)
    stale = sorted(site for site in writers.DECLARED_SITES if site not in found)
    assert not stale, (
        f"\n\n  {len(stale)} declared site(s) no longer write anything the scan can see:\n\n"
        + "\n".join(f"    {s}  ({writers.DECLARED_SITES[s]})" for s in stale)
        + "\n\n  The writer was moved, renamed or deleted. Update or remove the entry,\n"
          "  and record a retired inventory row in ci/writers.py RETIRED.\n"
    )


def test_every_unresolved_site_is_listed():
    found = writer_scan.unresolved(ROOT)
    missing = sorted(set(found) - set(writers.UNRESOLVED_SITES))
    assert not missing, (
        f"\n\n  {len(missing)} site(s) write through a table name the scan cannot resolve:\n\n"
        + "\n".join(f"    {s}  [{','.join(found[s].kinds)}]" for s in missing)
        + "\n\n  Either it is a generic record layer a declared writer calls, or a write to a\n"
          "  table the graph does not read: add it to UNRESOLVED_SITES with which of the two.\n"
          "  If it writes a graph source, it is a writer: declare it in WRITERS instead.\n"
    )


def test_no_stale_unresolved_entries():
    found = writer_scan.unresolved(ROOT)
    stale = sorted(set(writers.UNRESOLVED_SITES) - set(found))
    assert not stale, (
        "\n\n  UNRESOLVED_SITES names site(s) the scan no longer reports:\n\n"
        + "\n".join(f"    {s}" for s in stale) + "\n"
    )


def test_every_declared_hook_is_called_in_its_own_site():
    problems = []
    for writer in writers.WRITERS:
        for site in writer.hook_site:
            calls = writer_scan.calls_in(ROOT, site)
            if calls is None:
                problems.append(f"{writer.id}: {site} is not a function in this tree")
            elif writer.hook not in calls:
                problems.append(f"{writer.id}: {site} does not call {writer.hook}(); it calls {', '.join(calls)}")
    assert not problems, (
        "\n\n  A writer declares a hook its own code does not call:\n\n"
        + "\n".join(f"    {p}" for p in problems)
        + "\n\n  The graph hears nothing about these writes. Restore the call, or declare the\n"
          "  category code of the sync that repairs it instead.\n"
    )


def test_a_hook_helper_reaches_the_graph_sync_hooks():
    """A writer may enqueue through a helper, but the helper must reach hooks.enqueue."""
    problems = []
    for writer in writers.WRITERS:
        if not writer.hook or writer.hook.endswith("hooks.enqueue"):
            continue
        definitions = writer_scan.defined_at(writer.hook, ROOT)
        reached = [site for site in definitions
                   if "hooks.enqueue" in (writer_scan.calls_in(ROOT, site) or ())]
        if not definitions:
            problems.append(f"{writer.id}: nothing in the tree defines {writer.hook}")
        elif not reached:
            problems.append(f"{writer.id}: {writer.hook} is defined at {', '.join(definitions)} "
                            f"and none of those calls hooks.enqueue")
    assert not problems, "\n\n" + "\n".join(f"    {p}" for p in problems) + "\n"


def test_a_proxy_declares_its_tables_rather_than_inferring_them():
    """What Rails commits behind a proxy is a declaration, never a guess from a method name."""
    found = writer_scan.scan(ROOT)
    for site in found.values():
        if "seek_client" in site.kinds or "rails_runner" in site.kinds:
            assert site.tables == (), f"{site.site}: the scan inferred {site.tables}"
    for writer in writers.WRITERS:
        if "seek_client" not in writer.how and "rails_runner" not in writer.how:
            continue
        assert writer.tables or writer.reconcile == "NO_GRAPH_EFFECT", (
            f"{writer.id}: name the tables Rails commits for it, or declare NO_GRAPH_EFFECT"
        )


def test_the_kinds_the_scan_sees_are_the_kinds_declared():
    found = writer_scan.scan(ROOT)
    problems = []
    for writer in writers.WRITERS:
        seen = {kind for site in writer.sites for kind in found[site].kinds}
        seen -= set(writer_scan.UNRESOLVED_KINDS)
        extra = sorted(seen - set(writer.how))
        if extra:
            problems.append(f"{writer.id}: writes {extra} too; its how says {list(writer.how)}")
    assert not problems, "\n\n" + "\n".join(f"    {p}" for p in problems) + "\n"


def test_every_inventory_row_is_declared_or_retired():
    """WR-01 to WR-28 are the inventory's rows; every one is an entry here or a retired id."""
    accounted = set(writers.BY_ID) | set(writers.RETIRED)
    missing = sorted(f"WR-{n:02d}" for n in range(1, 29) if f"WR-{n:02d}" not in accounted)
    assert not missing, f"inventory rows with no entry and no RETIRED line: {missing}"


def test_each_writer_is_reachable_by_id_and_owns_its_sites():
    assert len(writers.BY_ID) == len(writers.WRITERS)
    assert len(writers.DECLARED_SITES) == sum(len(w.sites) for w in writers.WRITERS)
    for site, writer_id in writers.DECLARED_SITES.items():
        assert site in writers.BY_ID[writer_id].sites


def test_a_writer_says_either_what_it_calls_or_which_sync_repairs_it():
    with pytest.raises(ValueError):
        writers.Writer(id="WR-99", sites=(), tables=(), how=("sql",), note="neither")
    with pytest.raises(ValueError):
        writers.Writer(id="WR-99", sites=(), tables=(), how=("sql",), hook="hooks.enqueue",
                       hook_site="a/b.py::go", reconcile="RECONCILE_RAILS", note="both")
    with pytest.raises(ValueError):
        writers.Writer(id="WR-99", sites=(), tables=(), how=("sql",), hook="hooks.enqueue", note="no hook site")


def test_a_free_text_reason_and_an_unknown_table_are_refused():
    with pytest.raises(ValueError):
        writers.Writer(id="WR-99", sites=(), tables=(), how=("sql",),
                       reconcile="the operator runs it by hand", note="free text")
    with pytest.raises(ValueError):
        writers.Writer(id="WR-99", sites=(), tables=("policies",), how=("sql",),
                       reconcile="RECONCILE_OPERATOR", note="not a graph source")
    with pytest.raises(ValueError):
        writers.Writer(id="WR-99", sites="a/b.txt::go", tables=(), how=("sql",),
                       reconcile="RECONCILE_DEAD", note="not a scanned path")
    with pytest.raises(ValueError):
        writers.Writer(id="writer 99", sites=(), tables=(), how=("sql",),
                       reconcile="RECONCILE_DEAD", note="not an inventory id")


def test_one_string_is_normalised_to_one_site_not_one_per_character():
    writer = writers.Writer(id="WR-99", sites="a/b.py::go", tables="samples", how="sql",
                            reconcile="RECONCILE_DEAD", note="one of everything")
    assert writer.sites == ("a/b.py::go",)
    assert writer.tables == ("samples",)
    assert writer.how == ("sql",)


def test_the_scan_prints_every_site_it_found():
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert writer_scan.main(ROOT) == 0
    printed = out.getvalue()
    for site in writer_scan.scan(ROOT):
        assert site in printed
