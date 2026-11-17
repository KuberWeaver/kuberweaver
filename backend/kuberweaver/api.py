"""HTTP adapters around microkernel services."""
from fastapi import Depends, HTTPException
from pydantic import ValidationError
from .models import BlueprintRequest, Graph, JobRequest, Policies, Pricing, SettingsPatch
from .providers import integrations as integration_list, resolve_provider, provider_listing
from .runtime import TrustRequest, extension_listing, change_extension, ProviderPatch, change_provider_bindings
from .storage import now
import asyncio
import json
import secrets
from fastapi import Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from .catalog import Catalog
from . import reporting
from .policy import Admission


def install_api(app, plugins_dir, executor=None):
    store = app.state.store
    user, role = app.state.user_dependency, app.state.role_dependency
    app.state.plugins_dir = plugins_dir
    catalog = app.state.catalog = Catalog(plugins_dir)
    admission = app.state.admission = Admission(catalog, store)
    from .execution import Executor
    from .jobs import Jobs, Worker
    app.state.executor = executor if executor is not None else Executor(store.path, plugins_dir)
    jobs = app.state.jobs = Jobs(app)
    app.state.worker = Worker(app)
    from .environment_state import install_environment_state_api
    install_environment_state_api(app)
    from .declarations import install_declarations_api
    install_declarations_api(app, app.state.environment_states)
    from .runtime import configure
    configure(app)
    from .ssh_keys import install_ssh_keys_api
    install_ssh_keys_api(app)
    from .connections import install_connections_api
    install_connections_api(app)
    from .dashboards import install_dashboards_api
    install_dashboards_api(app)
    if hasattr(app.state.executor, 'secret_resolver'):
        app.state.executor.secret_resolver = lambda reference: resolve_provider(app, 'secrets').resolve(reference)
    from .notification_dispatcher import NotificationDispatcher
    services = getattr(app.state, 'background_services', [])
    services.append(NotificationDispatcher(app))
    app.state.background_services = services
    from .operations import install_operations_api
    install_operations_api(app)
    from .chatops import install_chatops_api
    install_chatops_api(app)

    @app.post('/api/jobs', status_code=201)
    def submit_job(data: JobRequest, current=Depends(role('admin', 'operator'))):
        return jobs.present(jobs.create(data.graph.model_dump(), current))

    @app.get('/api/jobs')
    def list_jobs(current=Depends(user)):
        return [jobs.present(job) for job in store.list('jobs')]

    @app.get('/api/jobs/{identity}')
    def get_job(identity: str, current=Depends(user)):
        return jobs.present(jobs.get(identity))

    @app.post('/api/jobs/{identity}/approve')
    def approve_job(identity: str, current=Depends(role('admin'))):
        return jobs.present(jobs.approve(identity, current))

    @app.post('/api/jobs/{identity}/reject')
    def reject_job(identity: str, current=Depends(role('admin'))):
        return jobs.present(jobs.reject(identity, current))

    @app.post('/api/jobs/{identity}/rollback')
    def rollback_job(identity: str, current=Depends(role('admin'))):
        return jobs.present(jobs.rollback(identity, current))

    @app.get('/api/plugins')
    def plugins(current=Depends(user)):
        return list(catalog.discover().values())

    @app.post('/api/plans/validate')
    def validate(graph: Graph, current=Depends(user)):
        jobs.notify('before_plan', {'job_id': None, 'status': 'planning', 'operation': 'validate', 'environment': graph.environment})
        return admission.validate(graph.model_dump(), current['role'])

    @app.get('/api/settings')
    def settings(current=Depends(user)):
        return {**admission.settings(), 'integrations': integration_list(app)}

    @app.patch('/api/settings')
    def update_settings(patch: SettingsPatch, current=Depends(role('admin'))):
        with store.lock:
            settings = admission.settings()
            try:
                if patch.policies is not None:
                    settings['policies'] = Policies.model_validate({**settings['policies'], **patch.policies}).model_dump()
                if patch.pricing is not None:
                    settings['pricing'] = Pricing.model_validate({**settings['pricing'], **patch.pricing}).model_dump()
            except ValidationError:
                raise HTTPException(422, 'Invalid settings field or value')
            from .runtime import persist_settings
            for key, value in settings.items():
                if key in patch.model_fields_set:
                    persist_settings(app, key, value)
            store.audit(current['username'], 'settings.updated', detail={'sections': list(patch.model_fields_set)})
            return {**settings, 'integrations': integration_list(app)}

    @app.get('/api/infrastructure')
    def infrastructure(current=Depends(user)):
        if getattr(app.state, 'provider_config_error', None):
            raise HTTPException(503, 'Provider binding configuration is invalid')
        if not app.state.provider_bindings.get('inventory'):
            result = {'bound': False, 'provider': None, 'source': None,
                      'nodes': [], 'links': [], 'errors': [],
                      'message': 'No inventory provider is selected. Bind one in Providers to read live topology.'}
            app.state.inventory_cache = result
            return result
        result = resolve_provider(app, 'inventory').read()
        app.state.inventory_cache = result
        return result

    @app.get('/api/integrations')
    def integrations(current=Depends(user)):
        return integration_list(app)

    @app.get('/api/diagnostics')
    def diagnostics(current=Depends(user)):
        return resolve_provider(app, 'diagnostics').read()

    import threading
    import time
    diagnostic_lock = threading.Lock()
    diagnostic_attempts = {}
    diagnostic_window = []

    @app.post('/api/diagnostics/run')
    def run_diagnostics(current=Depends(role('admin', 'operator'))):
        # A browser's disabled button is not an API budget boundary. Bound
        # concurrency globally and attempts per account, including failures.
        if not diagnostic_lock.acquire(blocking=False):
            raise HTTPException(429, 'A diagnostic is already running', headers={'Retry-After': '30'})
        try:
            stamp = time.monotonic()
            previous = diagnostic_attempts.get(current['id'])
            diagnostic_window[:] = [attempt for attempt in diagnostic_window if stamp - attempt < 60]
            if (previous is not None and stamp - previous < 30) or len(diagnostic_window) >= 5:
                raise HTTPException(429, 'Diagnostic invocation limit reached; retry later', headers={'Retry-After': '30'})
            provider = resolve_provider(app, 'diagnostics')
            run = getattr(provider, 'run', provider.read)
            diagnostic_attempts[current['id']] = stamp
            diagnostic_window.append(stamp)
            store.audit(current['username'], 'diagnostics.run_requested', detail={'provider': app.state.provider_bindings['diagnostics']})
            try:
                result = run()
            except Exception:
                # Provider HTTPExceptions are no more trusted than any upstream
                # error: their details/headers may contain tokens or URLs.
                store.audit(current['username'], 'diagnostics.run_failed', detail={'provider': app.state.provider_bindings['diagnostics']})
                raise HTTPException(503, 'Selected diagnostic provider unavailable') from None
            store.audit(current['username'], 'diagnostics.run_completed', detail={'provider': app.state.provider_bindings['diagnostics']})
            return result
        finally:
            diagnostic_lock.release()

    @app.get('/api/providers')
    def providers(current=Depends(user)):
        return provider_listing(app)

    @app.patch('/api/providers')
    def update_providers(patch: ProviderPatch, current=Depends(role('admin'))):
        return change_provider_bindings(app, patch, current)

    @app.get('/api/extensions')
    def extensions(current=Depends(user)):
        return extension_listing(app)

    @app.post('/api/extensions/{identity}/enable')
    def enable_extension(identity: str, data: TrustRequest, current=Depends(role('admin'))):
        return change_extension(app, identity, True, current, data.trusted)

    @app.get('/api/reporting')
    def reporting_surface(current=Depends(role('admin', 'operator', 'viewer'))):
        return reporting.describe(app.state.store)

    @app.post('/api/extensions/{identity}/disable')
    def disable_extension(identity: str, current=Depends(role('admin'))):
        return change_extension(app, identity, False, current)

    @app.get('/api/blueprints')
    def blueprints(current=Depends(user)):
        return store.list('blueprints')

    @app.post('/api/blueprints', status_code=201)
    def create_blueprint(data: BlueprintRequest, current=Depends(role('admin', 'operator'))):
        with store.lock:
            value = data.graph.model_dump()
            jobs.check(value, current['role'])
            record = {'id': secrets.token_hex(16), 'name': data.name, 'graph': catalog.normalize(value),
                      'created_at': now(), 'created_by': current['username']}
            store.put('blueprints', record['id'], record)
            store.audit(current['username'], 'blueprint.created', record['id'])
            return record

    @app.get('/api/blueprints/{identity}')
    def get_blueprint(identity: str, current=Depends(user)):
        record = store.get('blueprints', identity)
        if not record:
            raise HTTPException(404, 'Unknown blueprint')
        return record

    @app.get('/api/audit')
    def audit(current=Depends(user)):
        return [{**row, 'detail': json.loads(row['detail'])} for row in store.all('SELECT * FROM audit ORDER BY id DESC LIMIT 1000')]

    def costs():
        active = {}
        all_jobs = store.list('jobs')
        for job in reversed(all_jobs):
            if job['status'] == 'succeeded':
                active[(job['graph']['name'], job['environment'])] = job
        breakdown = []
        for job in active.values():
            report = admission.validate(job['graph'])
            breakdown += [{**row, 'job_id': job['id'], 'name': job['name']} for row in report['cost']['breakdown']]
        monthly = round(sum(row['monthly'] for row in breakdown), 2)
        return {'currency': 'EUR', 'monthly_total': monthly, 'breakdown': breakdown,
                'recommendations': ([{'code': 'review_dev_capacity', 'message': 'Review unused dev workloads and right-size declared resources; estimates are not cloud billing.'}] if breakdown else []),
                'pricing': admission.settings()['pricing'], 'basis': 'Declared resources of latest successful graph per environment, estimated monthly; not metered billing.'}

    @app.get('/api/finops')
    def finops(current=Depends(user)):
        return costs()

    @app.get('/api/overview')
    def overview(current=Depends(user)):
        all_jobs = store.list('jobs')
        counts = {status: sum(job['status'] == status for job in all_jobs) for status in ('pending_approval', 'approving', 'queued', 'running', 'rolling_back', 'succeeded', 'failed', 'rejected', 'rolled_back', 'rollback_failed')}
        counts.update(jobs=len(all_jobs), plugins=len(catalog.discover()), blueprints=len(store.list('blueprints')),
                      nodes=len(getattr(app.state, 'inventory_cache', {}).get('nodes', [])), pending_approvals=counts['pending_approval'])
        return {'counts': counts, 'recent_jobs': [jobs.present(job) for job in all_jobs[:10]], 'costs': costs(), 'integrations': integration_list(app)}

    @app.get('/metrics', response_class=PlainTextResponse)
    def metrics():
        from .observability import metrics_text
        return PlainTextResponse(metrics_text(app), media_type='text/plain; version=0.0.4')

    @app.get('/api/jobs/{identity}/events')
    async def events(identity: str, request: Request, current=Depends(user)):
        jobs.get(identity)
        try:
            cursor = max(0, int(request.headers.get('last-event-id', '0')))
        except ValueError:
            raise HTTPException(422, 'Invalid event cursor')
        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                if getattr(request.state, 'agent', None):
                    # Long-lived streams must re-check machine revocation and
                    # expiry, not reuse the initial request's authentication.
                    bearer = request.headers.get('authorization', '').partition(' ')[2]
                    refreshed = app.state.agent_tokens.authenticate(bearer)
                    if not refreshed or not ({'read', 'read:jobs'} & set(refreshed['scopes'])):
                        return
                elif not app.state.sessions.resolve(request.cookies.get('kw_session')):
                    return
                job = jobs.get(identity)
                for event in job['events']:
                    if event['id'] > cursor:
                        cursor = event['id']
                        yield f'id: {cursor}\nevent: log\ndata: {json.dumps(event)}\n\n'
                if job['status'] in {'succeeded', 'failed', 'rolled_back', 'rollback_failed', 'rejected'}:
                    yield 'event: done\ndata: ' + json.dumps({'status': job['status']}) + '\n\n'
                    return
                yield ': heartbeat\n\n'
                await asyncio.sleep(.75)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
