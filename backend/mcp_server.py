"""External-agent adapter. The HTTP API remains the authorization boundary."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit


def read_token(path: Path) -> str:
    """Open an owned private regular file without following a symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'r') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError('Token file must be private and owned by this account')
            token = stream.read(4097).strip()
            if not token or len(token) > 4096 or not token.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError('Invalid private token file')
            return token
    except (OSError, UnicodeError):
        raise ValueError('Cannot read private token file') from None


@dataclass(frozen=True)
class ServerConfig:
    base_url: str
    token_file: Path
    allow_remote_https: bool = False

    def __post_init__(self):
        try:
            parsed = urlsplit(self.base_url)
            if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or
                    parsed.username is not None or parsed.password is not None or
                    parsed.path not in {'', '/'} or parsed.query or parsed.fragment or
                    '?' in self.base_url or '#' in self.base_url or
                    any(c.isspace() or ord(c) < 32 for c in self.base_url)):
                raise ValueError()
            parsed.port
            try:
                loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = False
            if parsed.scheme == 'http' and not loopback:
                raise ValueError()
            if not loopback and not self.allow_remote_https:
                raise ValueError()
        except ValueError:
            raise ValueError('Expected numeric loopback HTTP(S) origin, or explicitly allowed remote HTTPS origin') from None
        read_token(Path(self.token_file))


import uuid
import asyncio
import httpx
import json
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, JsonValue, Field
from typing import Annotated, Literal
from .models import Graph, Policies, StrictModel
from .connections import ConnectionSpec, ConnectionPatch
from .runtime import ProviderKind, ProviderName

Identity = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
ExtensionID = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')]
BlueprintName = Annotated[str, Field(min_length=1, max_length=160)]


class SettingsUpdate(StrictModel):
    policies: Policies | None = None


class DomainResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: JsonValue
    request_id: str


REQUEST_DEADLINE = 25


class APITransport:
    def __init__(self, config: ServerConfig, transport=None):
        self.config, self.transport = config, transport

    async def request(self, method: str, path: str, body=None) -> DomainResult:
        try:
            async with asyncio.timeout(REQUEST_DEADLINE):
                return await self._request(method, path, body)
        except TimeoutError:
            raise ToolError('Upstream request deadline exceeded; mutation outcome may be unknown, inspect state before retry') from None

    async def _request(self, method: str, path: str, body=None) -> DomainResult:
        request_id = uuid.uuid4().hex
        try:
            token = read_token(Path(self.config.token_file))
            async with httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=httpx.Timeout(20, connect=5),
                transport=self.transport, limits=httpx.Limits(max_connections=4),
            ) as client:
                async with client.stream(method, self.config.base_url.rstrip('/') + path,
                    headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                             'X-Request-ID': request_id, 'X-KuberWeaver-Client': 'mcp-stdio'},
                    json=body) as response:
                    if response.status_code >= 300:
                        labels = {401: 'Authentication required', 403: 'Permission denied',
                                  404: 'Capability or resource unavailable', 409: 'Platform conflict',
                                  422: 'Platform validation rejected', 429: 'Platform rate limit'}
                        raise ToolError(f'{labels.get(response.status_code, "Upstream request failed")} (HTTP {response.status_code}; request {request_id})')
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 1_048_576:
                            raise ToolError('Upstream response exceeds size limit')
                        chunks.append(chunk)
                    data = json.loads(b''.join(chunks))
                    return DomainResult(data=data, request_id=request_id)
        except ToolError:
            raise
        except Exception:
            raise ToolError(f'Upstream unavailable or invalid response (request {request_id})') from None


class ClosedFastMCP(FastMCP):
    """SDK public overrides close schemas and validate before SDK coercion."""
    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema['additionalProperties'] = False
        return tools

    async def call_tool(self, name, arguments):
        from jsonschema import Draft202012Validator
        tool = next((tool for tool in await self.list_tools() if tool.name == name), None)
        if tool is None:
            raise ToolError('Unknown domain tool')
        if not Draft202012Validator(tool.inputSchema).is_valid(arguments):
            raise ToolError('Invalid domain tool parameters')
        try:
            return await super().call_tool(name, arguments)
        except ToolError:
            raise
        except Exception:
            raise ToolError('Domain tool failed') from None


