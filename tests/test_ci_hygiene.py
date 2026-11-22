"""The repository hygiene gate has to refuse what the documentation promises.

`CONTRIBUTING.md` and `SECURITY.md` tell a contributor that key material,
`.env` files and runtime state cannot be committed. This asserts that the scanner
actually enforces it, and that it still tolerates a test fixture which quotes a
key header without holding a credential.
"""
import importlib.util
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SCANNER = REPOSITORY / 'ops' / 'ci' / 'secret_scan.py'


def load_scanner():
    """Import the gate without depending on PYTHONPATH or an installed package."""
    spec = importlib.util.spec_from_file_location('kuberweaver_secret_scan', SCANNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_state_and_key_suffixes_are_refused():
    scan = load_scanner().scan
    for name in ('data/config/secrets.json', 'archive/docs/notes.md',
                 'server.pem', 'client.key', 'keystore.p12', 'bundle.pfx',
                 '.env'):
        assert scan(name, b'anything'), name


def test_a_documentation_example_stays_committable():
    # A template is useful and harmless; only a real environment file is refused.
    assert load_scanner().scan('.env.example', b'DATABASE_URL=') == []


def test_token_prefixes_are_refused_outside_fixtures():
    scan = load_scanner().scan
    for name, content in (('app.py', b'ghp_' + b'a' * 30),
                          ('config.py', b'github_pat_' + b'b' * 25),
                          ('inventory.py', b'AKIA' + b'C' * 16)):
        problems = scan(name, content)
        assert problems, name
        assert all('looks like' in problem for problem in problems), problems


def test_a_pasted_private_key_header_alone_is_not_flagged():
    # A regression test quotes the header. Without a body and footer it holds no
    # credential, so refusing it would block the very tests that guard this file.
    assert load_scanner().scan('tests/test_fixture_keys.py', b'-----BEGIN OPENSSH PRIVATE KEY-----') == []


def test_a_real_key_block_is_refused_outside_fixtures_and_tolerated_inside():
    # A test may carry a synthetic block to assert on the header; refusing it would
    # block the very tests that guard this file. Outside `tests/` it is refused.
    body = b'-----BEGIN OPENSSH PRIVATE KEY-----\n' + b'x' * 200 + b'\n-----END OPENSSH PRIVATE KEY-----\n'
    scan = load_scanner().scan
    assert scan('scripts/leak.py', body)
    assert scan('tests/test_leak.py', body) == []


def test_the_repository_carries_no_refused_path():
    """Cheap end-to-end check: the gate passes on the tree it ships with."""
    assert load_scanner().scan_tree() == []
