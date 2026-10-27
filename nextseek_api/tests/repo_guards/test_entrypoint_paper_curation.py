"""The curation loop runs in web mode only (the one service that reaches cc-spawner), behind the feature flag,
and the drain stops it first."""
from pathlib import Path

ENTRYPOINT = (Path(__file__).resolve().parents[3] / "docker/scripts/entrypoint.sh").read_text()


def _web_block() -> str:
    start = ENTRYPOINT.index('if [ "$MODE" = "web" ]; then\n  if [ "${NEXTSEEK_SERVER')
    return ENTRYPOINT[start:ENTRYPOINT.index("\nfi\n", start)]


def test_the_loop_starts_in_web_mode_behind_the_flag():
    web = _web_block()
    assert "manage.py run_paper_curation" in web
    assert '"${NEXTSEEK_PAPER_REINGEST:-0}" = "1"' in web
    assert ENTRYPOINT.count("manage.py run_paper_curation") == 1  # never in the worker


def test_the_drain_stops_the_loop_before_the_server():
    drain = _web_block().split("_drain_and_stop() {", 1)[1]
    assert drain.index('kill -TERM "$_curation_pid"') < drain.index('kill -TERM "$_server_pid"')
