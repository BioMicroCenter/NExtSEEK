"""SEEK OAuth2 support for NExtSEEK (issue #16, sub-project 1).

Deliberately empty. ``seek/models/nextseek.py`` imports
``seek.oauth.crypto.EncryptedTextField`` at module scope, so anything imported
here would be pulled in during model loading and any model import added to this
package would become a circular import. Import submodules directly.
"""
