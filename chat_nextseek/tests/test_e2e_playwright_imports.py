"""Confirm e2e.playwright public modules are importable."""


def test_playwright_subpackage_importable():
    from e2e import playwright  # noqa: F401


def test_trio_module_importable():
    from e2e.playwright import trio  # noqa: F401


def test_mysql_module_importable():
    from e2e.playwright import mysql  # noqa: F401


def test_ws_module_importable():
    from e2e.playwright import ws  # noqa: F401


def test_pages_module_importable():
    from e2e.playwright import pages  # noqa: F401


def test_runner_module_importable():
    from e2e.playwright import runner  # noqa: F401
