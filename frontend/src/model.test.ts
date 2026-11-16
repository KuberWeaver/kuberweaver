import { describe, it, expect } from "vitest";
import {
  emptyGraph,
  defaultsFor,
  serializeGraph,
  mayEdit,
  sortPlugins,
  canRollback,
  reconcileDraft,
} from "./model";
describe("Contrat du blueprint", () => {
  it("commence sans resource et conserve les valeurs exactes", () => {
    const graph = emptyGraph();
    expect(graph.nodes).toEqual([]);
    expect(graph.environment).toBe("dev");
    const out = serializeGraph({
      ...graph,
      nodes: [
        {
          id: "a",
          plugin_id: "nginx",
          label: "Web",
          position: { x: 12, y: 24 },
          config: { targets: ["app"], port: 18090 },
        },
      ],
    });
    expect(out.nodes[0]).toEqual({
      id: "a",
      plugin_id: "nginx",
      label: "Web",
      position: { x: 12, y: 24 },
      config: { targets: ["app"], port: 18090 },
    });
  });
  it("dérive les valeurs du manifeste et du schéma sans partager les tableaux", () => {
    const plugin: any = {
      defaults: { targets: ["app"] },
      schema: {
        properties: {
          version: { type: "string", default: "1.0.0" },
          port: { type: "number", default: 18090 },
          enabled: { type: "boolean", default: false },
        },
      },
    };
    const one = defaultsFor(plugin);
    expect(one).toEqual({
      version: "1.0.0",
      port: 18090,
      enabled: false,
      targets: ["app"],
    });
    (one.targets as string[]).push("branch");
    expect(defaultsFor(plugin).targets).toEqual(["app"]);
  });
  it("regroupe les capacités disponibles avant les cibles planifiées sans muter le catalogue", () => {
    const input: any = [
      { id: "cisco", name: "Cisco", capability: "planned" },
      { id: "nginx", name: "NGINX", capability: "live" },
      { id: "apache", name: "Apache", capability: "live" },
    ];
    expect(sortPlugins(input).map((p) => p.id)).toEqual([
      "apache",
      "nginx",
      "cisco",
    ]);
    expect(input[0].id).toBe("cisco");
  });
  it("interdit les modifications au viewer", () => {
    expect(mayEdit("viewer")).toBe(false);
    expect(mayEdit("admin")).toBe(true);
    expect(mayEdit("operator")).toBe(true);
    expect(mayEdit("editor")).toBe(false);
  });
});

describe("manual rollback eligibility", () => {
  it.each([
    [{ status: "failed" }, false],
    [{ status: "succeeded" }, true],
    [{ status: "rollback_failed" }, true],
    [{ status: "succeeded", can_rollback: false }, false],
    [{ status: "rollback_failed", can_rollback: false }, false],
    [{ status: "failed", can_rollback: true }, true],
    [{ status: "succeeded", requires_manual_review: true }, false],
    [{ status: "rollback_failed", can_rollback: true, requires_manual_review: true }, false],
    [{ status: "rolled_back" }, false],
    [{ status: "running" }, false],
    [{ status: "succeeded", can_rollback: "true" }, false],
  ])("uses authoritative eligibility and fails closed for manual review: %j", (job, eligible) => {
    expect(canRollback(job)).toBe(eligible);
  });
});

describe("Brouillon face à un schéma qui évolue", () => {
  const plugin = {
    id: "vmware-vm",
    name: "VMware VM",
    description: "",
    category: "compute",
    icon: "server",
    price_monthly: 0,
    capability: "live",
    resources: { cpu: 2, memory_mb: 2048, disk_gb: 20 },
    defaults: { cpu_sockets: 1, cores_per_socket: 2 },
    schema: {
      type: "object",
      additionalProperties: false,
      properties: {
        cpu_sockets: { type: "integer" },
        cores_per_socket: { type: "integer" },
      },
    },
    security: { status: "local_checks", checks: [] },
  } as never;
  const draft = {
    ...emptyGraph(),
    nodes: [
      {
        id: "vm",
        plugin_id: "vmware-vm",
        label: "VM",
        position: { x: 0, y: 0 },
        config: { cpu: 2, cpu_sockets: 1, cores_per_socket: 4 },
      },
    ],
  };
  it("retire un champ que le manifeste ne déclare plus", () => {
    const out = reconcileDraft(draft, [plugin]);
    expect(out.nodes[0].config).toEqual({ cpu_sockets: 1, cores_per_socket: 4 });
  });
  it("renvoie le même graphe quand tout est déclaré", () => {
    const clean = {
      ...draft,
      nodes: [{ ...draft.nodes[0], config: { cpu_sockets: 1, cores_per_socket: 4 } }],
    };
    expect(reconcileDraft(clean, [plugin])).toBe(clean);
  });
  it("laisse intact un noeud dont le plugin est inconnu", () => {
    const orphan = {
      ...draft,
      nodes: [{ ...draft.nodes[0], plugin_id: "absent" }],
    };
    expect(reconcileDraft(orphan, [plugin])).toBe(orphan);
  });
});
