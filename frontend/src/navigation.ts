export const pageIds = [
  "canvas",
  "catalogue",
  "studio",
  "offerings",
  "services",
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

export type PageRole = "admin" | "operator" | "viewer";

export type NavigationGroupId =
  | "build"
  | "services"
  | "operate"
  | "govern"
  | "administer";

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
  /** Absent means every signed-in role. */
  roles?: PageRole[];
};

export const defaultPageId: PageId = "canvas";

export const navigationGroups: NavigationGroup[] = [
  { id: "build", label: "Build" },
  { id: "services", label: "Services" },
  { id: "operate", label: "Operate" },
  { id: "govern", label: "Govern" },
  { id: "administer", label: "Administer" },
];

export const pages: PageDefinition[] = [
  {
    id: "canvas",
    label: "Canvas",
    group: "build",
    eyebrow: "BUILD · BLUEPRINT",
    title: "Blueprint canvas",
    description: "Compose a blueprint, validate policies and review the plan before deployment.",
  },
  {
    id: "catalogue",
    label: "Extension Catalog",
    group: "build",
    eyebrow: "BUILD · CATALOGUE",
    title: "Extension Catalog",
    description: "Raw building blocks for engineers: extensions declare their form schema, resources, security rules and executor.",
  },
  {
    id: "studio",
    label: "Engineer Studio",
    group: "build",
    roles: ["admin", "operator"],
    eyebrow: "BUILD · SERVICE STUDIO",
    title: "Engineer Studio",
    description: "Register artifact references and publish the service offerings customers can request.",
  },
  {
    id: "offerings",
    label: "Service Catalog",
    group: "services",
    eyebrow: "SERVICES · CATALOGUE",
    title: "Service Catalog",
    description: "Ready-made services. Requesting one opens a deployment that stays pending approval.",
  },
  {
    id: "services",
    label: "My services",
    group: "services",
    eyebrow: "SERVICES · MY SERVICES",
    title: "My services",
    description: "The services you requested, their approval state and a monthly estimate. Estimates are not bills.",
  },
  {
    id: "jobs",
    label: "Deployments",
    group: "operate",
    eyebrow: "OPERATE · DEPLOYMENTS",
    title: "Deployments",
    description: "From approval to rollback: every step leaves an audit trail.",
  },
  {
    id: "infrastructure",
    label: "Infrastructure",
    group: "operate",
    eyebrow: "OPERATE · OBSERVED",
    title: "Observed infrastructure",
    description: "Desired state from your blueprints, compared with an optional live topology.",
  },
  {
    id: "connections",
    label: "Connections",
    group: "operate",
    eyebrow: "OPERATE · CONNECTIONS",
    title: "Hypervisor connections",
    description: "Connection references and explicit observations of your hypervisors.",
  },
  {
    id: "keys",
    label: "Keys",
    group: "operate",
    eyebrow: "OPERATE · SSH KEYS",
    title: "SSH keys",
    description: "Generate and manage Ed25519 keys for guest access.",
  },
  {
    id: "governance",
    label: "Policies & pricing",
    group: "govern",
    eyebrow: "GOVERN · POLICIES & PRICING",
    title: "Approval policies",
    description: "Budget, images, network exposure and targets: explicit rules enforced by the server.",
  },
  {
    id: "finops",
    label: "FinOps",
    group: "govern",
    eyebrow: "GOVERN · COSTS",
    title: "Cost visibility",
    description: "Estimates based on declared resources and configured rates.",
  },
  {
    id: "diagnostics",
    label: "Diagnostics",
    group: "govern",
    eyebrow: "GOVERN · DIAGNOSTICS",
    title: "Infrastructure diagnostics",
    description: "Explained findings. No automatic remediation or infrastructure changes.",
  },
  {
    id: "audit",
    label: "Audit",
    group: "govern",
    eyebrow: "GOVERN · AUDIT",
    title: "Audit log",
    description: "Who did what, on which resource and when.",
  },
  {
    id: "integrations",
    label: "Providers",
    group: "administer",
    eyebrow: "ADMINISTER · PROVIDERS",
    title: "Providers and source of truth",
    description: "One implementation per capability, and an explicit answer to which state is authoritative.",
  },
  {
    id: "operations",
    label: "Operations",
    group: "administer",
    roles: ["admin"],
    eyebrow: "ADMINISTER · OPERATIONS",
    title: "Operations and integrations",
    description: "Actual capabilities, explicit configuration and controlled recovery.",
  },
  {
    id: "agents",
    label: "Agent access",
    group: "administer",
    roles: ["admin"],
    eyebrow: "ADMINISTER · AGENT ACCESS",
    title: "Agent access",
    description: "Create and revoke scoped API and MCP access without sharing a human account.",
  },
];

const pagesById = new Map<string, PageDefinition>(pages.map((page) => [page.id, page]));

export function pageFor(id: string): PageDefinition | null {
  return pagesById.get(id) ?? null;
}

export function roleCanSee(page: PageDefinition, role: string): boolean {
  return !page.roles || page.roles.includes(role as PageRole);
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
        (page) => page.group === group.id && roleCanSee(page, role),
      ),
    }))
    .filter((group) => group.pages.length > 0);
}
