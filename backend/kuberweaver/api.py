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
    from .runtime import configure
    configure(app)

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
        result = resolve_provider(app, 'inventory').read()
        app.state.inventory_cache = result
        return result

    @app.get('/api/integrations')
    def integrations(current=Depends(user)):
        return integration_list(app)

    @app.get('/api/diagnostics')
    def diagnostics(current=Depends(user)):
        return resolve_provider(app, 'diagnostics').read()

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
        counts = {status: sum(job['status'] == status for job in all_jobs) for status in ('pending_approval', 'queued', 'running', 'rolling_back', 'succeeded', 'failed', 'rejected', 'rolled_back', 'rollback_failed')}
        counts.update(jobs=len(all_jobs), plugins=len(catalog.discover()), blueprints=len(store.list('blueprints')),
                      nodes=len(getattr(app.state, 'inventory_cache', {}).get('nodes', [])), pending_approvals=counts['pending_approval'])
        return {'counts': counts, 'recent_jobs': [jobs.present(job) for job in all_jobs[:10]], 'costs': costs(), 'integrations': integration_list(app)}

    @app.get('/metrics', response_class=PlainTextResponse)
    def metrics():
        all_jobs = store.list('jobs')
        lines = ['# HELP kuberweaver_http_requests_total HTTP requests received by this process.',
                 '# TYPE kuberweaver_http_requests_total counter', f'kuberweaver_http_requests_total {app.state.request_count}',
                 '# HELP kuberweaver_jobs Persisted jobs by current status.', '# TYPE kuberweaver_jobs gauge']
        for status in ('pending_approval', 'queued', 'running', 'rolling_back', 'succeeded', 'failed', 'rejected', 'rolled_back', 'rollback_failed'):
            lines.append('kuberweaver_jobs{status="' + status + '"} ' + str(sum(job['status'] == status for job in all_jobs)))
        lines += ['# TYPE kuberweaver_audit_events_total counter', 'kuberweaver_audit_events_total ' + str(store.one('SELECT count(*) AS n FROM audit')['n']),
                  '# TYPE kuberweaver_queue_depth gauge', 'kuberweaver_queue_depth ' + str(app.state.worker.queue.qsize())]
        return PlainTextResponse('\n'.join(lines) + '\n', media_type='text/plain; version=0.0.4')

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
                if not store.session(request.cookies.get('kw_session')):
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
