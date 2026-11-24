"""Application integration: selection, controller lease and safe failure metadata."""
import pytest


def test_app_requests_controller_exclusivity_by_runtime_mode(tmp_path, monkeypatch):
    import backend.app as module
    original = module.Store
    seen = []

    def recording(*args, **kwargs):
        seen.append(kwargs.copy())
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'Store', recording)
    for testing in (True, False):
        app = module.create_app(tmp_path / str(testing), testing=testing)
        try:
            assert seen[-1].get('exclusive') is (not testing)
        finally:
            app.state.store.conn.close()


def test_observability_handles_postgres_storage_failure_without_details(api, monkeypatch):
    from backend.observability import snapshot
    from backend.storage import StorageError
    _, app = api

    def failed(*args, **kwargs):
        raise StorageError('private-storage-error-marker')

    monkeypatch.setattr(app.state.store, 'all', failed)
    evidence = snapshot(app)
    assert evidence['storage']['status'] == 'unavailable'
    assert evidence['jobs'] is None
    assert 'private-storage-error-marker' not in str(evidence)


from test_core_graph import api
from test_postgresql_store import pg_file


def test_postgresql_startup_flock_failure_releases_connection(tmp_path, pg_file):
    import asyncio
    import subprocess
    import sys
    from backend.app import create_app
    from backend.storage import Store

    app = create_app(tmp_path / 'server', testing=False)
    holder = subprocess.Popen(
        [sys.executable, '-c',
         'import fcntl,sys; f=open(sys.argv[1], "a"); '
         'fcntl.flock(f, fcntl.LOCK_EX); print("locked", flush=True); sys.stdin.read()',
         str(app.state.state_dir / '.server.lock')],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == 'locked'

        async def startup():
            async with app.router.lifespan_context(app):
                pytest.fail('startup must reject a foreign flock')

        with pytest.raises(RuntimeError, match='already running'):
            asyncio.run(startup())
        assert app.state.store.conn.raw.closed
        reopened = Store(tmp_path / 'after-failure', exclusive=True)
        reopened.conn.close()
    finally:
        holder.communicate(timeout=5)
        app.state.store.conn.close()


@pytest.mark.parametrize('stage', ['AgentTokens', 'install_api'])
@pytest.mark.parametrize('failure', [RuntimeError, KeyboardInterrupt])
def test_postgresql_construction_failure_releases_connection(tmp_path, pg_file, monkeypatch, stage, failure):
    import backend.app as module
    import backend.api as api_module
    from backend.storage import Store

    captured = []

    def recording(*args, **kwargs):
        store = Store(*args, **kwargs)
        captured.append(store)
        return store

    def failed(*args, **kwargs):
        raise failure('construction failed')

    monkeypatch.setattr(module, 'Store', recording)
    monkeypatch.setattr(module if stage == 'AgentTokens' else api_module, stage, failed)
    try:
        with pytest.raises(failure, match='construction failed'):
            module.create_app(tmp_path / 'server', testing=False)
        assert len(captured) == 1
        assert captured[0].conn.raw.closed
        reopened = Store(tmp_path / 'after-failure', exclusive=True)
        reopened.conn.close()
    finally:
        for store in captured:
            store.conn.close()


def test_postgresql_controller_lease_released_after_lifespan(tmp_path, pg_file):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.storage import Store, StorageError
    app = create_app(tmp_path / 'server', testing=False)
    try:
        with TestClient(app, base_url='http://localhost'):
            with pytest.raises(StorageError, match='active'):
                Store(tmp_path / 'contender', exclusive=True)
        reopened = Store(tmp_path / 'after-stop', exclusive=True)
        reopened.conn.close()
    finally:
        app.state.store.conn.close()
