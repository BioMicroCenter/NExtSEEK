"""Unit tests for the writer scan, one synthetic source per shape it recognises.

Pure string and AST work: no repository walk, no Django, no database. The scan
over the real tree is exercised by ci/gate/test_writer_registry.py, which diffs
it against ci/writers.py.

Standard library only, like the module it tests.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ci.gate import writer_scan  # noqa: E402


def sites(findings):
    return sorted({f.site for f in findings})


def kinds(findings):
    return sorted({f.kind for f in findings})


def tables(findings):
    return sorted({t for f in findings for t in f.tables})


def ops(findings):
    return sorted({o for f in findings for o in f.ops})


# --- SQL in a Python string -------------------------------------------------

def test_an_insert_into_a_watched_table_is_a_site():
    src = 'def store(conn):\n    conn.execute("INSERT INTO samples (id) VALUES (1)")\n'
    found = writer_scan.scan_module("app/writes.py", src)
    assert sites(found) == ["app/writes.py::store"]
    assert kinds(found) == ["sql"]
    assert tables(found) == ["samples"]
    assert ops(found) == ["create"]


def test_update_delete_replace_and_truncate_are_all_writes():
    src = (
        'def go(conn):\n'
        '    conn.execute("UPDATE samples SET json_metadata = 1 WHERE id = 2")\n'
        '    conn.execute("DELETE FROM projects_samples WHERE sample_id = 2")\n'
        '    conn.execute("REPLACE INTO assay_assets (id) VALUES (3)")\n'
        '    conn.execute("TRUNCATE TABLE clades")\n'
    )
    found = writer_scan.scan_module("app/writes.py", src)
    assert tables(found) == ["assay_assets", "clades", "projects_samples", "samples"]
    assert ops(found) == ["create", "delete", "update"]


def test_a_select_is_not_a_write():
    src = 'def read(conn):\n    conn.execute("SELECT id FROM samples WHERE id = 1")\n'
    assert writer_scan.scan_module("app/reads.py", src) == []


def test_a_table_the_graph_does_not_read_is_not_a_site():
    src = 'def go(conn):\n    conn.execute("INSERT INTO policies (id) VALUES (1)")\n'
    assert writer_scan.scan_module("app/policies.py", src) == []


def test_a_schema_qualified_and_backticked_table_still_resolves():
    src = 'def go(conn):\n    conn.execute("DELETE FROM `seek_production`.`samples` WHERE id = 1")\n'
    assert tables(writer_scan.scan_module("app/writes.py", src)) == ["samples"]


def test_an_f_string_and_a_concatenation_are_read_as_one_statement():
    src = (
        'def go(conn, ids):\n'
        '    conn.execute(f"UPDATE samples SET json_metadata = 1 WHERE id IN ({ids})")\n'
        '    conn.execute("DELETE FROM " + "assay_assets" + " WHERE id = 1")\n'
    )
    assert tables(writer_scan.scan_module("app/writes.py", src)) == ["assay_assets", "samples"]


def test_a_table_name_the_scan_cannot_resolve_is_reported_unresolved():
    src = 'def go(conn, table):\n    conn.execute(f"DELETE FROM {table} WHERE id = 1")\n'
    found = writer_scan.scan_module("app/generic.py", src)
    assert kinds(found) == [writer_scan.UNRESOLVED_KIND]
    assert tables(found) == []
    assert sites(found) == ["app/generic.py::go"]


def test_a_dynamic_name_resolves_inside_a_bound_legacy_table_class():
    src = (
        'class DBtable_sample(DBtable):\n'
        '    def __init__(self):\n'
        '        self.tablename = "samples"\n'
        '    def wipe(self, conn):\n'
        '        conn.execute(f"DELETE FROM {self.fulltablename} WHERE id = 1")\n'
    )
    by_class = writer_scan.tables_by_class({"seek/sample/table.py": src})
    found = writer_scan.scan_module("seek/sample/table.py", src, tables_by_class=by_class)
    assert tables(found) == ["samples"]
    assert kinds(found) == ["sql"]


def test_a_docstring_holding_sql_is_not_a_statement():
    src = 'def go(conn):\n    """Runs INSERT INTO samples (id) VALUES (1) eventually."""\n    return conn\n'
    assert writer_scan.scan_module("app/doc.py", src) == []


def test_a_string_compiled_as_a_regular_expression_is_not_a_statement():
    src = (
        'import re\n'
        '_RE = re.compile(r"^CREATE \\(n\\d+:(\\w+)\\)$")\n'
        '_SQL = re.compile("INSERT INTO samples", re.I)\n'
    )
    assert writer_scan.scan_module("app/parser.py", src) == []


def test_a_string_tested_with_startswith_is_not_a_statement():
    src = (
        'def parse(statement):\n'
        '    if statement.startswith(("CREATE INDEX", "MATCH (n:_ImportRef) REMOVE")):\n'
        '        return None\n'
        '    return statement\n'
    )
    assert writer_scan.scan_module("app/parser.py", src) == []


# --- the Django ORM ---------------------------------------------------------

def test_orm_writes_on_a_model_whose_table_is_watched():
    src = (
        'def go():\n'
        '    Samples.objects.create(id=1)\n'
        '    Sample_types.objects.filter(id=1).update(title="x")\n'
        '    Clades.objects.filter(id=1).delete()\n'
    )
    found = writer_scan.scan_module("app/orm.py", src)
    assert kinds(found) == ["orm"]
    assert tables(found) == ["clades", "sample_types", "samples"]
    assert ops(found) == ["create", "delete", "update"]


def test_an_orm_read_is_not_a_write():
    src = 'def go():\n    return Samples.objects.filter(id=1).first()\n'
    assert writer_scan.scan_module("app/orm.py", src) == []


def test_save_on_a_variable_bound_to_a_model_is_a_write():
    src = 'def go():\n    row = Internal_assays(title="x")\n    row.save()\n'
    found = writer_scan.scan_module("app/orm.py", src)
    assert tables(found) == ["internal_assays"]
    assert ops(found) == ["update"]


# --- the legacy table layer -------------------------------------------------

def test_the_legacy_table_layer_resolves_through_self():
    src = (
        'class DBtable_clades(DBtable):\n'
        '    def __init__(self):\n'
        '        self.tablename = "clades"\n'
        '    def new(self, request):\n'
        '        self.storeOneRecord(request)\n'
        '    def drop(self, request):\n'
        '        self.deleteOneRecord(request)\n'
    )
    by_class = writer_scan.tables_by_class({"dmac/dbtable_clades.py": src})
    found = writer_scan.scan_module("dmac/dbtable_clades.py", src, tables_by_class=by_class)
    assert sites(found) == ["dmac/dbtable_clades.py::DBtable_clades.drop",
                            "dmac/dbtable_clades.py::DBtable_clades.new"]
    assert kinds(found) == ["dbtable"]
    assert tables(found) == ["clades"]


def test_a_mixin_of_a_bound_class_is_bound_too():
    table_src = (
        'class DBtable_sample(SampleUploadMixin, DBtable):\n'
        '    def __init__(self):\n'
        '        self.tablename = "samples"\n'
    )
    mixin_src = (
        'class SampleUploadMixin:\n'
        '    def _storeSample(self, request):\n'
        '        self.storeOneRecord(request)\n'
    )
    by_class = writer_scan.tables_by_class({"seek/sample/table.py": table_src,
                                            "seek/sample/upload.py": mixin_src})
    found = writer_scan.scan_module("seek/sample/upload.py", mixin_src, tables_by_class=by_class)
    assert sites(found) == ["seek/sample/upload.py::SampleUploadMixin._storeSample"]
    assert tables(found) == ["samples"]


def test_a_variable_holding_a_legacy_table_instance_is_resolved():
    src = (
        'def go(request):\n'
        '    dbsample = DBtable_sample()\n'
        '    dbsample.processRecords(request, user, "save")\n'
    )
    by_class = {"DBtable_sample": "samples"}
    found = writer_scan.scan_module("seek/views/samples.py", src, tables_by_class=by_class)
    assert tables(found) == ["samples"]
    assert ops(found) == ["update"]


def test_a_read_operation_of_the_record_layer_is_not_a_write():
    src = (
        'def retrieveSamples(request):\n'
        '    dbsample = DBtable_sample()\n'
        '    return dbsample.processRecords(request, user, "retrieve")\n'
    )
    by_class = {"DBtable_sample": "samples"}
    assert writer_scan.scan_module("seek/views/samples.py", src, tables_by_class=by_class) == []


# --- SEEK's own API and the Rails runner ------------------------------------

def test_a_seek_client_write_is_a_site_and_carries_no_inferred_table():
    src = (
        'class SampleProxyViewSet(ViewSet):\n'
        '    def create(self, request):\n'
        '        return self.client.create_sample(payload)\n'
    )
    found = writer_scan.scan_module("nextseek_api/services/samples.py", src)
    assert sites(found) == ["nextseek_api/services/samples.py::SampleProxyViewSet.create"]
    assert kinds(found) == ["seek_client"]
    # What Rails commits behind a proxy is DECLARED in ci/writers.py, never inferred
    # here: the route trace corrected two of the guesses a table map made.
    assert tables(found) == []
    assert ops(found) == ["create"]


def test_a_seek_client_read_is_not_a_write():
    src = 'def go(client):\n    return client.get_sample(1)\n'
    assert writer_scan.scan_module("nextseek_api/services/samples.py", src) == []


def test_the_rails_runner_is_a_site():
    src = 'def create(self, request):\n    run_seek_rails_runner(SEEK_CREATE_RUBY)\n'
    found = writer_scan.scan_module("nextseek_api/services/users.py", src)
    assert kinds(found) == ["rails_runner"]
    assert tables(found) == []


# --- Cypher -----------------------------------------------------------------

def test_a_cypher_write_is_a_site():
    src = (
        'def push(session):\n'
        '    session.run("MERGE (s:Sample {id: $id}) SET s.search_text = $text", id=1, text="x")\n'
    )
    found = writer_scan.scan_module("app/graph.py", src)
    assert kinds(found) == ["cypher"]
    assert ops(found) == ["merge", "set"]
    assert tables(found) == []


def test_a_cypher_read_is_not_a_write():
    src = 'def read(session):\n    return session.run("MATCH (s:Sample {id: $id}) RETURN s.title")\n'
    assert writer_scan.scan_module("app/graph.py", src) == []


def test_sql_is_not_mistaken_for_cypher():
    src = 'def go(conn):\n    conn.execute("UPDATE policies SET access_type = 0 WHERE id = 1")\n'
    assert writer_scan.scan_module("app/policies.py", src) == []


# --- .sql files -------------------------------------------------------------

def test_a_sql_file_writing_a_watched_table_is_a_site():
    text = (
        "-- INSERT INTO samples (id) VALUES (0);\n"
        "/* DELETE FROM samples; */\n"
        "INSERT INTO sample_attributes (id, title) VALUES (1, 'DOI');\n"
        "UPDATE sample_attributes SET description = 'x' WHERE id = 1;\n"
    )
    found = writer_scan.scan_sql_text("startup/seed/sql/attrs.sql", text)
    assert sites(found) == ["startup/seed/sql/attrs.sql"]
    assert kinds(found) == ["sql_file"]
    assert tables(found) == ["sample_attributes"]
    assert ops(found) == ["create", "update"]


def test_a_sql_file_on_other_tables_is_not_a_site():
    assert writer_scan.scan_sql_text("x.sql", "INSERT INTO permissions (id) VALUES (1);\n") == []


# --- what is scanned at all -------------------------------------------------

def test_tests_migrations_and_history_are_not_scanned():
    assert not writer_scan.is_scanned("nextseek_api/tests/test_graph_sync_hooks.py")
    assert not writer_scan.is_scanned("seek/tests/conftest.py")
    assert not writer_scan.is_scanned("nextseek_api/migrations/0021_graph_sync_outbox_and_run.py")
    assert not writer_scan.is_scanned("NessieAI/history/2026-07/old_writer.py")
    assert not writer_scan.is_scanned("nextseek_api/batch_upload/tests/fixtures/make.py")


def test_the_application_and_its_tools_are_scanned():
    for rel in ("nextseek_api/services/samples.py", "seek/views/admin.py", "dmac/dbtable_clades.py",
                "api_app/views.py", "NessieAI/cc/cc_engine.py", "scripts/graph_search/parity.py",
                "startup/steps/seed.py", "ci/writers.py"):
        assert writer_scan.is_scanned(rel), rel


# --- the hook check ---------------------------------------------------------

def test_the_calls_inside_a_function_are_reported_by_name():
    src = (
        'from nextseek_api.graph_sync import hooks\n'
        'class Proxy:\n'
        '    def create(self, request):\n'
        '        row = save(request)\n'
        '        hooks.enqueue("samples", f"sample:{row.id}")\n'
        '        return row\n'
        '    def read(self, request):\n'
        '        return None\n'
    )
    assert "hooks.enqueue" in writer_scan.calls_in_source(src, "Proxy.create")
    assert "save" in writer_scan.calls_in_source(src, "Proxy.create")
    assert writer_scan.calls_in_source(src, "Proxy.read") == ()


def test_a_function_that_is_not_there_reports_no_call():
    assert writer_scan.calls_in_source("def go():\n    pass\n", "Missing.gone") is None


def test_a_hook_helper_is_found_by_its_name():
    src = (
        'def enqueueSampleSync(kind, sample_id):\n'
        '    return hooks.enqueue(kind, "sample:%d" % sample_id)\n'
        'class Mixin:\n'
        '    def store(self):\n'
        '        def enqueueSampleSync():\n'
        '            pass\n'
    )
    assert writer_scan.definitions_in_source(src, "enqueueSampleSync") == (
        "enqueueSampleSync", "Mixin.store.enqueueSampleSync")
    assert writer_scan.definitions_in_source(src, "absent") == ()
