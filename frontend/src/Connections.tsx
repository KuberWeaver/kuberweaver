import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { Server, RefreshCw } from "lucide-react";
import type { User } from "./model";
import { api } from "./api";
import { Badge, Empty, ErrorNotice, PageTitle, Spinner, errorMessage, useResource } from "./ui";

type Driver = { id: string; label: string; available: boolean };
type SecretSource = { id: string; available: boolean; can_store: boolean; references: string[] };
type Registry = { drivers: Driver[]; secret_sources: SecretSource[] };
type NamedResource = { id: string; name: string };
type Inventory = {
  server: { name: string; version: string; api_type: string; write_api_supported: boolean | null };
  datastores: (NamedResource & { capacity_bytes: number; free_bytes: number; accessible: boolean })[];
  networks: NamedResource[];
  resource_pools: NamedResource[];
  virtual_machines: (NamedResource & { uuid: string | null; power_state: string; cpu: number | null; memory_mb: number | null })[];
};
type Observation = {
  connection_id: string;
  revision: number;
  status: "reachable" | "unavailable";
  observed_at: string | number;
  inventory: Inventory | null;
  error: string | null;
};
type Connection = {
  id: string;
  label: string;
  driver: string;
  endpoint: string;
  secret_ref: string;
  secrets_provider: string;
  tls_sha256: string | null;
  enabled: boolean;
  revision: number;
  observation: Observation | null;
};
const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const connectionId = (value: unknown): value is string => typeof value === "string" && /^[a-f\d]{32}$/.test(value);
const logicalReference = (value: unknown): value is string => typeof value === "string" && value.length <= 128 && /^[a-z][a-z\d_-]*(?:\/[a-z][a-z\d_-]*)*$/.test(value);
const tlsFingerprint = (value: unknown): value is string | null => value === null || (typeof value === "string" && /^[a-f\d]{64}$/.test(value));
const nonnegative = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const revision = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 1;
function collection<T extends { id: string }>(value: unknown, valid: (row: unknown) => row is T): value is T[] {
  return Array.isArray(value) && value.every(valid) && new Set(value.map((row) => row.id)).size === value.length;
}
function isRegistry(value: unknown): value is Registry {
  return isRecord(value)
    && collection(value.drivers, (row): row is Driver => isRecord(row) && text(row.id) && text(row.label) && typeof row.available === "boolean")
    && collection(value.secret_sources, (row): row is SecretSource => isRecord(row) && text(row.id)
      && typeof row.available === "boolean" && typeof row.can_store === "boolean"
      && Array.isArray(row.references) && row.references.every(text));
}
function isNamed(value: unknown): value is NamedResource {
  return isRecord(value) && text(value.id) && text(value.name);
}
function isInventory(value: unknown): value is Inventory {
  return isRecord(value) && isRecord(value.server) && text(value.server.name)
    && typeof value.server.version === "string" && typeof value.server.api_type === "string"
    && (value.server.write_api_supported === null || typeof value.server.write_api_supported === "boolean")
    && collection(value.datastores, (row): row is Inventory["datastores"][number] => isRecord(row)
      && nonnegative(row.capacity_bytes) && nonnegative(row.free_bytes) && typeof row.accessible === "boolean" && isNamed(row))
    && collection(value.networks, isNamed) && collection(value.resource_pools, isNamed)
    && collection(value.virtual_machines, (row): row is Inventory["virtual_machines"][number] => isRecord(row)
      && (row.uuid === null || text(row.uuid)) && text(row.power_state)
      && (row.cpu === null || nonnegative(row.cpu)) && (row.memory_mb === null || nonnegative(row.memory_mb)) && isNamed(row));
}
function validTimestamp(value: unknown): value is string | number {
  return (text(value) && Number.isFinite(Date.parse(value)))
    || (nonnegative(value) && Number.isFinite(new Date(value * 1000).getTime()));
}
function isObservation(value: unknown): value is Observation {
  return isRecord(value) && connectionId(value.connection_id) && revision(value.revision)
    && validTimestamp(value.observed_at)
    && (value.status === "reachable"
      ? isInventory(value.inventory) && value.error === null
      : value.status === "unavailable" && value.inventory === null && text(value.error));
}
function validEndpoint(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 256 || !/^https:\/\/(?:\[[a-fA-F\d:.]+\]|(?:0|[1-9]\d{0,2})(?:\.(?:0|[1-9]\d{0,2})){3})(?::[1-9]\d{0,4})?$/.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.search && !url.hash
      && url.pathname === "/" && (url.hostname.startsWith("[") || url.hostname.split(".").every((part) => Number(part) <= 255));
  } catch { return false; }
}
function isConnection(value: unknown): value is Connection {
  return isRecord(value) && connectionId(value.id) && text(value.label) && text(value.driver)
    && validEndpoint(value.endpoint) && logicalReference(value.secret_ref) && text(value.secrets_provider)
    && tlsFingerprint(value.tls_sha256)
    && typeof value.enabled === "boolean" && revision(value.revision)
    && (value.observation === null || (isObservation(value.observation)
      && value.observation.connection_id === value.id && value.observation.revision <= value.revision));
}

