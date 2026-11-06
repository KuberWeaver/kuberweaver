let csrf = "";
export const setCsrf = (token: string) => {
  csrf = token;
};
export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, data: unknown) {
    const detail =
      (data as { detail?: unknown; message?: string })?.detail ||
      (data as { message?: string })?.message ||
      data || 'Aucun détail fourni par le serveur.';
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
      "Serveur inaccessible. Vérifiez votre connexion puis réessayez.",
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
