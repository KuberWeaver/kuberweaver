"""Service Studio: metadata-only artifact register, immutable offerings, owned services.

The studio never fetches an artifact, never stores a secret and never widens the
existing job surface: a service request is an ordinary job with durable
attribution, and approval is still an explicit administrator decision.
"""
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from test_core_auth import bootstrap
from test_core_graph import api, graph
from test_core_graph import manifest as sample_manifest
from test_core_jobs import RecordingExecutor, install_executor, login, wait_job
from test_postgresql_store import pg_file  # noqa: F401 - fixture used by the durable test


def write_plugin(directory, plugin_id='sample-service', capability='live'):
    """Create a catalog entry; a non-live capability is written after discovery shape."""
    body = dict(sample_manifest(directory, plugin_id))
    if capability != 'live':
        body['capability'] = capability
        (directory / plugin_id / 'manifest.yaml').write_text(json.dumps(body))
    return body


def save_blueprint(client, name='Web stack', candidate=None):
    response = client.post('/api/blueprints', json={'name': name, 'graph': candidate or graph()})
    assert response.status_code == 201, response.text
    return response.json()


def publish(client, title='Web stack', blueprint_id=None):
    return client.post('/api/studio/offerings', json={'title': title, 'blueprint_id': blueprint_id})


def test_studio_surfaces_require_authentication(api):
    client, _ = api
    client.cookies.clear()
    assert client.get('/api/studio/artifacts').status_code == 401
    assert client.get('/api/studio/offerings').status_code == 401
    assert client.get('/api/studio/services').status_code == 401
    assert client.post('/api/studio/artifacts',
                       json={'kind': 'oci', 'reference': 'registry.lab.example/x:1'}).status_code == 401
    assert client.post('/api/studio/offerings',
                       json={'title': 'Web stack', 'blueprint_id': 'a' * 32}).status_code == 401
    assert client.post('/api/studio/offerings/' + 'a' * 32 + '/request').status_code == 401


def test_artifact_register_is_metadata_only_and_role_bound(api):
    client, app = api
    jobs_before = len(client.get('/api/jobs').json())
    pin = 'sha256:' + 'a' * 64
    response = client.post('/api/studio/artifacts', json={
        'kind': 'oci', 'reference': 'registry.lab.example/kuberweaver/web:1.4.0', 'pin': pin})
    assert response.status_code == 201, response.text
    artifact = response.json()
    assert set(artifact) == {'id', 'kind', 'reference', 'pin'}
    assert artifact['pin'] == pin
    listing = client.get('/api/studio/artifacts')
    assert listing.status_code == 200
    assert [row['id'] for row in listing.json()] == [artifact['id']]
    assert set(listing.json()[0]) == {'id', 'kind', 'reference', 'pin'}
    # Registration is metadata only: it is never a job and never a fetch.
    assert len(client.get('/api/jobs').json()) == jobs_before
    assert client.get('/api/jobs').json() == []
    assert any(row['action'] == 'studio.artifact_registered'
               for row in client.get('/api/audit').json())
    assert app.state.store.one('SELECT count(*) AS n FROM studio_artifacts')['n'] == 1
    login(client, app.state.state_dir, 'viewer')
    assert client.get('/api/studio/artifacts').status_code == 403
    assert client.post('/api/studio/artifacts',
                       json={'kind': 'oci', 'reference': 'registry.lab.example/x:1'}).status_code == 403
    login(client, app.state.state_dir, 'operator')
    assert client.get('/api/studio/artifacts').status_code == 200
    assert client.post('/api/studio/artifacts',
                       json={'kind': 'ansible-role', 'reference': 'galaxy.lab.example/nginx/1.0'}).status_code == 201


def test_artifact_without_pin_reports_an_explicit_null(api):
    client, _ = api
    response = client.post('/api/studio/artifacts',
                           json={'kind': 'vm-image', 'reference': 'vmdk.lab.example/ubuntu-2404-base'})
    assert response.status_code == 201, response.text
    assert response.json()['pin'] is None
    assert set(response.json()) == {'id', 'kind', 'reference', 'pin'}


def test_artifact_kinds_are_closed(api):
    client, _ = api
    for kind in ('docker', 'oci-chart', '', 'ansible_role'):
        assert client.post('/api/studio/artifacts',
                           json={'kind': kind, 'reference': 'registry.lab.example/x:1'}).status_code == 422
    for kind in ('oci', 'vm-image', 'ansible-role'):
        response = client.post('/api/studio/artifacts',
                               json={'kind': kind, 'reference': 'registry.lab.example/x:1'})
        assert response.status_code == 201, response.text


@pytest.mark.parametrize('extra', [{'job_id': 'a' * 32}, {'created_by': 'admin'},
                                   {'body': {}}, {'served_by': 'job'}])
