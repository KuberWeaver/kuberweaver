import { useState } from "react";
import type { User } from "./model";
import { api } from "./api";
import { Badge, ErrorNotice, Spinner, errorMessage, useResource } from "./ui";

type Metadata = { id: string; label: string; role: string; scopes: string[]; expires_at: string | number; revoked_at: string | number | null };
type Registry = { tokens: Metadata[]; available_scopes: string[]; role_scopes: Record<string, string[]> };
function validTimestamp(value: unknown) {
  return typeof value === "string" || (typeof value === "number" && Number.isFinite(value) && Number.isFinite(new Date(value * 1000).getTime()));
}
function formatExpiry(value: string | number) { return typeof value === "number" ? new Date(value * 1000).toISOString() : value; }
function isRegistry(value: unknown): value is Registry {
  if (!value || typeof value !== "object") return false;
  const r = value as Registry;
  return Array.isArray(r.tokens) && r.tokens.every((t) => t && typeof t.id === "string" && typeof t.label === "string" && typeof t.role === "string" && Array.isArray(t.scopes) && t.scopes.every((s) => typeof s === "string") && validTimestamp(t.expires_at) && (t.revoked_at === null || validTimestamp(t.revoked_at)))
    && Array.isArray(r.available_scopes) && r.available_scopes.every((s) => typeof s === "string")
    && !!r.role_scopes && ["viewer", "operator", "admin"].every((role) => Array.isArray(r.role_scopes[role]) && r.role_scopes[role].every((s) => typeof s === "string"));
}
export function AgentAccess({ user }: { user: User }) {
  return user.role === "admin" ? <Administration /> : <section className="panel"><h1>Agent access</h1><p>Administrators only: create and revoke API and MCP access.</p></section>;
}
function Administration() {
  const resource = useResource<unknown>("/agent-tokens");
  const registry = isRegistry(resource.data) ? resource.data : null;
  const [label, setLabel] = useState("");
  const [role, setRole] = useState("operator");
  const [scopes, setScopes] = useState<string[]>([]);
  const [hours, setHours] = useState("24");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [token, setToken] = useState("");
  const [notice, setNotice] = useState("");
  async function refreshed() {
    const exact = await api<unknown>("/agent-tokens");
    if (!isRegistry(exact)) throw new Error("Access registry readback unavailable.");
    resource.setData(exact); return exact;
  }
  async function create() {
    if (busy || !label.trim() || !scopes.length) return;
    setBusy(true); setError(""); setToken(""); setNotice("");
    try {
      const created = await api<{ token: string; metadata: Metadata }>("/agent-tokens", { method: "POST", body: JSON.stringify({ label: label.trim(), role, scopes, expires_in_seconds: Number(hours) * 3600 }) });
      if (typeof created.token !== "string" || !created.token || !created.metadata?.id) throw new Error("Unsupported creation response. Check existing access before trying again.");
      // Keep the one-time value in component memory only, even if the following
      // readback fails: losing it would leave an inaccessible active credential.
      setToken(created.token);
      const exact = await refreshed();
      if (!exact.tokens.some((t) => t.id === created.metadata.id && !t.revoked_at)) throw new Error("Access created but not confirmed by registry readback.");
      setLabel(""); setScopes([]); setNotice("Access created and confirmed. The token will not be shown again after closing this view.");
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  async function revoke(id: string) {
    if (busy) return;
    setBusy(true); setError(""); setToken(""); setNotice("");
    try {
      await api(`/agent-tokens/${encodeURIComponent(id)}`, { method: "DELETE" });
      const exact = await refreshed();
      const target = exact.tokens.find((t) => t.id === id);
      if (target && !target.revoked_at) throw new Error("Revocation was not confirmed by the server.");
      setNotice("Revocation confirmed. Future requests using this credential will be denied.");
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <>
    <div className="page-heading"><div><h1>Agent access</h1><p className="muted">Control KuberWeaver from Hermes or any API/MCP-compatible agent without sharing a human account.</p></div></div>
    {error && <ErrorNotice message={error} />}
    {notice && <p className="notice" role="status">{notice}</p>}
    {token && <section className="panel" aria-label="One-time token display"><h2>Save in a private file</h2><p>This secret grants access to authorized operations. Never paste it into a conversation, blueprint or Git.</p><code style={{ overflowWrap: "anywhere" }}>{token}</code><div><button onClick={() => setToken("")}>Hide token</button></div></section>}
    {resource.loading ? <Spinner /> : !registry || resource.error ? <ErrorNotice message={resource.error || "Agent registry unavailable."} /> : <>
      <section className="panel settings-panel"><h2>Create limited access</h2>
        <div className="setting-row"><label htmlFor="agent-label">Agent name</label><input id="agent-label" value={label} maxLength={80} disabled={busy} onChange={(e) => setLabel(e.target.value)} /></div>
        <div className="setting-row"><label htmlFor="agent-role">Maximum role</label><select id="agent-role" value={role} disabled={busy} onChange={(e) => { setRole(e.target.value); setScopes([]); }}><option value="viewer">Viewer</option><option value="operator">Operator</option><option value="admin">Administrator, explicit delegation</option></select></div>
        <div className="setting-row"><label htmlFor="agent-hours">Expiration in hours</label><input id="agent-hours" type="number" min={1} max={720} value={hours} disabled={busy} onChange={(e) => setHours(e.target.value)} /></div>
        <fieldset disabled={busy}><legend>Allowed actions, none selected by default</legend>{registry.role_scopes[role].map((scope) => <label className="setting-row" key={scope}><span>{scope}</span><input type="checkbox" checked={scopes.includes(scope)} onChange={(e) => setScopes(e.target.checked ? [...scopes, scope] : scopes.filter((s) => s !== scope))} /></label>)}</fieldset>
        {role === "admin" && <p className="notice">An administrator agent granted approval permission can approve deployments. Delegate this permission deliberately; operators can submit without approving.</p>}
        <button className="primary" disabled={busy || !label.trim() || !scopes.length || !Number.isFinite(Number(hours)) || Number(hours) < 1 || Number(hours) > 720} onClick={create}>Create agent access</button>
      </section>
      <section className="panel"><div className="panel-title"><h2>Existing access</h2><button disabled={busy} onClick={() => resource.refresh()}>Refresh</button></div>
        {!registry.tokens.length && <p className="muted">No agent access issued.</p>}
        {registry.tokens.map((item) => <div className="setting-row" key={item.id}><div><strong>{item.label}</strong><small>{item.role} · {item.scopes.join(", ")}</small><small>Expires: {formatExpiry(item.expires_at)}</small></div><div><Badge tone={item.revoked_at ? "warning" : "info"}>{item.revoked_at ? "Revoked" : "Not revoked"}</Badge><button disabled={busy || !!item.revoked_at} onClick={() => revoke(item.id)}>Revoke {item.label}</button></div></div>)}
      </section>
    </>}
    <section className="panel"><h2>Agents follow the same rules as the portal</h2><p>Scopes limit actions; roles limit authority. Requests, approvals, quotas, secrets and audit records remain managed by KuberWeaver. MCP provides neither unrestricted system commands nor direct secret access.</p></section>
  </>;
}
