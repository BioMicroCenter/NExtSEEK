"""Unit tests for the native assistant granular-ops write gate.

The gate is the safety-critical control for the api-write op. It must mirror the
dmac sidecar's write_gate.py exactly: api-write is confirmation-only (strict
boolean True), api-read is allowlist-gated, and all other (read-class) ops pass.
These are pure-logic tests (SimpleTestCase, no DB).
"""
from django.test import SimpleTestCase

from nextseek_api.assistant.write_gate import (
    WriteBlockedError,
    build_gate,
    load_allowlist_from_entries,
)


ALLOWLIST_ENTRIES = [
    {"endpoint": "/nextseek_api/samples/advanced_search/", "methods": ["POST"]},
    {"endpoint": "/nextseek_api/samples/", "methods": ["GET"]},
]


class WriteGateTests(SimpleTestCase):
    def setUp(self):
        self.gate = build_gate(load_allowlist_from_entries(ALLOWLIST_ENTRIES))

    # --- api-write: confirmation-only ---
    def test_api_write_confirmed_true_passes(self):
        # Should not raise.
        self.assertIsNone(self.gate("api-write", None, None, True))

    def test_api_write_confirmed_false_blocks(self):
        with self.assertRaises(WriteBlockedError):
            self.gate("api-write", None, None, False)

    def test_api_write_confirmed_string_true_blocks(self):
        # Strict boolean identity: the string "true" must NOT confirm a write.
        with self.assertRaises(WriteBlockedError):
            self.gate("api-write", None, None, "true")

    def test_api_write_confirmed_one_blocks(self):
        # Integer 1 is truthy but is not bool True — must NOT confirm.
        with self.assertRaises(WriteBlockedError):
            self.gate("api-write", None, None, 1)

    # --- api-read: allowlist-gated ---
    def test_api_read_allowlisted_passes(self):
        self.assertIsNone(
            self.gate("api-read", "/nextseek_api/samples/advanced_search/", "POST", False)
        )

    def test_api_read_method_normalized(self):
        # Lower-case method should still match the allowlist (stored upper-cased).
        self.assertIsNone(
            self.gate("api-read", "/nextseek_api/samples/advanced_search/", "post", False)
        )

    def test_api_read_not_allowlisted_endpoint_blocks(self):
        with self.assertRaises(WriteBlockedError):
            self.gate("api-read", "/nextseek_api/samples/", "POST", False)  # POST not allowed here

    def test_api_read_unknown_endpoint_blocks(self):
        with self.assertRaises(WriteBlockedError):
            self.gate("api-read", "/nextseek_api/bogus/", "GET", False)

    # --- read-class ops always pass ---
    def test_read_class_ops_pass(self):
        for op in ("entity", "parse", "graph", "report", "generate-submission"):
            with self.subTest(op=op):
                self.assertIsNone(self.gate(op, None, None, False))

    # --- unknown op default-deny ---
    def test_unknown_op_blocks(self):
        with self.assertRaises(WriteBlockedError):
            self.gate("totally-unknown-op", None, None, True)