def test_artifact_registration_cannot_declare_a_job_association(api, extra):
    client, app = api
    payload = {'kind': 'oci', 'reference': 'registry.lab.example/x:1', **extra}
    assert client.post('/api/studio/artifacts', json=payload).status_code == 422
    assert app.state.store.one('SELECT count(*) AS n FROM studio_artifacts')['n'] == 0


def test_studio_exposes_no_lifecycle_mutations(api):
    client, app = api
    record = save_blueprint(client)
    offering = publish(client, 'Web stack', record['id']).json()
    before = app.state.store.get('studio_offerings', offering['id'])
    # The SPA catch-all owns the path shape, so an unknown method is 405 rather
    # than 404; either way there is no handler that can rewrite a publication.
    for response in (
            client.patch('/api/studio/offerings/' + offering['id'], json={'title': 'Renamed'}),
            client.delete('/api/studio/offerings/' + offering['id']),
            client.post('/api/studio/offerings/' + offering['id'] + '/retire'),
            client.post('/api/studio/services/' + offering['id'] + '/stop')):
        assert response.status_code in (404, 405), response.text
    assert app.state.store.get('studio_offerings', offering['id']) == before


@pytest.mark.parametrize('reference', [
    'https://registry-user:review-marker@registry.lab.example/web:1',
    'registry.lab.example/web:1?token=review-marker',
    'registry.lab.example/web:1&password=review-marker',
    'registry.lab.example/web 1',
    'registry.lab.example/web:1\n',
    'x' * 513,
    '',
])
def test_artifact_reference_must_be_bounded_and_secret_free(api, reference):
    client, app = api
    response = client.post('/api/studio/artifacts', json={'kind': 'vm-image', 'reference': reference})
    assert response.status_code == 422, response.text
    assert 'review-marker' not in response.text
    assert app.state.store.one('SELECT count(*) AS n FROM studio_artifacts')['n'] == 0
    assert not any(row['action'] == 'studio.artifact_registered'
                   for row in client.get('/api/audit').json())


def test_artifact_pin_must_be_a_bounded_immutable_locator(api):
    client, _ = api
    for pin in ('', 'sha256:zz zz', 'p' * 257, 'sha256:abc\n'):
        assert client.post('/api/studio/artifacts',
                           json={'kind': 'oci', 'reference': 'registry.lab.example/x:1',
                                 'pin': pin}).status_code == 422


def test_offering_publication_is_admin_only_and_refuses_unsupported_graphs(api):
    client, app = api
    record = save_blueprint(client)
    login(client, app.state.state_dir, 'operator')
    assert publish(client, blueprint_id=record['id']).status_code == 403
    login(client, app.state.state_dir, 'admin')
    assert publish(client, blueprint_id='0' * 32).status_code == 404
    write_plugin(app.state.plugins_dir, 'planned-service', 'planned')
    # A blueprint that predates the capability change stays readable, but it must
    # never become a purchasable offering.
    planned_graph = graph()
    planned_graph['nodes'][0]['plugin_id'] = 'planned-service'
    app.state.store.put('blueprints', 'a' * 32, {'id': 'a' * 32, 'name': 'Planned stack',
                                                 'graph': planned_graph, 'created_at': 'known',
                                                 'created_by': 'admin'})
    response = publish(client, 'Planned stack', 'a' * 32)
    assert response.status_code == 422, response.text
    assert 'planned_plugin' in {row['code'] for row in response.json()['detail']['violations']}
    missing_graph = graph()
    missing_graph['nodes'][0]['plugin_id'] = 'missing-service'
    app.state.store.put('blueprints', 'b' * 32, {'id': 'b' * 32, 'name': 'Missing stack',
                                                 'graph': missing_graph, 'created_at': 'known',
                                                 'created_by': 'admin'})
    assert publish(client, 'Missing stack', 'b' * 32).status_code == 422
    assert app.state.store.one('SELECT count(*) AS n FROM studio_offerings')['n'] == 0


