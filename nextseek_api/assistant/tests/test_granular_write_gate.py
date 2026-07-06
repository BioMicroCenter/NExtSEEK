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
    load_allowlist,
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


# Read-only endpoints added by the 2026-07-06 POST-endpoint read-safety audit.
# Each was confirmed non-mutating by source review; POST reads carry a body
# (identifier/type list) so they cannot be GETs. Kept here so the SHIPPED
# read_safe_endpoints.json can never silently drop them (regression guard for the
# T6 nextseek-api-read WRITE_BLOCKED failure on /admin/samples/retrieve/).
AUDIT_READ_ENDPOINTS = [
    ("/nextseek_api/admin/samples/retrieve/", "POST"),
    ("/nextseek_api/sample_types/get_parents/parents_by_child_types/", "POST"),
    ("/nextseek_api/entity_tree/lineage/", "POST"),
    ("/nextseek_api/batch-upload/validate/", "POST"),
    ("/nextseek_api/data_files/download/", "POST"),
    ("/nextseek_api/sops/download/", "POST"),
    ("/nextseek_api/investigations/", "GET"),
    ("/nextseek_api/people/", "GET"),
    ("/nextseek_api/sops/", "GET"),
]


class ShippedAllowlistTests(SimpleTestCase):
    """Assert the CANONICAL shipped allowlist permits every audited read endpoint.

    Unlike WriteGateTests (which uses a tiny inline fixture), this loads the real
    bundled ``read_safe_endpoints.json`` so a missing entry fails the suite.
    """

    def setUp(self):
        self.gate = build_gate(load_allowlist())  # default = bundled canonical file

    def test_audited_read_endpoints_permitted(self):
        for endpoint, method in AUDIT_READ_ENDPOINTS:
            with self.subTest(endpoint=endpoint, method=method):
                # api-read gate raises WriteBlockedError when not allowlisted.
                self.assertIsNone(self.gate("api-read", endpoint, method, False))
