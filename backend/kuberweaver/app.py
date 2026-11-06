"""KuberWeaver local-only microkernel. Provider hooks never replace security gates."""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import hmac
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .storage import Store, check_password
from .agent_auth import AgentTokens, install_agent_tokens, required_scope, permits

ROOT = Path(__file__).resolve().parents[2]


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=512)


def create_app(state_dir: Path | str | None = None, testing: bool = False, *,
               plugins_dir: Path | str | None = None, frontend_dir: Path | str | None = None,
               executor=None):
    state_dir = Path(state_dir or os.environ.get('KUBERWEAVER_STATE_DIR', ROOT / 'data'))
    store = Store(state_dir)
    agent_tokens = AgentTokens(store)

    @asynccontextmanager
    async def lifespan(app):
        import fcntl
        lock = None
        if not testing:
            lock = open(state_dir / '.server.lock', 'a')
            os.chmod(state_dir / '.server.lock', 0o600)
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                raise RuntimeError('KuberWeaver is already running for this state directory; use one server worker')
        started = []
        worker_started = False
        try:
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
                    if lock is not None:
                        lock.close()

    app = FastAPI(title='KuberWeaver', version='0.1.0', lifespan=lifespan)
    app.state.store = store
    app.state.agent_tokens = agent_tokens
    app.state.background_services = []
    app.state.state_dir = state_dir
    app.state.testing = testing
    app.state.request_count = 0
    app.state.login_attempts = {}

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
        authorization = request.headers.getlist('authorization')
        if authorization:
            if len(authorization) != 1 or request.cookies.get('kw_session'):
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
                session = store.session(request.cookies.get('kw_session'))
                if not session:
                    return JSONResponse({'detail': 'Authentication required'}, status_code=401)
                if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), session['csrf']):
                    return JSONResponse({'detail': 'CSRF token required'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def user(request: Request):
        if getattr(request.state, 'agent', None):
            return request.state.agent
        result = store.session(request.cookies.get('kw_session'))
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

    @app.post('/api/auth/login')
    def login(data: Login, request: Request, response: Response):
        key = request.client.host if request.client else 'local'
        timestamp = time.monotonic()
        attempts = app.state.login_attempts.setdefault(key, [])
        attempts[:] = [t for t in attempts if timestamp - t < 60]
        if len(attempts) >= 20:
            raise HTTPException(429, 'Too many login attempts; retry in one minute')
        account = store.one('SELECT * FROM users WHERE username=?', (data.username,))
        if not check_password(data.password, account['password_hash'] if account else store.dummy_hash):
            attempts.append(timestamp)
            store.audit('anonymous', 'auth.login_failed')
            raise HTTPException(401, 'Invalid credentials')
        token, csrf = store.create_session(account)
        response.set_cookie('kw_session', token, httponly=True, samesite='strict', secure=request.url.scheme == 'https', max_age=8 * 3600, path='/')
        current = {key: account[key] for key in ('id', 'username', 'role')}
        store.audit(account['username'], 'auth.login')
        return {'user': current, 'csrf_token': csrf}

    @app.get('/api/auth/me')
    def me(request: Request, current=Depends(user)):
        return {'user': current, 'csrf_token': None if current.get('principal_type') == 'agent' else store.session(request.cookies.get('kw_session'))['csrf']}

    @app.post('/api/auth/logout')
    def logout(request: Request, response: Response, current=Depends(user)):
        token = request.cookies.get('kw_session', '')
        store.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
        response.delete_cookie('kw_session', path='/')
        store.audit(current['username'], 'auth.logout')
        return {'ok': True}

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'service': 'kuberweaver'}

    from .api import install_api
    install_api(app, Path(plugins_dir or ROOT / 'plugins'), executor=executor)
    from fastapi.responses import FileResponse
    frontend = Path(frontend_dir or ROOT / 'frontend' / 'dist').resolve()

    @app.get('/{asset_path:path}', include_in_schema=False)
    def frontend_asset(asset_path: str):
        if asset_path == 'api' or asset_path.startswith('api/') or asset_path in {'healthz', 'metrics'}:
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
