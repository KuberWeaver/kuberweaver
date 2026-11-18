import { useEffect, useState } from "react";
import { Plus, ArrowRight, Save, FolderOpen, ShieldCheck, X, GitBranch, Box, CheckCircle2, AlertTriangle, Info, RotateCcw, ChevronDown, Store } from "lucide-react";
import type { Graph, Plugin, User, GraphNode } from "./model";
import { emptyGraph, serializeGraph, defaultsFor, mayEdit, money, reconcileDraft } from "./model";
import { api } from "./api";
import { pageHeading } from "./navigation";
import { Badge, ErrorNotice, PluginIcon, Spinner, errorMessage, useResource, PageTitle } from "./ui";
import { ResourceInspector } from "./ResourceInspector";
import { resourceKind, resourceKinds, wouldCycle } from "./resourceWorkspace";
import { offeringTitleError } from "./studio";
import "./resourceWorkspace.css";

export function loadDraft(key: string): Graph {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || "null");
    if (
      raw &&
      Array.isArray(raw.nodes) &&
      Array.isArray(raw.edges) &&
      raw.strategy &&
      ["dev", "staging", "production"].includes(raw.environment)
    )
      return serializeGraph(raw);
  } catch {
    /* an obsolete draft must not block the canvas */
  }
  return emptyGraph();
}
export function Canvas({
  user,
  plugins,
  pluginsLoading,
  pluginsError,
  retryPlugins,
  onJob,
  onOfferings,
}: {
  user: User;
  plugins: Plugin[];
  pluginsLoading: boolean;
  pluginsError: string;
  retryPlugins: () => void;
  onJob: () => void;
  onOfferings?: () => void;
}) {
  const draftKey = `kuberweaver.draft.${user.id}`;
  const [graph, setGraph] = useState<Graph>(() => loadDraft(draftKey)),
    [selected, setSelected] = useState<string | null>(null),
    [validation, setValidation] = useState<any>(null),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [published, setPublished] = useState(false),
    [blueprintsOpen, setBlueprintsOpen] = useState(false);
  const edit = mayEdit(user.role);
  const blueprints = useResource<any[]>("/blueprints");
  const [reconciled, setReconciled] = useState(false);
  useEffect(() => {
    if (reconciled || !plugins.length) return;
    setGraph((previous) => reconcileDraft(previous, plugins));
    setReconciled(true);
  }, [plugins, reconciled]);
  useEffect(() => {
    if (edit)
      try {
        localStorage.setItem(draftKey, JSON.stringify(serializeGraph(graph)));
      } catch {
        /* storage unavailable */
      }
  }, [graph, draftKey, edit]);
  useEffect(() => {
    function key(e: KeyboardEvent) {
      if (e.key === "Escape") setSelected(null);
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const mutate = (fn: (graph: Graph) => Graph) => {
    if (!edit || busy) return;
    setGraph(fn);
    setValidation(null);
    setMessage("");
    setPublished(false);
  };
  function add(plugin?: Plugin) {
    if (!edit || (plugin && plugin.capability !== "live")) return;
    const id = crypto.randomUUID();
    mutate((g) => ({ ...g, nodes: [...g.nodes, {
      id, plugin_id: plugin?.id ?? "", label: plugin?.name ?? "New resource",
      position: { x: 70 + (g.nodes.length % 3) * 260, y: 70 + Math.floor(g.nodes.length / 3) * 160 },
      config: plugin ? defaultsFor(plugin) : {},
    }] }));
    setSelected(id);
  }
  useEffect(() => {
    const id = sessionStorage.getItem("kuberweaver.add");
    if (id && plugins.length && edit) {
      sessionStorage.removeItem("kuberweaver.add");
      const plugin = plugins.find((p) => p.id === id);
      if (plugin) add(plugin);
    }
  }, [plugins, edit]);
  const chosen = graph.nodes.find((n) => n.id === selected);
  const complete = graph.nodes.length > 0 && graph.nodes.every((n) => plugins.some((p) => p.id === n.plugin_id && p.capability === "live"));
  const updateNode = (patch: Partial<GraphNode>) =>
    mutate((g) => ({
      ...g,
      nodes: g.nodes.map((n) => (n.id === selected ? { ...n, ...patch } : n)),
    }));
  const remove = () => {
    mutate((g) => ({
      ...g,
      nodes: g.nodes.filter((n) => n.id !== selected),
      edges: g.edges.filter(
        (e) => e.source !== selected && e.target !== selected,
      ),
    }));
    setSelected(null);
  };
  function dependency(source: string, enabled: boolean) {
    if (!selected) return;
    mutate((g) => {
      const same = (e: Graph["edges"][number]) => e.source === source && e.target === selected;
      if (!enabled) return { ...g, edges: g.edges.filter((e) => !same(e)) };
      if (g.edges.some(same) || wouldCycle(g, source, selected)) return g;
      return { ...g, edges: [...g.edges, { id: crypto.randomUUID(), source, target: selected }] };
    });
  }
  const executionBlocked = validation?.execution?.status === "unavailable";
  async function run(action: "validate" | "save" | "job" | "publish") {
    if (!complete || busy || (action !== "validate" && !edit)) return;
    if ((action === "publish" || action === "job") && (!validation?.allowed || executionBlocked)) return;
    if (action === "publish" && user.role !== "admin") return;
    setBusy(action);
    setError("");
    setMessage("");
    try {
      if (action === "validate") {
        setValidation(
          await api("/plans/validate", {
            method: "POST",
            body: JSON.stringify(serializeGraph(graph)),
          }),
        );
      } else if (action === "save" || action === "publish") {
        const title = graph.name.trim();
        if (action === "publish") {
          const problem = offeringTitleError(title);
          if (problem) throw new Error(problem);
        }
        const created = await api("/blueprints", {
          method: "POST",
          body: JSON.stringify({
            name: graph.name,
            graph: serializeGraph(graph),
          }),
        });
        blueprints.refresh();
        setMessage("Blueprint saved on the server.");
        if (action === "publish") {
          const offering = await api("/studio/offerings", {
            method: "POST", body: JSON.stringify({ title, blueprint_id: created.id }),
          });
          setMessage(`Published to Service Catalog: ${offering.title} · v${offering.version}.`);
          setPublished(true);
        }
      } else if (action === "job") {
        const job = await api("/jobs", {
          method: "POST",
          body: JSON.stringify({ graph: serializeGraph(graph) }),
        });
        await api(`/jobs/${encodeURIComponent(job.id)}`);
        sessionStorage.setItem("kuberweaver.job", job.id);
        onJob();
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function load(id: string) {
    setBusy("load");
    setError("");
    try {
      const b = await api(`/blueprints/${encodeURIComponent(id)}`);
      setGraph(serializeGraph(b.graph));
      setValidation(null);
      setSelected(null);
      setBlueprintsOpen(false);
      setMessage("Blueprint loaded.");
      setPublished(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  const estimate =
    validation?.cost?.monthly ??
    graph.nodes.reduce(
      (sum, n) =>
        sum + (plugins.find((p) => p.id === n.plugin_id)?.price_monthly || 0),
      0,
    );
  return (
    <>
      <PageTitle
        {...pageHeading("canvas")}
        title="Blueprint workspace"
        description="Add resource squares, configure them and publish the complete blueprint to self-service."
        actions={
          <>
            <button onClick={() => setBlueprintsOpen((v) => !v)}>
              <FolderOpen size={16} />
              My blueprints
            </button>
            <button disabled={!edit || !complete || !!busy} onClick={() => run("save")}>
              <Save size={16} />
              {busy === "save" ? "Saving…" : "Save"}
            </button>
          </>
        }
      />
      {!edit && (
        <div className="notice">
          <ShieldCheck size={17} />
          Read-only mode: composition and deployments are disabled for your role.
        </div>
      )}
      {error && <ErrorNotice message={error} />}{" "}
      {message && (
        <div className="notice success" role="status">
          <CheckCircle2 size={17} />
          {message}
          {published && onOfferings && <button onClick={onOfferings}>Open Service Catalog <ArrowRight size={14} /></button>}
        </div>
      )}
      {blueprintsOpen && (
        <section className="panel blueprint-browser">
          <div className="panel-title">
            <h3>Saved blueprints</h3>
            <button
              className="icon-button"
              onClick={() => setBlueprintsOpen(false)}
              aria-label="Close blueprints"
            >
              <X size={17} />
            </button>
          </div>
          {blueprints.loading ? (
            <Spinner />
          ) : blueprints.error ? (
            <ErrorNotice
              message={blueprints.error}
              retry={blueprints.refresh}
            />
          ) : !blueprints.data?.length ? (
            <p className="muted">No blueprints saved on the server.</p>
          ) : (
            <div className="blueprint-list">
              {blueprints.data.map((b) => (
                <button key={b.id} disabled={!!busy} onClick={() => load(b.id)}>
                  <GitBranch size={16} />
                  {b.name}
                  <ArrowRight size={14} />
                </button>
              ))}
            </div>
          )}
        </section>
      )}
      <div className="canvas-commandbar">
        <div className="blueprint-name">
          <GitBranch size={17} />
          <input
            aria-label="Blueprint name"
            value={graph.name}
            disabled={!edit}
            onChange={(e) => mutate((g) => ({ ...g, name: e.target.value }))}
          />
          <span className="draft-tag">Local draft</span>
        </div>
        <div className="canvas-options">
          <label>
            Environment
            <select
              aria-label="Environment"
              disabled={!edit}
              value={graph.environment}
              onChange={(e) =>
                mutate((g) => ({
                  ...g,
                  environment: e.target.value as Graph["environment"],
                }))
              }
            >
              <option value="dev">Development</option>
              <option value="staging">Staging</option>
              <option value="production">Production</option>
            </select>
          </label>
          <button
            className="icon-button"
            title="Reset canvas"
            aria-label="Reset canvas"
            disabled={!edit || !graph.nodes.length}
            onClick={() => {
              if (
                window.confirm(
                  "Clear this draft and start with an empty canvas?",
                )
              ) {
                mutate(() => emptyGraph());
                setSelected(null);
              }
            }}
          >
            <RotateCcw size={16} />
          </button>
        </div>
      </div>
      <div className={`resource-workspace ${chosen ? "has-selection" : ""}`}>
        <section className="resource-board panel" aria-labelledby="board-title">
          <header className="board-header"><div><h2 id="board-title">Resources</h2>
            <p className="muted">{graph.nodes.length ? "Select a square to configure it." : "Start with a blank resource square."}</p></div>
            <Badge>{graph.nodes.length}</Badge>
          </header>
          <div className="resource-grid">
            {graph.nodes.map((node) => {
              const plugin = plugins.find((p) => p.id === node.plugin_id);
              const kind = plugin && resourceKinds.find(({ id }) => id === resourceKind(plugin))?.label;
              return <button key={node.id} className="resource-square" aria-label={`${node.label || "Unnamed resource"} — ${kind ?? "Not configured"}`} aria-pressed={selected === node.id} onClick={() => setSelected(node.id)}>
                <span className="plugin-icon">{plugin ? <PluginIcon name={plugin.name} /> : <Box size={20} />}</span>
                <strong>{node.label || "Unnamed resource"}</strong>
                <small>{plugin ? `${kind} · ${plugin.engine}` : node.plugin_id ? "Adapter missing" : "Not configured"}</small>
              </button>;
            })}
            {edit && <button className="resource-square add-square" disabled={!!busy} onClick={() => add()}><Plus size={24} /><strong>Add resource</strong></button>}
          </div>
          {!complete && !!graph.nodes.length && <p className="muted">Choose an available adapter for every resource before validation or publication.</p>}
          <p className="resource-board-note"><ShieldCheck size={15} /> Nothing is deployed before approval.</p>
        </section>
        {chosen && <ResourceInspector key={chosen.id} node={chosen} graph={graph} plugins={plugins} loading={pluginsLoading} error={pluginsError}
          retry={retryPlugins} editable={edit && !busy} admin={user.role === "admin"} update={updateNode} dependency={dependency} close={() => setSelected(null)} remove={remove} />}
      </div>
      <section className="deployment-strip">
        <div className="strategy-copy">
          <span className="step-number">01</span>
          <div>
            <strong>Progressive deployment</strong>
            <small>Canary → successive batches → verification</small>
          </div>
        </div>
        <div className="strategy-controls">
          <label>
            Canary
            <input
              aria-label="Canary count"
              type="number"
              min="1"
              value={graph.strategy.canary}
              disabled={!edit}
              onChange={(e) =>
                mutate((g) => ({
                  ...g,
                  strategy: { ...g.strategy, canary: Number(e.target.value) },
                }))
              }
            />
          </label>
          <label>
            Batch size
            <input
              aria-label="Batch size"
              type="number"
              min="1"
              value={graph.strategy.batch_size}
              disabled={!edit}
              onChange={(e) =>
                mutate((g) => ({
                  ...g,
                  strategy: {
                    ...g.strategy,
                    batch_size: Number(e.target.value),
                  },
                }))
              }
            />
          </label>
          {user.role === "admin" && (
            <label className="failure-control">
              Failure injection
              <select
                aria-label="Intentional failure simulation"
                value={graph.failure_mode}
                onChange={(e) =>
                  mutate((g) => ({
                    ...g,
                    failure_mode: e.target.value as Graph["failure_mode"],
                  }))
                }
              >
                <option value="none">Disabled</option>
                <option value="canary">Intentional canary failure</option>
                <option value="rolling">Intentional rolling failure</option>
              </select>
            </label>
          )}
        </div>
      </section>
      <div className="canvas-review">
        <section className="validation-panel panel">
          <div className="panel-title">
            <h3>
              <ShieldCheck size={17} />
              Validation & governance
            </h3>
            {validation ? (
              <Badge tone={validation.allowed ? "success" : "danger"}>
                {validation.allowed ? "Allowed" : "Blocked"}
              </Badge>
            ) : (
              <Badge>Not validated</Badge>
            )}
          </div>
          {!validation ? (
            <div className="validation-placeholder">
              <span>
                Check policies, dependencies and budget before submitting.
              </span>
              <button
                disabled={!complete || !!busy}
                onClick={() => run("validate")}
              >
                <ShieldCheck size={15} />
                {busy === "validate" ? "Validation…" : "Validate blueprint"}
              </button>
            </div>
          ) : (
            <>
              <ul className="validation-items">
                {validation.violations?.map((v: any, i: number) => (
                  <li key={i} className="violation">
                    <AlertTriangle size={15} />
                    <div>
                      <strong>{v.code}</strong> {v.message}
                      {v.node_id && (
                        <small>
                          Resource:{" "}
                          {graph.nodes.find((n) => n.id === v.node_id)?.label ||
                            v.node_id}
                        </small>
                      )}
                    </div>
                  </li>
                ))}
                {validation.warnings?.map((v: any, i: number) => (
                  <li key={`w${i}`}>
                    <Info size={15} />
                    {typeof v === "string" ? v : v.message}
                  </li>
                ))}
                {validation.allowed && (
                  <li className="validation-ok">
                    <CheckCircle2 size={16} />
                    Policies allow this blueprint.
                  </li>
                )}
              </ul>
              {validation.execution?.status === "configured" && <p className="muted">
                Execution provider: {validation.execution.provider}. {validation.execution.requires_saved_plan && "A deployment plan is prepared before approval. "}
                Target connectivity and runtime health were not checked.
              </p>}
              {executionBlocked && <ErrorNotice message={validation.execution.reason} />}
              {validation.phases?.length > 0 && (
                <div className="phase-preview">
                  {validation.phases.map((p: any, i: number) => (
                    <span key={i}>
                      {typeof p === "string"
                        ? p
                        : p.name || p.phase || JSON.stringify(p)}
                      {i < validation.phases.length - 1 && (
                        <ArrowRight size={12} />
                      )}
                    </span>
                  ))}
                </div>
              )}
              <button
                className="text-button"
                disabled={!!busy}
                onClick={() => run("validate")}
              >
                Run validation again
              </button>
            </>
          )}
        </section>
        <section className="cost-panel panel">
          <div>
            <span className="eyebrow">MONTHLY ESTIMATE</span>
            <div className="monthly-cost">
              {money(estimate)}
              <small>/ month</small>
            </div>
            <p>
              {validation
                ? "Estimate validated by the server."
                : "Indicative · catalogue rates."}{" "}
              Excludes external costs.
            </p>
          </div>
          <button
            className="primary"
            disabled={
              !edit || !complete || !validation?.allowed || executionBlocked || !!busy
            }
            onClick={() => run("job")}
          >
            {busy === "job" ? "Creating…" : "Submit for approval"}
            <ArrowRight size={16} />
          </button>
          {user.role === "admin" && <button disabled={!complete || !validation?.allowed || executionBlocked || !!busy || published} onClick={() => run("publish")}>
            <Store size={16} /> {busy === "publish" ? "Publishing…" : "Publish to Service Catalog"}
          </button>}
          <small className="approval-note">
            <ShieldCheck size={12} />
            Administrator approval required
          </small>
        </section>
      </div>
      <div className="canvas-help">
        <Info size={14} />
        <span>
          The canvas describes desired state. Observed infrastructure state is available in Infrastructure.
        </span>
        <ChevronDown size={13} />
      </div>
    </>
  );
}
