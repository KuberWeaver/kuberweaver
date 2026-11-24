"""Declared prices: the requester types them, nothing derives them.

CPU, memory and disk stay quota dimensions. A price is a number on the graph node
plus the service's own manifest price, in one currency declared by the node or by
the manifest.
"""
import json

import pytest

from test_core_graph import api, graph  # noqa: E402


@pytest.fixture
def client(api):
    return api[0]


def _validate(client, graph_body):
    return client.post('/api/plans/validate', json=graph_body).json()


def _write_manifest(directory, body):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'manifest.yaml').write_text(json.dumps(body))


def _manifest(plugin_id, **extra):
    body = {'id': plugin_id, 'name': plugin_id, 'version': '1.0.0', 'capability': 'live',
            'engine': 'test', 'executor': {'provider': 'test'},
            'defaults': {'targets': ['app'], 'port': 18111},
            'schema': {'type': 'object', 'additionalProperties': False}}
    body.update(extra)
    return body


def test_a_node_without_price_is_free_and_needs_no_currency(client):
    result = _validate(client, graph())
    assert result['allowed'], result['violations']
    assert result['cost']['monthly'] == 3
    assert result['cost']['currency'] == 'EUR'
    assert result['cost']['by_currency'] == {'EUR': 3}


def test_the_requester_types_the_node_price(client):
    body = graph()
    body['nodes'][0]['price_monthly'] = 41.5
    result = _validate(client, body)
    assert result['allowed'], result['violations']
    assert result['cost']['monthly'] == pytest.approx(44.5)   # 3 from the manifest + 41.5
    assert result['cost']['breakdown'][0]['monthly'] == pytest.approx(44.5)


def test_zero_is_an_explicit_price_not_an_absent_one(client):
    body = graph()
    body['nodes'][0]['price_monthly'] = 0
    result = _validate(client, body)
    assert result['allowed'], result['violations']
    assert result['cost']['breakdown'][0]['monthly'] == 3


def test_a_node_currency_overrides_the_manifest_currency(client):
    body = graph()
    body['nodes'][0]['price_monthly'] = 10
    body['nodes'][0]['currency'] = 'USD'
    result = _validate(client, body)
    assert result['allowed'], result['violations']
    assert result['cost']['currency'] == 'USD'
    assert result['cost']['by_currency'] == {'USD': 13}
    assert result['cost']['breakdown'][0]['currency'] == 'USD'


def test_mixing_currencies_is_refused_with_a_named_error(client):
    plugins = client.app.state.plugins_dir
    _write_manifest(plugins / 'usd-service', _manifest('usd-service', price_monthly=7, currency='USD'))
    body = graph()
    body['nodes'].append({'id': 'node-2', 'plugin_id': 'usd-service', 'label': 'Paid',
                          'position': {'x': 2, 'y': 2}, 'config': {}})
    result = _validate(client, body)
    assert not result['allowed']
    assert 'mixed_currency' in {v['code'] for v in result['violations']}


def test_a_priced_service_without_any_currency_is_refused(client):
    plugins = client.app.state.plugins_dir
    _write_manifest(plugins / 'priceless-service', _manifest('priceless-service', price_monthly=9))

    candidate = graph()
    candidate['nodes'][0]['plugin_id'] = 'priceless-service'
    result = _validate(client, candidate)
    assert not result['allowed']
    codes = {v['code'] for v in result['violations']}
    assert 'missing_currency' in codes
    message = next(v['message'] for v in result['violations'] if v['code'] == 'missing_currency')
    assert 'priceless-service' in message


def test_an_unknown_currency_code_is_rejected_by_the_catalog(tmp_path):
    from backend.catalog import Catalog
    _write_manifest(tmp_path / 'bad-currency',
                    _manifest('bad-currency', price_monthly=5, currency='Euro'))
    catalog = Catalog(tmp_path)
    assert catalog.discover() == {}
    assert any('Currency' in error['error'] for error in catalog.errors)
