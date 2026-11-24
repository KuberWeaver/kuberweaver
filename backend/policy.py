"""Non-bypassable, provider-neutral admission gates for the scoped lab."""
from __future__ import annotations
import math
import re
from collections import deque
from jsonschema import Draft202012Validator
from .models import Policies

ALLOWED_TARGETS = frozenset({'app', 'branch-client'})
UNSAFE_KEYS = frozenset({'host', 'hosts', 'hostname', 'inventory', 'command', 'commands', 'cmd', 'script', 'playbook', 'shell', 'password', 'passwd', 'token', 'api_key', 'private_key', 'secret', 'secrets', 'ssh_key', 'ssh_password', 'become_password', 'ansible_host', 'ansible_user', 'ansible_password', 'connection', 'executable', 'environment', 'env'})


def unsafe_fields(value, prefix=''):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f'{prefix}.{key}' if prefix else key
            normalized = key.lower().replace('-', '_')
            reference = normalized.endswith('_ref') or normalized.endswith('_refs')
            if normalized in UNSAFE_KEYS or normalized.startswith('ansible_') or (not reference and any(word in normalized for word in ('password', 'passwd', 'token', 'private_key', 'api_key', 'secret'))):
                yield path
            yield from unsafe_fields(child, path)
    elif isinstance(value, list):
        for child in value:
            yield from unsafe_fields(child, prefix)