function ObservedConnection({ connection }: { connection: Connection }) {
  const observation = connection.observation;
  if (!observation) return <p className="muted">No observations recorded. Access has not been verified.</p>;
  const observedDate = new Date(typeof observation.observed_at === "number" ? observation.observed_at * 1000 : observation.observed_at);
  const inventory = observation.inventory;
  return <>
    <div className="panel-title"><h3>Last recorded result</h3><Badge tone={observation.status === "reachable" ? "success" : "warning"}>{observation.status === "reachable" ? "Accessible" : "Unavailable"}</Badge></div>
    <p>Observed at <time dateTime={typeof observation.observed_at === "string" ? observation.observed_at : observedDate.toISOString()}>{observedDate.toLocaleString("en-GB")}</time></p>
    <p className="muted">Observed revision: {observation.revision} · Configuration revision: {connection.revision}</p>
    <p className="muted">Point-in-time snapshot, without automatic monitoring. The timestamp does not guarantee current access.</p>
    {observation.revision !== connection.revision && <p className="notice">Stale observation: configuration has changed since this observation.</p>}
    {observation.error && <ErrorNotice message={observation.error} />}
    {!inventory ? <p className="muted">No inventory returned by the server.</p> : <>
      <dl className="integration-details">
        <div><dt>Observed server</dt><dd>{inventory.server.name}</dd></div>
        <div><dt>Version</dt><dd>{inventory.server.version || "Not specified"}</dd></div>
        <div><dt>API type</dt><dd>{inventory.server.api_type || "Not specified"}</dd></div>
      </dl>
      <p>API writes: {inventory.server.write_api_supported === null ? "unknown capability" : inventory.server.write_api_supported ? "declared supported" : "not supported"}</p>
      <h3>Datastores</h3><div className="table-wrap"><table aria-label="Datastores">
        <thead><tr><th>ID</th><th>Name</th><th>Capacity (bytes)</th><th>Free (bytes)</th><th>Accessibility</th></tr></thead>
        <tbody>{inventory.datastores.map((row) => <tr key={row.id}><td>{row.id}</td><td>{row.name}</td><td>{row.capacity_bytes}</td><td>{row.free_bytes}</td><td>{row.accessible ? "Yes" : "No"}</td></tr>)}{!inventory.datastores.length && <tr><td colSpan={5}>No datastores observed.</td></tr>}</tbody>
      </table></div>
      <h3>Networks</h3><div className="table-wrap"><table aria-label="Networks">
        <thead><tr><th>ID</th><th>Name</th></tr></thead><tbody>{inventory.networks.map((row) => <tr key={row.id}><td>{row.id}</td><td>{row.name}</td></tr>)}{!inventory.networks.length && <tr><td colSpan={2}>No networks observed.</td></tr>}</tbody>
      </table></div>
      <h3>Resource pools</h3><div className="table-wrap"><table aria-label="Resource pools">
        <thead><tr><th>ID</th><th>Name</th></tr></thead><tbody>{inventory.resource_pools.map((row) => <tr key={row.id}><td>{row.id}</td><td>{row.name}</td></tr>)}{!inventory.resource_pools.length && <tr><td colSpan={2}>No pools observed.</td></tr>}</tbody>
      </table></div>
      <h3>Virtual machines</h3><p className="muted">Inventory does not establish guest operating system installation or health.</p>
      <div className="table-wrap"><table aria-label="Virtual machines">
        <thead><tr><th>ID</th><th>Name</th><th>UUID</th><th>Power</th><th>CPU</th><th>Memory (MB)</th></tr></thead>
        <tbody>{inventory.virtual_machines.map((row) => <tr key={row.id}><td>{row.id}</td><td>{row.name}</td><td>{row.uuid ?? "Not specified"}</td><td>{row.power_state}</td><td>{row.cpu ?? "Not specified"}</td><td>{row.memory_mb ?? "Not specified"}</td></tr>)}{!inventory.virtual_machines.length && <tr><td colSpan={6}>No virtual machines observed.</td></tr>}</tbody>
      </table></div>
    </>}
  </>;
}

