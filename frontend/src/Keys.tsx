import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, exportSshKey } from "./api";
import { date } from "./model";
import type { User } from "./model";
import { generateKey, readKey, readKeys } from "./sshKeys";
import type { SshKey } from "./sshKeys";
import { Badge, Empty, ErrorNotice, LoadingState, PageTitle, errorMessage } from "./ui";
import { pageHeading } from "./navigation";
import { KeyRound } from "lucide-react";

export function GenerateKey({ onGenerated, disabled = false }: { onGenerated: (key: SshKey, keys: SshKey[]) => void; disabled?: boolean }) {
  const [name, setName] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const generatedCallback = useRef(onGenerated);
  generatedCallback.current = onGenerated;
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  async function generate() {
    if (!name.trim() || busy || disabled) return;
    setBusy(true); setError("");
    try {
      const result = await generateKey(name.trim());
      if (active.current) { setName(""); generatedCallback.current(result.key, result.keys); }
    } catch (e) { if (active.current) setError(errorMessage(e)); }
    finally { if (active.current) setBusy(false); }
  }
  return <div className="panel">
    <div className="field"><label>Key name<input value={name} maxLength={128} disabled={disabled || busy} onChange={(e) => setName(e.target.value)} /></label></div>
    <button type="button" disabled={disabled || busy || !name.trim()} onClick={generate}>{busy ? "Generating…" : "Generate key"}</button>
    {error && <ErrorNotice message={error} />}
  </div>;
}
function ExportDialog({ target, onClose }: { target: SshKey; onClose: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const active = useRef(true);
  useEffect(() => () => { active.current = false; }, []);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const input = form.elements.namedItem("password") as HTMLInputElement;
    const request = exportSshKey(target.id, input.value);
    input.value = "";
    setBusy(true); setError("");
    try {
      const blob = await request;
      if (!active.current) return;
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = url; anchor.download = `ssh-key-${target.id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
        anchor.click();
      } finally { URL.revokeObjectURL(url); }
      onClose();
    } catch (e) { if (active.current) setError(errorMessage(e)); }
    finally { if (active.current) setBusy(false); }
  }
  return <div className="modal-overlay" onClick={onClose}>
    <section className="modal" role="dialog" aria-modal="true" aria-labelledby="export-heading" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
    <h2 id="export-heading">Export private key: {target.name}</h2>
    <p>Reauthenticate with your current password. Save the download securely; never share it in chat or commit it to Git.</p>
    <form onSubmit={submit}>
      <label>Current password<input name="password" type="password" autoComplete="current-password" required disabled={busy} autoFocus /></label>
      <button disabled={busy}>{busy ? "Exporting…" : "Confirm export"}</button>
      <button type="button" onClick={onClose}>Cancel</button>
    </form>
    {error && <ErrorNotice message={error} />}
    </section>
  </div>;
}
function KeyUsage({ id }: { id: string }) {
  const [key, setKey] = useState<SshKey | null>(null), [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<unknown>(`/ssh-keys/${encodeURIComponent(id)}`).then((value) => {
      const result = readKey(value, id);
      if (active) setKey(result);
    }).catch((e) => { if (active) setError(errorMessage(e)); });
    return () => { active = false; };
  }, [id]);
  if (error) return <ErrorNotice message={error} />;
  if (!key) return <LoadingState label="Loading key usage" />;
  return <div><h3>Usage: {key.name}</h3>{key.usage === undefined ? <p>Usage information is not available.</p> : <pre>{JSON.stringify(key.usage, null, 2)}</pre>}</div>;
}
export function Keys({ user }: { user: User }) {
  const [keys, setKeys] = useState<SshKey[]>([]), [loading, setLoading] = useState(true), [error, setError] = useState(""), [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(""), [exportTarget, setExportTarget] = useState<SshKey | null>(null), [usage, setUsage] = useState("");
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let current = true;
    setLoading(true); setError(""); setKeys([]);
    api<unknown>("/ssh-keys").then((value) => { const rows = readKeys(value); if (current) setKeys(rows); })
      .catch((e) => { if (current) setError(errorMessage(e)); }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [revision]);
  async function toggle(key: SshKey) {
    setBusy(key.id); setError("");
    try {
      const path = `/ssh-keys/${encodeURIComponent(key.id)}`;
      const updated = readKey(await api<unknown>(path, { method: "PATCH", body: JSON.stringify({ enabled: !key.enabled }) }), key.id);
      const confirmed = readKey(await api<unknown>(path), key.id);
      if (updated.enabled !== !key.enabled || confirmed.enabled !== updated.enabled || confirmed.fingerprint !== key.fingerprint) throw new Error("SSH key update was not confirmed by server readback.");
      if (active.current) setKeys((rows) => rows.map((row) => row.id === key.id ? confirmed : row));
    } catch (e) { if (active.current) setError(errorMessage(e)); }
    finally { if (active.current) setBusy(""); }
  }
  return <>
    <PageTitle {...pageHeading("keys")} actions={<button disabled={!!busy || loading} onClick={() => { setExportTarget(null); setUsage(""); setRevision((v) => v + 1); }}>Refresh keys</button>} />
    <p className="notice">Disabling a key prevents new selection. It does not remove authorized_keys entries on existing guests or revoke copies already exported.</p>
    {user.role === "admin" && <GenerateKey disabled={loading || !!busy} onGenerated={(_key, rows) => { setKeys(rows); setError(""); }} />}
    {error && <ErrorNotice message={error} />}
    {loading ? <LoadingState label="Loading SSH keys" /> : !error && !keys.length ? <Empty title="No SSH keys yet." icon={<KeyRound size={27} />}>Generate a key to give guests access without sharing a password.</Empty> : <div className="table-wrap panel"><table>
      <thead><tr><th>Name</th><th>Fingerprint / public key</th><th>Status</th><th>Created</th><th>Usage / actions</th></tr></thead>
      <tbody>{keys.map((key) => <tr key={key.id}>
        <td>{key.name}<small>{key.algorithm}</small></td><td><code>{key.fingerprint}</code><pre>{key.public_key}</pre></td>
        <td><Badge>{key.enabled ? "Enabled" : "Disabled"}</Badge></td><td>{date(key.created_at)}<small>{key.created_by}</small></td>
        <td><button onClick={() => { setExportTarget(null); setUsage(key.id); }}>View usage</button>{user.role === "admin" && <>
          <button disabled={!!busy} onClick={() => toggle(key)}>{key.enabled ? "Disable" : "Enable"}</button>
          <button disabled={!!busy} onClick={() => { setUsage(""); setExportTarget(key); }}>Export private key</button>
        </>}</td>
      </tr>)}</tbody>
    </table></div>}
    {usage && <KeyUsage key={usage} id={usage} />}
    {exportTarget && <ExportDialog key={exportTarget.id} target={exportTarget} onClose={() => setExportTarget(null)} />}
  </>;
}
