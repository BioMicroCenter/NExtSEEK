"""ChatPage selector contracts — uses mocked Playwright Locators."""
from unittest.mock import MagicMock

from e2e.playwright.pages import ChatPage


def _page_with_locators(locator_map):
    """Build a fake Playwright page where get_by_test_id returns the configured mock."""
    page = MagicMock()
    def _get(testid):
        return locator_map.get(testid, MagicMock(name=f"locator-{testid}"))
    page.get_by_test_id.side_effect = _get
    page.locator.side_effect = lambda sel: locator_map.get(sel, MagicMock(name=f"loc-{sel}"))
    return page


def test_chat_page_send_query_clicks_send_button():
    chat_input = MagicMock()
    send_button = MagicMock()
    page = _page_with_locators({"chat-input": chat_input, "send-button": send_button})

    cp = ChatPage(page)
    cp.send_query("hello world")

    chat_input.fill.assert_called_once_with("hello world")
    send_button.click.assert_called_once()


def test_chat_page_latest_assistant_reply_returns_text():
    bubble = MagicMock()
    bubble.text_content.return_value = "the assistant said hi"
    bubbles = MagicMock()
    bubbles.count.return_value = 2
    bubbles.nth.return_value = bubble
    # Return bubbles when filtering message-bubble locators by role=assistant
    page = MagicMock()
    page.locator.return_value = bubbles
    cp = ChatPage(page)
    assert cp.latest_assistant_reply() == "the assistant said hi"


def test_chat_page_open_new_chat_clicks_new_chat_button():
    btn = MagicMock()
    page = _page_with_locators({"new-chat-button": btn})
    cp = ChatPage(page)
    cp.open_new_chat()
    btn.click.assert_called_once()


def test_chat_page_artifact_present():
    artifact = MagicMock()
    artifact.count.return_value = 1
    page = MagicMock()
    page.locator.return_value = artifact
    cp = ChatPage(page)
    assert cp.has_artifact("geo_seq.xlsx") is True


def test_chat_page_artifact_absent():
    artifact = MagicMock()
    artifact.count.return_value = 0
    page = MagicMock()
    page.locator.return_value = artifact
    cp = ChatPage(page)
    assert cp.has_artifact("nonexistent.xlsx") is False


def test_chat_page_stepper_status_returns_data_status():
    step = MagicMock()
    step.get_attribute.return_value = "complete"
    page = MagicMock()
    page.get_by_test_id.return_value = step
    cp = ChatPage(page)
    assert cp.stepper_status("entity") == "complete"
    page.get_by_test_id.assert_called_with("step-entity")
