import { useEffect, useMemo, useState, useRef } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  applyNodeChanges,
  useReactFlow,
  MarkerType,
} from "@xyflow/react";
import type { Node, NodeProps, Connection, NodeChange } from "@xyflow/react";
import {
  Plus,
  Search,
  ArrowRight,
  Save,
  FolderOpen,
  ShieldCheck,
  Trash2,
  X,
  GitBranch,
  MousePointer2,
  Box,
  CheckCircle2,
  AlertTriangle,
  Info,
  RotateCcw,
  ChevronDown,
} from "lucide-react";
import type { Graph, Plugin, User, GraphNode } from "./model";
import {
  emptyGraph,
  serializeGraph,
  defaultsFor,
  mayEdit,
  money,
  sortPlugins,
  reconcileDraft,
} from "./model";
import { api } from "./api";
import { DiscoverySchemaFields } from "./DiscoverySchemaFields";
import { pageHeading } from "./navigation";
import {
  Badge,
  ErrorNotice,
  PluginIcon,
  Spinner,
  errorMessage,
  useResource,
  PageTitle,
} from "./ui";
type ResourceNode = Node<
  {
    label: string;
    plugin: string;
    engine: string;
    cost: number;
    readonly: boolean;
  },
  "resource"
>;
function ResourceCard({ data, selected }: NodeProps<ResourceNode>) {
  return (
    <div className={`resource-node ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="resource-top">
        <span className="plugin-icon">
          <PluginIcon name={data.plugin} />
        </span>
        <span className="node-type">{data.plugin}</span>
        <span className="node-dot" />
      </div>
      <strong>{data.label}</strong>
      <div className="node-meta">
        <span>{data.engine}</span>
        <span>{money(data.cost)} / month</span>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { resource: ResourceCard };
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
}: {
  user: User;
  plugins: Plugin[];
  pluginsLoading: boolean;
  pluginsError: string;
  retryPlugins: () => void;
  onJob: () => void;
}) {
  const draftKey = `kuberweaver.draft.${user.id}`;
  const [graph, setGraph] = useState<Graph>(() => loadDraft(draftKey)),
    [selected, setSelected] = useState<string | null>(null),
    [query, setQuery] = useState(""),
    [validation, setValidation] = useState<any>(null),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [paletteOpen, setPaletteOpen] = useState(false),
    [blueprintsOpen, setBlueprintsOpen] = useState(false);
  const canvasRef = useRef<HTMLDivElement>(null);
  const flow = useReactFlow();
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
    if (!edit) return;
    setGraph(fn);
    setValidation(null);
    setMessage("");
  };
  function add(plugin: Plugin, position?: { x: number; y: number }) {
    if (!edit || plugin.capability !== "live") return;
    const id = crypto.randomUUID();
    const node: GraphNode = {
      id,
      plugin_id: plugin.id,
      label: plugin.name,
      position: position || {
        x: 70 + (graph.nodes.length % 3) * 260,
        y: 70 + Math.floor(graph.nodes.length / 3) * 160,
      },
      config: defaultsFor(plugin),
    };
    mutate((g) => ({ ...g, nodes: [...g.nodes, node] }));
    setSelected(id);
    setPaletteOpen(false);
  }
  useEffect(() => {
    const id = sessionStorage.getItem("kuberweaver.add");
    if (id && plugins.length && edit) {
      sessionStorage.removeItem("kuberweaver.add");
      const plugin = plugins.find((p) => p.id === id);
      if (plugin) add(plugin);
    }
  }, [plugins, edit]);
  const nodes: ResourceNode[] = useMemo(
    () =>
      graph.nodes.map((n) => {
        const p = plugins.find((p) => p.id === n.plugin_id);
        return {
          id: n.id,
          type: "resource",
          position: n.position,
          selected: n.id === selected,
          data: {
            label: n.label,
            plugin: p?.name || n.plugin_id,
            engine: p?.engine || "Extension missing",
            cost: p?.price_monthly || 0,
            readonly: !edit,
          },
        };
      }),
    [graph.nodes, plugins, selected, edit],
  );
  const edges = graph.edges.map((e) => ({
    ...e,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed },
    style: { stroke: "#7b929d", strokeWidth: 1.6 },
  }));
  const chosen = graph.nodes.find((n) => n.id === selected),
    chosenPlugin = plugins.find((p) => p.id === chosen?.plugin_id);
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
  function onNodesChange(changes: NodeChange<ResourceNode>[]) {
    if (!edit) return;
    const next = applyNodeChanges(changes, nodes);
    const removed = new Set(
      changes.filter((c) => c.type === "remove").map((c) => c.id),
    );
    if (changes.some((c) => c.type === "position" || c.type === "remove"))
      mutate((g) => ({
        ...g,
        nodes: g.nodes
          .filter((n) => !removed.has(n.id))
          .map((n) => ({
            ...n,
            position: next.find((v) => v.id === n.id)?.position || n.position,
          })),
        edges: g.edges.filter(
          (e) => !removed.has(e.source) && !removed.has(e.target),
        ),
      }));
  }
  function connect(c: Connection) {
    if (
      c.source === c.target ||
      graph.edges.some((e) => e.source === c.source && e.target === c.target)
    )
      return;
    mutate((g) => ({
      ...g,
      edges: [
        ...g.edges,
        { id: crypto.randomUUID(), source: c.source, target: c.target },
      ],
    }));
  }
  async function run(action: string) {
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
      } else if (action === "save") {
        const created = await api("/blueprints", {
          method: "POST",
          body: JSON.stringify({
            name: graph.name,
            graph: serializeGraph(graph),
          }),
        });
        await api(`/blueprints/${encodeURIComponent(created.id)}`);
        blueprints.refresh();
        setMessage("Blueprint saved on the server.");
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
      setTimeout(() => flow.fitView({ padding: 0.2 }), 80);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  const filtered = sortPlugins(plugins).filter((p) =>
    `${p.name} ${p.description} ${p.category}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
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
        actions={
          <>
            <button onClick={() => setBlueprintsOpen((v) => !v)}>
              <FolderOpen size={16} />
              My blueprints
            </button>
            <button disabled={!edit || !!busy} onClick={() => run("save")}>
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
      <div className={`canvas-workspace ${chosen ? "has-inspector" : ""}`}>
        <aside className={`palette ${paletteOpen ? "mobile-open" : ""}`}>
          <div className="palette-heading">
            <h2>Components</h2>
            <Badge>
              {plugins.filter((p) => p.capability === "live").length}
            </Badge>
            <button
              className="icon-button mobile-close"
              aria-label="Close components"
              onClick={() => setPaletteOpen(false)}
            >
              <X size={17} />
            </button>
          </div>
          <p>Your platform extensions.</p>
          <div className="search-input">
            <Search size={15} />
            <input
              aria-label="Search components"
              placeholder="Search…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          {pluginsLoading ? (
            <Spinner />
          ) : pluginsError ? (
            <ErrorNotice message={pluginsError} retry={retryPlugins} />
          ) : (
            <div className="palette-list">
              {filtered.map((p, i) => (
                <div key={p.id}>
                  {i === 0 || p.capability !== filtered[i - 1].capability ? (
                    <div className="palette-section">
                      {p.capability === "live"
                        ? "AVAILABLE"
                        : "UNAVAILABLE TARGETS"}
                    </div>
                  ) : null}
                  <div
                    className={`palette-tile ${p.capability === "planned" ? "planned" : ""}`}
                    draggable={edit && p.capability === "live"}
                    onDragStart={(e) => {
                      e.dataTransfer.setData(
                        "application/kuberweaver-plugin",
                        p.id,
                      );
                      e.dataTransfer.effectAllowed = "move";
                    }}
                  >
                    <span className="plugin-icon">
                      <PluginIcon name={p.name} />
                    </span>
                    <div className="palette-tile-copy">
                      <strong>{p.name}</strong>
                      <small>
                        {p.capability === "live"
                          ? p.engine
                          : "Target unavailable"}
                      </small>
                    </div>
                    <button
                      className="icon-button"
                      aria-label={`Add ${p.name}`}
                      title={
                        p.capability === "live"
                          ? "Add to canvas"
                          : "Planned — target unavailable"
                      }
                      disabled={!edit || p.capability !== "live"}
                      onClick={() => add(p)}
                    >
                      <Plus size={16} />
                    </button>
                  </div>
                </div>
              ))}
              {!filtered.length && (
                <p className="muted">No components found.</p>
              )}
            </div>
          )}
          <div className="palette-foot">
            <Info size={14} />
            <span>Drag a component or use + to add it.</span>
          </div>
        </aside>
        <div
          className="flow-canvas"
          ref={canvasRef}
          onDragOver={(e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
          }}
          onDrop={(e) => {
            e.preventDefault();
            const p = plugins.find(
              (p) =>
                p.id ===
                e.dataTransfer.getData("application/kuberweaver-plugin"),
            );
            if (p)
              add(p, flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }));
          }}
        >
          <div className="canvas-breadcrumb">
            <span className="canvas-live-dot" />
            DESIRED STATE<span className="canvas-divider">/</span>
            <span>
              {graph.nodes.length} resource
              {graph.nodes.length !== 1 ? "s" : ""}
            </span>
          </div>
          <button
            className="mobile-palette"
            disabled={!edit}
            onClick={() => setPaletteOpen(true)}
          >
            <Plus size={15} />
            Components
          </button>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={(changes) => {
              const removed = new Set(
                changes.filter((c) => c.type === "remove").map((c) => c.id),
              );
              if (removed.size)
                mutate((g) => ({
                  ...g,
                  edges: g.edges.filter((e) => !removed.has(e.id)),
                }));
            }}
            onConnect={connect}
            onNodeClick={(_, n) => setSelected(n.id)}
            onPaneClick={() => setSelected(null)}
            nodesDraggable={edit}
            nodesConnectable={edit}
            edgesReconnectable={edit}
            elementsSelectable
            deleteKeyCode={edit ? ["Backspace", "Delete"] : null}
            minZoom={0.2}
            maxZoom={1.5}
            defaultViewport={{ x: 20, y: 40, zoom: 0.9 }}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#d4dde2" gap={22} size={1} />
            <Controls showInteractive={false} />
            {graph.nodes.length > 3 && <MiniMap pannable zoomable />}
          </ReactFlow>
          {!graph.nodes.length && (
            <div className="canvas-empty">
              <div className="canvas-empty-art">
                <Box size={35} strokeWidth={1.2} />
                <span className="art-plus">
                  <Plus size={13} />
                </span>
              </div>
              <span className="eyebrow">
                START WITH A BLANK CANVAS
              </span>
              <h2>Shape your infrastructure.</h2>
              <p>
                Add your first component from the catalogue.
                <br />
                Connect your resources, then configure them.
              </p>
              <button
                className="empty-add"
                disabled={
                  !edit || !plugins.some((p) => p.capability === "live")
                }
                onClick={() => {
                  if (window.innerWidth < 760) setPaletteOpen(true);
                  else {
                    const live = plugins.find((p) => p.capability === "live");
                    if (live) add(live);
                  }
                }}
              >
                <Plus size={16} />
                Add a component
                <ArrowRight size={15} />
              </button>
              <div className="empty-shortcuts">
                <span>
                  <MousePointer2 size={13} />
                  Drag and drop
                </span>
                <span>
                  <GitBranch size={13} />
                  Connect
                </span>
                <span>
                  <ShieldCheck size={13} />
                  Validate
                </span>
              </div>
            </div>
          )}
          <div className="canvas-bottom-note">
            <ShieldCheck size={13} />
            No infrastructure changes before approval.
          </div>
        </div>
        {chosen && (
          <aside className="inspector">
            <div className="panel-title">
              <div>
                <div className="eyebrow">CONFIGURATION</div>
                <h3>{chosenPlugin?.name || chosen.plugin_id}</h3>
              </div>
              <button
                className="icon-button"
                aria-label="Close inspector"
                onClick={() => setSelected(null)}
              >
                <X size={17} />
              </button>
            </div>
            <p className="muted inspector-description">
              {chosenPlugin?.description ||
                "This extension is no longer in the registry."}
            </p>
            <div className="field">
              <label htmlFor="node-label">Resource name</label>
              <input
                id="node-label"
                value={chosen.label}
                disabled={!edit}
                onChange={(e) => updateNode({ label: e.target.value })}
              />
            </div>
            {chosenPlugin && (
              <DiscoverySchemaFields
                key={chosen.id}
                canManageKeys={user.role === "admin"}
                schema={chosenPlugin.schema}
                value={chosen.config}
                onChange={(config) => updateNode({ config })}
                disabled={!edit}
                prefix={`node-${chosen.id}`}
              />
            )}
            <div className="inspector-cost">
              <span>Monthly estimate</span>
              <strong>{money(chosenPlugin?.price_monthly)}</strong>
            </div>
            <button
              className="danger subtle full"
              disabled={!edit}
              onClick={remove}
            >
              <Trash2 size={15} />
              Remove resource
            </button>
          </aside>
        )}
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
                disabled={!graph.nodes.length || !!busy}
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
              !edit || !graph.nodes.length || !validation?.allowed || !!busy
            }
            onClick={() => run("job")}
          >
            {busy === "job" ? "Creating…" : "Submit for approval"}
            <ArrowRight size={16} />
          </button>
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
