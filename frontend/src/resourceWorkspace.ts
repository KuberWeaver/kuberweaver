import type { Graph, Plugin } from "./model";

export const resourceKinds = [
  { id: "vm", label: "Virtual machine" },
  { id: "container", label: "Container" },
  { id: "configuration", label: "Ansible configuration" },
  { id: "other", label: "Other" },
] as const;
export type ResourceKind = (typeof resourceKinds)[number]["id"];

export function resourceKind(plugin: Plugin): ResourceKind {
  if (plugin.kind === "workload" || plugin.implementation?.role) return "configuration";
  if (plugin.category === "compute") return "vm";
  if (plugin.service?.image) return "container";
  return "other";
}

export const referenceDirectories: Record<ResourceKind, { label: string; url: string }[]> = {
  vm: [
    { label: "Amazon EC2 images (AMI)", url: "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/finding-an-ami.html" },
    { label: "OpenTofu Registry", url: "https://search.opentofu.org/" },
  ],
  container: [{ label: "Docker Hub", url: "https://hub.docker.com/search" }],
  configuration: [{ label: "Ansible Galaxy", url: "https://galaxy.ansible.com/" }],
  other: [],
};

export function parseResourceVariables(text: string, plugin: Plugin): Record<string, unknown> {
  if (text.length > 65536) throw new Error("Configuration must be 64 KiB or less.");
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error("Enter a valid JSON object."); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Enter a JSON object of configuration values.");
  const keys = Object.keys(value);
  const allowed = plugin.schema.properties ?? {};
  if (!keys.length) throw new Error("Enter at least one configuration value.");
  if (keys.some((key) => !Object.hasOwn(allowed, key))) throw new Error("Only fields declared by the selected adapter can be imported.");
  if (keys.some((key) => /password|passphrase|secret|token|private[_-]?key|credential/i.test(key) && !/_(id|ref)$/.test(key))) {
    throw new Error("Use credential references, not secret values.");
  }
  return value as Record<string, unknown>;
}

export function wouldCycle(graph: Graph, source: string, target: string): boolean {
  const pending = [target];
  const visited = new Set<string>();
  while (pending.length) {
    const current = pending.pop()!;
    if (current === source) return true;
    if (visited.has(current)) continue;
    visited.add(current);
    for (const edge of graph.edges) if (edge.source === current) pending.push(edge.target);
  }
  return false;
}
