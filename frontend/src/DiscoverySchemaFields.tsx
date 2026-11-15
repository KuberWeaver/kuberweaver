import { useEffect, useState } from "react";
import type { ComponentProps } from "react";
import { api } from "./api";
import type { Schema } from "./model";
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
    throw new Error("Réponse de connexions invalide. Options indisponibles.");
  }
  return value as Connection[];
}

function readOptions(value: unknown): Option[] | null {
  if (!Array.isArray(value) || value.some((entry) => !record(entry) || !text(entry.id) || !text(entry.name)) ||
    new Set(value.map((entry: Option) => entry.id)).size !== value.length) return null;
  return value as Option[];
}

function hasOptions(schema: Schema): boolean {
  return schema["x-options-source"] !== undefined ||
    Boolean(schema.items && hasOptions(schema.items)) ||
    Object.values(schema.properties || {}).some(hasOptions);
}

export function DiscoverySchemaFields(props: ComponentProps<typeof SchemaFields>) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [error, setError] = useState("");
  const annotated = hasOptions(props.schema);
  useEffect(() => {
    if (!annotated) return;
    let active = true;
    setError("");
    setConnections([]);
    api<unknown>("/connections").then((result) => {
      if (active) setConnections(readConnections(result));
    }).catch((e: unknown) => { if (active) setError(errorMessage(e)); });
    return () => { active = false; };
  }, [annotated]);
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
    if (source === "connections") {
      options = connections.filter((entry) => entry.enabled).map((entry) => ({ id: entry.id, name: entry.label }));
    } else if (["datastores", "networks", "resource_pools"].includes(source)) {
      const discovered = inventory ? readOptions(inventory[source]) : [];
      if (discovered === null) bindingErrors.add(`Inventaire ${source} invalide. Options indisponibles.`);
      else options = discovered;
    } else {
      bindingErrors.add(`Source d’options inconnue : ${source}.`);
    }
    if (!options.length) unavailable = true;
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
  return <>
    {error && <ErrorNotice message={error} />}
    {[...bindingErrors].map((message) => <ErrorNotice key={message} message={message} />)}
    {unavailable && <p className="muted">Options indisponibles. Ouvrez Connexions et lancez une découverte explicite.</p>}
    {observed?.observed_at && <p className="muted">Dernière observation : <time dateTime={observed.observed_at}>{date(observed.observed_at)}</time></p>}
    <SchemaFields {...props} schema={schema} />
  </>;
}
