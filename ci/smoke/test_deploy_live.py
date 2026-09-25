"""Two changes the rest of the suite cannot tell apart from the build before them.

  * The served chat bundle is the one the checkout commits. The chat page's
    script tag comes from the app image's manifest, but the file it names is
    served by nginx from the static volume, which only collectstatic fills. A
    rebuilt bundle without collectstatic renders the page with a script tag
    that 404s; a stale volume serves last week's bundle under the same name
    only if the name did not change. Both are caught by comparing bytes.
  * /seek/search/ renders its phone "Sample type" dropdown once per type, in
    seconds, and logs no failed template lookup. Before the 2026-09-25 fix the
    dropdown looped over a JSON string: about 10,000 empty options, one
    formatted traceback each in django.log, and 8 s a load.

No model call and no write. The static asset and /seek/search/ are declared for
every profile, so this runs on prod too.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.smoke.assertions import check_gateway, describe_shape
from ci.smoke.deploy_live import (
    CHAT_BUNDLE_DIR,
    CHAT_MANIFEST,
    REPO_ROOT,
    appended_since,
    chat_bundle_files,
    phone_type_options,
    template_lookup_failures,
)

# The fastest of SEEK_SEARCH_LOADS warm loads must come in under this. Measured on
# 2026-09-25: 7.7 to 8.4 s a load on both boxes before the fix, 18 to 32 ms of view
# work after it; a cold worker after a restart took 16 to 20 s, which is why the
# fastest of several loads is judged, not the first.
SEEK_SEARCH_MAX_S = 4.0
SEEK_SEARCH_LOADS = 3

# The app's log directory is bind-mounted from the checkout (docker-compose.yml
# ./logs:/app/logs), so on the box ./startup.sh ci runs on, the host can read it.
DJANGO_LOG = REPO_ROOT / "logs" / "django.log"


def test_the_served_chat_bundle_is_the_committed_one(anon, base_url):
    """collectstatic ran after the rebuild that changed the bundle."""
    files = chat_bundle_files()
    for name in files:
        url = f"{base_url}/static/{(CHAT_BUNDLE_DIR / name).relative_to('static').as_posix()}"
        r = anon.get(url, timeout=60)
        check_gateway(r)
        assert r.status_code == 200, (
            f"{url} returned {r.status_code}, but the checkout's {CHAT_MANIFEST} names "
            f"{name}: the rebuilt chat bundle was not collected. Run: docker compose "
            f"exec nextseek uv run manage.py collectstatic --noinput"
        )
        committed = (REPO_ROOT / CHAT_BUNDLE_DIR / name).read_bytes()
        assert r.content == committed, (
            f"{url} serves {len(r.content)} bytes that differ from the checkout's "
            f"{len(committed)}: the static volume holds another build's {name}. Run: "
            f"docker compose exec nextseek uv run manage.py collectstatic --noinput"
        )


def test_the_chat_page_loads_the_committed_bundle(web, base_url):
    """The app image's manifest names the checkout's bundle (the page's script
    tag is rendered from it), so the page and the served file agree."""
    r = web.get(f"{base_url}/seek/assistant/", timeout=120)
    check_gateway(r)
    assert r.status_code == 200, f"/seek/assistant/ answered {describe_shape(r)}"
    js = chat_bundle_files()[0]
    assert f"/static/{(CHAT_BUNDLE_DIR / js).relative_to('static').as_posix()}" in r.text, (
        f"the chat page does not load {js}, which the checkout's {CHAT_MANIFEST} names: "
        "the running app image carries another bundle. Rebuild the app: ./startup.sh rebuild"
    )


def test_seek_search_renders_its_type_dropdown_once_per_type_and_fast(web, base_url):
    offset = DJANGO_LOG.stat().st_size if DJANGO_LOG.is_file() else None
    timings: list[float] = []
    body = ""
    for _ in range(SEEK_SEARCH_LOADS):
        started = time.monotonic()
        r = web.get(f"{base_url}/seek/search/", timeout=120)
        timings.append(time.monotonic() - started)
        check_gateway(r)
        assert r.status_code == 200, f"/seek/search/ answered {describe_shape(r)}"
        body = r.text

    options = phone_type_options(body)
    assert options is not None, "/seek/search/ has no phone Sample type dropdown (m_sampletype)"
    empty = sum(1 for value in options if not value)
    assert empty == 0, (
        f"the phone Sample type dropdown has {len(options)} options, {empty} of them "
        "empty: it loops over the JSON string of the types, one option per character. "
        "Expected one valued option per sample type (the 2026-09-25 fix, "
        "report.type_option_list): the running app predates it. ./startup.sh rebuild"
    )
    fastest = min(timings)
    assert fastest < SEEK_SEARCH_MAX_S, (
        f"/seek/search/ took {', '.join(f'{t:.1f}' for t in timings)} s over "
        f"{SEEK_SEARCH_LOADS} loads; the fastest must be under {SEEK_SEARCH_MAX_S} s "
        "(about 8 s a load is the failed-template-lookup cost the 2026-09-25 fix removed)"
    )

    if offset is None:
        pytest.skip(f"{DJANGO_LOG} is not readable from here, so the log half is not checked")
    try:
        failures = template_lookup_failures(appended_since(DJANGO_LOG, offset))
    except OSError as exc:
        pytest.skip(f"cannot read {DJANGO_LOG}: {exc}")
    assert failures == 0, (
        f"django.log gained {failures} 'Exception while resolving variable' records "
        f"during {SEEK_SEARCH_LOADS} loads of /seek/search/: the django.template logger "
        "is at DEBUG, and each record formats a traceback. dmac/settings.py sets it to "
        "INFO; the running app predates that. ./startup.sh rebuild"
    )
