import { money, statuses } from "./model";

export type ArtifactKind = "oci" | "vm-image" | "ansible-role";

export type StudioArtifact = {
  id: string;
  kind: string;
  reference: string;
  pin?: string | null;
};

export type Offering = { id: string; title: string; version: number | string };

export type Service = {
  job_id: string;
  title: string;
  status: string;
  monthly_estimate?: number | null;
};

export type BlueprintRecord = {
  id: string;
  name?: string;
  graph?: { name?: string };
};

export type ArtifactDraft = { kind: string; reference: string; pin?: string };

export type ValidatedArtifact =
  | { kind: ArtifactKind; reference: string; pin?: string };

export const artifactKinds: {
  id: ArtifactKind;
  label: string;
  hint: string;
}[] = [
  { id: "oci", label: "OCI image", hint: "registry.example/team/app:1.4" },
  { id: "vm-image", label: "VM image", hint: "vault://images/debian-12" },
  { id: "ansible-role", label: "Ansible role", hint: "galaxy://team.role" },
];

export function artifactKindLabel(kind?: string | null): string {
  const match = artifactKinds.find((entry) => entry.id === kind);
  return match ? match.label : kind?.trim() || "Unknown kind";
}

const maxReference = 512;
const maxPin = 256;
const maxTitle = 160;
// Locators are comparable single-line strings: printable ASCII without spaces.
const locator = /^[\x21-\x7e]+$/;
const pinPattern = /^[A-Za-z0-9][A-Za-z0-9._:+/=@-]*$/;
const singleLine = /^[\x20-\x7e]+$/;

/** Artifacts are references only. Validation mirrors the server contract so a
 *  typo is explained before the request instead of after a 4xx. */
export function validateArtifact(
  draft: ArtifactDraft,
):
  | { ok: true; value: ValidatedArtifact }
  | { ok: false; error: string } {
  const kind = artifactKinds.find((entry) => entry.id === draft.kind);
  if (!kind)
    return {
      ok: false,
      error: "Choose a supported artifact kind: OCI image, VM image or Ansible role.",
    };
  const reference = (draft.reference || "").trim();
  if (!reference) return { ok: false, error: "Reference is required." };
  if (reference.length > maxReference)
    return { ok: false, error: "Reference must be 512 characters or fewer." };
  if (!locator.test(reference))
    return {
      ok: false,
      error: "Reference must be a single-line locator without spaces.",
    };
  const pin = (draft.pin || "").trim();
  if (pin.length > maxPin)
    return { ok: false, error: "Pin must be 256 characters or fewer." };
  if (pin && !pinPattern.test(pin))
    return {
      ok: false,
      error: "Pin must be a digest or version: letters, digits and . _ : + / = @ - only.",
    };
  return {
    ok: true,
    value: pin ? { kind: kind.id, reference, pin } : { kind: kind.id, reference },
  };
}

/** Titles are single-line labels; the server refuses anything else. */
export function offeringTitleError(title: string): string | null {
  const clean = title.trim();
  if (!clean) return "Offering title is required.";
  if (clean.length > maxTitle)
    return "Offering title must be 160 characters or fewer.";
  if (!singleLine.test(clean))
    return "Offering title must be a single-line label.";
  return null;
}

export const canPublishOfferings = (role: string) => role === "admin";

export const canRequestOffering = (role: string) =>
  role === "admin" || role === "operator";

export const offeringRequestMessage = (title: string, jobId: string) =>
  `Requested "${title}". Deployment job ${jobId} is pending approval: no infrastructure is created until an operator approves it.`;

export function serviceStatusLabel(status?: string | null): string {
  if (!status) return "Unknown";
  return statuses[status] ?? status;
}

/** Estimates describe declared resources. They are not metered bills. */
export function serviceEstimate(value?: number | null): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${money(value)} / month`
    : "Not estimated";
}

export const blueprintLabel = (record: BlueprintRecord) =>
  record.name?.trim() || record.graph?.name?.trim() || record.id;
