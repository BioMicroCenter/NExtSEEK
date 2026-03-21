"""Tests for Schema RAG error codes, messages, and exceptions.

Covers the missing lines in nextseek_api/schema_rag/errors.py:
- Line 43: get_error_message with unknown code
- Lines 46-48: get_error_message with missing kwargs (KeyError fallback)
"""

from django.test import SimpleTestCase

from nextseek_api.schema_rag.errors import (
    EMBEDDING_FAILED,
    ERROR_MESSAGES,
    NO_GOOD_MATCH,
    SCHEMA_FETCH_FAILED,
    SCHEMA_TOO_LARGE,
    SESSION_MISSING_OR_EXPIRED,
    SchemaFetchError,
    get_error_message,
)


class ErrorCodeConstantsTests(SimpleTestCase):
    """Verify error code constants are defined correctly."""

    def test_all_codes_are_strings(self):
        codes = [
            SCHEMA_FETCH_FAILED,
            SESSION_MISSING_OR_EXPIRED,
            NO_GOOD_MATCH,
            SCHEMA_TOO_LARGE,
            EMBEDDING_FAILED,
        ]
        for code in codes:
            self.assertIsInstance(code, str)

    def test_all_codes_have_messages(self):
        codes = [
            SCHEMA_FETCH_FAILED,
            SESSION_MISSING_OR_EXPIRED,
            NO_GOOD_MATCH,
            SCHEMA_TOO_LARGE,
            EMBEDDING_FAILED,
        ]
        for code in codes:
            self.assertIn(code, ERROR_MESSAGES)


class GetErrorMessageTests(SimpleTestCase):
    """Tests for the get_error_message helper."""

    def test_known_code_with_kwargs(self):
        msg = get_error_message(SCHEMA_FETCH_FAILED, schema_url="https://example.com/api")
        self.assertIn("https://example.com/api", msg)

    def test_known_code_no_kwargs_needed(self):
        msg = get_error_message(NO_GOOD_MATCH)
        self.assertIn("No endpoints matched", msg)

    def test_unknown_code_returns_fallback(self):
        """Line 43: unknown code returns 'Unknown error: CODE'."""
        msg = get_error_message("TOTALLY_UNKNOWN_CODE")
        self.assertEqual(msg, "Unknown error: TOTALLY_UNKNOWN_CODE")

    def test_missing_kwargs_returns_template_as_is(self):
        """Lines 46-48: SCHEMA_FETCH_FAILED requires schema_url kwarg.
        When it's missing, KeyError is caught and template returned as-is."""
        msg = get_error_message(SCHEMA_FETCH_FAILED)
        # Should return the raw template string since {schema_url} can't be formatted
        self.assertIn("{schema_url}", msg)

    def test_session_missing_message(self):
        msg = get_error_message(SESSION_MISSING_OR_EXPIRED)
        self.assertIn("missing or expired", msg)

    def test_schema_too_large_message(self):
        msg = get_error_message(SCHEMA_TOO_LARGE)
        self.assertIn("too many endpoints", msg)

    def test_embedding_failed_message(self):
        msg = get_error_message(EMBEDDING_FAILED)
        self.assertIn("semantic search", msg)


class SchemaFetchErrorTests(SimpleTestCase):
    """Tests for SchemaFetchError exception class."""

    def test_instantiation(self):
        err = SchemaFetchError("fetch failed")
        self.assertIsInstance(err, Exception)
        self.assertEqual(str(err), "fetch failed")

    def test_raise_and_catch(self):
        with self.assertRaises(SchemaFetchError) as ctx:
            raise SchemaFetchError("connection timeout")
        self.assertEqual(str(ctx.exception), "connection timeout")

    def test_is_exception_subclass(self):
        self.assertTrue(issubclass(SchemaFetchError, Exception))

    def test_empty_message(self):
        err = SchemaFetchError()
        self.assertIsInstance(err, SchemaFetchError)
