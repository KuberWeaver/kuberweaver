import { Info } from "lucide-react";

/**
 * Two questions people actually ask about this platform: which copy of the
 * truth am I looking at, and what can I change without the API?
 */
const SOURCES = [
  {
    term: "Desired state",
    detail:
      "Your blueprints and declarations, versioned in Git. This is what should exist, and the only thing that authorises a change.",
  },
  {
    term: "Observed state",
    detail:
      "Whatever the bound inventory provider reports right now, read only. Compared with the desired state to show drift. It never overwrites a blueprint.",
  },
  {
    term: "Recorded effects",
    detail:
      "Jobs, approvals, execution evidence and audit entries written by the server. Append only: this is what actually happened, not what was intended.",
  },
  {
    term: "Reporting",
    detail:
      "Read only SQL views over the recorded effects, for Grafana, Metabase or pandas. A projection, never a source.",
  },
];

const ACTIONS: { action: string; gui: string; api: string }[] = [
  { action: "Choose which implementation serves a capability", gui: "This page, admin only", api: "PATCH /api/providers" },
  { action: "Create a blueprint", gui: "Canvas", api: "POST /api/blueprints" },
  { action: "Validate a plan and its policy", gui: "Canvas", api: "POST /api/plans/validate" },
  { action: "Submit a job", gui: "Canvas", api: "POST /api/jobs" },
  { action: "Approve or reject a saved plan", gui: "Jobs", api: "POST /api/jobs/{id}/approve" },
  { action: "Register a dashboard link", gui: "Dashboards", api: "POST /api/dashboards" },
  { action: "Generate or export an SSH key", gui: "Keys", api: "POST /api/ssh-keys" },
  { action: "Read the observed topology", gui: "Infrastructure", api: "GET /api/infrastructure" },
  { action: "Query the reporting views", gui: "Not offered, on purpose", api: "GET /api/reporting" },
  { action: "Scrape metrics or readiness", gui: "Not offered, on purpose", api: "GET /metrics, GET /readyz" },
];

export function SourceOfTruth() {
  return (
    <>
      <section className="panel" aria-label="Source of truth">
        <div className="panel-title">
          <h2>Where the truth comes from</h2>
        </div>
        <dl className="integration-details">
          {SOURCES.map((source) => (
            <div key={source.term}>
              <dt>{source.term}</dt>
              <dd>{source.detail}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section className="panel" aria-label="What you can change">
        <div className="panel-title">
          <h2>What you can change from here</h2>
        </div>
        <div className="notice">
          <Info size={15} />
          The GUI calls the same endpoints an agent would call, under the same
          role checks. It never bypasses policy, and it grants no extra power.
        </div>
        <div className="table-wrap">
          <table aria-label="GUI and API actions">
            <thead>
              <tr>
                <th scope="col">Action</th>
                <th scope="col">GUI</th>
                <th scope="col">API</th>
              </tr>
            </thead>
            <tbody>
              {ACTIONS.map((row) => (
                <tr key={row.action}>
                  <td>{row.action}</td>
                  <td>{row.gui}</td>
                  <td className="mono">{row.api}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
