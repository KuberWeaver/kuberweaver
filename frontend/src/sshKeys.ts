import { api } from "./api";
export type SshKey = { id: string; name: string; algorithm: "ed25519"; public_key: string; fingerprint: string; enabled: boolean; created_at: string; created_by: string; usage?: unknown };
const text = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0;
export function readKey(value: unknown, id?: string): SshKey {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid SSH key response.");
  const k = value as Record<string, unknown>;
  if (!text(k.id) || (id !== undefined && k.id !== id) || !text(k.name) || k.algorithm !== "ed25519" || !text(k.public_key) || !text(k.fingerprint) || typeof k.enabled !== "boolean" || !text(k.created_at) || !Number.isFinite(Date.parse(k.created_at)) || !text(k.created_by)) throw new Error("Invalid SSH key response.");
  // Copy only public metadata; never retain unexpected private fields.
  return { id: k.id, name: k.name, algorithm: k.algorithm, public_key: k.public_key, fingerprint: k.fingerprint, enabled: k.enabled, created_at: k.created_at, created_by: k.created_by, ...(k.usage !== undefined ? { usage: k.usage } : {}) };
}
export function readKeys(value: unknown): SshKey[] {
  if (!Array.isArray(value)) throw new Error("Invalid SSH key response.");
  const keys = value.map((k) => readKey(k));
  if (new Set(keys.map((k) => k.id)).size !== keys.length) throw new Error("Invalid SSH key response.");
  return keys;
}
export async function generateKey(name: string): Promise<{ key: SshKey; keys: SshKey[] }> {
  const created = readKey(await api<unknown>("/ssh-keys", { method: "POST", body: JSON.stringify({ name }) }));
  const keys = readKeys(await api<unknown>("/ssh-keys"));
  const key = keys.find((k) => k.id === created.id);
  if (!key || !key.enabled || key.name !== created.name || key.fingerprint !== created.fingerprint || key.public_key !== created.public_key) throw new Error("Generated key was not confirmed by server readback. Refresh before trying again.");
  return { key, keys };
}
