"""Password hashing and verification utilities using bcrypt."""

import bcrypt


def hash_password(plain: str) -> str:
    """Hash a plain-text password with bcrypt."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a bcrypt hash.

    Also works with legacy plain-text passwords for backward compatibility.
    """
    if hashed.startswith("$2b$") or hashed.startswith("$2a$") or hashed.startswith("$2y$"):
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    # Legacy plaintext fallback
    return plain == hashed
