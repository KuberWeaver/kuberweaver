export type Schema = {
  type?: string;
  title?: string;
  description?: string;
  enum?: unknown[];
  enumNames?: string[];
  "x-options-source"?: string;
  default?: unknown;
  minimum?: number;
  minItems?: number;
  maxItems?: number;
  pattern?: string;
  maxLength?: number;
  maximum?: number;
  items?: Schema;
  properties?: Record<string, Schema>;
  required?: string[];
};
export type Plugin = {
  id: string;
  name: string;
  version: string;
  description: string;
  category: string;
  icon?: string;
  price_monthly: number;
  capability: "live" | "planned";
  engine: string;
  schema: Schema;
  defaults: Record<string, unknown>;
  resources?: unknown;
  security?: unknown;
  executor?: unknown;
  kind?: string;
  service?: { image?: string };
  implementation?: { role?: string; version?: string; source?: string };
  iac?: { template?: string };
};
export type GraphNode = {
  id: string;
  plugin_id: string;
  label: string;
  position: { x: number; y: number };
  config: Record<string, unknown>;
};
export type Graph = {
  name: string;
  environment: "dev" | "staging" | "production";
  nodes: GraphNode[];
  edges: { id: string; source: string; target: string }[];
  strategy: { canary: number; batch_size: number };
  failure_mode: "none" | "canary" | "rolling";
};
export type User = { id: string; username: string; role: string };
export type Event = {
  id: string;
  time: string;
  phase: string;
  level: string;
  message: string;
};
export type PreparedPlan = {
  plan_digest: string;
  actions: Record<string, unknown> | unknown[];
  summary: Record<string, unknown>;
  bindings: Record<string, unknown>;
};
export type Job = {
  id: string;
  name: string;
  status: string;
  created_at: string;
  created_by: string;
  environment: string;
  graph: Graph;
  cost: unknown;
  git_commit?: string;
  events: Event[];
  result?: unknown;
  approved_by?: string;
  error?: string;
  can_rollback?: boolean;
  can_decommission?: boolean;
  requires_manual_review?: boolean;
  prepared_plan?: PreparedPlan;
  requires_saved_plan?: boolean;
};
export function canRollback(job: {
  status: string;
  can_rollback?: unknown;
  requires_manual_review?: unknown;
}): boolean {
  if (job.requires_manual_review) return false;
  if ("can_rollback" in job) return job.can_rollback === true;
  return job.status === "succeeded" || job.status === "rollback_failed";
}
export const emptyGraph = (): Graph => ({
  name: "My blueprint",
  environment: "dev",
  nodes: [],
  edges: [],
  strategy: { canary: 1, batch_size: 1 },
  failure_mode: "none",
});
export function defaultsFor(
  plugin: Pick<Plugin, "defaults" | "schema">,
): Record<string, unknown> {
  return structuredClone({
    ...Object.fromEntries(
      Object.entries(plugin.schema?.properties || {})
        .filter(([, s]) => s.default !== undefined)
        .map(([key, s]) => [key, s.default]),
    ),
    ...plugin.defaults,
  });
}
/** Drop configuration keys the current manifest no longer declares.
 *  A draft stored before a schema change would otherwise keep an obsolete
 *  field and be refused at admission with no way to remove it from the form. */
export function reconcileDraft(graph: Graph, plugins: Plugin[]): Graph {
  const declared = new Map(
    plugins.map((plugin) => [
      plugin.id,
      new Set(Object.keys(plugin.schema?.properties || {})),
    ]),
  );
  let dropped = false;
  const nodes = graph.nodes.map((node) => {
    const keys = declared.get(node.plugin_id);
    if (!keys) return node;
    const config: Record<string, unknown> = {};
    let removed = false;
    for (const [key, value] of Object.entries(node.config || {}))
      if (keys.has(key)) config[key] = value;
      else removed = true;
    if (!removed) return node;
    dropped = true;
    return { ...node, config };
  });
  return dropped ? { ...graph, nodes } : graph;
}
export function serializeGraph(graph: Graph): Graph {
  return {
    name: graph.name,
    environment: graph.environment,
    nodes: graph.nodes.map((n) => ({
      id: n.id,
      plugin_id: n.plugin_id,
      label: n.label,
      position: n.position,
      config: n.config,
    })),
    edges: graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
    })),
    strategy: graph.strategy,
    failure_mode: graph.failure_mode,
  };
}
export const sortPlugins = (plugins: Plugin[]) =>
  [...plugins].sort(
    (a, b) =>
      Number(b.capability === "live") - Number(a.capability === "live") ||
      a.name.localeCompare(b.name, "en"),
  );
export const mayEdit = (role: string) => ["admin", "operator"].includes(role);
export const money = (value: unknown) =>
  new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(Number(value) || 0);
export const date = (value: string) =>
  value ? new Date(value).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "—";
export const statuses: Record<string, string> = {
  pending_approval: "Awaiting approval",
  approving: "Approval and publication",
  queued: "Queued",
  planning: "Preparing plan",
  running: "Running",
  rolling_back: "Rolling back",
  succeeded: "Succeeded",
  failed: "Failed",
  rolled_back: "Rolled back",
  rollback_failed: "Rollback failed",
  rejected: "Rejected",
  decommission_queued: "Retirement queued",
  decommissioning: "Retiring",
  decommissioned: "Retired",
  decommission_failed: "Retirement failed",
};
