"""Password/token hashing utilities.

Passwords use argon2id (slow by design); opaque access tokens use
plain SHA-256 — a high-entropy random string gains nothing from a
slow KDF (ADR-3).
"""

import contextlib
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

_ph = PasswordHasher()

# Constant dummy hash for timing-safe dummy verification
# (prevents username enumeration via response time).
_DUMMY_HASH = _ph.hash("dummy-constant-for-timing-attack-mitigation")


def hash_password(password: str) -> str:
    """Hash a password with argon2id.

    Args:
        password: Plaintext password.

    Returns:
        Argon2id hash string including parameters and salt.
    """
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored hash.

    Args:
        password: Candidate plaintext.
        password_hash: Stored argon2id hash.

    Returns:
        True on match; False on mismatch or invalid hash format.
    """
    try:
        _ph.verify(password_hash, password)
        return True
    except (Argon2Error, InvalidHashError):
        return False


def verify_password_dummy(password: str) -> None:
    """Run a dummy verification to equalize timing.

    Used when user is not found to prevent timing-based
    username enumeration (always takes ~argon2 time).

    Args:
        password: Candidate plaintext to verify against dummy.
    """
    with contextlib.suppress(Argon2Error, InvalidHashError):
        _ph.verify(_DUMMY_HASH, password)


def generate_token() -> str:
    """Generate a new opaque token.

    Returns:
        URL-safe string carrying 256 bits of entropy.
    """
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash an opaque token for storage (ADR-3).

    Args:
        token: Raw plaintext token.

    Returns:
        Hex SHA-256 digest (64 chars).
    """
    return hashlib.sha256(token.encode()).hexdigest()
