"""``EncryptedTextField`` must protect stored tokens without becoming a new
way for the site to fall over.

The bar this has to clear is low but specific: today the user's SEEK *password*
sits in the Django session in plaintext (``dmac/views.py:127-128``). Encrypting
tokens at rest is strictly better -- but only if losing or rotating the key
degrades to "everyone logs in again" rather than "every request 500s". These
tests pin that asymmetry:

* an unreadable row reads as ``None`` (recoverable user state), while
* an unkeyed deployment raises ``ImproperlyConfigured`` (operator error).

Getting those two the wrong way round is the failure mode worth guarding. A row
that raised would take out every view touching a token; a misconfiguration that
silently returned ``None`` would look exactly like "all users need to log in
again" and send someone hunting in the wrong place.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from seek.oauth import crypto

# Bytes 0..31 and 32..63. Non-secret, fixed, and never used anywhere real.
KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
KEY_B = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="


# -- round trip --------------------------------------------------------------


@override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_A)
def test_a_token_survives_a_round_trip():
    assert crypto.decrypt(crypto.encrypt("s3cr3t-access-token")) == "s3cr3t-access-token"


@override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_A)
def test_the_ciphertext_does_not_contain_the_plaintext():
    assert "s3cr3t-access-token" not in crypto.encrypt("s3cr3t-access-token")


@override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_A)
def test_encryption_is_nondeterministic():
    """Fernet includes a random IV, which is why the column cannot be queried
    by equality and why neither token column carries an index."""
    assert crypto.encrypt("same") != crypto.encrypt("same")


@override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_A)
def test_non_ascii_survives():
    """Tokens are ASCII in practice, but the field is a general TextField and a
    UTF-8 round trip is one `.encode()` argument away from being wrong."""
    assert crypto.decrypt(crypto.encrypt("tökén-✓-Ω")) == "tökén-✓-Ω"


# -- rotation ----------------------------------------------------------------


def test_a_key_added_in_front_still_decrypts_older_rows():
    """The rotation procedure: prepend the new key, restart, let rows
    re-encrypt as they refresh. Rows written under the old key must keep
    working in the meantime."""
    with override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_A):
        old = crypto.encrypt("written-under-key-a")

    with override_settings(SEEK_OAUTH_TOKEN_KEYS=f"{KEY_B},{KEY_A}"):
        assert crypto.decrypt(old) == "written-under-key-a"
        fresh = crypto.encrypt("written-under-key-b")

    # ...and the new key is genuinely the one encrypting, so retiring KEY_A
    # later does not strand anything written after the rotation.
    with override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_B):
        assert crypto.decrypt(fresh) == "written-under-key-b"


def test_retiring_a_key_too_early_is_a_relogin_not_a_crash():
    with override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_A):
        stranded = crypto.encrypt("written-under-key-a")

    with override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_B):
        assert crypto.decrypt(stranded) is None


# -- unreadable rows are None ------------------------------------------------


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param("", id="empty"),
        pytest.param("not-a-fernet-token", id="garbage"),
        pytest.param("gAAAAAB" + "x" * 40, id="truncated-lookalike"),
        pytest.param("tökén", id="non-ascii-column"),
    ],
)
@override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_A)
def test_an_unreadable_column_reads_as_none(corrupt):
    assert crypto.decrypt(corrupt) is None


# -- but a misconfiguration is loud ------------------------------------------


@override_settings(SEEK_OAUTH_TOKEN_KEYS="")
def test_no_keys_configured_raises_rather_than_silently_returning_none():
    """The inverse of the case above, and the reason decrypt() does not simply
    wrap everything in a try/except: an operator who forgot the key must not
    see the same symptom as a user whose row went bad."""
    with pytest.raises(ImproperlyConfigured):
        crypto.encrypt("anything")
    with pytest.raises(ImproperlyConfigured):
        crypto.decrypt("anything")


@override_settings(SEEK_OAUTH_TOKEN_KEYS="this-is-not-a-fernet-key")
def test_a_malformed_key_raises_rather_than_failing_at_first_use():
    with pytest.raises(ImproperlyConfigured):
        crypto.encrypt("anything")


# -- the field wrapper -------------------------------------------------------


@override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_A)
def test_field_encrypts_on_the_way_down_and_decrypts_on_the_way_up():
    field = crypto.EncryptedTextField()
    stored = field.get_prep_value("a-token")
    assert stored != "a-token"
    assert field.from_db_value(stored, None, None) == "a-token"


@override_settings(SEEK_OAUTH_TOKEN_KEYS=KEY_A)
def test_field_passes_none_through_untouched():
    """refresh_token is nullable: Doorkeeper does not always issue one, and a
    NULL column must not be handed to Fernet."""
    field = crypto.EncryptedTextField(null=True)
    assert field.get_prep_value(None) is None
    assert field.from_db_value(None, None, None) is None
