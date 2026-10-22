"""The embedded chat bundle must never inline a login, and the e2e specs must
not carry the published demo password. Plain file reads, no node, no git."""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "NessieAI" / "chat_frontend"
PW = "demo" + "password"  # two halves so this file never trips itself


def _strip_ts_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def login_defaults_empty(js):
    """Keyed on the auth service constructor, whose body starts
    `this.baseUrl=` (minifier-proof: the names change, that shape does not).
    Its 2nd and 3rd arguments are the login and password; each default must
    be an empty string. Returns False if no such constructor is found."""
    found = False
    for m in re.finditer(r"constructor\(([^()]*)\)\{this\.baseUrl=", js):
        found = True
        args = [a.strip() for a in m.group(1).split(",")]
        for a in args[1:3]:
            if "=" in a and a.split("=", 1)[1].strip() not in ('""', "''", "``"):
                return False
    return found


def test_login_defaults_check_on_synthetic_snippets():
    ok = 'class A{constructor(r="",a="",i=""){this.baseUrl=r,this.user=a}}'
    bad = 'class A{constructor(r="",a="someuser",i="somepass"){this.baseUrl=r}}'
    assert login_defaults_empty(ok)
    assert not login_defaults_empty(bad)
    # key text absent (renamed field, unminified spacing): must not pass silently
    assert not login_defaults_empty('class A{constructor(r="",a="",i=""){ this.base = r }}')


def test_embedded_vite_config_blanks_the_login_vars():
    src = _strip_ts_comments((FRONTEND / "vite.config.embedded.ts").read_text())
    for name in ("VITE_API_USER", "VITE_API_PASS"):
        assert re.search(r'"import\.meta\.env\.%s"\s*:\s*[\'"]\\?"\\?"[\'"]' % name, src), name


def test_e2e_specs_hold_no_published_password():
    e2e = FRONTEND / "e2e"
    assert e2e.is_dir()
    hits = []
    for dp, dn, fn in os.walk(e2e):
        dn[:] = [d for d in dn if d not in ("node_modules", "dist", "test-results")]
        hits += [str(Path(dp, f)) for f in fn
                 if PW in Path(dp, f).read_text(errors="ignore")]
    assert not hits, hits


def test_built_bundle_has_no_login_default():
    assets = ROOT / "static" / "js" / "chat_assistant" / "assets"
    bad = []
    checked = 0
    for p in assets.glob("*.js"):
        t = p.read_text(errors="ignore")
        if PW in t or re.search(r"constructor\([^)]*nextseek-dev\.mit\.edu", t):
            bad.append(p.name)
        if "this.baseUrl=" in t:
            checked += 1
            if not login_defaults_empty(t):
                bad.append(p.name + " (non-empty login default)")
    assert not bad, bad
    assert checked, "no bundle file holds the auth constructor key `this.baseUrl=`: update login_defaults_empty's key to the new build shape"
