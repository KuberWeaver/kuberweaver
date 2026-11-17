import { useEffect, useRef, useState } from "react";
import type { ComponentProps } from "react";
import { api } from "./api";
import type { Schema } from "./model";
import { GenerateKey } from "./Keys";
import { readKeys } from "./sshKeys";
import type { SshKey } from "./sshKeys";
import { date } from "./model";
import { SchemaFields } from "./SchemaFields";
import { ErrorNotice, errorMessage } from "./ui";

type Option = { id: string; name: string };
type Connection = {
  id: string;
  label: string;
  enabled: boolean;
  revision: number;
  observation: null | {
    revision: number;
    status: "reachable" | "unavailable";
    observed_at: string;
    inventory: Record<string, unknown> | null;
  };
};

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const revision = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
function readConnections(value: unknown): Connection[] {
  if (!Array.isArray(value) || value.some((entry) => {
    if (!record(entry) || !text(entry.id) || !text(entry.label) || typeof entry.enabled !== "boolean" || !revision(entry.revision)) return true;
    const observed = entry.observation;
    return observed !== null && (!record(observed) || !revision(observed.revision) ||
      (observed.status !== "reachable" && observed.status !== "unavailable") ||
      !text(observed.observed_at) || !Number.isFinite(Date.parse(observed.observed_at)) ||
      !(record(observed.inventory) || (observed.status === "unavailable" && observed.inventory === null)));
  }) || new Set(value.map((entry: Connection) => entry.id)).size !== value.length) {
    throw new Error("Invalid connections response. Options unavailable.");
  }
  return value as Connection[];
}

function readOptions(value: unknown): Option[] | null {
  if (!Array.isArray(value) || value.some((entry) => !record(entry) || !text(entry.id) || !text(entry.name)) ||
    new Set(value.map((entry: Option) => entry.id)).size !== value.length) return null;
  return value as Option[];
}

function hasOptions(schema: Schema, match: (source: string) => boolean = () => true): boolean {
  return (schema["x-options-source"] !== undefined && match(schema["x-options-source"])) ||
    Boolean(schema.items && hasOptions(schema.items, match)) ||
    Object.values(schema.properties || {}).some((field) => hasOptions(field, match));
}

export function DiscoverySchemaFields(props: ComponentProps<typeof SchemaFields> & { canManageKeys?: boolean }) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [keys, setKeys] = useState<SshKey[]>([]);
  const [keyError, setKeyError] = useState("");
  const [keysLoading, setKeysLoading] = useState(false);
  const keyRevision = useRef(0);
  const needsKeys = hasOptions(props.schema, (source) => source === "ssh-keys");
  const needsConnections = hasOptions(props.schema, (source) => source !== "ssh-keys");
  const [error, setError] = useState("");
  const annotated = hasOptions(props.schema);
  useEffect(() => {
    if (!needsConnections) return;
    let active = true;
    setError("");
    setConnections([]);
    api<unknown>("/connections").then((result) => {
      if (active) setConnections(readConnections(result));
    }).catch((e: unknown) => { if (active) setError(errorMessage(e)); });
    return () => { active = false; };
  }, [needsConnections]);
  useEffect(() => {
    if (!needsKeys) return;
    let active = true;
    const requestedRevision = ++keyRevision.current;
    setKeys([]); setKeyError(""); setKeysLoading(true);
    api<unknown>("/ssh-keys").then((value) => { const rows = readKeys(value); if (active && requestedRevision === keyRevision.current) setKeys(rows); })
      .catch((e) => { if (active && requestedRevision === keyRevision.current) setKeyError(errorMessage(e)); })
      .finally(() => { if (active && requestedRevision === keyRevision.current) setKeysLoading(false); });
    return () => { active = false; };
  }, [needsKeys]);
  if (!annotated) return <SchemaFields {...props} />;
  const selected = connections.find((entry) => entry.id === props.value.connection_id);
  const observed = selected?.observation;
  const inventory = selected?.enabled && observed?.status === "reachable" && observed.revision === selected.revision
    ? observed.inventory : undefined;
  let unavailable = false;
  const bindingErrors = new Set<string>();
  const withOptions = (field: Schema): Schema => {
    const source = field["x-options-source"];
    if (source === undefined) return field;
    let options: Option[] = [];
    if (source === "ssh-keys") {
      options = keys.filter((key) => key.enabled).map((key) => ({ id: key.id, name: `${key.name} · ${key.fingerprint}` }));
    } else if (source === "connections") {
      options = connections.filter((entry) => entry.enabled).map((entry) => ({ id: entry.id, name: entry.label }));
    } else if (["datastores", "networks", "resource_pools"].includes(source)) {
      const discovered = inventory ? readOptions(inventory[source]) : [];
      if (discovered === null) bindingErrors.add(`Inventory ${source} invalid. Options unavailable.`);
      else options = discovered;
    } else {
      bindingErrors.add(`Unknown options source: ${source}.`);
    }
    if (!options.length && source !== "ssh-keys") unavailable = true;
    return { ...field, enum: options.map((option) => option.id), enumNames: options.map((option) => option.name) };
  };
  const resolve = (field: Schema): Schema => {
    const resolved = withOptions(field);
    return {
      ...resolved,
      ...(field.items ? { items: resolve(field.items) } : {}),
      ...(field.properties ? { properties: Object.fromEntries(Object.entries(field.properties).map(([key, child]) => [key, resolve(child)])) } : {}),
    };
  };
  const schema = resolve(props.schema);
  const change = (next: Record<string, unknown>) => {
    if (next.connection_id !== props.value.connection_id) {
      const reset = (field: Schema, values: Record<string, unknown>): Record<string, unknown> =>
        Object.fromEntries(Object.entries(field.properties || {}).map(([key, child]) => {
          const source = child["x-options-source"] ?? child.items?.["x-options-source"];
          if (source !== undefined && source !== "connections" && source !== "ssh-keys") return [key, child.type === "array" ? [] : ""];
          if (child.type === "object") return [key, reset(child, record(values[key]) ? values[key] : {})];
          return [key, values[key]];
        }).filter(([, value]) => value !== undefined));
      props.onChange({ ...next, ...reset(props.schema, next) });
    } else props.onChange(next);
  };
  return <>
    {needsConnections && error && <ErrorNotice message={error} />}
    {needsKeys && keyError && <ErrorNotice message={keyError} />}
    {needsKeys && !keysLoading && !keyError && !keys.some((key) => key.enabled) && <p className="muted">No enabled SSH keys available. Ask an administrator to generate a key.</p>}
    {[...bindingErrors].map((message) => <ErrorNotice key={message} message={message} />)}
    {unavailable && needsConnections && <p className="muted">Options unavailable. Open Connections and run explicit discovery.</p>}
    {observed?.observed_at && <p className="muted">Last observation: <time dateTime={observed.observed_at}>{date(observed.observed_at)}</time></p>}
    <SchemaFields {...props} schema={schema} onChange={change} fieldAction={(field, update) => field["x-options-source"] === "ssh-keys" && props.canManageKeys ? <GenerateKey disabled={props.disabled} onGenerated={(key, rows) => { ++keyRevision.current; setKeys(rows); setKeysLoading(false); setKeyError(""); update(key.id); }} /> : null} />
  </>;
}
