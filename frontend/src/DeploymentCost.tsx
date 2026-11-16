import { money } from "./model";

const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
const amount = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) && value >= 0
    ? `${money(value)} / month` : "Unavailable";
const text = (value: unknown) => typeof value === "string" ? value : "—";

export function DeploymentCost({ cost }: { cost: unknown }) {
  const estimate = record(cost);
  const rows = Array.isArray(estimate.breakdown)
    ? estimate.breakdown.filter((row) => row && typeof row === "object" && !Array.isArray(row)) : [];
  // Admission currently prices in EUR. Never mislabel an unknown currency as EUR.
  const supported = !estimate.currency || estimate.currency === "EUR";
  return (
    <section className="deployment-cost" aria-label="Internal deployment estimate">
      <h3>Internal deployment estimate</h3>
      <p className="cost-total"><strong>{supported ? amount(estimate.monthly) : "Unsupported currency"}</strong></p>
      <p className="muted">Server-calculated rates · EUR / month · internal estimate, not metered billing or a provider invoice.</p>
      {rows.length ? <div className="table-wrap" tabIndex={0} role="region" aria-label="Estimated breakdown by resource">
        <table>
          <caption>Monthly breakdown by requested resource</caption>
          <thead><tr><th scope="col">Resource</th><th scope="col">Extension</th><th scope="col">Targets</th><th scope="col">Declared resources per target</th><th scope="col">Monthly estimate</th></tr></thead>
          <tbody>{rows.map((value, index) => {
            const row = record(value);
            const resources = Object.entries(record(row.resources))
              .filter(([, value]) => typeof value === "number" || typeof value === "string")
              .map(([key, value]) => `${key}: ${value}`).join(" · ");
            return <tr key={index}><td>{text(row.node_id)}</td><td>{text(row.plugin_id)}</td><td>{typeof row.targets === "number" ? row.targets : "—"}</td><td>{resources || "—"}</td><td>{supported ? amount(row.monthly) : "Unavailable"}</td></tr>;
          })}</tbody>
        </table>
      </div> : <p className="muted">Breakdown unavailable from the server.</p>}
    </section>
  );
}
