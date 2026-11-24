"""Argon2id hashing and a NIST-aligned password policy.

Existing PBKDF2 verifiers stay readable so a store populated before this module
exists keeps working; the account is upgraded to Argon2id on its next successful
login. The policy refuses length, blocklisted, account-derived and repetitive
passwords, and deliberately imposes no character-class composition rule
(NIST SP 800-63B): a long passphrase must not be rejected for lacking a symbol.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

MIN_LENGTH = 12
MAX_LENGTH = 512
MIN_DISTINCT = 4

# Offline blocklist for the passwords an operator reaches for under pressure.
# It complements, never replaces, a breach-corpus check at deployment.
COMMON = frozenset('''
123456 1234567 12345678 123456789 1234567890 111111 000000 qwerty qwerty123
qwertyuiop azerty azerty123 password password1 password12 password123
passwordpassword passw0rd p@ssw0rd letmein letmeinletmein welcome welcome1
admin admin123 administrator root toor changeme iloveyou monkey dragon
football baseball sunshine princess motdepasse motdepasse123 bonjour salut
coucou
'''.split())

PARAMETERS = {'time_cost': 2, 'memory_cost': 19456, 'parallelism': 1,
              'hash_len': 32, 'salt_len': 16, 'type': Type.ID}
HASHER = PasswordHasher(**PARAMETERS)

DEFAULT_ADMIN_PASSWORD = 'admin'
SEED_ENVIRONMENT = {
    'admin': 'KUBERWEAVER_ADMIN_PASSWORD',
    'operator': 'KUBERWEAVER_OPERATOR_PASSWORD',
    'viewer': 'KUBERWEAVER_VIEWER_PASSWORD',
}


def seeded_password(role: str) -> str:
    """First-startup password for a seeded account.

    The administrator gets a development default unless the operator overrides it
    with `KUBERWEAVER_ADMIN_PASSWORD`, so a fresh local install is reachable
    without opening the bootstrap file. Every other role keeps a random password.
    Rotation still enforces `PasswordPolicy`, so this default cannot be restored
    once changed.
    """
    override = os.environ.get(SEED_ENVIRONMENT.get(role, ''))
    if override:
        return override
    return DEFAULT_ADMIN_PASSWORD if role == 'admin' else secrets.token_urlsafe(24)


def is_default_admin_password(password: str) -> bool:
    return password == os.environ.get(SEED_ENVIRONMENT['admin']) or \
        (not os.environ.get(SEED_ENVIRONMENT['admin']) and password == DEFAULT_ADMIN_PASSWORD)

MESSAGES = {
    'blank': 'Password must not be blank',
    'too_short': 'Password must be at least 12 characters',
    'too_long': 'Password must be at most 512 characters',
    'common': 'Password is too common',
    'contains_username': 'Password must not contain the username',
    'repetitive': 'Password must use at least 4 distinct characters',
}


class WeakPassword(ValueError):
    """Refusal raised with a stable reason code, never with the password."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(MESSAGES[reason])


def hash_password(password: str) -> str:
    return HASHER.hash(password)


def _legacy_pbkdf2(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, digest = encoded.split('$')
        if scheme != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(digest, actual)
    except (ValueError, TypeError):
        return False


def check_password(password, encoded) -> bool:
    if not isinstance(password, str) or not isinstance(encoded, str) or not encoded:
        return False
    if encoded.startswith('$argon2'):
        try:
            return HASHER.verify(encoded, password)
        except (VerificationError, InvalidHashError):
            return False
    return _legacy_pbkdf2(password, encoded)


def needs_rehash(encoded) -> bool:
    if not isinstance(encoded, str) or not encoded.startswith('$argon2id$'):
        return True
    try:
        return HASHER.check_needs_rehash(encoded)
    except (VerificationError, InvalidHashError):
        return True


class PasswordPolicy:
    """Rules applied on creation and rotation, before any hash is computed."""

    def __init__(self, *, min_length=MIN_LENGTH, max_length=MAX_LENGTH,
                 common=COMMON, min_distinct=MIN_DISTINCT):
        self.min_length = min_length
        self.max_length = max_length
        self.common = common
        self.min_distinct = min_distinct

    def validate(self, password, username='') -> str:
        if not isinstance(password, str) or not password.strip():
            raise WeakPassword('blank')
        if len(password) < self.min_length:
            raise WeakPassword('too_short')
        if len(password) > self.max_length:
            raise WeakPassword('too_long')
        normalized = password.casefold()
        if normalized in self.common or normalized.strip() in self.common:
            raise WeakPassword('common')
        if isinstance(username, str) and len(username) >= 3 and username.casefold() in normalized:
            raise WeakPassword('contains_username')
        if len(set(password)) < self.min_distinct:
            raise WeakPassword('repetitive')
        return password