type Spec = Pick<Connection, "label" | "driver" | "endpoint" | "secret_ref" | "secrets_provider" | "tls_sha256" | "enabled">;
function connectionSpec(value: Spec): Spec {
  return { label: value.label, driver: value.driver, endpoint: value.endpoint, secret_ref: value.secret_ref,
    secrets_provider: value.secrets_provider, tls_sha256: value.tls_sha256, enabled: value.enabled };
}
function sameSpec(left: Spec, right: Spec) {
  return JSON.stringify(connectionSpec(left)) === JSON.stringify(connectionSpec(right));
}
function sameValue(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((value, index) => sameValue(value, right[index]));
  if (!isRecord(left) || !isRecord(right)) return false;
  return Object.keys(left).length === Object.keys(right).length
    && Object.keys(left).every((key) => Object.hasOwn(right, key) && sameValue(left[key], right[key]));
}
function validDraft(value: Spec, registry: Registry) {
  return text(value.label) && value.label.length <= 80 && validEndpoint(value.endpoint) && logicalReference(value.secret_ref)
    && tlsFingerprint(value.tls_sha256)
    && registry.drivers.some((driver) => driver.id === value.driver && driver.available)
    && registry.secret_sources.some((source) => source.id === value.secrets_provider && source.available);
}
async function readConnection(id: string): Promise<Connection> {
  const result = await api<unknown>(`/connections/${id}`);
  if (!isConnection(result) || result.id !== id) throw new Error("Connection readback: unsupported server response or mismatched target.");
  return result;
}
function ConnectionForm({ registry, initial, busy, onSave, onCancel }: {
  registry: Registry; initial?: Connection; busy: boolean; onSave: (value: Spec) => void; onCancel: () => void;
}) {
  const [draft, setDraft] = useState<Spec>(() => initial ? connectionSpec(initial) : { label: "", driver: "", endpoint: "", secret_ref: "", secrets_provider: "", tls_sha256: null, enabled: true });
  const source = registry.secret_sources.find((item) => item.id === draft.secrets_provider);
  const valid = validDraft(draft, registry) && (!initial || !sameSpec(draft, initial));
  const invalid = draft.endpoint && !validEndpoint(draft.endpoint) ? "Invalid endpoint: use an https:// origin with an IP address, without a path or credentials."
    : draft.secret_ref && !logicalReference(draft.secret_ref) ? "Invalid logical reference: lowercase segments separated by /, using letters, digits, hyphens and underscores only."
      : !tlsFingerprint(draft.tls_sha256) ? "Invalid TLS fingerprint: 64 lowercase hexadecimal characters without separators." : "";
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busy && valid) onSave(connectionSpec(draft));
  }
  return <section className="panel settings-panel">
    <h2>{initial ? "Edit connection reference" : "New connection"}</h2>
    <p className="muted">Saving configuration does not test access. Use a logical secret name, never its value.</p>
    <form aria-label="Connection configuration" onSubmit={submit}>
      <fieldset disabled={busy}>
        <legend>Connection reference</legend>
        <div className="setting-row"><label htmlFor="connection-label">Label</label><input id="connection-label" required maxLength={80} value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} /></div>
        <div className="setting-row"><label htmlFor="connection-driver">Connection driver</label><select id="connection-driver" required value={draft.driver} onChange={(e) => setDraft({ ...draft, driver: e.target.value })}>
          <option value="">Choose a driver</option>{draft.driver && !registry.drivers.some((item) => item.id === draft.driver) && <option value={draft.driver} disabled>{draft.driver} · Unavailable</option>}{registry.drivers.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.label}{item.available ? "" : " · Unavailable"}</option>)}
        </select></div>
        <div className="setting-row"><div><label htmlFor="connection-endpoint">HTTPS endpoint (IP address)</label><small>IPv4 or IPv6 HTTPS origin, optional port, without a path or credentials.</small></div><input id="connection-endpoint" required type="url" value={draft.endpoint} placeholder="https://192.0.2.10" onChange={(e) => setDraft({ ...draft, endpoint: e.target.value })} /></div>
        <div className="setting-row"><label htmlFor="connection-source">Secret source</label><select id="connection-source" required value={draft.secrets_provider} onChange={(e) => setDraft({ ...draft, secrets_provider: e.target.value, secret_ref: "" })}>
          <option value="">Choose a source</option>{draft.secrets_provider && !source && <option value={draft.secrets_provider} disabled>{draft.secrets_provider} · Unavailable</option>}{registry.secret_sources.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.id}{item.available ? "" : " · Unavailable"}</option>)}
        </select></div>
        {source && <p className="muted">Declared references: {source.references.join(", ") || "aucune"}. Actual secret availability must still be verified.</p>}
        <div className="setting-row"><label htmlFor="connection-secret-ref">Logical secret reference</label><div><input id="connection-secret-ref" required list="connection-secret-references" value={draft.secret_ref} onChange={(e) => setDraft({ ...draft, secret_ref: e.target.value })} /><datalist id="connection-secret-references">{source?.references.map((ref) => <option key={ref} value={ref} />)}</datalist></div></div>
        <div className="setting-row"><div><label htmlFor="connection-tls">TLS SHA-256 fingerprint (optional)</label><small>64 lowercase hexadecimal characters without separators.</small></div><input id="connection-tls" value={draft.tls_sha256 ?? ""} onChange={(e) => setDraft({ ...draft, tls_sha256: e.target.value === "" ? null : e.target.value })} /></div>
        <div className="setting-row"><label htmlFor="connection-enabled">Connection enabled</label><input id="connection-enabled" type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /></div>
      </fieldset>
      {invalid && <ErrorNotice message={invalid} />}
      <div className="heading-actions"><button className="primary" disabled={busy || !valid}>{initial ? "Save connection" : "Add connection"}</button><button type="button" disabled={busy} onClick={onCancel}>Cancel</button></div>
    </form>
  </section>;
}

