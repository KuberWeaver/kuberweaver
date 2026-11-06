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
  return user.role === "admin" ? <Administration /> : <section className="panel"><h1>Accès des agents</h1><p>Réservé aux administrateurs : création et révocation des accès API et MCP.</p></section>;
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
    if (!isRegistry(exact)) throw new Error("Lecture de contrôle des accès indisponible.");
    resource.setData(exact); return exact;
  }
  async function create() {
    if (busy || !label.trim() || !scopes.length) return;
    setBusy(true); setError(""); setToken(""); setNotice("");
    try {
      const created = await api<{ token: string; metadata: Metadata }>("/agent-tokens", { method: "POST", body: JSON.stringify({ label: label.trim(), role, scopes, expires_in_seconds: Number(hours) * 3600 }) });
      if (typeof created.token !== "string" || !created.token || !created.metadata?.id) throw new Error("Réponse de création non prise en charge. Vérifiez les accès avant de réessayer.");
      // Keep the one-time value in component memory only, even if the following
      // readback fails: losing it would leave an inaccessible active credential.
      setToken(created.token);
      const exact = await refreshed();
      if (!exact.tokens.some((t) => t.id === created.metadata.id && !t.revoked_at)) throw new Error("Accès créé mais non confirmé par la relecture du registre.");
      setLabel(""); setScopes([]); setNotice("Accès créé et confirmé. Le jeton ne sera plus affiché après fermeture de cette vue.");
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
      if (target && !target.revoked_at) throw new Error("Révocation non confirmée par le serveur.");
      setNotice("Révocation confirmée. Les prochaines requêtes de cet accès seront refusées.");
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <>
    <div className="page-heading"><div><h1>Accès des agents</h1><p className="muted">Piloter KuberWeaver depuis Hermes ou tout agent compatible API/MCP, sans partager un compte humain.</p></div></div>
    {error && <ErrorNotice message={error} />}
    {notice && <p className="notice" role="status">{notice}</p>}
    {token && <section className="panel" aria-label="Jeton affiché une seule fois"><h2>À conserver dans un fichier privé</h2><p>Ce secret donne accès aux opérations autorisées. Ne le collez pas dans une conversation, un blueprint ou Git.</p><code style={{ overflowWrap: "anywhere" }}>{token}</code><div><button onClick={() => setToken("")}>Masquer le jeton</button></div></section>}
    {resource.loading ? <Spinner /> : !registry || resource.error ? <ErrorNotice message={resource.error || "Registre des agents indisponible."} /> : <>
      <section className="panel settings-panel"><h2>Créer un accès limité</h2>
        <div className="setting-row"><label htmlFor="agent-label">Nom de l’agent</label><input id="agent-label" value={label} maxLength={80} disabled={busy} onChange={(e) => setLabel(e.target.value)} /></div>
        <div className="setting-row"><label htmlFor="agent-role">Rôle maximal</label><select id="agent-role" value={role} disabled={busy} onChange={(e) => { setRole(e.target.value); setScopes([]); }}><option value="viewer">Lecteur</option><option value="operator">Opérateur</option><option value="admin">Administrateur, délégation explicite</option></select></div>
        <div className="setting-row"><label htmlFor="agent-hours">Expiration, en heures</label><input id="agent-hours" type="number" min={1} max={720} value={hours} disabled={busy} onChange={(e) => setHours(e.target.value)} /></div>
        <fieldset disabled={busy}><legend>Actions autorisées, aucune sélection par défaut</legend>{registry.role_scopes[role].map((scope) => <label className="setting-row" key={scope}><span>{scope}</span><input type="checkbox" checked={scopes.includes(scope)} onChange={(e) => setScopes(e.target.checked ? [...scopes, scope] : scopes.filter((s) => s !== scope))} /></label>)}</fieldset>
        {role === "admin" && <p className="notice">Un agent administrateur auquel vous accordez l’approbation peut approuver un déploiement. Ne déléguez ce droit que volontairement ; le rôle opérateur permet de soumettre sans approuver.</p>}
        <button className="primary" disabled={busy || !label.trim() || !scopes.length || !Number.isFinite(Number(hours)) || Number(hours) < 1 || Number(hours) > 720} onClick={create}>Créer un accès agent</button>
      </section>
      <section className="panel"><div className="panel-title"><h2>Accès existants</h2><button disabled={busy} onClick={() => resource.refresh()}>Actualiser</button></div>
        {!registry.tokens.length && <p className="muted">Aucun accès agent émis.</p>}
        {registry.tokens.map((item) => <div className="setting-row" key={item.id}><div><strong>{item.label}</strong><small>{item.role} · {item.scopes.join(", ")}</small><small>Expire : {formatExpiry(item.expires_at)}</small></div><div><Badge tone={item.revoked_at ? "warning" : "info"}>{item.revoked_at ? "Révoqué" : "Non révoqué"}</Badge><button disabled={busy || !!item.revoked_at} onClick={() => revoke(item.id)}>Révoquer {item.label}</button></div></div>)}
      </section>
    </>}
    <section className="panel"><h2>L’agent respecte les mêmes règles que le portail</h2><p>Les scopes limitent les actions ; le rôle limite les pouvoirs. Les demandes, approbations, quotas, secrets et traces restent gérés par KuberWeaver. Le MCP n’offre ni commande système libre ni accès direct aux secrets.</p></section>
  </>;
}
