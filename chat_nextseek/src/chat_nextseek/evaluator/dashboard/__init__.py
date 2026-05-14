"""Static HTML dashboard renderer for evaluator batch runs.

Consumes ``build_dashboard_payload(batch_report)`` and writes a
self-contained HTML artifact next to the JSON report.
Use it when you need an offline viewer for evaluator output.
Invariant: rendering is pure file generation with no live JS fetches.
"""
from .render import find_latest_report, render, to_js_format

__all__ = ["find_latest_report", "render", "to_js_format"]
