"""Page-object wrapper for the chat UI.

All data-testid-based DOM access lives here. Tests never call page.locator
directly — they go through ChatPage methods.
"""
from __future__ import annotations

from typing import Any


class ChatPage:
    """Wraps a Playwright page that has the chat UI loaded."""

    def __init__(self, page: Any) -> None:
        self.page = page

    # ── Drive ───────────────────────────────────────────────────────────

    def open_new_chat(self) -> None:
        self.page.get_by_test_id("new-chat-button").click()

    def send_query(self, text: str) -> None:
        self.page.get_by_test_id("chat-input").fill(text)
        self.page.get_by_test_id("send-button").click()

    # ── Inspect ─────────────────────────────────────────────────────────

    def latest_assistant_reply(self) -> str:
        """Return the text of the most recent assistant message bubble."""
        bubbles = self.page.locator('[data-testid="message-bubble"][data-role="assistant"]')
        n = bubbles.count()
        if n == 0:
            return ""
        return bubbles.nth(n - 1).text_content() or ""

    def bubble_count(self) -> int:
        return self.page.locator('[data-testid="message-bubble"]').count()

    def has_artifact(self, filename: str) -> bool:
        loc = self.page.locator(f'[data-testid="artifact-download"][data-filename="{filename}"]')
        return loc.count() > 0

    def click_artifact(self, filename: str) -> Any:
        """Click an artifact download button. Caller must set up download handler before this."""
        loc = self.page.locator(f'[data-testid="artifact-download"][data-filename="{filename}"]').first
        return loc.click()

    def stepper_status(self, step_name: str) -> str | None:
        """Return data-status of step-<name>, or None if absent."""
        loc = self.page.get_by_test_id(f"step-{step_name}")
        try:
            return loc.get_attribute("data-status")
        except Exception:
            return None
