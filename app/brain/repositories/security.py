import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _ph.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False


def generate_token() -> str:
    """Новый opaque-токен: 256 бит энтропии, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 opaque-токена (ADR-3): быстрый хэш достаточен для
    высокоэнтропийной строки; argon2id оставлен только паролям."""
    return hashlib.sha256(token.encode()).hexdigest()
