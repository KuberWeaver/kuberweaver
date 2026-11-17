import { useState } from "react";
import type { User } from "./model";
import { api } from "./api";
import { Badge, ErrorNotice, LoadingState, errorMessage, useResource } from "./ui";

type Descriptor = { kind: string; name: string; available?: boolean; status?: string; description?: string };
type ProviderConfig = { bindings: Record<string, string>; available: Descriptor[]; errors: unknown[] };

/**
 * Capabilities that may legitimately stay unbound. Everything else needs an
 * implementation for the platform to work, so the server refuses to clear it.
 */
const OPTIONAL_KINDS = new Set(["inventory"]);

const KIND_HELP: Record<string, string> = {
  inventory: "Reads a live network topology so you can compare it with your blueprints. Optional: the platform provisions without it.",
  diagnostics: "Reasons about a plan before approval. Read only, never mutates.",
  execution: "Runs an approved plan and records the evidence.",
  notifications: "Delivers job summaries to a channel you control.",
  secrets: "Resolves secret references. Values are never stored here.",
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
function parseConfig(value: unknown): ProviderConfig | null {
  if (!isRecord(value) || !isRecord(value.bindings) || !Array.isArray(value.available)
    || Object.values(value.bindings).some((name) => typeof name !== "string")) return null;
  return {
    bindings: value.bindings as Record<string, string>,
    available: value.available.filter((row): row is Descriptor => isRecord(row)
      && typeof row.kind === "string" && typeof row.name === "string"),
    errors: Array.isArray(value.errors) ? value.errors : [],
  };
}
function describeError(value: unknown): string {
  if (typeof value === "string") return value;
  if (isRecord(value) && typeof value.error === "string") return value.error;
  return JSON.stringify(value) ?? "Configuration error";
}
export function ProviderConfiguration({ user }: { user: User }) {
  const resource = useResource<unknown>("/providers");
  const config = parseConfig(resource.data);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const changes = Object.fromEntries(Object.entries(draft).filter(([kind, name]) =>
    config && Object.hasOwn(config.bindings, kind) && name !== config.bindings[kind]));
  const validChanges = config && Object.entries(changes).every(([kind, name]) =>
    name === ""
      ? OPTIONAL_KINDS.has(kind)
      : config.available.some((row) => row.kind === kind && row.name === name && row.available === true));
  async function save() {
    if (user.role !== "admin" || busy || !validChanges || !Object.keys(changes).length) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await api("/providers", { method: "PATCH", body: JSON.stringify({ bindings: changes }) });
      const exact = await api<unknown>("/providers");
      const confirmed = parseConfig(exact);
      if (!confirmed) throw new Error("Binding verification failed: unsupported server response.");
      resource.setData(exact);
      if (Object.entries(changes).some(([kind, name]) => confirmed.bindings[kind] !== name)) {
        throw new Error("Binding verification failed: selection not confirmed by the server.");
      }
      setDraft({});
      setSaved(true);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return <section className="panel settings-panel provider-configuration" aria-label="Provider configuration">
    <div className="panel-title"><h2>Selected providers</h2><button disabled={busy} onClick={() => { setDraft({}); setSaved(false); setError(""); resource.refresh(); }}>Refresh providers</button></div>
    <p className="muted">One implementation per capability. Changing a binding does not grant trust or activate extensions: enabling Python code stays a separate, explicit decision.</p>
    {error && <ErrorNotice message={error} />}
    {saved && <p className="notice success" role="status">Bindings saved and verified from the server.</p>}
    {resource.loading ? <LoadingState label="Loading provider configuration" /> : resource.error || !config ? <p className="notice" role="status">Provider configuration unavailable. {resource.error || "Unsupported server response."}</p> : <>
      {config.errors.map((value, index) => <p className="notice error" role="alert" key={index}>{describeError(value)}</p>)}
      {!Object.keys(config.bindings).length && <p className="muted">No capabilities declared by the server.</p>}
      {Object.entries(config.bindings).map(([kind, current]) => {
        const choices = config.available.filter((row) => row.kind === kind);
        const active = choices.find((row) => row.name === current);
        const available = current !== "" && active?.available === true;
        const optional = OPTIONAL_KINDS.has(kind);
        return <div className="setting-row" key={kind}>
          <div>
            <label htmlFor={`provider-${kind}`}>Provider {kind}</label>
            <small>{current ? `${current} · ${available ? "Active" : "Unavailable"}` : "No provider selected"}</small>
            <small>{KIND_HELP[kind] || "Capability served by one selected implementation."}</small>
            <Badge tone={available ? "success" : optional && !current ? "neutral" : "warning"}>
              {available ? "Active binding" : optional && !current ? "Not selected" : "Binding unavailable"}
            </Badge>
          </div>
          <div><select id={`provider-${kind}`} disabled={busy || user.role !== "admin"} value={draft[kind] ?? current} onChange={(event) => { setSaved(false); setError(""); setDraft({ ...draft, [kind]: event.target.value }); }}>
            {optional && <option value="">Not selected</option>}
            {current !== "" && !active && <option value={current} disabled>{current} · Unavailable</option>}
            {choices.map((choice) => <option key={choice.name} value={choice.name} disabled={choice.available !== true}>{choice.name}{choice.available === true ? "" : " · Unavailable"}</option>)}
          </select>
          {typeof active?.description === "string" && <small>{active.description}</small>}
          {optional && <small>Leaving this empty hides the observed topology instead of showing a stale one.</small>}</div>
        </div>;
      })}
      {user.role !== "admin" && <p className="muted">Only an administrator can change bindings.</p>}
      {user.role === "admin" && <div className="provider-actions"><button className="primary" disabled={busy || !validChanges || !Object.keys(changes).length} onClick={save}>{busy ? "Saving…" : "Save providers"}</button></div>}
    </>}
  </section>;
}
