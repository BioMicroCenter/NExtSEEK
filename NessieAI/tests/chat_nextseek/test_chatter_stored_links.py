"""P6 T4: a web address the turn's data stores stays in the reply; any other address goes (made-up entities only)."""
from __future__ import annotations

from chat_nextseek.agents.chatter import _keep_stored_links

LINK = "https://repo.example/search?text=abc"


def test_a_stored_link_in_the_rows_stays_in_the_reply():
    rows = {"data": [{"uuid": "ZZD-990101QQQ-1", "Link_PrimaryData": LINK}]}
    out = _keep_stored_links(f"The file is at [the study]({LINK}).", None, rows["data"])
    assert LINK in out


def test_a_stored_link_in_a_retrieve_record_metadata_stays():
    body = {"data": [{"sample_type": "D.ZZ", "samples": [
        {"uuid": "ZZD-990101QQQ-2", "metadata": {"Link_SecondaryData": "https://files.example/r2"}}]}]}
    out = _keep_stored_links("See https://files.example/r2, then check it.", body["data"], None)
    assert "https://files.example/r2" in out


def test_an_invented_address_is_removed():
    out = _keep_stored_links(f"Try https://other.example/x or {LINK}.", [{"Link_PrimaryData": LINK}])
    assert "other.example" not in out and LINK in out


def test_the_request_url_in_the_envelope_is_not_stored_data():
    out = _keep_stored_links("Called http://127.0.0.1:8000/nextseek_api/samples/retrieve/ ok.", [{"a": 1}], None)
    assert "127.0.0.1" not in out
