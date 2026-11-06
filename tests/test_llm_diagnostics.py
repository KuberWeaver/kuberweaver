"""Optional diagnostics protocol tests; local HTTP is not real model inference."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kuberweaver.extensions import ExtensionRegistry
from kuberweaver.providers import registered_provider


def configured(tmp_path, config=None, jobs=()):
    registry = ExtensionRegistry()
    app = SimpleNamespace(state=SimpleNamespace(state_dir=tmp_path, registry=registry,
        store=SimpleNamespace(list=lambda table: list(jobs)),
        provider_bindings={'secrets': 'selected'}, provider_config_error=None))
    if config is not None:
        path = tmp_path / 'config' / 'diagnostics-llm.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(config))
    from kuberweaver.llm_diagnostics import register_llm_diagnostics
    register_llm_diagnostics(registry, app)
    return registry, app


JOB = {'id': 'a' * 32, 'status': 'failed', 'events': [
    {'id': 1, 'phase': 'execution', 'message': 'DO-NOT-SEND-log-password'},
    {'id': 2, 'phase': 'complete', 'message': 'DO-NOT-SEND-error'}],
    'graph': {'password': 'DO-NOT-SEND-graph'}, 'config': 'DO-NOT-SEND-config',
    'error': 'DO-NOT-SEND-error'}
FINDING = {'id': 'attention', 'severity': 'warning', 'title': 'Review failure',
    'message': 'Execution failed.', 'resource': JOB['id'], 'event_ids': [1, 2],
    'recommendation': 'Have an administrator review persisted evidence.'}


@contextmanager
def compatible_server(result=None, *, raw=None, status=200, delay=0):
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            observed.append({'path': self.path, 'body': json.loads(body),
                             'authorization': self.headers.get('Authorization')})
            if delay:
                import time
                time.sleep(delay)
            payload = raw if raw is not None else json.dumps({'choices': [{'message': {
                'role': 'assistant', 'content': json.dumps(result or {
                    'read_only': True, 'findings': [FINDING]})}, 'finish_reason': 'stop'}],
                'usage': {'completion_tokens': 100}}).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {'enabled': True, 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
               'allow_loopback_http': True, 'model': 'local-test', 'timeout': 1.0}, observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_local_protocol_inference_only_receives_bounded_redacted_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'DO-NOT-SEND-env')
    with compatible_server() as (config, observed):
        registry, _ = configured(tmp_path, config, [JOB])
        provider = registered_provider(registry, 'diagnostics', 'llm')
        result = provider.run()
        assert result['mode'] == 'llm'
        assert result['read_only'] is True
        assert result['findings'] == [FINDING]
        assert result['summary'] == {'total': 1, 'critical': 0, 'warning': 1}
        assert len(observed) == 1
        request = observed[0]
        assert request['path'] == '/v1/chat/completions'
        assert request['authorization'] is None
        assert 'DO-NOT-SEND' not in json.dumps(request)
        payload = request['body']
        assert not ({'tools', 'functions', 'tool_choice'} & payload.keys())
        assert payload['model'] == 'local-test'
        assert payload['max_tokens'] <= 2048
        assert payload['response_format']['type'] == 'json_schema'
        evidence = json.loads(payload['messages'][1]['content'])
        assert evidence['jobs'] == [{'id': JOB['id'], 'status': 'failed',
                                      'phases': ['execution', 'complete'],
                                      'events': [{'id': 1, 'phase': 'execution'}, {'id': 2, 'phase': 'complete'}]}]
        assert next(row for row in registry.list() if row['name'] == 'llm')['status'] == 'configured'


@pytest.mark.parametrize('override', [
    {'enabled': 'true'}, {'base_url': 'http://example.org/v1'},
    {'base_url': 'http://localhost:9000/v1'}, {'allow_loopback_http': False},
    {'base_url': 'https://user:password@example.org/v1'},
    {'base_url': 'https://example.org/v1?api_key=secret'},
    {'base_url': 'https://example.org/v1#fragment'},
    {'base_url': 'https://example.org:invalid/v1'},
    {'base_url': 'https://example.org/v1'},  # remote needs explicit secret reference
    {'model': ''}, {'timeout': 0}, {'timeout': 61}, {'timeout': True},
    {'timeout': float('nan')}, {'api_key': 'DO-NOT-SEND-key'},
    {'api_key_ref': '../credential'}, {'unexpected': 'value'},
])
def test_invalid_administrator_config_has_unavailable_descriptor(tmp_path, override):
    config = {'enabled': True, 'base_url': 'http://127.0.0.1:9000/v1',
              'allow_loopback_http': True, 'model': 'local-test', 'timeout': 1}
    config.update(override)
    registry, _ = configured(tmp_path, config)
    row = next(row for row in registry.list() if row['name'] == 'llm')
    assert row['status'] == 'unavailable'
    assert 'DO-NOT-SEND' not in json.dumps(row)
    with pytest.raises(HTTPException):
        registered_provider(registry, 'diagnostics', 'llm')


def test_disabled_descriptor_does_not_expose_implementation(tmp_path):
    registry, _ = configured(tmp_path, {'enabled': False})
    row = next(row for row in registry.list() if row['name'] == 'llm')
    assert row['status'] == 'disabled'
    with pytest.raises(HTTPException):
        registered_provider(registry, 'diagnostics', 'llm')


def test_malformed_config_is_sanitized_unavailable(tmp_path):
    path = tmp_path / 'config' / 'diagnostics-llm.json'
    path.parent.mkdir()
    path.write_text('DO-NOT-SEND-invalid-json')
    registry, _ = configured(tmp_path)
    assert registry.list()[0]['status'] == 'unavailable'
    assert 'DO-NOT-SEND' not in json.dumps(registry.list())


def test_selected_secret_is_resolved_only_at_explicit_run_and_rotates(tmp_path):
    from kuberweaver.secrets import RestrictedFileProvider
    key = tmp_path / 'key'
    with compatible_server() as (config, observed):
        config['api_key_ref'] = 'diagnostics/key'
        registry, app = configured(tmp_path, config, [JOB])
        # Key/provider did not exist during registration.
        key.write_text('DO-NOT-SEND-first-key')
        key.chmod(0o600)
        registry.register('secrets', 'selected', RestrictedFileProvider({'diagnostics/key': key}))
        provider = registered_provider(registry, 'diagnostics', 'llm')
        provider.run()
        assert observed[0]['authorization'] == 'Bearer DO-NOT-SEND-first-key'
        assert 'DO-NOT-SEND' not in json.dumps(observed[0]['body'])
        key.write_text('DO-NOT-SEND-rotated-key')
        provider.run()
        assert observed[1]['authorization'] == 'Bearer DO-NOT-SEND-rotated-key'
        assert 'DO-NOT-SEND' not in json.dumps(registry.list())
        assert 'DO-NOT-SEND' not in repr(provider)


@pytest.mark.parametrize('result', [
    {'read_only': False, 'findings': [FINDING]},
    {'read_only': 1, 'findings': [FINDING]},
    {'findings': [FINDING]},
    {'read_only': True, 'findings': [{**FINDING, 'severity': 'urgent'}]},
    {'read_only': True, 'findings': [{**FINDING, 'resource': 'invented-job'}]},
    {'read_only': True, 'findings': [{**FINDING, 'citations': ['invented-job']}]},
    {'read_only': True, 'findings': [{**FINDING, 'message': 'x' * 2001}]},
    {'read_only': True, 'findings': [{**FINDING, 'title': 42}]},
    {'read_only': True, 'findings': [{**FINDING, 'id': ''}]},
    {'read_only': True, 'findings': [FINDING] * 21},
    {'read_only': True, 'findings': [FINDING, FINDING]},
    {'read_only': True, 'findings': [], 'tools': []},
])
def test_untrusted_model_schema_fails_closed(tmp_path, result):
    with compatible_server(result) as (config, observed):
        registry, _ = configured(tmp_path, config, [JOB])
        with pytest.raises(HTTPException) as exc:
            registered_provider(registry, 'diagnostics', 'llm').run()
        assert exc.value.status_code == 503
        assert exc.value.detail == 'LLM diagnostics unavailable or invalid response'
        assert len(observed) == 1  # no retries or fallback inference


@pytest.mark.parametrize('options', [
    {'raw': b'DO-NOT-SEND-malformed'},
    {'raw': b'DO-NOT-SEND-upstream-error', 'status': 500},
    {'raw': b'DO-NOT-SEND-upstream-error', 'status': 302},
    {'raw': b'x' * 32769},
    {'delay': 0.3},
    {'raw': json.dumps({'choices': [{'finish_reason': 'length', 'message': {
        'content': json.dumps({'read_only': True, 'findings': [FINDING]})}}]}).encode()},
    {'raw': json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps({'read_only': True, 'findings': [FINDING]}),
        'tool_calls': [{'function': {'name': 'execute'}}]}}]}).encode()},
    {'raw': json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps({'read_only': True, 'findings': [FINDING]})}}],
        'usage': {'completion_tokens': 2049}}).encode()},
])
def test_transport_and_protocol_failures_are_sanitized_503(tmp_path, options):
    with compatible_server(**options) as (config, observed):
        config['timeout'] = 0.05
        registry, _ = configured(tmp_path, config, [JOB])
        with pytest.raises(HTTPException) as exc:
            registered_provider(registry, 'diagnostics', 'llm').run()
        assert exc.value.status_code == 503
        assert exc.value.detail == 'LLM diagnostics unavailable or invalid response'
        assert 'DO-NOT-SEND' not in str(exc.value)


def test_api_key_echo_is_rejected_and_not_exposed(tmp_path):
    from kuberweaver.secrets import RestrictedFileProvider
    key = tmp_path / 'key'
    key.write_text('DO-NOT-SEND-api-key')
    key.chmod(0o600)
    with compatible_server({'read_only': True, 'findings': [
            {**FINDING, 'message': 'Echo DO-NOT-SEND-api-key'}]}) as (config, observed):
        config['api_key_ref'] = 'diagnostics/key'
        registry, _ = configured(tmp_path, config, [JOB])
        registry.register('secrets', 'selected', RestrictedFileProvider({'diagnostics/key': key}))
        with pytest.raises(HTTPException) as exc:
            registered_provider(registry, 'diagnostics', 'llm').run()
        assert exc.value.status_code == 503
        assert 'DO-NOT-SEND' not in str(exc.value)


def test_read_never_infers_and_explicit_run_populates_safe_cache(tmp_path):
    with compatible_server() as (config, observed):
        registry, _ = configured(tmp_path, config, [JOB])
        provider = registered_provider(registry, 'diagnostics', 'llm')
        assert provider.requires_explicit_run is True
        initial = provider.read()
        assert initial['mode'] == 'llm'
        assert initial['status'] == 'not_run'
        assert initial['read_only'] is True
        assert initial['findings'] == []
        assert initial['summary'] == {'total': 0, 'critical': 0, 'warning': 0}
        assert observed == []
        result = provider.run()
        assert result['status'] == 'cached'
        assert provider.read() == result
        provider.read()['findings'].clear()  # caller cannot mutate the cache
        assert provider.read()['findings'] == [FINDING]
        assert len(observed) == 1
        row = next(row for row in registry.list() if row['name'] == 'llm')
        assert row['requires_explicit_run'] is True


def test_persisted_evidence_is_bounded_and_unknown_values_are_not_exported(tmp_path):
    from kuberweaver.storage import Store
    with compatible_server({'read_only': True, 'findings': []}) as (config, observed):
        registry, app = configured(tmp_path, config)
        store = Store(tmp_path / 'persisted')
        try:
            app.state.store = store
            for index in range(120):
                identity = f'{index:032x}'
                store.put('jobs', identity, {**JOB, 'id': identity,
                    'status': 'DO-NOT-SEND-status', 'events': [
                        {'id': 1, 'phase': 'execution', 'message': 'DO-NOT-SEND-log'},
                        {'id': 2, 'phase': 'DO-NOT-SEND-phase'},
                        {'id': 3, 'phase': 'complete'}] * 300})
            store.put('jobs', 'malformed', {**JOB, 'id': 'DO-NOT-SEND-id'})
            result = registered_provider(registry, 'diagnostics', 'llm').run()
            assert result['findings'] == []
            prompt = observed[0]['body']['messages'][1]['content']
            assert len(prompt.encode()) <= 8192
            assert 'DO-NOT-SEND' not in json.dumps(observed[0]['body'])
            jobs = json.loads(prompt)['jobs']
            assert 0 < len(jobs) <= 50
            assert all(row['status'] == 'unknown' for row in jobs)
            assert all(row['phases'] == ['execution', 'complete'] for row in jobs)
            assert all(row['events'] == [{'id': 1, 'phase': 'execution'},
                                         {'id': 3, 'phase': 'complete'}] for row in jobs)
        finally:
            store.conn.close()


def test_invented_event_reference_is_rejected(tmp_path):
    result = {'read_only': True, 'findings': [{**FINDING, 'event_ids': [9999]}]}
    with compatible_server(result) as (config, _):
        registry, _ = configured(tmp_path, config, [JOB])
        with pytest.raises(HTTPException) as exc:
            registered_provider(registry, 'diagnostics', 'llm').run()
        assert exc.value.status_code == 503


def test_existing_but_omitted_job_cannot_be_cited(tmp_path):
    jobs = [{**JOB, 'id': f'{index:032x}'} for index in range(100)] + [JOB]
    with compatible_server() as (config, _):
        registry, _ = configured(tmp_path, config, jobs)
        with pytest.raises(HTTPException) as exc:
            registered_provider(registry, 'diagnostics', 'llm').run()
        assert exc.value.status_code == 503


@pytest.mark.parametrize('location,field,value', [
    ('message', 'actions', [{'command': 'execute'}]),
    ('message', 'tool_calls', []), ('message', 'function_call', None),
    ('message', 'role', 'tool'),
    ('choice', 'tools', []), ('envelope', 'tool_calls', []),
])
def test_unknown_model_protocol_fields_are_not_silently_discarded(tmp_path, location, field, value):
    message = {'role': 'assistant', 'content': json.dumps({'read_only': True, 'findings': [FINDING]})}
    choice = {'finish_reason': 'stop', 'message': message}
    envelope = {'choices': [choice]}
    target = {'message': message, 'choice': choice, 'envelope': envelope}[location]
    target[field] = value
    with compatible_server(raw=json.dumps(envelope).encode()) as (config, _):
        registry, _ = configured(tmp_path, config, [JOB])
        with pytest.raises(HTTPException) as exc:
            registered_provider(registry, 'diagnostics', 'llm').run()
        assert exc.value.status_code == 503


def test_missing_selected_secret_is_sanitized_without_network(tmp_path):
    with compatible_server() as (config, observed):
        config['api_key_ref'] = 'diagnostics/key'
        registry, _ = configured(tmp_path, config, [JOB])
        provider = registered_provider(registry, 'diagnostics', 'llm')
        assert provider.read()['status'] == 'not_run'
        with pytest.raises(HTTPException) as exc:
            provider.run()
        assert exc.value.status_code == 503
        assert observed == []
        assert provider.read()['status'] == 'not_run'


@pytest.mark.parametrize('raw', [
    b'{"choices": [{"message": {"tool_calls": [{"name": "execute"}]}, "message": {"content": "{\\"read_only\\":true,\\"findings\\":[]}"}, "finish_reason": "stop"}]}',
    b'{"choices": [{"message": {"content": "{\\"read_only\\":false,\\"read_only\\":true,\\"findings\\":[]}"}, "finish_reason": "stop"}]}',
])
def test_duplicate_json_fields_cannot_hide_untrusted_model_data(tmp_path, raw):
    with compatible_server(raw=raw) as (config, _):
        registry, _ = configured(tmp_path, config, [JOB])
        with pytest.raises(HTTPException) as exc:
            registered_provider(registry, 'diagnostics', 'llm').run()
        assert exc.value.status_code == 503


def test_unconfigured_descriptor_fails_closed(tmp_path):
    registry, _ = configured(tmp_path)
    row = next(row for row in registry.list() if row['name'] == 'llm')
    assert row['status'] == 'unconfigured'
    assert row['read_only'] is True
    assert row['mode'] == 'llm'
    with pytest.raises(HTTPException) as exc:
        registered_provider(registry, 'diagnostics', 'llm')
    assert exc.value.status_code == 503
