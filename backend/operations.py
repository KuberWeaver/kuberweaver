"""Administrative inspection and explicit recovery of optional integrations."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from .providers import provider_listing, resolve_provider

CAPABILITIES = (
    ('notifications', 'Notifications externes', 'notifications', 'webhook', 'notifications.json'),
)


def install_operations_api(app):
    admin = app.state.role_dependency('admin')

    @app.get('/api/operations')
    def inspect_operations(current=Depends(admin)):
        listing = provider_listing(app)
        rows = []
        for identity, label, kind, name, filename in CAPABILITIES:
            descriptor = next((item for item in listing['available'] if item['kind'] == kind and item['name'] == name), {})
            configured = bool(descriptor.get('available')) and descriptor.get('status') == 'configured'
            rows.append({'id': identity, 'label': label, 'kind': kind, 'provider': name,
                         'configured': configured, 'selected': listing['bindings'].get(kind) == name,
                         'status': descriptor.get('status', 'unconfigured'), 'config_file': filename})
        from .observability import snapshot
        return {'schema_version': 1, 'capabilities': rows, 'observability': snapshot(app),
                'security': 'Administrator configuration files contain references only. Secret values are never returned.'}

    @app.get('/api/operations/notifications')
    def notification_status(current=Depends(admin)):
        provider = resolve_provider(app, 'notifications')
        read = getattr(provider, 'read', None)
        if not callable(read):
            return {'provider': app.state.provider_bindings['notifications'], 'deliveries': [], 'external_delivery': False, 'truncated': False}
        try:
            result = read()
            rows = result.get('deliveries', []) if isinstance(result, dict) else []
            if not isinstance(rows, list):
                raise ValueError('Invalid delivery status')
            safe = []
            for row in rows[-100:]:
                if not isinstance(row, dict):
                    continue
                identity, status, attempts = row.get('id'), row.get('status'), row.get('attempts')
                if (isinstance(identity, str) and len(identity) == 64 and all(c in '0123456789abcdef' for c in identity)
                        and status in {'pending', 'in_flight', 'delivered', 'uncertain', 'exhausted'} and type(attempts) is int and 0 <= attempts <= 5):
                    safe.append({'id': identity, 'status': status, 'attempts': attempts})
            return {'provider': app.state.provider_bindings['notifications'], 'deliveries': safe,
                    'external_delivery': True, 'truncated': len(rows) > 100}
        except Exception:
            raise HTTPException(503, 'Notification delivery status unavailable') from None

    @app.post('/api/operations/notifications/retry')
    def retry_notifications(current=Depends(admin)):
        provider = resolve_provider(app, 'notifications')
        retry = getattr(provider, 'retry_pending', None)
        if not callable(retry):
            raise HTTPException(503, 'Selected notification provider does not support delivery retry')
        app.state.store.audit(current['username'], 'notifications.retry_requested')
        try:
            result = retry()
        except Exception:
            raise HTTPException(503, 'Notification retry unavailable; inspect private delivery status') from None
        # Provider errors and arbitrary payloads can contain credentials: do not echo them.
        safe = {}
        if isinstance(result, dict):
            deliveries = result.get('deliveries')
            if isinstance(deliveries, list):
                safe['attempted'] = len(deliveries)
                for public, status in [('delivered', 'delivered'), ('pending', 'pending'), ('uncertain', 'uncertain'), ('failed', 'exhausted')]:
                    safe[public] = sum(isinstance(row, dict) and row.get('status') == status for row in deliveries)
            for key in ('attempted', 'delivered', 'failed', 'pending', 'uncertain', 'skipped'):
                value = result.get(key)
                if type(value) is int and value >= 0:
                    safe[key] = value
        app.state.store.audit(current['username'], 'notifications.retry_completed', detail=safe)
        return {'result': safe, 'provider': app.state.provider_bindings['notifications']}
