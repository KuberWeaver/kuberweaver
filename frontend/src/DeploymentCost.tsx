import { money } from "./model";

const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
const amount = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) && value >= 0
    ? `${money(value)} / mois` : "Indisponible";
const text = (value: unknown) => typeof value === "string" ? value : "—";

export function DeploymentCost({ cost }: { cost: unknown }) {
  const estimate = record(cost);
  const rows = Array.isArray(estimate.breakdown)
    ? estimate.breakdown.filter((row) => row && typeof row === "object" && !Array.isArray(row)) : [];
  // Admission currently prices in EUR. Never mislabel an unknown currency as EUR.
  const supported = !estimate.currency || estimate.currency === "EUR";
  return (
    <section className="deployment-cost" aria-label="Estimation interne du déploiement">
      <h3>Estimation interne du déploiement</h3>
      <p className="cost-total"><strong>{supported ? amount(estimate.monthly) : "Devise non prise en charge"}</strong></p>
      <p className="muted">Tarifs calculés par le serveur · EUR / mois · estimation interne, pas une facturation mesurée ni une facture fournisseur.</p>
      {rows.length ? <div className="table-wrap" tabIndex={0} role="region" aria-label="Répartition estimée par ressource">
        <table>
          <caption>Répartition mensuelle par ressource demandée</caption>
          <thead><tr><th scope="col">Ressource</th><th scope="col">Extension</th><th scope="col">Cibles</th><th scope="col">Ressources déclarées par cible</th><th scope="col">Estimation mensuelle</th></tr></thead>
          <tbody>{rows.map((value, index) => {
            const row = record(value);
            const resources = Object.entries(record(row.resources))
              .filter(([, value]) => typeof value === "number" || typeof value === "string")
              .map(([key, value]) => `${key}: ${value}`).join(" · ");
            return <tr key={index}><td>{text(row.node_id)}</td><td>{text(row.plugin_id)}</td><td>{typeof row.targets === "number" ? row.targets : "—"}</td><td>{resources || "—"}</td><td>{supported ? amount(row.monthly) : "Indisponible"}</td></tr>;
          })}</tbody>
        </table>
      </div> : <p className="muted">Répartition non disponible depuis le serveur.</p>}
    </section>
  );
}