function CredentialsForm({ busy, onStore, onClose }: {
  busy: boolean; onStore: (values: FormData) => void; onClose: () => void;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    // Keep the password in this ephemeral FormData/request, never in state or
    // browser storage. Clear the DOM before any asynchronous operation.
    form.reset();
    onStore(values);
    values.delete("password");
  }
  return <section className="panel settings-panel" aria-label="Credential storage">
    <h3>Store in secret source</h3>
    <p className="muted">Human administrator session only, enforced by the server. This panel never reads back or retains passwords. Storing credentials does not test hypervisor access.</p>
    <form autoComplete="off" onSubmit={submit}>
      <fieldset disabled={busy}><legend>Credentials for selected reference</legend>
        <div className="setting-row"><label htmlFor="hypervisor-username">Hypervisor username</label><input id="hypervisor-username" name="username" required autoComplete="off" /></div>
        <div className="setting-row"><label htmlFor="hypervisor-password">Hypervisor password</label><input id="hypervisor-password" name="password" type="password" required autoComplete="off" /></div>
      </fieldset>
      <div className="heading-actions"><button className="primary" disabled={busy}>Save credentials</button><button type="button" disabled={busy} onClick={onClose}>Close credential storage</button></div>
    </form>
  </section>;
}

export function Connections({ user }: { user: User }) {
  const resource = useResource<unknown>("/connections");
  const drivers = useResource<unknown>("/connection-drivers");
  const connections = collection(resource.data, isConnection) ? resource.data : null;
  const registry = isRegistry(drivers.data) ? drivers.data : null;
  const [selectedId, setSelectedId] = useState("");
  const [editor, setEditor] = useState<Connection | "new" | null>(null);
  const [credentialsFor, setCredentialsFor] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [unconfirmedId, setUnconfirmedId] = useState("");
  const selected = connections?.find((item) => item.id === selectedId) ?? connections?.[0];
  const selectedSource = registry?.secret_sources.find((item) => item.id === selected?.secrets_provider);
  const canStore = user.role === "admin" && selectedSource?.available === true && selectedSource.can_store;
  function accept(exact: Connection) {
    resource.setData([...(connections ?? []).filter((item) => item.id !== exact.id), exact]);
    setSelectedId(exact.id);
    setUnconfirmedId("");
  }
  async function save(spec: Spec, previous?: Connection) {
    const disabling = previous?.enabled && !spec.enabled && sameSpec(spec, { ...connectionSpec(previous), enabled: false });
    if (user.role !== "admin" || lock.current || !registry || (!validDraft(spec, registry) && !disabling)) return;
    const changes = previous ? Object.fromEntries(Object.entries(connectionSpec(spec)).filter(([key, value]) => value !== previous[key as keyof Spec])) : null;
    if (changes && !Object.keys(changes).length) return;
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const created = await api<unknown>(previous ? `/connections/${previous.id}` : "/connections", {
        method: previous ? "PATCH" : "POST", body: JSON.stringify(previous ? { ...changes, revision: previous.revision } : connectionSpec(spec)),
      });
      if (!isConnection(created) || !sameSpec(created, spec)
        || (previous && (created.id !== previous.id || created.revision <= previous.revision))
        || (!previous && connections?.some((item) => item.id === created.id))) throw new Error("Write: unsupported server response or mismatched configuration.");
      const exact = await readConnection(created.id);
      if (exact.revision !== created.revision || !sameSpec(exact, created)) throw new Error("Configuration was not confirmed by server readback.");
      accept(exact); setEditor(null); setNotice("Configuration saved and verified by readback.");
    } catch (error) {
      if (previous) setUnconfirmedId(previous.id);
      setError(`Write not confirmed. Refresh connections before trying again. ${errorMessage(error)}`);
    }
    finally { lock.current = false; setBusy(false); }
  }
  async function observe(target: Connection, action: "test" | "discover") {
    if (!["admin", "operator"].includes(user.role) || !target.enabled || lock.current) return;
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const result = await api<unknown>(`/connections/${target.id}/${action}`, { method: "POST" });
      if (!isObservation(result) || result.connection_id !== target.id || result.revision !== target.revision) throw new Error("Observation: unsupported server response or inconsistent result.");
      const exact = await readConnection(target.id);
      if (exact.revision !== target.revision || !sameSpec(exact, target) || !sameValue(exact.observation, result)) throw new Error("Observation was not confirmed by server readback.");
      accept(exact); setNotice("Observation saved and verified by readback.");
    } catch (error) { setUnconfirmedId(target.id); setError(errorMessage(error)); }
    finally { lock.current = false; setBusy(false); }
  }
  async function store(target: Connection, values: FormData) {
    const username = values.get("username");
    let password = values.get("password");
    values.delete("password");
    const source = registry?.secret_sources.find((item) => item.id === target.secrets_provider);
    if (user.role !== "admin" || !source?.available || !source.can_store || lock.current || !text(username) || typeof password !== "string" || !password.length) { password = null; return; }
    lock.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const pending = api<unknown>(`/connections/${target.id}/credentials`, { method: "PUT", body: JSON.stringify({ username, password }) });
      password = null;
      const result = await pending;
      if (!isRecord(result) || result.stored !== true || result.connection_id !== target.id) throw new Error("Invalid credential acknowledgement");
      const exact = await readConnection(target.id);
      if (exact.revision < target.revision || !sameSpec(exact, target)) throw new Error("Credential target changed during readback");
      accept(exact); setNotice("Vault reports credentials stored; connection read back, hypervisor access not tested.");
    } catch {
      // This endpoint might echo submitted values in an error. Never render
      // its body, exception message, or raw response in the interface.
      setUnconfirmedId(target.id); setError("Storage not confirmed. Check the secret source and your permissions before trying again. The password has been cleared.");
    } finally { password = null; lock.current = false; setBusy(false); }
  }
  return <>
    <PageTitle title="Hypervisor connections" description="Connection references and explicit observations of your hypervisors." actions={<>
      <button disabled={busy} onClick={() => { setCredentialsFor(""); setEditor(null); setError(""); setNotice(""); resource.refresh(); drivers.refresh(); }}><RefreshCw size={15} /> Refresh connections</button>
      {user.role === "admin" && connections && registry && !resource.loading && !drivers.loading && <button className="primary" disabled={busy} onClick={() => { setCredentialsFor(""); setEditor("new"); setError(""); setNotice(""); }}>New connection</button>}
    </>} />
    <p className="notice">A vault reference does not establish valid credentials. No automatic tests or discovery.</p>
    {error && <ErrorNotice message={error} />}
    {notice && <p className="notice" role="status">{notice}</p>}
    {resource.loading || drivers.loading ? <Spinner /> : resource.error || drivers.error || !connections || !registry ? <ErrorNotice message={resource.error || drivers.error || "Unsupported server response."} /> : <>
      {editor && user.role === "admin" && <ConnectionForm key={editor === "new" ? "new" : `${editor.id}:${editor.revision}`} registry={registry} initial={editor === "new" ? undefined : editor} busy={busy} onSave={(spec) => save(spec, editor === "new" ? undefined : editor)} onCancel={() => setEditor(null)} />}
      <section className="panel" aria-label="Saved connections">
        <div className="panel-title"><h2><Server size={18} /> Saved connections</h2></div>
        {!connections.length && <Empty title="No saved connections" icon={<Server size={27} />}>Add a connection reference, then test it explicitly.</Empty>}
        {connections.map((item) => <div className="setting-row" key={item.id}>
          <div><strong>{item.label}</strong><small>{item.driver} · Revision {item.revision}</small></div>
          <div><Badge tone={item.enabled ? "info" : "warning"}>{item.enabled ? "Enabled" : "Disabled"}</Badge> <button disabled={busy} onClick={() => { setCredentialsFor(""); setSelectedId(item.id); setEditor(null); setError(""); setNotice(""); }}>View {item.label}</button></div>
        </div>)}
      </section>
      {selected && <section className="panel settings-panel" aria-label="Connection details">
        <div className="panel-title"><h2>{selected.label}</h2><Badge>Revision {selected.revision}</Badge></div>
        <dl className="integration-details">
          <div><dt>HTTPS endpoint</dt><dd>{selected.endpoint}</dd></div>
          <div><dt>Driver</dt><dd>{selected.driver}</dd></div>
          <div><dt>Secret source</dt><dd>{selected.secrets_provider}</dd></div>
          <div><dt>Logical reference</dt><dd>{selected.secret_ref}</dd></div>
          <div><dt>TLS SHA-256 fingerprint</dt><dd>{selected.tls_sha256 ?? "Not specified"}</dd></div>
        </dl>
        {user.role === "admin" && <div className="heading-actions">
          <button disabled={busy} onClick={() => { setCredentialsFor(""); setEditor(selected); setError(""); setNotice(""); }}>Edit connection</button>
          <button disabled={busy || !selected.enabled} onClick={() => save({ ...connectionSpec(selected), enabled: false }, selected)}>Disable connection</button>
          {canStore && <button disabled={busy} onClick={() => { setEditor(null); setCredentialsFor(selected.id); setError(""); setNotice(""); }}>Store credentials</button>}
        </div>}
        {canStore && credentialsFor === selected.id && <CredentialsForm key={`${selected.id}:${selected.revision}`} busy={busy} onStore={(values) => store(selected, values)} onClose={() => setCredentialsFor("")} />}
        {["admin", "operator"].includes(user.role) && <div className="heading-actions">
          <button disabled={busy || !selected.enabled} onClick={() => observe(selected, "test")}>Test connection</button>
          <button disabled={busy || !selected.enabled} onClick={() => observe(selected, "discover")}>Discover inventory</button>
        </div>}
        {unconfirmedId === selected.id && <p className="notice">Action result not confirmed. Showing the last validated read.</p>}
        <ObservedConnection connection={selected} />
      </section>}
    </>}
  </>;
}
