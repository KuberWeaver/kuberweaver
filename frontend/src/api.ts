let csrf = "";
export const setCsrf = (token: string) => {
  csrf = token;
};
// Private material bypasses the JSON API/error helpers and is consumed as a Blob only.
export async function exportSshKey(id: string, password: string): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch(`/api/ssh-keys/${encodeURIComponent(id)}/export`, {
      method: "POST", credentials: "include", cache: "no-store", redirect: "error",
      headers: { Accept: "application/octet-stream", "Content-Type": "application/json", ...(csrf ? { "X-CSRF-Token": csrf } : {}) },
      body: JSON.stringify({ password }),
    });
  } catch { throw new Error("Private key export failed. Check your connection and try again."); }
  if (!response.ok) throw new Error(`Private key export failed (HTTP ${response.status}). Verify your password and permissions.`);
  if (!response.headers.get("Content-Disposition")?.toLowerCase().startsWith("attachment") || !response.headers.get("Cache-Control")?.toLowerCase().includes("no-store")) throw new Error("Unsafe private key export response. Download blocked.");
  return response.blob();
}
export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, data: unknown) {
    const detail =
      (data as { detail?: unknown; message?: string })?.detail ||
      (data as { message?: string })?.message ||
      data || 'No details provided by the server.';
    super(
      `HTTP ${status} · ${typeof detail === "string" ? detail : JSON.stringify(detail)}`,
    );
    this.status = status;
    this.data = data;
  }
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...((options.headers || {}) as Record<string, string>),
  };
  if (!["GET", "HEAD"].includes(method) && csrf) headers["X-CSRF-Token"] = csrf;
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers,
      credentials: "include",
    });
  } catch {
    throw new Error(
      "Server unreachable. Check your connection and try again.",
    );
  }
  const text = await response.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) throw new ApiError(response.status, data);
  return data as T;
}
