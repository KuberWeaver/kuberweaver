// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Keys } from "./Keys";
import { setCsrf } from "./api";
const admin = { id: "human", username: "admin", role: "admin" };
const key = { id: "key-one", name: "Guest access", algorithm: "ed25519", public_key: "ssh-ed25519 AAAA", fingerprint: "SHA256:sample", enabled: true, created_at: "2026-01-10T12:00:00Z", created_by: "human" };
const response = (value: unknown) => new Response(JSON.stringify(value));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrf(""); });
it("lists validated public key records and reads generated keys back before showing success", async () => {
  let generated = false;
  const fetcher = vi.fn(async (_path: string, options?: RequestInit) => {
    if (options?.method === "POST") { generated = true; return response(key); }
    return response(generated ? [key] : []);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<Keys user={admin} />);
  await screen.findByText("No SSH keys yet.");
  fireEvent.change(screen.getByLabelText("Key name"), { target: { value: key.name } });
  fireEvent.click(screen.getByRole("button", { name: "Generate key" }));
  expect(await screen.findByText(key.name)).toBeTruthy();
  expect(screen.getByText(key.fingerprint)).toBeTruthy();
  expect(screen.getByText(key.public_key)).toBeTruthy();
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual(["/api/ssh-keys", "/api/ssh-keys", "/api/ssh-keys"]);
});
it.each([null, [null], [{ ...key, enabled: "yes" }], [key, key]])("rejects malformed key collections %j", async (value) => {
  vi.stubGlobal("fetch", vi.fn(async () => response(value)));
  render(<Keys user={admin} />);
  expect((await screen.findByRole("alert")).textContent).toContain("Invalid SSH key response");
});
it("does not expose administrative controls to readers", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response([key])));
  render(<Keys user={{ ...admin, role: "viewer" }} />);
  await screen.findByText(key.name);
  expect(screen.queryByRole("button", { name: "Generate key" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Export private key" })).toBeNull();
});
it("confirms disable by exact readback and explains existing guest access", async () => {
  let enabled = true;
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.method === "PATCH") enabled = false;
    return response(path === "/api/ssh-keys" ? [{ ...key, enabled }] : { ...key, enabled });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<Keys user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Disable" }));
  await screen.findByRole("button", { name: "Enable" });
  expect(screen.getByText(/does not remove.*authorized_keys/)).toBeTruthy();
  expect(fetcher.mock.calls.some(([path, options]) => path === "/api/ssh-keys/key-one" && options?.method === "PATCH")).toBe(true);
});
it("exports a checked Blob with CSRF and clears the reauthentication password immediately", async () => {
  setCsrf("test-csrf");
  let resolveExport!: (r: Response) => void;
  const fetcher = vi.fn(async (path: string) => path.endsWith("/export") ? new Promise<Response>((resolve) => { resolveExport = resolve; }) : response([key]));
  vi.stubGlobal("fetch", fetcher);
  const create = vi.fn((_blob: Blob) => "blob:download");
  Object.defineProperty(URL, "createObjectURL", { value: create, configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<Keys user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Export private key" }));
  expect(screen.getByRole("dialog").closest(".modal-overlay")).not.toBeNull();
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "reauth-value" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm export" }));
  expect(screen.getByLabelText("Current password")).toHaveProperty("value", "");
  await waitFor(() => expect(resolveExport).toBeTypeOf("function"));
  resolveExport(new Response("private fixture", { headers: { "Content-Type": "application/octet-stream", "Content-Disposition": "attachment; filename=key", "Cache-Control": "no-store" } }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0][0]).toBeDefined();
  expect(Object.prototype.toString.call(create.mock.calls[0][0])).toBe("[object Blob]");
  expect(fetcher).toHaveBeenCalledWith("/api/ssh-keys/key-one/export", expect.objectContaining({ credentials: "include", cache: "no-store", headers: expect.objectContaining({ "X-CSRF-Token": "test-csrf" }) }));
  expect(document.body.textContent).not.toContain("private fixture");
  expect(localStorage.length).toBe(0);
});

it("clears previous usage immediately and rejects a detail response for another key", async () => {
  const other = { ...key, id: "key-two", name: "Second key" };
  let resolveDetail!: (r: Response) => void;
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    if (path === "/api/ssh-keys") return response([key, other]);
    if (path.endsWith("key-one")) return response({ ...key, usage: ["first-key-usage"] });
    return new Promise<Response>((resolve) => { resolveDetail = resolve; });
  }));
  render(<Keys user={admin} />);
  const controls = await screen.findAllByRole("button", { name: "View usage" });
  fireEvent.click(controls[0]);
  await screen.findByText(/first-key-usage/);
  fireEvent.click(controls[1]);
  expect(screen.queryByText(/first-key-usage/)).toBeNull();
  await act(async () => { resolveDetail(response({ ...key, usage: ["foreign-key-usage"] })); });
  expect((await screen.findByRole("alert")).textContent).toContain("Invalid SSH key response");
  expect(screen.queryByText(/foreign-key-usage/)).toBeNull();
});
it("blocks downloads after cancellation and targets the newly chosen key only", async () => {
  const other = { ...key, id: "key two", name: "Second key" };
  let resolveExport!: (r: Response) => void;
  const fetcher = vi.fn(async (path: string) => path.endsWith("/export") ? new Promise<Response>((resolve) => { resolveExport = resolve; }) : response([key, other]));
  vi.stubGlobal("fetch", fetcher);
  const create = vi.fn();
  Object.defineProperty(URL, "createObjectURL", { value: create, configurable: true });
  render(<Keys user={admin} />);
  const controls = await screen.findAllByRole("button", { name: "Export private key" });
  fireEvent.click(controls[0]);
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "password" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm export" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.click(controls[1]);
  await act(async () => { resolveExport(new Response("private fixture", { headers: { "Content-Disposition": "attachment; filename=key", "Cache-Control": "no-store" } })); });
  expect(create).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog").textContent).toContain(other.name);
  expect(screen.getByLabelText("Current password")).toHaveProperty("value", "");
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "password" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm export" }));
  expect(fetcher).toHaveBeenLastCalledWith("/api/ssh-keys/key%20two/export", expect.anything());
  await act(async () => { resolveExport(new Response("sensitive server error", { status: 403 })); });
  expect((await screen.findByRole("alert")).textContent).toContain("HTTP 403");
  expect(document.body.textContent).not.toContain("sensitive server error");
  expect(create).not.toHaveBeenCalled();
});
it.each([{}, { "Content-Disposition": "attachment; filename=key" }, { "Cache-Control": "no-store" }])("rejects unchecked export headers %j", async (headers) => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => path.endsWith("/export") ? new Response("private fixture", { headers: headers as Record<string, string> }) : response([key])));
  const create = vi.fn();
  Object.defineProperty(URL, "createObjectURL", { value: create, configurable: true });
  render(<Keys user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Export private key" }));
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "password" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm export" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Download blocked");
  expect(create).not.toHaveBeenCalled();
  expect(document.body.textContent).not.toContain("private fixture");
});
it("does not trust a PATCH or readback for a foreign key", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => response(path === "/api/ssh-keys" ? [key] : { ...key, id: "foreign", enabled: false })));
  render(<Keys user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Disable" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Invalid SSH key response");
  expect(screen.queryByRole("button", { name: "Enable" })).toBeNull();
});
