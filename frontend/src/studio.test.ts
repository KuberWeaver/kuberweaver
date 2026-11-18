import { expect, it } from "vitest";
import {
  artifactKindLabel,
  artifactKinds,
  canPublishOfferings,
  canRequestOffering,
  offeringRequestMessage,
  offeringTitleError,
  serviceEstimate,
  serviceStatusLabel,
  validateArtifact,
} from "./studio";

it("offers exactly the three supported artifact kinds", () => {
  expect(artifactKinds.map((kind) => kind.id)).toEqual(["oci", "vm-image", "ansible-role"]);
  expect(artifactKindLabel("vm-image")).toBe("VM image");
  expect(artifactKindLabel("oci")).toBe("OCI image");
  expect(artifactKindLabel("ansible-role")).toBe("Ansible role");
  expect(artifactKindLabel("container")).toBe("container");
  expect(artifactKindLabel(undefined)).toBe("Unknown kind");
});

it("accepts an artifact reference without a pin and trims both fields", () => {
  expect(
    validateArtifact({ kind: "oci", reference: "  registry.example/app:1.4  " }),
  ).toEqual({ ok: true, value: { kind: "oci", reference: "registry.example/app:1.4" } });
});

it("keeps an optional pin and drops it when blank", () => {
  expect(
    validateArtifact({ kind: "ansible-role", reference: "galaxy://team.role", pin: " 1.2.0 " }),
  ).toEqual({ ok: true, value: { kind: "ansible-role", reference: "galaxy://team.role", pin: "1.2.0" } });
  expect(
    validateArtifact({ kind: "oci", reference: "app:1", pin: "   " }),
  ).toEqual({ ok: true, value: { kind: "oci", reference: "app:1" } });
});

it("refuses an unsupported kind, an empty reference and oversized fields", () => {
  expect(validateArtifact({ kind: "container", reference: "app:1" })).toEqual({
    ok: false,
    error: "Choose a supported artifact kind: OCI image, VM image or Ansible role.",
  });
  expect(validateArtifact({ kind: "oci", reference: "   " })).toEqual({
    ok: false,
    error: "Reference is required.",
  });
  expect(validateArtifact({ kind: "oci", reference: "a".repeat(513) }).ok).toBe(false);
  expect(validateArtifact({ kind: "oci", reference: "app:1", pin: "a".repeat(257) }).ok).toBe(false);
  expect(validateArtifact({ kind: "oci", reference: "app:\n1" }).ok).toBe(false);
  expect(validateArtifact({ kind: "oci", reference: "registry.example/app 1" }).ok).toBe(false);
});

it("separates publishing from requesting and keeps viewers out of both", () => {
  expect(canPublishOfferings("admin")).toBe(true);
  expect(canPublishOfferings("operator")).toBe(false);
  expect(canPublishOfferings("viewer")).toBe(false);
  expect(canRequestOffering("admin")).toBe(true);
  expect(canRequestOffering("operator")).toBe(true);
  expect(canRequestOffering("viewer")).toBe(false);
});

it("states that a request is pending approval instead of implying deployment", () => {
  const message = offeringRequestMessage("Platform VM", "job-42");
  expect(message).toContain("Platform VM");
  expect(message).toContain("job-42");
  expect(message).toContain("pending approval");
  expect(message.toLowerCase()).toContain("no infrastructure is created");
});

it("requires a bounded single-line offering title", () => {
  expect(offeringTitleError("")).toBe("Offering title is required.");
  expect(offeringTitleError("   ")).toBe("Offering title is required.");
  expect(offeringTitleError("Managed database")).toBeNull();
  expect(offeringTitleError("a".repeat(161))).toBe(
    "Offering title must be 160 characters or fewer.",
  );
  expect(offeringTitleError("two\nlines")).toBe(
    "Offering title must be a single-line label.",
  );
});

it("labels service status and monthly estimates without pretending to bill", () => {
  expect(serviceStatusLabel("pending_approval")).toBe("Awaiting approval");
  expect(serviceStatusLabel("unheard_of")).toBe("unheard_of");
  expect(serviceEstimate(42.75)).toBe("€42.75 / month");
  expect(serviceEstimate(0)).toBe("€0.00 / month");
  expect(serviceEstimate(null)).toBe("Not estimated");
  expect(serviceEstimate(undefined)).toBe("Not estimated");
  expect(serviceEstimate(Number.NaN)).toBe("Not estimated");
});


it("reports plan preparation without claiming the job is awaiting approval", () => {
  const message = offeringRequestMessage("Platform VM", "job-43", "planning");
  expect(message).toContain("Preparing plan");
  expect(message).not.toContain("pending approval");
});
