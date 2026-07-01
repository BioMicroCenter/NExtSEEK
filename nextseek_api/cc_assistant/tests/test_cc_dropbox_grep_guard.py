"""Guard: Dropbox copy + laptop path must not reappear in the CC route."""
from pathlib import Path

CC = Path(__file__).resolve().parents[1]   # nextseek_api/cc_assistant


def test_no_dropbox_reply_copy():
    assert "Saved to your Dropbox" not in (CC / "cc_engine.py").read_text()
    assert "artifacts_published" not in (CC / "cc_engine.py").read_text()
    svc = (CC.parent / "services" / "cc_assistant.py").read_text()
    assert "Saved to your Dropbox" not in svc


def test_no_laptop_default_path():
    cfg = (CC / "cc_config.py").read_text()
    assert "/Users/taishajoseph" not in cfg
    assert '/srv/dmac/users' in cfg            # neutral default present
