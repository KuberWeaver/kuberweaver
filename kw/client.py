"""Thin HTTP client over the KuberWeaver API.

One door: the same routes, roles and pins as the GUI. Human auth is a session
cookie plus X-CSRF-Token; automation is a scoped agent token. The CLI never
writes to the database and never bypasses approval.
"""
from __future__ import annotations
import httpx

from kw import config

# Test hook: when set, requests are routed in-process (httpx.ASGITransport).
_TEST_TRANSPORT = None

SESSION_COOKIE = "kw_session"


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


def set_test_transport(transport) -> None:
    global _TEST_TRANSPORT
    _TEST_TRANSPORT = transport


class Client:
    def __init__(self, base_url: str, *, use_session: bool = True):
        self.base_url = base_url
        self._http = httpx.Client(base_url=base_url, timeout=30, trust_env=False,
                                  transport=_TEST_TRANSPORT)
        self.agent = False
        token = config.agent_token()
        if token:
            self._http.headers["Authorization"] = f"Bearer {token}"
            self.agent = True
        elif use_session:
            session = config.load_session()
            if session.get("url") == base_url and session.get("cookie"):
                self._http.cookies.set(SESSION_COOKIE, session["cookie"],
                                       domain=httpx.URL(base_url).host)
                if session.get("csrf"):
                    self._http.headers["X-CSRF-Token"] = session["csrf"]

    def close(self):
        self._http.close()

    def request(self, method: str, path: str, **kw):
        response = self._http.request(method, path, **kw)
        if response.status_code >= 400:
            detail = _detail(response)
            raise ApiError(response.status_code, detail)
        return response

    def json(self, method: str, path: str, **kw):
        response = self.request(method, path, **kw)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get(self, path, **kw):
        return self.json("GET", path, **kw)

    def post(self, path, **kw):
        return self.json("POST", path, **kw)

    def patch(self, path, **kw):
        return self.json("PATCH", path, **kw)

    def put(self, path, **kw):
        return self.json("PUT", path, **kw)

    def delete(self, path, **kw):
        return self.json("DELETE", path, **kw)

    def login(self, username: str, password: str) -> dict:
        response = self._http.request("POST", "/api/auth/login",
                                      json={"username": username, "password": password})
        if response.status_code >= 400:
            raise ApiError(response.status_code, _detail(response))
        data = response.json()
        cookie = response.cookies.get(SESSION_COOKIE)
        config.save_session({
            "url": self.base_url,
            "username": data["user"]["username"],
            "role": data["user"]["role"],
            "cookie": cookie,
            "csrf": data["csrf_token"],
            "expires_in": data.get("expires_in"),
        })
        self._http.headers["X-CSRF-Token"] = data["csrf_token"]
        return data


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and "detail" in body:
            detail = body["detail"]
            return detail if isinstance(detail, str) else str(detail)
        return str(body)
    except Exception:
        return response.text or response.reason_phrase


def build_client(url_option: str | None = None, *, use_session: bool = True) -> Client:
    return Client(config.resolve_url(url_option), use_session=use_session)
