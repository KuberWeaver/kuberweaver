import { useState } from "react";
import type { User } from "./model";
import { api } from "./api";
import { Badge, ErrorNotice, Spinner, errorMessage, useResource } from "./ui";

type Capability = { id: string; label: string; kind: string; provider: string; configured: boolean; selected: boolean; status: string; config_file: string };
type Snapshot = { schema_version: number; capabilities: Capability[] };
function parseSnapshot(value: unknown): Snapshot | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (row.schema_version !== 1 || !Array.isArray(row.capabilities)) return null;
  const keys = ["id", "label", "kind", "provider", "status", "config_file"];
  if (!row.capabilities.every((item) => item && typeof item === "object" && !Array.isArray(item)
    && keys.every((key) => typeof item[key] === "string" && item[key].length > 0 && item[key].length <= 300)
    && typeof item.configured === "boolean" && typeof item.selected === "boolean")) return null;
  return { schema_version: 1, capabilities: row.capabilities as Capability[] };
}

export function Operations({ user }: { user: User }) {
  return user.role === "admin" ? <AdministrativeOperations /> : <section className="panel"><h1>Operations</h1><p>Administrators only: integration configuration and recovery.</p></section>;
}

function AdministrativeOperations() {
  const resource = useResource<Snapshot>("/operations");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const data = parseSnapshot(resource.data);
  const valid = data !== null;
  const delivery = valid && data.capabilities.find((row) => row.id === "notifications");
  async function retry() {
    if (busy) return;
    setBusy(true); setError(""); setResult("");
    try {
      const response = await api<{ result: { delivered?: number; failed?: number } }>("/operations/notifications/retry", { method: "POST" });
      const exact = parseSnapshot(await api<unknown>("/operations"));
      if (!exact) throw new Error("Readback unavailable.");
      resource.setData(exact);
      const delivered = response.result?.delivered;
      setResult(typeof delivered === "number" ? `${delivered} confirmed delivery, ${response.result.failed ?? 0} failed. Uncertain deliveries require verification with the recipient.` : "Attempt complete. Check delivery statuses; an accepted request does not prove receipt.");
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <>
    <div className="page-heading"><div><h1>Operations and integrations</h1><p className="muted">Actual capabilities, explicit configuration and controlled recovery.</p></div></div>
    {error && <ErrorNotice message={error} />}
    {result && <p className="notice" role="status">{result}</p>}
    {resource.loading ? <Spinner /> : resource.error || !valid ? <ErrorNotice message={resource.error || "Unsupported server response."} /> : <section className="panel settings-panel">
      <div className="panel-title"><h2>Capability status</h2><button disabled={busy} onClick={() => resource.refresh()}>Refresh</button></div>
      {data.capabilities.map((row) => <div className="setting-row" key={row.id}>
        <div><strong>{row.label}</strong><small>{row.kind} / {row.provider}</small></div>
        <div><Badge tone={row.configured ? "success" : "warning"}>{row.configured ? row.selected ? "Configured and selected" : "Configured, not selected" : "Configuration required"}</Badge><small>Administrator configuration: <code>{row.config_file}</code></small></div>
      </div>)}
      <p className="muted">Configuration files are in the private data/config directory. Use references to the selected secret provider, never keys or tokens in blueprints. After changes, reload the service and select the provider in Integrations.</p>
      <button disabled={busy || !delivery || !delivery.configured || !delivery.selected} onClick={retry}>{busy ? "Delivering…" : "Retry pending notifications"}</button>
    </section>}
    <section className="panel"><h2>Separate guarantees</h2><p>A notification is not a ChatOps approval. AI diagnostics cannot execute a fix. Unconfigured integrations do not silently fall back to another provider.</p></section>
  </>;
}
