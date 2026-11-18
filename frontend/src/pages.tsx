import { useState, useEffect, useRef } from "react";
import { ReactFlow, Background, Controls, MarkerType } from "@xyflow/react";
import {
  Plus,
  Search,
  RefreshCw,
  ShieldCheck,
  CheckCircle2,
  XCircle,
  RotateCcw,
  Terminal,
  Clock,
  Network,
  GitBranch,
  LockKeyhole,
  Activity,
  Info,
  Package,
  Save,
  ChevronRight,
  ChartNoAxesCombined,
} from "lucide-react";
import type { User, Plugin, Job } from "./model";
import { money, date, statuses, mayEdit, canRollback } from "./model";
import { api } from "./api";
import { capabilityState } from "./capabilities";
import { pageHeading } from "./navigation";
import { DeploymentCost } from "./DeploymentCost";
import { ProviderConfiguration } from "./ProviderConfiguration";
import { SourceOfTruth } from "./SourceOfTruth";
import {
  useResource,
  PageTitle,
  ResourceBoundary,
  Empty,
  Badge,
  PluginIcon,
  Notice,
  Details,
  ErrorNotice,
  Spinner,
  errorMessage,
} from "./ui";

export function Catalogue({
  plugins,
  loading,
  error,
  refresh,
  user,
  onCanvas,
  onOfferings,
}: {
  plugins: Plugin[];
  loading: boolean;
  error: string;
  refresh: () => void;
  user: User;
  onCanvas: () => void;
  onOfferings?: () => void;
}) {
  const [query, setQuery] = useState(""),
    [category, setCategory] = useState("all"),
    [showPlanned, setShowPlanned] = useState(false),
    [selected, setSelected] = useState<Plugin | null>(null);
  const categories = [...new Set(plugins.map((p) => p.category))];
  const hidden = plugins.filter((p) => p.capability === "planned").length;
  const filtered = plugins.filter(
    (p) =>
      (showPlanned || p.capability !== "planned") &&
      (category === "all" || p.category === category) &&
      `${p.name} ${p.description}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <>
      <PageTitle {...pageHeading("catalogue")} />
      <Notice
        action={
          onOfferings && (
            <button className="text-button" onClick={onOfferings}>
              Service Catalog
            </button>
          )
        }
      >
        Raw building blocks for engineers: only extensions executable on an available
        target can be added to the canvas. Customer-facing offerings live in the service
        catalogue.
      </Notice>
      <div className="list-toolbar">
        <div className="search-input">
          <Search size={16} />
          <input
            aria-label="Search catalogue"
            placeholder="Search extensions…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <label className="inline-label">
          Category
          <select
            aria-label="Filter by category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="all">All categories</option>
            {categories.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        {hidden > 0 && (
          <label className="inline-label">
            <input
              type="checkbox"
              checked={showPlanned}
              onChange={(e) => setShowPlanned(e.target.checked)}
            />{" "}
            Show {hidden} planned
          </label>
        )}
        <button
          className="icon-button"
          aria-label="Refresh catalogue"
          onClick={refresh}
        >
          <RefreshCw size={17} />
        </button>
      </div>
      <ResourceBoundary
        loading={loading}
        error={error}
        retry={refresh}
        label="Loading catalogue"
      >
        <div className="catalogue-grid">
          {filtered.map((p) => (
            <article
              className={`catalogue-card ${p.capability === "planned" ? "planned" : ""}`}
              key={p.id}
            >
              <div className="catalogue-card-top">
                <span className="plugin-icon large">
                  <PluginIcon name={p.name} size={26} />
                </span>
                <Badge tone={p.capability === "live" ? "success" : "neutral"}>
                  {p.capability === "live"
                    ? "Available"
                    : "Planned"}
                </Badge>
              </div>
              <span className="eyebrow">{p.category}</span>
              <h2>
                {p.name}
                <small>v{p.version}</small>
              </h2>
              <p>{p.description}</p>
              <div className="catalogue-meta">
                <span>{p.engine || "Provider not configured"}</span>
                <strong>
                  {money(p.price_monthly)}
                  <small> / month</small>
                </strong>
              </div>
              {p.capability === "planned" && (
                <div className="planned-note">
                  <LockKeyhole size={13} />
                  Target unavailable · not executable
                </div>
              )}
              <div className="catalogue-actions">
                <button onClick={() => setSelected(p)}>
                  Manifest
                  <ChevronRight size={14} />
                </button>
                <button
                  className="primary"
                  disabled={!mayEdit(user.role) || p.capability !== "live"}
                  onClick={() => {
                    sessionStorage.setItem("kuberweaver.add", p.id);
                    onCanvas();
                  }}
                >
                  <Plus size={15} />
                  Add
                </button>
              </div>
            </article>
          ))}
        </div>
        {!filtered.length && (
          <Empty title="No matching extensions">
            Change your search or refresh the registry.
          </Empty>
        )}
      </ResourceBoundary>
      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label={`Manifest ${selected.name}`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="panel-title">
              <h2>{selected.name}</h2>
              <button
                onClick={() => setSelected(null)}
                aria-label="Close manifest"
              >
                Close
              </button>
            </div>
            <p>{selected.description}</p>
            <Details title="Form schema" data={selected.schema} />
            <Details
              title="Resources, security and executor"
              data={{
                resources: selected.resources,
                security: selected.security,
                executor: selected.executor,
                defaults: selected.defaults,
              }}
            />
          </section>
        </div>
      )}
    </>
  );
}

function JobStatus({ status }: { status: string }) {
  return (
    <Badge
      tone={
        status === "succeeded" || status === "rolled_back"
          ? "success"
          : status === "failed" ||
              status === "rollback_failed" ||
              status === "rejected"
            ? "danger"
            : status === "running" || status === "rolling_back" || status === "planning"
              ? "info"
              : status === "pending_approval"
                ? "warning"
                : "neutral"
      }
    >
      {statuses[status] || status}
    </Badge>
  );
}

const planRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const planPrimitive = (value: unknown): value is string | number | boolean =>
  typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value));
function validPreparedPlan(value: unknown): value is NonNullable<Job["prepared_plan"]> {
  return planRecord(value) && typeof value.plan_digest === "string" && value.plan_digest.trim().length > 0 &&
    (planRecord(value.actions) || Array.isArray(value.actions)) && planRecord(value.summary) && planRecord(value.bindings);
}
function planText(value: unknown, lists = false): string | null {
  if (planPrimitive(value)) return String(value);
  return lists && Array.isArray(value) && value.every(planPrimitive) ? value.map(String).join(" · ") : null;
}
function PlanFields({ value, lists = false }: { value: unknown; lists?: boolean }) {
  const fields = planRecord(value) ? Object.entries(value)
    .map(([key, item]) => [key, planText(item, lists)] as const).filter(([, item]) => item !== null) : [];
  return fields.length ? <dl className="definition-grid">
    {fields.map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{item}</dd></div>)}
  </dl> : <p className="muted">No readable details available.</p>;
}
function PreparedPlanSummary({ plan }: { plan: NonNullable<Job["prepared_plan"]> }) {
  return <section className="deployment-cost" aria-label="Saved plan">
    <h3>Saved plan</h3>
    <p className="muted">Preparation only: this plan does not establish execution or operating system installation.</p>
    <p>Plan fingerprint: <code className="mono">{plan.plan_digest}</code></p>
    <h4>Planned actions</h4>
    <ul>{Object.entries(plan.actions).map(([key, action]) => <li key={key}>
      {!Array.isArray(plan.actions) && <strong>{key} : </strong>}
      {planText(action, true) ?? <PlanFields value={action} lists />}
    </li>)}</ul>
    <h4>Summary</h4>
    <PlanFields value={plan.summary} />
  </section>;
}

export function Jobs({
  user,
  onCreate,
}: {
  user: User;
  onCreate?: () => void;
}) {
  const jobs = useResource<Job[]>("/jobs", 2000);
  const [selected, setSelected] = useState<string | null>(() =>
    sessionStorage.getItem("kuberweaver.job"),
  );
  return (
    <>
      <PageTitle
        {...pageHeading("jobs")}
        actions={
          <button onClick={jobs.refresh}>
            <RefreshCw size={15} />
            Refresh
          </button>
        }
      />
      <ResourceBoundary
        loading={jobs.loading}
        error={jobs.error}
        retry={jobs.refresh}
        label="Loading deployments"
      >
        {!jobs.data?.length ? (
          <Empty
            title="No deployments yet"
            icon={<GitBranch size={28} />}
            action={
              onCreate && (
                <button onClick={onCreate}>Open canvas</button>
              )
            }
          >
            Validate a blueprint on the canvas, then submit it for approval.
          </Empty>
        ) : (
          <div className="jobs-layout">
            <section className="panel jobs-list">
              <div className="panel-title">
                <h3>Request history</h3>
                <Badge>{jobs.data.length}</Badge>
              </div>
              {jobs.data.map((j) => (
                <button
                  key={j.id}
                  className={`job-row ${selected === j.id ? "selected" : ""}`}
                  onClick={() => {
                    setSelected(j.id);
                    sessionStorage.setItem("kuberweaver.job", j.id);
                  }}
                >
                  <div className="job-row-title">
                    <strong>
                      {j.name || j.graph?.name || j.id.slice(0, 8)}
                    </strong>
                    <JobStatus status={j.status} />
                  </div>
                  <div className="job-row-meta">
                    <span>{j.environment}</span>
                    <span>{date(j.created_at)}</span>
                  </div>
                  <small>{j.created_by}</small>
                </button>
              ))}
            </section>
            {selected ? (
              <JobDetail key={selected} id={selected} user={user} onChange={jobs.refresh} />
            ) : (
              <section className="panel">
                <Empty title="Select a request">
                  View its phases, events and GitOps state.
                </Empty>
              </section>
            )}
          </div>
        )}
      </ResourceBoundary>
    </>
  );
}
const jobIdentityError = "Inconsistent response: the identifier does not match the selected request. Actions blocked.";

function JobDetail({
  id,
  user,
  onChange,
}: {
  id: string;
  user: User;
  onChange: () => void;
}) {
  const r = useResource<Job>(`/jobs/${encodeURIComponent(id)}`, 2000),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const active = useRef(false);
  useEffect(() => {
    active.current = true;
    setError("");
    setMessage("");
    return () => { active.current = false; };
  }, [id]);
  const j = r.data?.id === id ? r.data : null;
  const identityError = error === jobIdentityError || (!r.loading && !r.error && !j) ? jobIdentityError : "";
  const preparedPlan = validPreparedPlan(j?.prepared_plan) ? j.prepared_plan : null;
  const invalidPreparedPlan = j?.prepared_plan != null && !preparedPlan;
  const approvalBlocked = j?.requires_manual_review || invalidPreparedPlan || (j?.requires_saved_plan && !preparedPlan);
  const canMutate = !!j && !r.loading && !r.error && !identityError && !busy && user.role === "admin";
  async function action(name: "approve" | "reject" | "rollback") {
    if (!active.current || !canMutate || r.data?.id !== id || (name === "approve" && approvalBlocked)) return;
    if (
      name === "rollback" &&
      !window.confirm("Restore state from before this deployment? This can remove resources created by the request or restore previous versions. It is not a permanent service decommission.")
    )
      return;
    setBusy(name);
    setError("");
    setMessage("");
    try {
      const result = await api<Job>(`/jobs/${encodeURIComponent(id)}/${name}`, { method: "POST" });
      if (!active.current) return;
      if (result?.id !== id) throw new Error(jobIdentityError);
      const exact = await api<Job>(`/jobs/${encodeURIComponent(id)}`);
      if (!active.current) return;
      if (exact?.id !== id) throw new Error(jobIdentityError);
      r.setData(exact);
      onChange();
      setMessage(
        name === "approve"
          ? "Request approved. Tracking deployment progress."
          : name === "reject"
            ? "Request rejected."
            : "Rollback requested. Tracking progress.",
      );
    } catch (e) {
      if (active.current) setError(errorMessage(e));
    } finally {
      if (active.current) setBusy("");
    }
  }
  const groups = [...new Set((j?.events || []).map((e) => e.phase))];
  return (
    <section className="panel job-detail">
      <ResourceBoundary
        loading={r.loading && !j}
        error={identityError || r.error}
        retry={() => { setError(""); r.setData(null); r.refresh(); }}
        label="Loading request"
      >
        {j && (
          <>
            <div className="panel-title">
              <div>
                <div className="eyebrow">REQUEST {j.id.slice(0, 8)}</div>
                <h2>{j.name || j.graph?.name || "Deployment"}</h2>
              </div>
              <JobStatus status={j.status} />
            </div>
            <dl className="definition-grid">
              <div>
                <dt>Environment</dt>
                <dd>{j.environment}</dd>
              </div>
              <div>
                <dt>Requested by</dt>
                <dd>{j.created_by}</dd>
              </div>
              <div>
                <dt>Approval</dt>
                <dd>{j.approved_by || "Not approved"}</dd>
              </div>
              <div>
                <dt>GitOps commit</dt>
                <dd className="mono">
                  {j.git_commit ? (
                    <span title={j.git_commit}>
                      {j.git_commit.slice(0, 12)}
                    </span>
                  ) : (
                    "No published commit"
                  )}
                </dd>
              </div>
            </dl>
            {error && <ErrorNotice message={error} />}{" "}
            {message && (
              <div className="notice success" role="status">
                <CheckCircle2 size={16} />
                {message}
              </div>
            )}
            {j.error && (
              <ErrorNotice
                message={
                  typeof j.error === "string"
                    ? j.error
                    : JSON.stringify(j.error)
                }
              />
            )}
            <DeploymentCost cost={j.cost} />
            {preparedPlan && <PreparedPlanSummary plan={preparedPlan} />}
            {invalidPreparedPlan && <ErrorNotice message="Invalid saved plan: approval blocked." />}
            {j.status === "pending_approval" && approvalBlocked && !invalidPreparedPlan && <ErrorNotice message={j.requires_manual_review
              ? "Manual review required: approval blocked."
              : "Saved plan unavailable: approval blocked."} />}
            {j.status === "pending_approval" && (
              <div className="approval-box">
                <ShieldCheck size={22} />
                <div>
                  <strong>Approval is required</strong>
                  <p>
                    {user.role === "admin"
                      ? "Review the blueprint and policies before proceeding."
                      : "An administrator must approve this request."}
                  </p>
                </div>
                {user.role === "admin" && (
                  <div className="approval-actions">
                    <button
                      className="danger"
                      disabled={!canMutate}
                      onClick={() => action("reject")}
                    >
                      <XCircle size={15} />
                      Reject
                    </button>
                    {!approvalBlocked && <button
                      className="primary"
                      disabled={!canMutate}
                      onClick={() => action("approve")}
                    >
                      <CheckCircle2 size={15} />
                      {busy === "approve" ? "Approval…" : "Approve"}
                    </button>}
                  </div>
                )}
              </div>
            )}
            <h3 className="section-heading">
              <Activity size={16} />
              Observed phases
            </h3>
            {!groups.length ? (
              <p className="muted">
                No phases executed. Events will appear here.
              </p>
            ) : (
              <div className="execution-phases">
                {groups.map((phase, i) => {
                  const events = j.events.filter((e) => e.phase === phase);
                  const last = events[events.length - 1];
                  return (
                    <div key={phase}>
                      <span
                        className={`phase-number ${last.level === "error" ? "phase-error" : ""}`}
                      >
                        {i + 1}
                      </span>
                      <strong>{phase}</strong>
                      <small>
                        {events.length} event{events.length > 1 ? "s" : ""}
                      </small>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="logs-heading">
              <h3>
                <Terminal size={16} />
                Execution log
              </h3>
              <span>
                <Clock size={12} />
                Refreshing every 2 s
              </span>
            </div>
            <div
              className="log-console"
              role="log"
              aria-label="Execution log"
            >
              {!j.events?.length ? (
                <span className="log-empty">
                  Waiting for the first event…
                </span>
              ) : (
                j.events.map((e, i) => (
                  <div className={`log-line ${e.level}`} key={e.id || i}>
                    <time>{date(e.time)}</time>
                    <span className="log-phase">[{e.phase}]</span>
                    <span>{e.message}</span>
                  </div>
                ))
              )}
            </div>
            <Details title="Requested blueprint" data={j.graph} />
            {j.result && (
              <Details title="Execution result" data={j.result} />
            )}{" "}
            {["succeeded", "rollback_failed", "rolled_back"].includes(j.status) && (
                <div className="rollback-box">
                  <div>
                    <strong>{j.status === "rolled_back" ? "Rollback completed" : "Restore previous state"}</strong>
                    <p>
                      {j.status === "rolled_back" ? "The request was rolled back. Previously existing resources may have been restored." :
                        j.requires_manual_review ? "Manual review is required. Automatic rollback is blocked." :
                        !canRollback(j) ? "The server does not currently permit rollback for this deployment." :
                        user.role !== "admin" ? "Only an administrator can request rollback." :
                        "Rollback is scoped to this request. It may remove newly created resources or restore previous versions; it does not retire the service."}
                    </p>
                  </div>
                  {user.role === "admin" && canRollback(j) && <button
                    className="danger"
                    disabled={!canMutate}
                    onClick={() => action("rollback")}
                  >
                    <RotateCcw size={15} />
                    {busy === "rollback" ? "Requesting…" : "Manual rollback"}
                  </button>}
                </div>
              )}
          </>
        )}
      </ResourceBoundary>
    </section>
  );
}

export function Infrastructure() {
  const r = useResource("/infrastructure");
  const data = r.data;
  const nodes = (data?.nodes || []).map((n: any, i: number) => ({
    id: n.id || n.node_id,
    position: n.position || {
      x: typeof n.x === 'number' ? n.x : 50 + (i % 3) * 240,
      y: typeof n.y === 'number' ? n.y : 50 + Math.floor(i / 3) * 150,
    },
    data: {
      label: (
        <div className="infra-node">
          <Network size={20} />
          <strong>{n.name}</strong>
          <span>
            {n.type || n.node_type} · {n.status}
          </span>
        </div>
      ),
    },
    style: {
      background: "#fff",
      border: "1px solid #b8cbd0",
      borderRadius: 9,
      padding: 14,
      width: 200,
    },
  }));
  const links = (data?.links || [])
    .map((l: any, i: number) => ({
      id: l.id || l.link_id || `link-${i}`,
      source: l.source || l.nodes?.[0]?.node_id,
      target: l.target || l.nodes?.[1]?.node_id,
      markerEnd: { type: MarkerType.ArrowClosed },
    }))
    .filter((l: any) => l.source && l.target);
  return (
    <>
      <PageTitle
        {...pageHeading("infrastructure")}
        actions={
          <button onClick={r.refresh}>
            <RefreshCw size={15} />
            Refresh topology
          </button>
        }
      />
      {data?.bound === false ? (
        <section className="panel empty-state">
          <h2>No inventory provider selected</h2>
          <p>{data.message}</p>
          <p className="muted">
            A topology source is optional. KuberWeaver provisions from blueprints;
            bind an inventory provider only if you want to compare it with a live
            lab network.
          </p>
        </section>
      ) : (
      <ResourceBoundary
        loading={r.loading}
        error={r.error}
        retry={r.refresh}
        label="Loading infrastructure"
      >
        {data && (
          <>
            <div className="infra-meta panel">
              <div>
                <span className="eyebrow">SOURCE</span>
                <strong>{data.source || "Not reported"}</strong>
              </div>
              <div>
                <span className="eyebrow">PROJECT</span>
                <strong className="mono">
                  {data.project_id || "Not configured"}
                </strong>
              </div>
              <div>
                <span className="eyebrow">SERVER</span>
                <strong>{data.server_url || "Not configured"}</strong>
              </div>
              <Badge>
                {nodes.length} node{nodes.length !== 1 ? "s" : ""}
              </Badge>
            </div>
            {data.errors?.map((e: any, i: number) => (
              <ErrorNotice
                key={i}
                message={
                  typeof e === "string" ? e : e.message || JSON.stringify(e)
                }
              />
            ))}
            {!nodes.length ? (
              <Empty
                title="No observable devices"
                icon={<Network size={28} />}
              >
                Check the inventory provider configuration and project access.
              </Empty>
            ) : (
              <>
                <section
                  className="infra-flow panel"
                  aria-label="Observed topology"
                >
                  <ReactFlow
                    nodes={nodes}
                    edges={links}
                    fitView
                    nodesDraggable={false}
                    nodesConnectable={false}
                    deleteKeyCode={null}
                    proOptions={{ hideAttribution: true }}
                  >
                    <Background color="#d7dfe4" gap={22} />
                    <Controls showInteractive={false} />
                  </ReactFlow>
                </section>
                <div className="table-wrap panel">
                  <table>
                    <thead>
                      <tr>
                        <th>Device</th>
                        <th>Type</th>
                        <th>Observed state</th>
                        <th>ID</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.nodes.map((n: any) => (
                        <tr key={n.id || n.node_id}>
                          <td>
                            <strong>{n.name}</strong>
                          </td>
                          <td>{n.type || n.node_type}</td>
                          <td>
                            <Badge
                              tone={
                                n.status === "started" ? "success" : "neutral"
                              }
                            >
                              {n.status}
                            </Badge>
                          </td>
                          <td className="mono">{n.id || n.node_id}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </ResourceBoundary>
      )}
    </>
  );
}

function SettingFields({
  value,
  onChange,
  disabled,
  path = "policy",
}: {
  value: Record<string, any>;
  onChange: (v: Record<string, any>) => void;
  disabled: boolean;
  path?: string;
}) {
  return (
    <div className="settings-fields">
      {Object.entries(value).map(([key, v]) => {
        const id = `${path}-${key}`;
        const label = key.replaceAll("_", " ");
        return (
          <div className="setting-row" key={key}>
            <div>
              <label htmlFor={id}>{label}</label>
              <small>
                {Array.isArray(v)
                  ? "Allowlist · one value per line"
                  : typeof v === "number"
                    ? "Numeric value enforced by the server"
                    : typeof v === "boolean"
                      ? "Rule enforced during plan validation"
                      : "Platform configuration"}
              </small>
            </div>
            <div>
              {Array.isArray(v) ? (
                <textarea
                  id={id}
                  disabled={disabled}
                  rows={Math.min(5, Math.max(2, v.length))}
                  value={v.join("\n")}
                  onChange={(e) =>
                    onChange({ ...value, [key]: e.target.value.split("\n") })
                  }
                />
              ) : typeof v === "boolean" ? (
                <label className="toggle">
                  <input
                    id={id}
                    type="checkbox"
                    checked={v}
                    disabled={disabled}
                    onChange={(e) =>
                      onChange({ ...value, [key]: e.target.checked })
                    }
                  />
                  {v ? "Enabled" : "Disabled"}
                </label>
              ) : typeof v === "object" && v !== null ? (
                <SettingFields
                  value={v}
                  onChange={(next) => onChange({ ...value, [key]: next })}
                  disabled={disabled}
                  path={id}
                />
              ) : (
                <input
                  id={id}
                  disabled={disabled}
                  type={typeof v === "number" ? "number" : "text"}
                  step="any"
                  value={v ?? ""}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      [key]:
                        typeof v === "number"
                          ? Number(e.target.value)
                          : e.target.value,
                    })
                  }
                />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
export function Governance({ user }: { user: User }) {
  const r = useResource("/settings");
  const [draft, setDraft] = useState<any>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  useEffect(() => {
    if (r.data)
      setDraft(
        structuredClone({ policies: r.data.policies, pricing: r.data.pricing }),
      );
  }, [r.data]);
  async function save() {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const clean = (v: any): any =>
        Array.isArray(v)
          ? v
              .map((x) => (typeof x === "string" ? x.trim() : x))
              .filter((x) => x !== "")
          : v && typeof v === "object"
            ? Object.fromEntries(
                Object.entries(v).map(([k, val]) => [k, clean(val)]),
              )
            : v;
      await api("/settings", {
        method: "PATCH",
        body: JSON.stringify(clean(draft)),
      });
      const exact = await api("/settings");
      r.setData(exact);
      setSaved(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        {...pageHeading("governance")}
        actions={
          user.role === "admin" ? (
            <button
              className="primary"
              disabled={busy || !draft}
              onClick={save}
            >
              <Save size={16} />
              {busy ? "Saving…" : "Save policies"}
            </button>
          ) : undefined
        }
      />
      <Notice>
        {user.role === "admin"
          ? "Changes apply to future validations. Enter allowlists with one value per line."
          : "Policies are read-only. Only administrators can change them."}
      </Notice>
      {error && <ErrorNotice message={error} />}{" "}
      {saved && (
        <div className="notice success" role="status">
          <CheckCircle2 size={17} />
          Settings saved and read back from the server.
        </div>
      )}
      <ResourceBoundary
        loading={r.loading}
        error={r.error}
        retry={r.refresh}
        label="Loading policies"
      >
        {draft && (
          <>
            <section className="panel settings-panel">
              <div className="panel-title">
                <h2>
                  <ShieldCheck size={19} />
                  Approval policies
                </h2>
                <Badge>Server controls</Badge>
              </div>
              <SettingFields
                value={draft.policies || {}}
                onChange={(policies) => {
                  setSaved(false);
                  setDraft({ ...draft, policies });
                }}
                disabled={user.role !== "admin"}
              />
            </section>
            <section className="panel settings-panel">
              <div className="panel-title">
                <h2>
                  <ChartNoAxesCombined size={19} />
                  Infrastructure pricing
                </h2>
                <Badge>EUR / month</Badge>
              </div>
              <SettingFields
                value={draft.pricing || {}}
                onChange={(pricing) => {
                  setSaved(false);
                  setDraft({ ...draft, pricing });
                }}
                disabled={user.role !== "admin"}
              />
            </section>
          </>
        )}
      </ResourceBoundary>
    </>
  );
}

export function FinOps() {
  const r = useResource("/finops");
  const d = r.data;
  return (
    <>
      <PageTitle
        {...pageHeading("finops")}
        actions={
          <button onClick={r.refresh}>
            <RefreshCw size={15} />
            Refresh
          </button>
        }
      />
      <ResourceBoundary
        loading={r.loading}
        error={r.error}
        retry={r.refresh}
        label="Loading costs"
      >
        {d && (
          <>
            <div className="finops-summary">
              <section className="panel">
                <span className="eyebrow">ESTIMATED MONTHLY TOTAL</span>
                <div className="finops-total">
                  {money(d.monthly_total)}
                  <small>/ month</small>
                </div>
                <p className="muted">
                  Estimate in {d.currency || "EUR"}. Not a provider invoice.
                </p>
              </section>
              <section className="panel">
                <h3>Understandable costs</h3>
                <p>
                  Rates can be managed in Governance. External costs that are not collected are excluded.
                </p>
                <div className="notice">
                  <Info size={16} />
                  No cloud billing data is simulated.
                </div>
              </section>
            </div>
            <section className="panel">
              <div className="panel-title">
                <h2>Cost breakdown</h2>
                <Badge>{d.currency || "EUR"}</Badge>
              </div>
              {!d.breakdown?.length ? (
                <Empty title="No costs to allocate">
                  Deployed resources will appear here.
                </Empty>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Resource</th>
                        <th>Quantity</th>
                        <th>Monthly estimate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.breakdown.map((row: any, i: number) => (
                        <tr key={i}>
                          <td>
                            {row.name ||
                              row.label ||
                              row.plugin_id ||
                              row.resource ||
                              row.category ||
                              `Resource ${i + 1}`}
                          </td>
                          <td>{row.quantity ?? row.count ?? "—"}</td>
                          <td>
                            <strong>
                              {money(
                                row.monthly ??
                                  row.cost ??
                                  row.total ??
                                  row.amount,
                              )}
                            </strong>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <section className="panel recommendations">
              <h2>Recommendations</h2>
              {!d.recommendations?.length ? (
                <p className="muted">
                  No rule-based recommendations at this time.
                </p>
              ) : (
                d.recommendations.map((rec: any, i: number) => (
                  <div className="recommendation" key={i}>
                    <Info size={18} />
                    <div>
                      <strong>
                        {typeof rec === "string"
                          ? rec
                          : rec.title || rec.message}
                      </strong>
                      {rec.description && <p>{rec.description}</p>}
                      {rec.savings_monthly != null && (
                        <small>
                          Potential savings: {money(rec.savings_monthly)} / month
                        </small>
                      )}
                    </div>
                  </div>
                ))
              )}
              <Details data={d.pricing} title="Rate schedule" />
            </section>
          </>
        )}
      </ResourceBoundary>
    </>
  );
}

export function Diagnostics({ user }: { user?: User } = {}) {
  const r = useResource("/diagnostics");
  const d = r.data;
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState("");
  async function runAnalysis() {
    if (running) return;
    setRunning(true); setRunError("");
    try { r.setData(await api("/diagnostics/run", { method: "POST" })); }
    catch (e) { setRunError(errorMessage(e)); }
    finally { setRunning(false); }
  }
  return (
    <>
      <PageTitle
        {...pageHeading("diagnostics")}
        actions={
          <><button onClick={r.refresh} disabled={running}><RefreshCw size={15} />Refresh result</button>
          <button onClick={runAnalysis} disabled={running || user?.role === "viewer"}>{running ? "Analyzing…" : "Run explicit analysis"}</button></>
        }
      />
      <div className="diagnostics-mode panel">
        <span className="plugin-icon">
          <Activity size={22} />
        </span>
        <div>
          <h3>{d?.mode === "llm" ? "AI diagnostics" : "Rules engine"}</h3>
          <p>{d?.mode === "llm" ? `Provider: ${d.provider || "llm"} · Model: ${d.model || "not provided"} · Explicit execution only.` : "Deterministic diagnostics · No automatic remediation."}</p>
          {d?.mode === "llm" && d.status === "not_run" && <p>No AI analysis executed. Loading this page does not trigger a model call.</p>}
          {d?.generated_at && <small>Result produced: {date(d.generated_at)}</small>}
        </div>
        <Badge>Read only</Badge>
      </div>
      {runError && <ErrorNotice message={runError} />}
      <ResourceBoundary
        loading={r.loading}
        error={r.error}
        retry={r.refresh}
        label="Loading diagnostics"
      >
        {d && (
          <>
            {d.summary && (
              <section className="panel diagnostic-summary">
                <h3>Summary</h3>
                {typeof d.summary === "string" ? (
                  <p>{d.summary}</p>
                ) : (
                  <Details data={d.summary} title="Selected provider summary" />
                )}
              </section>
            )}
            <section className="panel">
              <div className="panel-title">
                <h2>Findings</h2>
                <Badge>{d.findings?.length || 0}</Badge>
              </div>
              {!d.findings?.length ? (
                <Empty
                  title="No findings reported"
                  icon={<ShieldCheck size={28} />}
                >
                  No findings are available in this result. An analysis that has not run does not prove the absence of problems; no diagnostic is a comprehensive security guarantee.
                </Empty>
              ) : (
                d.findings.map((f: any, i: number) => (
                  <article className="finding" key={f.id || i}>
                    <div>
                      <Badge
                        tone={
                          ["critical", "error", "high"].includes(f.severity)
                            ? "danger"
                            : f.severity === "warning"
                              ? "warning"
                              : "info"
                        }
                      >
                        {f.severity || "information"}
                      </Badge>
                      <h3>{f.title || f.code || "Finding"}</h3>
                    </div>
                    <p>{f.message || f.description}</p>
                    {f.resource && <small>Resource: {f.resource}</small>}
                    {Array.isArray(f.citations) && f.citations.length > 0 && <p className="muted">Sources: {f.citations.map((c: any) => `${c.job_id} / event ${c.event_id}`).join(", ")}</p>}
                    {f.recommendation && (
                      <div className="notice">
                        <Info size={15} />
                        {f.recommendation}
                      </div>
                    )}
                  </article>
                ))
              )}
            </section>
          </>
        )}
      </ResourceBoundary>
    </>
  );
}

export function Audit() {
  const r = useResource<any[]>("/audit");
  const [query, setQuery] = useState("");
  const entries = (r.data || []).filter((e) =>
    JSON.stringify(e).toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <>
      <PageTitle
        {...pageHeading("audit")}
        actions={
          <button onClick={r.refresh}>
            <RefreshCw size={15} />
            Refresh
          </button>
        }
      />
      <div className="list-toolbar">
        <div className="search-input">
          <Search size={15} />
          <input
            aria-label="Search audit log"
            placeholder="Actor, action, resource…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      <ResourceBoundary
        loading={r.loading}
        error={r.error}
        retry={r.refresh}
        label="Loading audit events"
      >
        {!entries.length ? (
          <Empty title="No audit events">
            Platform operations will be recorded here.
          </Empty>
        ) : (
          <div className="panel table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Resource & detail</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.id}>
                    <td className="nowrap">{date(e.time)}</td>
                    <td>
                      <strong>{e.actor}</strong>
                    </td>
                    <td>
                      <Badge>{e.action}</Badge>
                    </td>
                    <td>
                      <span className="mono">{e.resource || "—"}</span>
                      {e.detail && (
                        <details>
                          <summary>Event detail</summary>
                          <pre>
                            {typeof e.detail === "string"
                              ? e.detail
                              : JSON.stringify(e.detail, null, 2)}
                          </pre>
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ResourceBoundary>
    </>
  );
}

export function Integrations({ user }: { user: User }) {
  const r = useResource<any[]>("/integrations");
  const extensions = useResource<any>("/extensions");
  const [showPlanned, setShowPlanned] = useState(false);
  const registry = Array.isArray(extensions.data)
    ? extensions.data
    : extensions.data?.extensions || extensions.data?.registry || [];
  const rows = Array.isArray(r.data) ? r.data : [];
  const planned = rows.filter((row) => capabilityState(row).label === "Planned");
  const visible = rows.filter(
    (row) => showPlanned || capabilityState(row).label !== "Planned",
  );
  return (
    <>
      <PageTitle
        {...pageHeading("integrations")}
        actions={
          <button
            onClick={() => {
              r.refresh();
              extensions.refresh();
            }}
          >
            <RefreshCw size={15} />
            Refresh status
          </button>
        }
      />
      <Notice>
        A planned capability is not a connection. Endpoints and secrets are configured on the server, never in the browser.
      </Notice>
      <SourceOfTruth />
      {planned.length > 0 && (
        <label className="inline-label">
          <input
            type="checkbox"
            checked={showPlanned}
            onChange={(e) => setShowPlanned(e.target.checked)}
          />{" "}
          Show {planned.length} not configured
        </label>
      )}
      <ResourceBoundary
        loading={r.loading}
        error={r.error}
        retry={r.refresh}
        label="Loading integrations"
      >
        <div className="integrations-grid">
          {visible.map((integration: any, i: number) => {
            const state = capabilityState(integration);
            return (
              <section
                className="panel integration-card"
                key={integration.id || i}
              >
                <div className="panel-title">
                  <span className="plugin-icon">
                    <PluginIcon
                      name={integration.name || integration.id}
                      size={23}
                    />
                  </span>
                  <Badge tone={state.tone}>{state.label}</Badge>
                </div>
                <h2>{integration.name || integration.id}</h2>
                <p>
                  {integration.description ||
                    integration.reason ||
                    "No description provided by the provider."}
                </p>
                {integration.message && <p>{integration.message}</p>}
                {integration.reason && integration.description && (
                  <div className="notice">
                    <Info size={15} />
                    {integration.reason}
                  </div>
                )}
                <dl className="integration-details">
                  {integration.provider && (
                    <div>
                      <dt>Provider</dt>
                      <dd>{integration.provider}</dd>
                    </div>
                  )}
                  {integration.endpoint && (
                    <div>
                      <dt>Endpoint</dt>
                      <dd className="mono">{integration.endpoint}</dd>
                    </div>
                  )}
                  {integration.enabled !== undefined && (
                    <div>
                      <dt>Capability</dt>
                      <dd>{integration.enabled ? "Enabled" : "Disabled"}</dd>
                    </div>
                  )}
                </dl>
              </section>
            );
          })}
        </div>
        {!r.data?.length && (
          <Empty title="No providers declared">
            The server reports no configured integrations.
          </Empty>
        )}
      </ResourceBoundary>
      <ProviderConfiguration user={user} />
      <section className="panel extension-panel">
        <div className="panel-title">
          <h2>
            <Package size={19} />
            Extension registry
          </h2>
          <Badge>Extensible core</Badge>
        </div>
        <p className="muted">
          Extension activation determines the capabilities available in this portal.
        </p>
        {extensions.loading ? (
          <Spinner />
        ) : extensions.error ? (
          <div className="notice">
            <Info size={17} />
            <span>Registry status unavailable. {extensions.error}</span>
          </div>
        ) : Array.isArray(registry) && registry.length ? (
          <div className="extension-list">
            {registry.map((e: any, i: number) => (
              <div className="extension-row" key={e.id || i}>
                <span className="plugin-icon">
                  <Package size={18} />
                </span>
                <div>
                  <strong>{e.name || e.id}</strong>
                  <small>
                    {e.description || e.reason || e.version || e.module}
                  </small>
                </div>
                <Badge tone={capabilityState(e).tone}>
                  {capabilityState(e).label}
                </Badge>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">No optional extensions installed. Core providers are still listed below.</p>
        )}
        {extensions.data?.config_errors?.map((err: unknown, i: number) => <ErrorNotice key={i} message={typeof err === 'string' ? err : JSON.stringify(err)}/>)}
        {extensions.data?.providers?.length > 0 && <Details data={extensions.data.providers} title="Providers registered in the core"/>}
      </section>
    </>
  );
}
