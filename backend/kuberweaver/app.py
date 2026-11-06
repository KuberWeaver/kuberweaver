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
        try:
            app.state.worker.start()
            yield
        finally:
            app.state.worker.close()
            if lock is not None:
                lock.close()

    app = FastAPI(title='KuberWeaver', version='0.1.0', lifespan=lifespan)
    app.state.store = store
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
            if request.url.path.startswith('/api/') and request.url.path != '/api/auth/login':
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
        return {'user': current, 'csrf_token': store.session(request.cookies.get('kw_session'))['csrf']}

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
