"""dmac.settings opt-ins and defaults, each read in a fresh interpreter with the env set."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _settings(**env):
    base = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_")}
    base["LOG_DIR"] = tempfile.mkdtemp()
    base.update(env)
    code = (
        "import json, dmac.settings as s;"
        "print(json.dumps({'sc': s.SESSION_COOKIE_SECURE, 'cc': s.CSRF_COOKIE_SECURE,"
        " 'proxy': s.SECURE_PROXY_SSL_HEADER,"
        " 'cors': list(s.CORS_ALLOWED_ORIGINS), 'mw': list(s.MIDDLEWARE),"
        " 'dj': s.LOGGING['loggers']['django']['level'], 'seek': s.LOGGING['loggers']['seek']['level'],"
        " 'dmac': s.LOGGING['loggers']['dmac']['level'],"
        " 'lf': s.LOGGING['handlers']['logfile']['level']}))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=base, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_secure_cookies_when_asked():
    on = _settings(DJANGO_SECURE_COOKIES="1")
    assert on["sc"] is True and on["cc"] is True
    off = _settings()
    assert not off["sc"] and not off["cc"]


def test_proxy_header_opt_in():
    on = _settings(DJANGO_TRUST_X_FORWARDED_PROTO="1")
    assert on["proxy"] == ["HTTP_X_FORWARDED_PROTO", "https"]
    assert _settings()["proxy"] is None


def test_no_localhost_cors_unless_debug():
    assert "http://localhost:5173" not in _settings()["cors"]
    assert "http://localhost:5173" in _settings(DJANGO_DEBUG="1")["cors"]
    got = _settings(DJANGO_CORS_ALLOWED_ORIGINS="https://a.example https://b.example")["cors"]
    assert got == ["https://a.example", "https://b.example"]


def test_profiler_only_under_debug():
    prof = "django_cprofile_middleware.middleware.ProfilerMiddleware"
    assert prof not in _settings()["mw"]
    assert prof in _settings(DJANGO_DEBUG="1")["mw"]


def test_loggers_are_info_without_debug():
    off = _settings()
    assert (off["dj"], off["seek"], off["dmac"], off["lf"]) == ("INFO",) * 4
    on = _settings(DJANGO_DEBUG="1")
    assert (on["dj"], on["seek"], on["dmac"], on["lf"]) == ("DEBUG",) * 4