def test_offering_publish_copies_the_blueprint_and_versions_by_title(api):
    from kuberweaver.jobs import digest

    client, app = api
    record = save_blueprint(client)
    first = publish(client, 'Web stack', record['id'])
    assert first.status_code == 201, first.text
    assert set(first.json()) == {'id', 'title', 'version'}
    assert first.json()['version'] == 1
    stored = app.state.store.get('studio_offerings', first.json()['id'])
    # The offering owns a copy of the graph and the fingerprints that were reviewed.
    assert stored['graph'] == record['graph']
    assert stored['graph'] is not record['graph']
    assert stored['pins']['graph_hash'] == digest(stored['graph'])
    assert set(stored['pins']['source_hashes']) == {'sample-service'}
    assert set(stored['pins']['manifest_hashes']) == {'sample-service'}
    # A later blueprint edit is not an offering edit: publication is immutable.
    app.state.store.put('blueprints', record['id'],
                        {**record, 'graph': {**record['graph'], 'name': 'Edited'}})
    second = publish(client, 'Web stack', record['id'])
    assert second.status_code == 201, second.text
    assert second.json()['version'] == 2
    assert second.json()['id'] != first.json()['id']
    assert app.state.store.get('studio_offerings', first.json()['id'])['graph']['name'] == 'My graph'
    assert app.state.store.get('studio_offerings', second.json()['id'])['graph']['name'] == 'Edited'
    listing = client.get('/api/studio/offerings')
    assert listing.status_code == 200
    assert [(row['id'], row['title'], row['version']) for row in listing.json()] == [
        (second.json()['id'], 'Web stack', 2), (first.json()['id'], 'Web stack', 1)]
    assert set(listing.json()[0]) == {'id', 'title', 'version'}
    assert any(row['action'] == 'studio.offering_published'
               for row in client.get('/api/audit').json())


def test_offering_request_creates_a_pending_job_with_durable_attribution(api):
    client, app = api
    record = save_blueprint(client)
    offering = publish(client, 'Web stack', record['id']).json()
    login(client, app.state.state_dir, 'operator')
    owner = client.get('/api/auth/me').json()['user']['id']
    response = client.post(f"/api/studio/offerings/{offering['id']}/request")
    assert response.status_code == 201, response.text
    assert set(response.json()) == {'job_id', 'status'}
    assert response.json()['status'] == 'pending_approval'
    job = client.get('/api/jobs/' + response.json()['job_id']).json()
    assert job['status'] == 'pending_approval'  # no approval bypass
    assert job['created_by'] == 'operator'
    assert job['graph'] == app.state.store.get('studio_offerings', offering['id'])['graph']
    attribution = job['service_request']
    assert attribution['offering_id'] == offering['id']
    assert attribution['offering_version'] == 1
    assert attribution['title'] == 'Web stack'
    assert attribution['requested_by'] == owner
    assert attribution['requested_by_username'] == 'operator'
    assert client.post('/api/studio/offerings/' + '0' * 32 + '/request').status_code == 404
    login(client, app.state.state_dir, 'viewer')
    assert client.post(f"/api/studio/offerings/{offering['id']}/request").status_code == 403
    # A viewer cannot bypass the studio to reach the same job surface either.
    assert client.post('/api/jobs', json={'graph': graph()}).status_code == 403


@pytest.mark.parametrize('drift', ['source', 'defaults', 'missing'])
def test_offering_request_refuses_executable_drift(api, drift):
    client, app = api
    record = save_blueprint(client)
    offering = publish(client, 'Web stack', record['id']).json()
    plugin = app.state.plugins_dir / 'sample-service'
    if drift == 'source':
        (plugin / 'lifecycle.yml').write_text('changed reviewed source\n')
    elif drift == 'defaults':
        body = json.loads((plugin / 'manifest.yaml').read_text())
        body['defaults']['port'] = 18091
        (plugin / 'manifest.yaml').write_text(json.dumps(body))
    else:
        shutil.rmtree(plugin)
    response = client.post(f"/api/studio/offerings/{offering['id']}/request")
    assert response.status_code == 409, response.text
    assert client.get('/api/jobs').json() == []
    assert app.state.store.one('SELECT count(*) AS n FROM jobs')['n'] == 0


def test_service_job_cannot_approve_source_drift_after_request(api):
    client, app = api
    record = save_blueprint(client)
    offering = publish(client, 'Web stack', record['id']).json()
    identity = client.post(f"/api/studio/offerings/{offering['id']}/request").json()['job_id']
    (app.state.plugins_dir / 'sample-service' / 'lifecycle.yml').write_text('source changed after request\n')
    response = client.post(f'/api/jobs/{identity}/approve')
    assert response.status_code == 409, response.text
    assert app.state.store.get('jobs', identity)['status'] == 'pending_approval'


def test_services_are_ownership_scoped_with_status_and_cost(api):
    client, app = api
    record = save_blueprint(client)
    offering = publish(client, 'Web stack', record['id']).json()
    login(client, app.state.state_dir, 'operator')
    mine = client.post(f"/api/studio/offerings/{offering['id']}/request").json()['job_id']
    services = client.get('/api/studio/services')
    assert services.status_code == 200
    assert [row['job_id'] for row in services.json()] == [mine]
    row = services.json()[0]
    assert set(row) == {'job_id', 'title', 'status', 'monthly_estimate'}
    assert row['title'] == 'Web stack'
    assert row['status'] == 'pending_approval'
    assert row['monthly_estimate'] == pytest.approx(
        client.get('/api/jobs/' + mine).json()['cost']['monthly'])
    login(client, app.state.state_dir, 'admin')
    # The administrator does not inherit another account's service requests.
    assert client.get('/api/studio/services').json() == []
    admin_job = client.post(f"/api/studio/offerings/{offering['id']}/request").json()['job_id']
    assert [row['job_id'] for row in client.get('/api/studio/services').json()] == [admin_job]
    login(client, app.state.state_dir, 'viewer')
    assert client.get('/api/studio/services').json() == []


