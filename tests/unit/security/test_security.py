"""Unit tests for ``app.brain.repositories.security``."""

from __future__ import annotations

from app.brain.repositories.security import (
    generate_token,
    hash_password,
    hash_token,
    verify_password,
)


class TestPasswordHashing:
    """argon2id hash and verify."""

    def test_hash_returns_string(self) -> None:
        h = hash_password("mypassword")
        assert isinstance(h, str)
        assert len(h) > 0

    def test_verify_correct_password(self) -> None:
        h = hash_password("correcthorse")
        assert verify_password("correcthorse", h) is True

    def test_verify_wrong_password(self) -> None:
        h = hash_password("correcthorse")
        assert verify_password("wrongpassword", h) is False

    def test_different_hashes_for_same_input(self) -> None:
        h1 = hash_password("same")
        h2 = hash_password("same")
        # argon2id uses random salt — hashes differ
        assert h1 != h2

    def test_verify_empty_password(self) -> None:
        h = hash_password("")
        assert verify_password("", h) is True
        assert verify_password("nonempty", h) is False


class TestTokenGeneration:
    """Token generation and hashing."""

    def test_generate_token_length(self) -> None:
        token = generate_token()
        assert isinstance(token, str)
        # 32 bytes urlsafe = ~43 chars
        assert len(token) >= 20

    def test_generate_token_unique(self) -> None:
        t1 = generate_token()
        t2 = generate_token()
        assert t1 != t2

    def test_hash_token_deterministic(self) -> None:
        token = "my-secret-token-abc"
        h1 = hash_token(token)
        h2 = hash_token(token)
        assert h1 == h2

    def test_hash_token_sha256_hex_length(self) -> None:
        h = hash_token("test")
        # SHA-256 hex = 64 chars
        assert len(h) == 64

    def test_hash_token_different_inputs(self) -> None:
        assert hash_token("a") != hash_token("b")
