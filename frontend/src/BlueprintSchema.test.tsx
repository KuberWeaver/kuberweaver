// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { DiscoverySchemaFields } from "./DiscoverySchemaFields";
import type { Schema } from "./model";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("utilise le contrat VM générique : discovered IDs, 1 to 4 networks and a managed SSH key", async () => {
  const schema: Schema = {
    type: "object", required: ["connection_id", "vm_name", "datastore_id", "network_ids", "ssh_key_id"],
    properties: {
      connection_id: { type: "string", title: "Connection", "x-options-source": "connections" },
      vm_name: { type: "string", title: "Name" },
      datastore_id: { type: "string", title: "Storage", "x-options-source": "datastores" },
      network_ids: { type: "array", title: "Networks", minItems: 1, maxItems: 4, items: { type: "string", "x-options-source": "networks" } },
      resource_pool_id: { type: "string", title: "Pool", "x-options-source": "resource_pools" },
      cpu: { type: "integer", default: 2 }, memory_mb: { type: "integer", default: 2048 },
      disk_gb: { type: "integer", default: 20, minimum: 8, maximum: 100 },
      image_id: { type: "string", enum: ["ubuntu-24.04"], default: "ubuntu-24.04" },
      ssh_key_id: { type: "string", title: "SSH key", "x-options-source": "ssh-keys" },
    },
  };
  const managedKey = { id: "managed-key", name: "Guest access", algorithm: "ed25519", public_key: "ssh-ed25519 AAAA", fingerprint: "SHA256:test", enabled: true, created_at: "2026-01-10T12:00:00Z", created_by: "admin" };
  const fetcher = vi.fn(async (path: string) => new Response(JSON.stringify(path === "/api/ssh-keys" ? [managedKey] : [{
    id: "esxi", label: "Hôte labo", enabled: true, revision: 1,
    observation: { revision: 1, status: "reachable", observed_at: "2026-01-10T12:30:00Z", inventory: {
      datastores: [{ id: "ds-1", name: "Stockage labo" }], resource_pools: [{ id: "pool-1", name: "Pool labo" }],
      networks: Array.from({ length: 5 }, (_, i) => ({ id: i < 2 ? ["HaNetwork-VM Network", "HaNetwork-VM IntNet"][i] : `net-${i}`, name: i < 2 ? ["VM Network", "VM IntNet"][i] : `Réseau ${i}` })),
    } },
  }])));
  vi.stubGlobal("fetch", fetcher);
  let saved: Record<string, unknown> = {};
  function Form() {
    const [value, setValue] = useState<Record<string, unknown>>({ connection_id: "esxi" });
    saved = value;
    return <DiscoverySchemaFields schema={schema} value={value} onChange={setValue} />;
  }
  render(<Form />);
  await screen.findByRole("checkbox", { name: "VM Network" });
  expect(screen.getByLabelText(/Storage/)).toHaveProperty("required", true);
  expect(screen.getByLabelText(/Storage/)).toHaveProperty("value", "");
  fireEvent.change(screen.getByLabelText(/Storage/), { target: { value: "ds-1" } });
  const selectedKey = screen.getByLabelText(/SSH key/) as HTMLSelectElement;
  expect(selectedKey.required).toBe(true);
  expect(selectedKey.value).toBe("");
  expect(screen.queryByLabelText(/public key/i)).toBeNull();
  fireEvent.change(selectedKey, { target: { value: managedKey.id } });
  fireEvent.click(screen.getByRole("checkbox", { name: "VM Network" }));
  expect(screen.getByRole("checkbox", { name: "VM Network" })).toHaveProperty("disabled", true);
  for (const name of ["VM IntNet", "Réseau 2", "Réseau 3"]) fireEvent.click(screen.getByRole("checkbox", { name }));
  expect(screen.getByRole("checkbox", { name: "Réseau 4" })).toHaveProperty("disabled", true);
  expect(saved).toMatchObject({ datastore_id: "ds-1", network_ids: ["HaNetwork-VM Network", "HaNetwork-VM IntNet", "net-2", "net-3"], ssh_key_id: "managed-key" });
  expect(fetcher.mock.calls).toHaveLength(2);
  expect(fetcher).toHaveBeenCalledWith("/api/connections", expect.anything());
  expect(fetcher).toHaveBeenCalledWith("/api/ssh-keys", expect.anything());
});
