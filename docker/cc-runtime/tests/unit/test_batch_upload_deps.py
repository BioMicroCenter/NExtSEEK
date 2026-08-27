import importlib
import pytest

@pytest.mark.parametrize("mod", ["polars", "fastexcel", "xlsxwriter"])
def test_excel_stack_importable_on_host(mod):
    # polars (model + read) / fastexcel (calamine read engine) / xlsxwriter (polars write backend).
    assert importlib.import_module(mod) is not None

def test_polars_reads_via_calamine_engine():
    # The calamine read engine is provided by fastexcel; this proves the stack is wired.
    import polars as pl, xlsxwriter, tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "rt.xlsx")
    pl.DataFrame({"UID": ["x"], "Name": ["m1"]}).write_excel(
        workbook=p, worksheet="Samples", include_header=True, autofilter=False, table_style=None)
    df = pl.read_excel(p, sheet_name="Samples", engine="calamine")
    assert df.columns[0] == "UID" and df["Name"][0] == "m1"

def test_dependency_declarations():
    import tomllib, pathlib
    data = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
    main = data["project"]["dependencies"]
    def _named(deps, name):
        return any(d.split("[")[0].split(">")[0].split("=")[0].split("~")[0].strip() == name for d in deps)
    assert _named(main, "polars") and _named(main, "fastexcel") and _named(main, "xlsxwriter")
    assert _named(main, "orjson")
    assert not _named(main, "openpyxl"), "do not add openpyxl as a direct dependency"
    extra = data["project"].get("optional-dependencies", {}).get("container", [])
    assert any("markitdown" in d for d in extra), "markitdown must be image-only (optional-dependencies.container)"
    assert not any("markitdown" == d.split("[")[0].split(">")[0].split("=")[0].strip() for d in main)
