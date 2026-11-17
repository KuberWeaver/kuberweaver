export const pageIds = [
  "canvas",
  "catalogue",
  "jobs",
  "infrastructure",
  "connections",
  "keys",
  "governance",
  "finops",
  "diagnostics",
  "audit",
  "integrations",
  "operations",
  "agents",
] as const;

export type PageId = (typeof pageIds)[number];

export type NavigationGroupId = "build" | "operate" | "govern" | "administer";

export type NavigationGroup = { id: NavigationGroupId; label: string };

export type PageHeading = {
  eyebrow: string;
  title: string;
  description: string;
};

export type PageDefinition = PageHeading & {
  id: PageId;
  label: string;
  group: NavigationGroupId;
  adminOnly: boolean;
};

export const defaultPageId: PageId = "canvas";

export const navigationGroups: NavigationGroup[] = [
  { id: "build", label: "Build" },
  { id: "operate", label: "Operate" },
  { id: "govern", label: "Govern" },
  { id: "administer", label: "Administer" },
];

export const pages: PageDefinition[] = [
  {
    id: "canvas",
    label: "Canvas",
    group: "build",
    adminOnly: false,
    eyebrow: "BUILD · BLUEPRINT",
    title: "Blueprint canvas",
    description: "Compose a blueprint, validate policies and review the plan before deployment.",
  },
  {
    id: "catalogue",
    label: "Catalogue",
    group: "build",
    adminOnly: false,
    eyebrow: "BUILD · CATALOGUE",
    title: "Extension catalogue",
    description: "Extensions declare their form schema, resources, security rules and executor.",
  },
  {
    id: "jobs",
    label: "Deployments",
    group: "operate",
    adminOnly: false,
    eyebrow: "OPERATE · DEPLOYMENTS",
    title: "Deployments",
    description: "From approval to rollback: every step leaves an audit trail.",
  },
  {
    id: "infrastructure",
    label: "Infrastructure",
    group: "operate",
    adminOnly: false,
    eyebrow: "OPERATE · OBSERVED",
    title: "Observed infrastructure",
    description: "Observed GNS3 topology, distinct from the desired state in your blueprints.",
  },
  {
    id: "connections",
    label: "Connections",
    group: "operate",
    adminOnly: false,
    eyebrow: "OPERATE · CONNECTIONS",
    title: "Hypervisor connections",
    description: "Connection references and explicit observations of your hypervisors.",
  },
  {
    id: "keys",
    label: "Keys",
    group: "operate",
    adminOnly: false,
    eyebrow: "OPERATE · SSH KEYS",
    title: "SSH keys",
    description: "Generate and manage Ed25519 keys for guest access.",
  },
  {
    id: "governance",
    label: "Governance",
    group: "govern",
    adminOnly: false,
    eyebrow: "GOVERN · POLICIES",
    title: "Approval policies",
    description: "Budget, images, network exposure and targets: explicit rules enforced by the server.",
  },
  {
    id: "finops",
    label: "FinOps",
    group: "govern",
    adminOnly: false,
    eyebrow: "GOVERN · COSTS",
    title: "Cost visibility",
    description: "Estimates based on declared resources and configured rates.",
  },
  {
    id: "diagnostics",
    label: "Diagnostics",
    group: "govern",
    adminOnly: false,
    eyebrow: "GOVERN · DIAGNOSTICS",
    title: "Infrastructure diagnostics",
    description: "Explained findings. No automatic remediation or infrastructure changes.",
  },
  {
    id: "audit",
    label: "Audit",
    group: "govern",
    adminOnly: false,
    eyebrow: "GOVERN · AUDIT",
    title: "Audit log",
    description: "Who did what, on which resource and when.",
  },
  {
    id: "integrations",
    label: "Integrations",
    group: "administer",
    adminOnly: false,
    eyebrow: "ADMINISTER · PROVIDERS",
    title: "Providers and extensions",
    description: "Providers connect the core to external systems. Their status is explicit, never assumed.",
  },
  {
    id: "operations",
    label: "Operations",
    group: "administer",
    adminOnly: true,
    eyebrow: "ADMINISTER · OPERATIONS",
    title: "Operations and integrations",
    description: "Actual capabilities, explicit configuration and controlled recovery.",
  },
  {
    id: "agents",
    label: "Agent access",
    group: "administer",
    adminOnly: true,
    eyebrow: "ADMINISTER · AGENT ACCESS",
    title: "Agent access",
    description: "Create and revoke scoped API and MCP access without sharing a human account.",
  },
];

const pagesById = new Map<string, PageDefinition>(pages.map((page) => [page.id, page]));

export function pageFor(id: string): PageDefinition | null {
  return pagesById.get(id) ?? null;
}

export function pageHeading(id: PageId): PageHeading {
  const page = pagesById.get(id);
  if (!page) throw new Error(`Unknown page: ${id}`);
  return { eyebrow: page.eyebrow, title: page.title, description: page.description };
}

export type NavigationSection = NavigationGroup & { pages: PageDefinition[] };

export function navigationFor(role: string): NavigationSection[] {
  return navigationGroups
    .map((group) => ({
      ...group,
      pages: pages.filter(
        (page) => page.group === group.id && (!page.adminOnly || role === "admin"),
      ),
    }))
    .filter((group) => group.pages.length > 0);
}
