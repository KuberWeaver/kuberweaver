export type Schema = {
  type?: string;
  title?: string;
  description?: string;
  enum?: unknown[];
  enumNames?: string[];
  "x-options-source"?: string;
  default?: unknown;
  minimum?: number;
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
  name: "Mon blueprint",
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
      a.name.localeCompare(b.name, "fr"),
  );
export const mayEdit = (role: string) => ["admin", "operator"].includes(role);
export const money = (value: unknown) =>
  new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(Number(value) || 0);
export const date = (value: string) =>
  value ? new Date(value).toLocaleString("fr-FR") : "—";
export const statuses: Record<string, string> = {
  pending_approval: "À approuver",
  approving: "Approbation et publication",
  queued: "En file",
  planning: "Préparation du plan",
  running: "En cours",
  rolling_back: "Restauration",
  succeeded: "Réussi",
  failed: "Échec",
  rolled_back: "Restauré",
  rollback_failed: "Échec restauration",
  rejected: "Refusé",
};
