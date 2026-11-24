"""Read-only health observations for registered dashboards; never remediates.

A probe is one GET against the record origin and its probe path. It sends no
credentials, follows no redirect, reads at most 64 KiB, and records the outcome
next to the record. The registry itself is never edited: a failed observation is
evidence, not a reason to disable or rewrite the bookmark.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Callable, NamedTuple
from urllib.parse import urlsplit

INTERVAL_VARIABLE = 'KUBERWEAVER_DASHBOARD_PROBE_SECONDS'
TIMEOUT_VARIABLE = 'KUBERWEAVER_DASHBOARD_PROBE_TIMEOUT'
DEFAULT_INTERVAL = 300.0
DEFAULT_TIMEOUT = 5.0
MIN_TIMEOUT = 0.5
MAX_TIMEOUT = 30.0
MAX_INTERVAL = 86400.0
MAX_BODY = 64 * 1024
USER_AGENT = 'kuberweaver-dashboard-probe/1'

HEALTHY = 'healthy'
UNHEALTHY = 'unhealthy'
UNREACHABLE = 'unreachable'
MALFORMED = 'malformed'
UNKNOWN = 'unknown'
DISABLED = 'disabled'
PROBE_STATUSES = frozenset({HEALTHY, UNHEALTHY, UNREACHABLE, MALFORMED})
DRIFT_STATUSES = frozenset({UNHEALTHY, UNREACHABLE, MALFORMED})
JSON_KINDS = frozenset({'grafana', 'netdata'})
TEXT_KINDS = frozenset({'prometheus'})
HTML_KINDS = frozenset({'uptime-kuma'})


class ProbeResponse(NamedTuple):
    status: int
    headers: dict
    body: bytes


class ResponseTooLarge(ValueError):
    """The response exceeded the probe budget and was not read further."""


@dataclass(frozen=True)
class ProbeSettings:
    interval: float
    timeout: float
    error: str | None


def probe_settings():
    """Read the periodic-probe settings; an unusable value falls back and reports."""
    errors = []
    interval = DEFAULT_INTERVAL
    raw = os.environ.get(INTERVAL_VARIABLE)
    if raw not in (None, ''):
        try:
            interval = float(raw)
            if not 0 <= interval <= MAX_INTERVAL or 0 < interval < 1:
                raise ValueError
        except ValueError:
            interval = DEFAULT_INTERVAL
            errors.append(f'{INTERVAL_VARIABLE} must be 0 or a number of seconds '
                          f'between 1 and {MAX_INTERVAL:g}')
    timeout = DEFAULT_TIMEOUT
    raw = os.environ.get(TIMEOUT_VARIABLE)
    if raw not in (None, ''):
        try:
            timeout = float(raw)
            if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
                raise ValueError
        except ValueError:
            timeout = DEFAULT_TIMEOUT
            errors.append(f'{TIMEOUT_VARIABLE} must be between {MIN_TIMEOUT:g} '
                          f'and {MAX_TIMEOUT:g} seconds')
    return ProbeSettings(interval, timeout, '; '.join(errors) or None)


def fetch_once(url, timeout):
    """One GET on the registered origin; no redirect, cookie or credential."""
    parsed = urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError('Invalid probe URL')
    factory = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    connection = factory(parsed.hostname, parsed.port, timeout=timeout)
    path = parsed.path or '/'
    if parsed.query:
        path += '?' + parsed.query
    try:
        connection.request('GET', path, headers={'User-Agent': USER_AGENT,
                                                 'Accept': '*/*',
                                                 'Connection': 'close'})
        response = connection.getresponse()
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ResponseTooLarge('Response body exceeds the probe budget')
        headers = {name.lower(): value for name, value in response.getheaders()}
        return ProbeResponse(response.status, headers, body)
    finally:
        connection.close()


def _result(status, *, http_status=None, latency_ms=None, detail=None):
    return {'status': status, 'http_status': http_status,
            'latency_ms': latency_ms, 'detail': detail}


def _shape(kind, content_type, body):
    """Return the state and detail for a 2xx answer of the declared kind."""
    if kind in JSON_KINDS:
        if 'json' not in content_type:
            return MALFORMED, 'the endpoint did not return JSON'
        try:
            document = json.loads(body)
        except ValueError:
            return MALFORMED, 'the endpoint returned invalid JSON'
        if not isinstance(document, dict):
            return MALFORMED, 'the endpoint returned JSON that is not an object'
        if kind == 'grafana' and document.get('database') not in (None, 'ok'):
            return UNHEALTHY, 'the tool reports its database is not ready'
        return HEALTHY, None
    if kind in TEXT_KINDS:
        if 'text/plain' not in content_type:
            return MALFORMED, 'the endpoint did not return plain text'
        try:
            text = body.decode('utf-8')
        except UnicodeDecodeError:
            return MALFORMED, 'the endpoint returned text that is not UTF-8'
        if 'Healthy' not in text:
            return MALFORMED, 'the health document did not report the server healthy'
        return HEALTHY, None
    if kind in HTML_KINDS:
        if 'html' not in content_type:
            return MALFORMED, 'the endpoint did not return HTML'
        if not body.strip():
            return MALFORMED, 'the endpoint returned an empty document'
        return HEALTHY, None
    return HEALTHY, None


def classify(kind, response, latency_ms):
    """Turn one HTTP answer into a probe result; the response body is never echoed."""
    status, headers, body = response
    headers = {str(name).lower(): value for name, value in (headers or {}).items()}
    if 300 <= status < 400:
        return _result(MALFORMED, http_status=status, latency_ms=latency_ms,
                       detail='the endpoint answered a redirect, which the probe does not follow')
    if not 200 <= status < 300:
        return _result(UNHEALTHY, http_status=status, latency_ms=latency_ms,
                       detail=f'the endpoint answered HTTP {status}')
    content_type = (headers.get('content-type') or '').split(';', 1)[0].strip().lower()
    state, detail = _shape(kind, content_type, body)
    return _result(state, http_status=status, latency_ms=latency_ms, detail=detail)


def probe_dashboard(url, kind, *, timeout=DEFAULT_TIMEOUT, fetch=None):
    """Probe one endpoint; every transport failure becomes evidence, not an error."""
    fetch = fetch or fetch_once
    started = time.monotonic()
    try:
        response = fetch(url, timeout)
    except ResponseTooLarge:
        return _result(MALFORMED, detail=f'the response exceeded the {MAX_BODY // 1024} KiB budget')
    except (TimeoutError, socket.timeout):
        return _result(UNREACHABLE, detail=f'the endpoint did not answer within {timeout:g} seconds')
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
        return _result(UNREACHABLE, detail='the endpoint could not be reached')
    except Exception:
        return _result(UNREACHABLE, detail='the probe failed before an answer')
    latency = max(0, int((time.monotonic() - started) * 1000))
    try:
        return classify(kind, response, latency)
    except Exception:
        return _result(MALFORMED, http_status=getattr(response, 'status', None),
                       latency_ms=latency, detail='the response could not be read')


class DashboardProbeLoop:
    """One sweep per interval; a failed sweep never stops the observation loop."""

    def __init__(self, sweep: Callable[[], object], *, interval, initial_delay=None):
        self.sweep = sweep
        self.interval = float(interval)
        self.initial_delay = (min(5.0, self.interval) if initial_delay is None
                              else float(initial_delay))
        self._stop = threading.Event()
        self.thread = None

    def start(self):
        if self.interval <= 0:
            return
        if self.thread is not None and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name='kuberweaver-dashboard-probes',
                                       daemon=True)
        self.thread.start()

    def _run(self):
        if self._stop.wait(self.initial_delay):
            return
        while True:
            try:
                self.sweep()
            except Exception:
                # One unreachable integration must not end the observation loop.
                pass
            if self._stop.wait(self.interval):
                return

    def close(self):
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