def create_server(config: ServerConfig, *, transport=None) -> FastMCP:
    server = ClosedFastMCP('KuberWeaver', instructions='Scoped external control of KuberWeaver. The API enforces roles, admission, approvals, budgets and execution provenance.')
    api = APITransport(config, transport)

    def register(fn, *, read=False, dangerous=True):
        server.add_tool(fn, annotations=ToolAnnotations(
            readOnlyHint=read, destructiveHint=not read and dangerous,
            idempotentHint=read, openWorldHint=not read),
            meta={'kuberweaver/access': 'read' if read else 'write',
                  'kuberweaver/dangerous': not read and dangerous}, structured_output=True)

    def read_tool(name, path, description):
        async def read() -> DomainResult:
            return await api.request('GET', path)
        read.__name__, read.__doc__ = name, description
        register(read, read=True)

    for name, path, description in (
        ('list_plugins', '/api/plugins', 'Read plugin catalogue and configuration schemas.'),
        ('get_overview', '/api/overview', 'Read platform overview and latest jobs.'),
        ('get_topology', '/api/infrastructure', 'Read inventory topology through the selected provider.'),
        ('get_settings', '/api/settings', 'Read policies and integration summaries.'),
        ('get_finops', '/api/finops', 'Read declared-resource cost estimates, not metered billing.'),
        ('get_diagnostics_cache', '/api/diagnostics', 'Read diagnostic provider cache; never request inference.'),
        ('list_audit', '/api/audit', 'Read the most recent platform audit events.'),
        ('list_integrations', '/api/integrations', 'Read integration availability, without credentials.'),
        ('list_providers', '/api/providers', 'Read provider descriptors and selected bindings, never resolve secrets.'),
        ('list_extensions', '/api/extensions', 'Read installed trusted-extension descriptors.'),
        ('get_operations', '/api/operations', 'Read administrative optional capability status.'),
        ('get_notification_deliveries', '/api/operations/notifications', 'Read safe administrative notification delivery status; never payloads, URLs or credentials.'),
        ('list_jobs', '/api/jobs', 'Read jobs and their status, events and approval metadata.'),
        ('list_blueprints', '/api/blueprints', 'Read saved blueprints.'),
        ('list_connection_drivers', '/api/connection-drivers', 'Read registered infrastructure drivers and reference-only credential-source capabilities.'),
        ('list_connections', '/api/connections', 'Read saved hypervisor connections and dated observations; never run remote tests.'),
    ):
        read_tool(name, path, description)

    async def get_job(identity: Identity) -> DomainResult:
        """Read a job including bounded stored events; no unbounded SSE stream."""
        return await api.request('GET', f'/api/jobs/{identity}')

    async def get_blueprint(identity: Identity) -> DomainResult:
        """Read a saved blueprint by its platform identifier."""
        return await api.request('GET', f'/api/blueprints/{identity}')

    async def get_connection(identity: Identity) -> DomainResult:
        """Read one configured connection and its cached observations, never credential values."""
        return await api.request('GET', f'/api/connections/{identity}')

    async def discover_connection(identity: Identity) -> DomainResult:
        """Explicit read-only remote inventory through a configured connection; updates its dated observation."""
        return await api.request('POST', f'/api/connections/{identity}/discover')

    async def create_connection(connection: ConnectionSpec) -> DomainResult:
        """Configure a connection using approved secret references, never inline credentials; administrator authority required."""
        return await api.request('POST', '/api/connections', connection.model_dump())

    async def update_connection(identity: Identity, patch: ConnectionPatch) -> DomainResult:
        """Patch an administrator-configured connection at its exact revision, without credential values."""
        return await api.request('PATCH', f'/api/connections/{identity}', patch.model_dump(exclude_unset=True))

    async def validate_plan(graph: Graph) -> DomainResult:
        """Validate admission, plugin schema, policy and estimated cost; no execution."""
        return await api.request('POST', '/api/plans/validate', graph.model_dump())

    async def save_blueprint(name: BlueprintName, graph: Graph) -> DomainResult:
        """Persist a blueprint only after platform admission checks."""
        return await api.request('POST', '/api/blueprints', {'name': name, 'graph': graph.model_dump()})

    async def submit_job(graph: Graph) -> DomainResult:
        """Submit through normal admission, approval and execution controls. May execute infrastructure changes."""
        return await api.request('POST', '/api/jobs', {'graph': graph.model_dump()})

    async def approve_job(identity: Identity) -> DomainResult:
        """Approve a pending job with this token's authority; may execute infrastructure changes."""
        return await api.request('POST', f'/api/jobs/{identity}/approve')

    async def reject_job(identity: Identity) -> DomainResult:
        """Reject a pending job using platform role and scope checks."""
        return await api.request('POST', f'/api/jobs/{identity}/reject')

    async def rollback_job(identity: Identity) -> DomainResult:
        """Request controlled rollback with platform provenance and snapshot checks."""
        return await api.request('POST', f'/api/jobs/{identity}/rollback')

    async def update_settings(settings: SettingsUpdate) -> DomainResult:
        """Patch explicitly supplied policy fields; administrator authority required."""
        return await api.request('PATCH', '/api/settings', settings.model_dump(exclude_unset=True, exclude_none=True))

    async def update_provider_bindings(bindings: Annotated[dict[ProviderKind, ProviderName], Field(min_length=1)]) -> DomainResult:
        """Select registered providers only; platform checks compatibility and idle queue."""
        return await api.request('PATCH', '/api/providers', {'bindings': bindings})

    async def enable_extension(identity: ExtensionID, trusted: Literal[True]) -> DomainResult:
        """Explicitly trust and enable an administrator-installed extension (executes trusted code)."""
        return await api.request('POST', f'/api/extensions/{identity}/enable', {'trusted': trusted})

    async def disable_extension(identity: ExtensionID) -> DomainResult:
        """Disable an installed trusted extension through platform controls."""
        return await api.request('POST', f'/api/extensions/{identity}/disable')

    async def run_diagnostics() -> DomainResult:
        """Explicitly request diagnostics; selected provider may incur external inference cost. Budget/rate controls remain active."""
        return await api.request('POST', '/api/diagnostics/run')

    async def retry_notifications() -> DomainResult:
        """Explicitly retry pending notifications through the selected provider; may deliver external messages."""
        return await api.request('POST', '/api/operations/notifications/retry')

    async def issue_chatops_action(job_id: Identity, decision: Literal['approve', 'reject']) -> DomainResult:
        """Issue a bounded ChatOps action, if configured; not a signed callback or approval bypass."""
        return await api.request('POST', '/api/chatops/actions', {'job_id': job_id, 'decision': decision})

    for fn in (get_job, get_blueprint, get_connection):
        register(fn, read=True)
    register(discover_connection, dangerous=False)
    register(validate_plan, dangerous=False)
    for fn in (save_blueprint, submit_job, approve_job, reject_job, rollback_job,
               update_settings, update_provider_bindings, enable_extension, disable_extension,
               run_diagnostics, retry_notifications, issue_chatops_action, create_connection, update_connection):
        register(fn)

    return server


def main(argv=None):
    """Administrator-only process configuration; token values are never CLI arguments."""
    import argparse
    parser = argparse.ArgumentParser(description='KuberWeaver MCP stdio adapter (no embedded model)')
    parser.add_argument('--base-url', default=os.environ.get('KUBERWEAVER_MCP_BASE_URL', 'http://127.0.0.1:8000'))
    parser.add_argument('--token-file', type=Path, default=os.environ.get('KUBERWEAVER_MCP_TOKEN_FILE'))
    parser.add_argument('--allow-remote-https', action='store_true', help='Explicitly permit an administrator-configured remote HTTPS origin')
    args = parser.parse_args(argv)
    if args.token_file is None:
        parser.error('--token-file or KUBERWEAVER_MCP_TOKEN_FILE is required')
    try:
        config = ServerConfig(args.base_url, args.token_file, args.allow_remote_https)
    except ValueError:
        parser.error('Invalid API origin or private token file')
    create_server(config).run(transport='stdio')


if __name__ == '__main__':
    main()
