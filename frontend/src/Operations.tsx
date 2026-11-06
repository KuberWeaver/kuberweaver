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
  return user.role === "admin" ? <AdministrativeOperations /> : <section className="panel"><h1>Opérations</h1><p>Réservé aux administrateurs : configuration et reprise des intégrations.</p></section>;
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
      if (!exact) throw new Error("Lecture de contrôle indisponible.");
      resource.setData(exact);
      const delivered = response.result?.delivered;
      setResult(typeof delivered === "number" ? `${delivered} livraison confirmée, ${response.result.failed ?? 0} échec. Les livraisons incertaines demandent une vérification du destinataire.` : "Tentative terminée. Vérifiez les statuts de livraison ; une requête acceptée ne prouve pas la réception.");
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <>
    <div className="page-heading"><div><h1>Opérations et intégrations</h1><p className="muted">Capacités réelles, configuration explicite et reprise contrôlée.</p></div></div>
    {error && <ErrorNotice message={error} />}
    {result && <p className="notice" role="status">{result}</p>}
    {resource.loading ? <Spinner /> : resource.error || !valid ? <ErrorNotice message={resource.error || "Réponse serveur non prise en charge."} /> : <section className="panel settings-panel">
      <div className="panel-title"><h2>État des capacités</h2><button disabled={busy} onClick={() => resource.refresh()}>Actualiser</button></div>
      {data.capabilities.map((row) => <div className="setting-row" key={row.id}>
        <div><strong>{row.label}</strong><small>{row.kind} / {row.provider}</small></div>
        <div><Badge tone={row.configured ? "success" : "warning"}>{row.configured ? row.selected ? "Configuré et sélectionné" : "Configuré, non sélectionné" : "À configurer"}</Badge><small>Configuration administrateur : <code>{row.config_file}</code></small></div>
      </div>)}
      <p className="muted">Les fichiers de configuration se trouvent dans le répertoire privé data/config. Utilisez des références vers le fournisseur de secrets sélectionné, jamais des clés ou tokens dans les blueprints. Après modification, rechargez le service puis sélectionnez le fournisseur dans Intégrations.</p>
      <button disabled={busy || !delivery || !delivery.configured || !delivery.selected} onClick={retry}>{busy ? "Livraison en cours…" : "Réessayer les notifications en attente"}</button>
    </section>}
    <section className="panel"><h2>Des garanties distinctes</h2><p>Une notification n’est pas une approbation ChatOps. Un diagnostic IA ne peut pas exécuter une correction. Une intégration non configurée n’utilise pas de remplacement silencieux.</p></section>
  </>;
}