class Admission:
    def __init__(self, catalog, store):
        self.catalog, self.store = catalog, store

    def settings(self):
        return {'policies': self.store.get('settings', 'policies') or Policies().model_dump()}

    def validate(self, graph, actor_role='admin'):
        manifests = self.catalog.discover()
        graph = self.catalog.normalize(graph)
        settings = self.settings()
        policies = settings['policies']
        violations, warnings, breakdown = [], [], []
        resources = {'cpu': 0., 'memory_mb': 0., 'disk_gb': 0.}
        by_currency: dict[str | None, float] = {}

        def deny(code, message, node_id=None):
            item = {'code': code, 'message': message}
            if node_id:
                item['node_id'] = node_id
            violations.append(item)

        profiles = [manifests[node['plugin_id']].get('admission_profile')
                    for node in graph['nodes'] if node['plugin_id'] in manifests]
        if any(profile not in (None, 'virtual-machine') for profile in profiles):
            deny('admission_profile', 'Unsupported manifest admission profile')
        if profiles and any(profile != profiles[0] for profile in profiles):
            deny('mixed_profiles', 'A job must use one admission profile')
        virtual_machine = bool(profiles) and all(profile == 'virtual-machine' for profile in profiles)
        if virtual_machine and (len(graph['nodes']) != 1 or graph['edges']
                                or graph['environment'] != 'dev' or graph['failure_mode'] != 'none'):
            deny('virtual_machine_scope', 'Virtual-machine jobs require one node, no edges, dev and no failure injection')
        kinds = [manifests[node['plugin_id']].get('kind') for node in graph['nodes']
                 if node['plugin_id'] in manifests]
        workload = bool(kinds) and all(kind == 'workload' for kind in kinds)
        if workload and (len(graph['nodes']) != 1 or graph['edges'] or graph['failure_mode'] != 'none'):
            deny('workload_scope', 'Workload jobs require one brick, no edges and no failure injection')
        names = []
        for node in graph['nodes']:
            plugin = manifests.get(node['plugin_id'], {})
            if 'execution_provider' in plugin:
                name = plugin['execution_provider']
                if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', name):
                    deny('execution_provider', 'Invalid manifest execution provider name', node['id'])
                else:
                    names.append(name)
        if len(set(names)) > 1:
            deny('mixed_execution_providers', 'A job must use one execution provider')

        if len(graph['nodes']) > policies['max_nodes']:
            deny('quota_nodes', 'Node quota exceeded')
        if graph['environment'] not in policies['allowed_environments']:
            deny('environment_not_allowed', 'Environment is disabled by policy')
        if graph['failure_mode'] != 'none' and actor_role != 'admin':
            deny('failure_mode_admin_only', 'Only administrators may request lab failure injection')
        strategy = graph['strategy']
        if not virtual_machine and strategy['canary'] != 1:
            deny('unsupported_canary', 'This isolated lab supports exactly one canary target per component')
        if not virtual_machine and graph['environment'] == 'production' and policies['require_canary_production'] and strategy['canary'] < 1:
            deny('canary_required', 'Production requires at least one canary target')
        all_targets = set()
        claims = set()
        for error in getattr(self, 'config_errors', []):
            deny('configuration_error', 'Administrator configuration is invalid; execution is disabled')
        ids = [node['id'] for node in graph['nodes']]
        if len(set(ids)) != len(ids):
            deny('duplicate_node', 'Node identifiers must be unique')
        if len({edge['id'] for edge in graph['edges']}) != len(graph['edges']):
            deny('duplicate_edge', 'Edge identifiers must be unique')
        for node in graph['nodes']:
            identity, config = node['id'], node['config']
            plugin = manifests.get(node['plugin_id'])
            for path in unsafe_fields(config):
                deny('unsafe_field', f'Inline secrets and executable/connection fields are forbidden: {path}', identity)
            if not plugin:
                deny('unknown_plugin', 'Plugin is not present in the catalog', identity)
                continue
            if plugin['capability'] != 'live':
                deny('planned_plugin', 'Plugin is planned and cannot execute', identity)
            if virtual_machine and (plugin['schema'].get('type') != 'object'
                                    or plugin['schema'].get('additionalProperties') is not False):
                deny('config_schema', 'Virtual-machine manifests require a closed object schema', identity)
            validator = Draft202012Validator(plugin['schema'])
            try:
                for error in validator.iter_errors(config):
                    # Do not echo values: jsonschema messages may contain inline secrets.
                    path = '.'.join(str(p) for p in error.absolute_path)
                    if error.validator == 'additionalProperties':
                        # Field names are schema-level metadata, never values.
                        extra = sorted(set(error.instance) - set(error.schema.get('properties', {})))
                        deny('config_schema', 'Config violates additionalProperties at '
                             + (path or '<root>') + ': ' + ', '.join(extra), identity)
                        continue
                    deny('config_schema', f'Config violates {error.validator} at ' + path, identity)
            except Exception:
                deny('config_schema', 'Plugin schema could not validate this configuration', identity)
            targets = [] if (virtual_machine or workload) else config.get('targets', [])
            if not virtual_machine and not workload:
                if not isinstance(targets, list) or not targets or any(not isinstance(t, str) or t not in ALLOWED_TARGETS for t in targets):
                    deny('target_not_allowed', 'Targets must be app and/or branch-client', identity)
                else:
                    all_targets.update(targets)
                    if len(set(targets)) != len(targets):
                        deny('duplicate_target', 'Targets must be unique', identity)
                port = config.get('port')
                if port is not None and (type(port) is not int or not 18090 <= port <= 18109):
                    deny('port_not_allowed', 'Deployment ports must be in 18090..18109', identity)
                if port is not None and isinstance(targets, list):
                    for target in targets:
                        if not isinstance(target, str):
                            continue
                        claim = (target, str(port))
                        if claim in claims:
                            deny('resource_conflict', 'Two nodes claim the same target and port', identity)
                        claims.add(claim)
                if config.get('public_cidr', '127.0.0.1/32') != '127.0.0.1/32':
                    deny('public_exposure', 'Only loopback exposure is allowed', identity)
            node_resources = {}
            for resource, default in [('cpu', 1), ('memory_mb', 256), ('disk_gb', 2)]:
                value = config.get(resource, plugin.get('resources', {}).get(resource, default))
                if resource == 'cpu' and resource not in config:
                    # Topology manifests declare sockets and cores instead of a total.
                    sockets, cores = config.get('cpu_sockets'), config.get('cores_per_socket')
                    if type(sockets) is int and type(cores) is int:
                        value = sockets * cores
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    deny('invalid_resources', f'Resource value must be a finite nonnegative number: {resource}', identity)
                    value = 0
                node_resources[resource] = value
                # Charge each target, not merely each graph node.
                resources[resource] += value * max(1, len(targets) if isinstance(targets, list) else 1)
            count = max(1, len(targets) if isinstance(targets, list) else 1)
            # The price is declared, never derived: each node carries what the
            # requester typed in, plus the service's own declared monthly price.
            # CPU, memory and disk are quota dimensions, not pricing inputs.
            declared = plugin.get('price_monthly', 0) + float(node.get('price_monthly', 0))
            currency = node.get('currency') or plugin.get('currency')
            if declared * count > 0 and not currency:
                deny('missing_currency',
                     f'Service {plugin["id"]} is priced without a currency; set "currency" '
                     'on the graph node or in its manifest', identity)
            amount = declared * count
            by_currency[currency] = round(by_currency.get(currency, 0.0) + amount, 2)
            breakdown.append({'node_id': identity, 'plugin_id': plugin['id'], 'targets': count,
                              'monthly': round(amount, 2), 'currency': currency,
                              'resources': node_resources})
        for resource in resources:
            if resources[resource] > policies[f'max_{resource}']:
                deny('quota_' + resource, f'{resource} quota exceeded')
        priced_currencies = {ccy for ccy, amount in by_currency.items() if ccy and amount > 0}
        if len(priced_currencies) > 1:
            deny('mixed_currency', 'A job cannot mix currencies; declare one per deployment')
        currency = next(iter(priced_currencies), None)
        monthly = round(by_currency.get(currency, 0.0), 2)
        if monthly > policies['max_monthly_cost']:
            deny('quota_cost', 'Monthly cost limit exceeded')
        if all_targets and strategy['canary'] > len(all_targets):
            deny('canary_size', 'Canary count exceeds distinct deployment targets')
        if all_targets and strategy['batch_size'] > len(all_targets):
            deny('batch_size', 'Batch size exceeds distinct deployment targets')
        indegree = {identity: 0 for identity in ids}
        adjacency = {identity: [] for identity in ids}
        for edge in graph['edges']:
            if edge['source'] not in indegree or edge['target'] not in indegree:
                deny('unknown_edge', 'Dependency edge references an unknown node')
            else:
                indegree[edge['target']] += 1
                adjacency[edge['source']].append(edge['target'])
        todo = deque(identity for identity, degree in indegree.items() if degree == 0)
        order = []
        while todo:
            identity = todo.popleft()
            order.append(identity)
            for target in adjacency[identity]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    todo.append(target)
        if len(order) != len(indegree):
            deny('cycle', 'Dependency graph contains a cycle')
        phases = [{'phase': 'dependency', 'nodes': order}, {'phase': 'canary', 'count': strategy['canary']}, {'phase': 'rolling', 'batch_size': strategy['batch_size']}]
        return {'allowed': not violations, 'violations': violations, 'warnings': warnings,
                'cost': {'monthly': monthly, 'currency': currency,
                         'by_currency': {ccy: amount for ccy, amount in by_currency.items() if ccy},
                         'breakdown': breakdown}, 'phases': phases}