def test_service_status_follows_the_existing_job_lifecycle(api):
    client, app = api
    record = save_blueprint(client)
    offering = publish(client, 'Web stack', record['id']).json()
    executor = install_executor(app, RecordingExecutor())
    login(client, app.state.state_dir, 'operator')
    identity = client.post(f"/api/studio/offerings/{offering['id']}/request").json()['job_id']
    assert executor.calls == []
    assert client.post(f'/api/jobs/{identity}/approve').status_code == 403
    login(client, app.state.state_dir, 'admin')
    assert client.post(f'/api/jobs/{identity}/approve').status_code == 200
    result = wait_job(client, identity)
    assert result['status'] == 'succeeded', result
    login(client, app.state.state_dir, 'operator')
    row = client.get('/api/studio/services').json()[0]
    assert row['status'] == 'succeeded'
    assert row['monthly_estimate'] == pytest.approx(result['cost']['monthly'])
    assert row['job_id'] == identity
    assert client.post(f'/api/jobs/{identity}/rollback').status_code == 403
    login(client, app.state.state_dir, 'admin')
    assert client.post(f'/api/jobs/{identity}/rollback').status_code == 200
    assert wait_job(client, identity)['status'] == 'rolled_back'
    login(client, app.state.state_dir, 'operator')
    assert client.get('/api/studio/services').json()[0]['status'] == 'rolled_back'
    assert executor.calls == [('execute', identity), ('rollback', identity)]


def test_jobs_without_studio_attribution_never_appear_in_services(api):
    client, app = api
    job = client.post('/api/jobs', json={'graph': graph()}).json()
    assert job['status'] == 'pending_approval'
    login(client, app.state.state_dir, 'operator')
    assert client.get('/api/studio/services').json() == []


def test_postgresql_registers_the_studio_tables():
    from kuberweaver.postgres import COLUMNS, DDL, ORDERED, translate_sql

    for table in ('studio_artifacts', 'studio_offerings'):
        assert COLUMNS[table] == ('id', 'body')
        assert '_kw_rowid BIGSERIAL UNIQUE NOT NULL' in DDL[table]
        assert table in ORDERED
    assert 'ORDER BY _kw_rowid DESC' in translate_sql(
        'SELECT body FROM studio_offerings ORDER BY rowid DESC')
    assert 'INSERT INTO studio_artifacts(id,body) VALUES(%s,%s)' == translate_sql(
        'INSERT INTO studio_artifacts(id,body) VALUES(?,?)', ('id', '{}'))


def test_postgresql_studio_records_are_durable(tmp_path, pg_file):
    from kuberweaver.app import create_app
    from kuberweaver.storage import Store

    plugins = tmp_path / 'plugins'
    sample_manifest(plugins)
    state = tmp_path / 'pg-state'
    app = create_app(state_dir=state, plugins_dir=plugins, testing=True)
    install_executor(app, RecordingExecutor())
    assert app.state.store.backend == 'postgresql'
    try:
        with TestClient(app) as client:
            login(client, state, 'admin')
            artifact = client.post('/api/studio/artifacts', json={
                'kind': 'oci', 'reference': 'registry.lab.example/web:1.4.0'}).json()
            blueprint_record = save_blueprint(client)
            offering = publish(client, 'Web stack', blueprint_record['id']).json()
            job_id = client.post(f"/api/studio/offerings/{offering['id']}/request").json()['job_id']
            assert client.get('/api/studio/services').json()[0]['job_id'] == job_id
    finally:
        app.state.store.conn.close()
    reopened = Store(state)
    try:
        assert reopened.backend == 'postgresql'
        assert json.loads(reopened.one('SELECT body FROM studio_artifacts WHERE id=?',
                                       (artifact['id'],))['body'])['reference'] == \
            'registry.lab.example/web:1.4.0'
        assert json.loads(reopened.one('SELECT body FROM studio_offerings WHERE id=?',
                                       (offering['id'],))['body'])['version'] == 1
        job = json.loads(reopened.one('SELECT body FROM jobs WHERE id=?', (job_id,))['body'])
        assert job['service_request']['offering_id'] == offering['id']
    finally:
        reopened.conn.close()
