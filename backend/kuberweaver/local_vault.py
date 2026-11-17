"""Optional local Fernet credentials; no implicit initialization or network IO.

Key and ciphertext share one private directory: this does not protect against a
compromised OS account/root. Back up both privately. Rotation is not implemented.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import stat

from cryptography.fernet import Fernet

from .secrets import SecretError, SecretValue, _name

MAX_SECRET = 128 * 1024
MAX_TOKEN = 180 * 1024
_CHECK = b'kuberweaver-local-vault-v1'
_ERROR = 'local vault unavailable or not protected'


def _validate_directory(fd, *, private=False, shielded=False):
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in ({os.getuid()} if private else {0, os.getuid()})
            or (stat.S_IMODE(info.st_mode) != 0o700 if private
                else (not shielded and info.st_mode & 0o002))):
        raise SecretError(_ERROR)


@contextmanager
def _directory(path, *, create=False, exclusive=False):
    """Pin each ancestor and all subsequent IO to descriptors, never symlinks."""
    fd = None
    try:
        parts = Path(path).absolute().parts
        if '..' in parts or len(parts) < 2:
            raise SecretError(_ERROR)
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        _validate_directory(fd)
        shielded = False
        for index, part in enumerate(parts[1:]):
            final = index == len(parts) - 2
            if create and final:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            _validate_directory(fd, private=final, shielded=shielded)
            info = os.fstat(fd)
            # An earlier owner-only traversal barrier also protects nested paths.
            shielded = shielded or (info.st_uid == os.getuid()
                                    and stat.S_IMODE(info.st_mode) == 0o700)
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield fd
    except (OSError, ValueError):
        raise SecretError(_ERROR) from None
    finally:
        if fd is not None:
            os.close(fd)


def _read(fd, name, limit=MAX_TOKEN):
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(child, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > limit):
            raise SecretError(_ERROR)
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise SecretError(_ERROR)
    return raw


def _atomic(fd, name, raw):
    """Same-directory exclusive temporary, durable atomic publication."""
    temporary = '.pending-' + secrets.token_hex(16)
    owned = False
    try:
        child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=fd)
        owned = True
        with os.fdopen(child, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        owned = False
        os.fsync(fd)
    finally:
        if owned:
            os.unlink(temporary, dir_fd=fd)


def _cipher(fd):
    cipher = Fernet(_read(fd, 'key', 44))
    if cipher.decrypt(_read(fd, 'check', 256)) != _CHECK:
        raise SecretError(_ERROR)
    return cipher


def initialize(directory):
    """Initialize empty storage only; existing keys/data are never reset."""
    try:
        with _directory(directory, create=True, exclusive=True) as fd:
            if os.listdir(fd):
                _cipher(fd)  # idempotent validation, never recreate missing material
                return
            key = Fernet.generate_key()
            _atomic(fd, 'key', key)
            _atomic(fd, 'check', Fernet(key).encrypt(_CHECK))
            _cipher(fd)
    except Exception:
        raise SecretError(_ERROR) from None


class LocalVaultProvider:
    def __init__(self, directory, *, allow_write=False):
        if type(allow_write) is not bool:
            raise SecretError('explicit boolean required for local vault writes')
        self._directory = Path(directory)
        self._allow_write = allow_write

    def describe(self):
        return {'provider': 'local-vault', 'can_store': self._allow_write,
                'capabilities': ['read', 'store'] if self._allow_write else ['read'],
                'namespaced_references': True, 'persistent_secret_cache': False,
                'encryption': 'fernet'}

    def verify(self):
        """Validate existing key and marker; creates nothing and reads no secret."""
        with _directory(self._directory) as fd:
            _cipher(fd)

    def _filename(self, name):
        return hashlib.sha256(_name(name).encode('ascii')).hexdigest() + '.fernet'

    @staticmethod
    def _decrypt(cipher, token, name):
        prefix = name.encode('ascii') + b'\0'
        plain = cipher.decrypt(token)
        if not plain.startswith(prefix) or not 0 < len(plain) - len(prefix) <= MAX_SECRET:
            raise SecretError(_ERROR)
        return plain[len(prefix):]

    def store(self, name, value):
        if not self._allow_write:
            raise SecretError('local vault is read-only')
        filename = self._filename(name)
        try:
            raw = value.bytes()
            if not 0 < len(raw) <= MAX_SECRET:
                raise SecretError('local vault value is empty or too large')
            with _directory(self._directory, exclusive=True) as fd:
                cipher = _cipher(fd)
                try:
                    previous = _read(fd, filename)
                    self._decrypt(cipher, previous, name)
                except FileNotFoundError:
                    previous = None
                token = cipher.encrypt(name.encode('ascii') + b'\0' + raw)
                try:
                    _atomic(fd, filename, token)
                    readback = self._decrypt(cipher, _read(fd, filename), name)
                    if not hmac.compare_digest(raw, readback):
                        raise SecretError(_ERROR)
                except Exception:
                    if previous is None:
                        os.unlink(filename, dir_fd=fd)
                        os.fsync(fd)
                    else:
                        _atomic(fd, filename, previous)
                    raise
        except Exception:
            raise SecretError('local vault write or verification failed; inspect state before retry') from None

    @contextmanager
    def resolve(self, name):
        filename = self._filename(name)
        try:
            with _directory(self._directory) as fd:
                cipher = _cipher(fd)
                value = SecretValue(self._decrypt(cipher, _read(fd, filename), name))
        except Exception:
            raise SecretError('local vault secret unavailable') from None
        try:
            yield value
        finally:
            value.close()
