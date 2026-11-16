// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DiscoverySchemaFields } from "./DiscoverySchemaFields";
import type { Schema } from "./model";
const key = { id: "key one", name: "Guest key", algorithm: "ed25519", public_key: "ssh-ed25519 AAAA", fingerprint: "SHA256:sample", enabled: true, created_at: "2026-01-10T12:00:00Z", created_by: "human" };
const schema: Schema = { properties: { ssh_key_id: { type: "string", title: "SSH key", "x-options-source": "ssh-keys" }, hostname: { type: "string", title: "Hostname" } } };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("explains missing enabled keys without sending users to connection discovery", async () => {
 vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(path === "/api/ssh-keys" ? "[]" : JSON.stringify([{ id: "connection", label: "Host", enabled: true, revision: 1, observation: null }]))));
 render(<DiscoverySchemaFields schema={{ properties: { ...schema.properties, connection_id: { type: "string", title: "Connection", "x-options-source": "connections" } } }} value={{}} onChange={() => {}} />);
 expect(await screen.findByText("No enabled SSH keys available. Ask an administrator to generate a key.")).toBeTruthy();
 expect(screen.queryByText(/Open Connections and run explicit discovery/)).toBeNull();
});
it("resolves only enabled managed keys by fixed route with no implicit selection", async () => {
 const fetcher = vi.fn(async () => new Response(JSON.stringify([key, { ...key, id: "disabled", name: "Disabled key", enabled: false }])));
 vi.stubGlobal("fetch", fetcher);
 const change = vi.fn();
 render(<DiscoverySchemaFields schema={schema} value={{ hostname: "keep" }} onChange={change} />);
 await screen.findByRole("option", { name: `${key.name} · ${key.fingerprint}` });
 expect(screen.queryByRole("option", { name: /Disabled key/ })).toBeNull();
 expect(screen.getByLabelText("SSH key")).toHaveProperty("value", "");
 expect(change).not.toHaveBeenCalled();
 expect(fetcher.mock.calls.map(() => "call")).toHaveLength(1);
 expect(fetcher).toHaveBeenCalledWith("/api/ssh-keys", expect.anything());
 fireEvent.change(screen.getByLabelText("SSH key"), { target: { value: key.id } });
 expect(change).toHaveBeenCalledWith({ hostname: "keep", ssh_key_id: key.id });
});
it("does not let an older list response hide a freshly generated key", async () => {
 let initial!: (r: Response) => void;
 let calls = 0;
 vi.stubGlobal("fetch", vi.fn(async (_path: string, options?: RequestInit) => {
   if (options?.method === "POST") return new Response(JSON.stringify(key));
   if (++calls === 1) return new Promise<Response>((resolve) => { initial = resolve; });
   return new Response(JSON.stringify([key]));
 }));
 render(<DiscoverySchemaFields schema={schema} value={{ hostname: "keep" }} onChange={() => {}} canManageKeys />);
 fireEvent.change(screen.getByLabelText("Key name"), { target: { value: "Guest key" } });
 fireEvent.click(screen.getByRole("button", { name: "Generate key" }));
 await screen.findByRole("option", { name: `${key.name} · ${key.fingerprint}` });
 await act(async () => { initial(new Response("[]")); });
 expect(screen.getByRole("option", { name: `${key.name} · ${key.fingerprint}` })).toBeTruthy();
});
it("generates inline and selects only after collection readback without losing the form", async () => {
 let generated = false;
 let confirm!: (r: Response) => void;
 const fetcher = vi.fn(async (_path: string, options?: RequestInit) => {
  if(options?.method === "POST") { generated = true; return new Response(JSON.stringify(key)); }
  if(generated) return new Promise<Response>((resolve) => { confirm = resolve; });
  return new Response("[]");
 });
 vi.stubGlobal("fetch", fetcher);
 const change = vi.fn();
 render(<DiscoverySchemaFields schema={schema} value={{ hostname: "keep" }} onChange={change} canManageKeys />);
 fireEvent.change(screen.getByLabelText("Key name"), { target: { value: "Guest key" } });
 fireEvent.click(screen.getByRole("button", { name: "Generate key" }));
 await waitFor(() => expect(confirm).toBeTypeOf("function"));
 expect(change).not.toHaveBeenCalled();
 confirm(new Response(JSON.stringify([key])));
 await waitFor(() => expect(change).toHaveBeenCalledWith({ hostname: "keep", ssh_key_id: key.id }));
});
