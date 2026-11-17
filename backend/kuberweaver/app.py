"""KuberWeaver local-only microkernel. Provider hooks never replace security gates."""
from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
import math
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .storage import Store
from .agent_auth import AgentTokens, install_agent_tokens, required_scope, permits
from .configuration import frontend_dir as configured_frontend_dir
from .configuration import plugins_dir as configured_plugins_dir
from .configuration import state_dir as configured_state_dir
from .login_throttle import LoginThrottle
from .oidc import OidcConfigurationError, OidcProvider, install_oidc
from .sessions import SESSION_COOKIE, Sessions
from .users import UserStore, install_users


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=512)


def create_app(state_dir: Path | str | None = None, testing: bool = False, *,
               plugins_dir: Path | str | None = None, frontend_dir: Path | str | None = None,
               executor=None, oidc_http=None):
    state_dir = Path(state_dir or configured_state_dir())
    store = Store(state_dir, exclusive=not testing)
    try:
        return _create_app(store, state_dir, testing, plugins_dir=plugins_dir,
                           frontend_dir=frontend_dir, executor=executor, oidc_http=oidc_http)
    except BaseException:
        if store.backend == 'postgresql':
            store.conn.close()
        raise


def _create_app(store, state_dir, testing, *, plugins_dir, frontend_dir, executor, oidc_http=None):
    agent_tokens = AgentTokens(store)

    @asynccontextmanager
    async def lifespan(app):
        import fcntl
        lock = None
        started = []
        worker_started = False
        try:
            if not testing:
                lock = open(state_dir / '.server.lock', 'a')
                os.chmod(state_dir / '.server.lock', 0o600)
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError('KuberWeaver is already running for this state directory; use one server worker')
            app.state.worker.start()
            worker_started = True
            for service in app.state.background_services:
                service.start()
                started.append(service)
            yield
        finally:
            try:
                if worker_started:
                    app.state.worker.close()
            finally:
                try:
                    # ExitStack also runs remaining cleanup if one close raises.
                    from contextlib import ExitStack
                    with ExitStack() as cleanup:
                        for service in started:
                            cleanup.callback(service.close)
                finally:
                    try:
                        if store.backend == 'postgresql':
                            store.conn.close()
                    finally:
                        if lock is not None:
                            lock.close()

    app = FastAPI(title='KuberWeaver', version='0.1.0', lifespan=lifespan)
    app.state.store = store
    app.state.agent_tokens = agent_tokens
    app.state.users = UserStore(store)
    app.state.sessions = Sessions(store)
    app.state.login_throttle = LoginThrottle()
    app.state.background_services = []
    app.state.state_dir = state_dir
    app.state.testing = testing
    app.state.request_count = 0
    try:
        app.state.oidc = OidcProvider.from_environment(store, app.state.users, http=oidc_http)
    except OidcConfigurationError as error:
        # A typo in an optional provider must not brick local password sign-in;
        # the status endpoint reports the refusal and every OIDC route answers 503.
        app.state.oidc = OidcProvider(None, store, app.state.users, http=oidc_http, detail=str(error))

    @app.middleware('http')
    async def security_boundary(request, call_next):
        app.state.request_count += 1
        try:
            parsed_host = urlsplit('http://' + request.headers.get('host', ''))
            host = parsed_host.hostname
            if parsed_host.username or parsed_host.password or parsed_host.path or parsed_host.query or parsed_host.fragment:
                host = None
            parsed_host.port
        except ValueError:
            host = None
        allowed_hosts = {'localhost', '127.0.0.1', '::1'} | ({'testserver'} if testing else set())
        if host not in allowed_hosts:
            return JSONResponse({'detail': 'Local host required'}, status_code=400)
        request.state.agent = None
        # Session state is only needed by the API. Health and static probes must
        # stay usable while durable storage is unavailable, so they never touch
        # the sessions table.
        api_request = request.url.path.startswith('/api/')
        cookie = request.cookies.get(SESSION_COOKIE)
        request.state.session = app.state.sessions.resolve(cookie) if cookie and api_request else None
        request.state.stale_session = bool(cookie) and api_request and request.state.session is None
        authorization = request.headers.getlist('authorization')
        if authorization:
            if len(authorization) != 1 or request.cookies.get(SESSION_COOKIE):
                return JSONResponse({'detail': 'Ambiguous credentials'}, status_code=401)
            scheme, separator, value = authorization[0].partition(' ')
            principal = agent_tokens.authenticate(value) if separator and scheme.lower() == 'bearer' else None
            if not principal:
                return JSONResponse({'detail': 'Invalid agent credentials'}, status_code=401)
            scope = required_scope(request.method, request.url.path)
            if not permits(principal['scopes'], scope):
                store.audit(principal['username'], 'agent.request_denied', detail={'method': request.method, 'scope': scope or 'unsupported'})
                return JSONResponse({'detail': 'Agent scope does not permit this operation'}, status_code=403)
            request.state.agent = principal
            request.state.agent_scope = scope
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            try:
                too_large = int(request.headers.get('content-length', '0')) > 1_048_576
            except ValueError:
                return JSONResponse({'detail': 'Invalid content length'}, status_code=400)
            if too_large or len(await request.body()) > 1_048_576:
                return JSONResponse({'detail': 'Request body too large'}, status_code=413)
            origin = request.headers.get('origin')
            expected = f'{request.url.scheme}://{request.headers.get("host", "")}'
            if origin and origin != expected:
                return JSONResponse({'detail': 'Cross-origin request rejected'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': 'Cross-site request rejected'}, status_code=403)
            signed_callback = request.method == 'POST' and request.url.path == '/api/chatops/callback'
            if request.url.path.startswith('/api/') and request.url.path != '/api/auth/login' and not request.state.agent and not signed_callback:
                if not request.state.session:
                    return JSONResponse({'detail': 'Authentication required'}, status_code=401)
                if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), request.state.session['csrf']):
                    return JSONResponse({'detail': 'CSRF token required'}, status_code=403)
        response = await call_next(request)
        if request.state.agent:
            agent_tokens.record_use(request.state.agent, request.method, request.url.path,
                                    response.status_code, getattr(request.state, 'agent_scope', None))
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        # A page that frames an allowlisted dashboard sets both embedding headers
        # on its own response; every other response keeps the default boundary,
        # which forbids framing this portal and any remote origin.
        if 'x-frame-options' not in response.headers:
            response.headers['X-Frame-Options'] = 'DENY'
        if 'content-security-policy' not in response.headers:
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        if request.state.stale_session and request.url.path != '/api/auth/login' \
                and SESSION_COOKIE not in response.headers.get('set-cookie', ''):
            app.state.sessions.clear_cookie(response, secure=request.url.scheme == 'https')
        return response

    def user(request: Request):
        if getattr(request.state, 'agent', None):
            return request.state.agent
        result = getattr(request.state, 'session', None)
        if not result:
            raise HTTPException(401, 'Authentication required')
        return {key: result[key] for key in ('id', 'username', 'role')}

    def role(*roles):
        def dependency(current=Depends(user)):
            if current['role'] not in roles:
                raise HTTPException(403, 'Insufficient role')
            return current
        return dependency

    app.state.user_dependency = user
    app.state.role_dependency = role
    install_agent_tokens(app)
    install_users(app)
    install_oidc(app)

    @app.post('/api/auth/login')
    def login(data: Login, request: Request, response: Response):
        client = request.client.host if request.client else 'local'
        username = data.username.strip().lower()
        wait = app.state.login_throttle.retry_after(client, username)
        if wait:
            store.audit('anonymous', 'auth.login_throttled')
            raise HTTPException(429, 'Too many login attempts; retry later',
                                headers={'Retry-After': str(max(1, math.ceil(wait)))})
        account = app.state.users.authenticate(data.username, data.password)
        if not account:
            app.state.login_throttle.failure(client, username)
            store.audit('anonymous', 'auth.login_failed', detail={'username': username})
            raise HTTPException(401, 'Invalid credentials')
        app.state.login_throttle.success(client, username)
        app.state.sessions.prune()
        # A cookie presented at login is replaced, never promoted: this closes
        # session fixation and leaves one live session per browser.
        app.state.sessions.revoke(request.cookies.get(SESSION_COOKIE))
        token, csrf = app.state.sessions.create(account)
        app.state.sessions.set_cookie(response, token, secure=request.url.scheme == 'https')
        current = {key: account[key] for key in ('id', 'username', 'role')}
        store.audit(account['username'], 'auth.login')
        return {'user': current, 'csrf_token': csrf, 'expires_in': app.state.sessions.absolute_timeout}

    @app.get('/api/auth/me')
    def me(request: Request, current=Depends(user)):
        if current.get('principal_type') == 'agent':
            return {'user': current, 'csrf_token': None}
        session = getattr(request.state, 'session', None) or app.state.sessions.resolve(request.cookies.get(SESSION_COOKIE))
        if not session:
            raise HTTPException(401, 'Authentication required')
        return {'user': current, 'csrf_token': session['csrf']}

    @app.post('/api/auth/logout')
    def logout(request: Request, response: Response, current=Depends(user)):
        app.state.sessions.revoke(request.cookies.get(SESSION_COOKIE))
        app.state.sessions.clear_cookie(response, secure=request.url.scheme == 'https')
        store.audit(current['username'], 'auth.logout')
        return {'ok': True}

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'service': 'kuberweaver'}

    from .api import install_api
    install_api(app, Path(plugins_dir or configured_plugins_dir()), executor=executor)
    from .health import install_health
    install_health(app)
    from fastapi.responses import FileResponse
    frontend = Path(frontend_dir or configured_frontend_dir()).resolve()

    @app.get('/{asset_path:path}', include_in_schema=False)
    def frontend_asset(asset_path: str):
        if asset_path == 'api' or asset_path.startswith('api/') or asset_path in {'healthz', 'readyz', 'metrics'}:
            raise HTTPException(404, 'Unknown API endpoint')
        target = (frontend / asset_path).resolve()
        if not target.is_relative_to(frontend) or any(part.startswith('.') for part in Path(asset_path).parts):
            raise HTTPException(404, 'Asset not found')
        if target.is_file():
            return FileResponse(target)
        index = frontend / 'index.html'
        if index.is_file() and '.' not in Path(asset_path).name:
            return FileResponse(index)
        raise HTTPException(404, 'Frontend not built; run npm run build inside frontend/')
    return app


app = create_app()
