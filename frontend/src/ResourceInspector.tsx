import { useState } from "react";
import { ExternalLink, Trash2, X } from "lucide-react";
import type { Graph, GraphNode, Plugin } from "./model";
import { defaultsFor, sortPlugins } from "./model";
import { DiscoverySchemaFields } from "./DiscoverySchemaFields";
import { ErrorNotice, Spinner } from "./ui";
import { parseResourceVariables, referenceDirectories, resourceKind, resourceKinds, wouldCycle } from "./resourceWorkspace";
import type { ResourceKind } from "./resourceWorkspace";

export function ResourceInspector({ node, graph, plugins, loading, error, retry, editable, admin, update, dependency, close, remove }: {
  node: GraphNode; graph: Graph; plugins: Plugin[]; loading: boolean; error: string; retry: () => void;
  editable: boolean; admin: boolean; update: (patch: Partial<GraphNode>) => void;
  dependency: (source: string, enabled: boolean) => void; close: () => void; remove: () => void;
}) {
  const plugin = plugins.find((p) => p.id === node.plugin_id);
  const [filter, setFilter] = useState<ResourceKind | "">(plugin ? resourceKind(plugin) : "");
  const [query, setQuery] = useState("");
  const [json, setJson] = useState("");
  const [notice, setNotice] = useState("");
  const [importError, setImportError] = useState("");
  const sources = filter ? referenceDirectories[filter] : [];
  const options = sortPlugins(plugins).filter((p) => (!filter || resourceKind(p) === filter) &&
    [p.name, p.engine, p.id, p.service?.image, p.implementation?.role, p.description].join(" ").toLowerCase().includes(query.trim().toLowerCase()));
  const choices = plugin && !options.some((p) => p.id === plugin.id) ? [plugin, ...options] : options;

  return <aside className="resource-inspector panel" aria-labelledby="resource-inspector-heading">
    <div className="panel-title">
      <div><span className="eyebrow">RESOURCE CONFIGURATION</span><h3 id="resource-inspector-heading">{node.label || "Resource"}</h3></div>
      <button className="icon-button" aria-label="Close inspector" onClick={close}><X size={17} /></button>
    </div>
    <div className="field"><label htmlFor="node-label">Resource name</label>
      <input id="node-label" value={node.label} disabled={!editable} onChange={(e) => update({ label: e.target.value })} />
    </div>
    <section className="resource-section">
      <h4>Type & origin</h4>
      <div className="resource-types" aria-label="Filter by resource type">
        {([{ id: "", label: "All" }, ...resourceKinds] as const).map(({ id, label }) =>
          <button key={id} type="button" aria-pressed={filter === id} onClick={() => { setFilter(id); setQuery(""); }}>{label}</button>)}
      </div>
      {sources.length > 0 && <div className="resource-sources">{sources.map((source) =>
        <a key={source.url} href={source.url} target="_blank" rel="noreferrer">{source.label} <ExternalLink size={13} /></a>)}</div>}
      {loading ? <Spinner /> : error ? <ErrorNotice message={error} retry={retry} /> : <>
        <div className="field"><label htmlFor="resource-search">Search adapters and sources</label>
          <input id="resource-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Name, image, role, provider…" />
        </div>
        <div className="field"><label htmlFor="resource-adapter">Deployment adapter</label>
          <select id="resource-adapter" value={node.plugin_id} disabled={!editable} onChange={(e) => {
            const next = plugins.find((p) => p.id === e.target.value && p.capability === "live");
            if (!next) return;
            if (plugin && plugin.id !== next.id && !window.confirm("Replace this adapter and reset its configuration?")) return;
            update({ plugin_id: next.id, label: node.label === "New resource" ? next.name : node.label, config: defaultsFor(next) });
            setFilter(resourceKind(next)); setQuery(""); setJson(""); setNotice(""); setImportError("");
          }}>
            <option value="" disabled>Choose an adapter</option>
            {node.plugin_id && !plugin && <option value={node.plugin_id} disabled>{node.plugin_id} — missing</option>}
            {choices.map((p) => <option key={p.id} value={p.id} disabled={p.capability !== "live"}>
              {p.name} · {p.engine}{p.capability !== "live" ? " (unavailable)" : ""}
            </option>)}
          </select>
        </div>
        {!options.some((p) => p.capability === "live") && <p className="muted">No available adapter matches this view. Try All or enable a compatible integration.</p>}
        {choices.filter((p) => p.capability !== "live").map((p) => <p className="muted" key={p.id}><strong>{p.name} — unavailable.</strong> {p.description}</p>)}
      </>}
      {plugin && <div className="resource-source-summary">
        <strong>{plugin.name} · {plugin.engine}</strong>
        <span>{plugin.description}</span>
        {plugin.service?.image && <span>Image: <code>{plugin.service.image}</code></span>}
        {plugin.implementation?.role && <span>Role: <code>{plugin.implementation.role}{plugin.implementation.version && ` @ ${plugin.implementation.version}`}</code></span>}
        {plugin.implementation?.source && <span>Origin: <code>{plugin.implementation.source}</code></span>}
        {plugin.iac?.template && <span>Template: <code>{plugin.iac.template}</code></span>}
      </div>}
      <p className="muted">External directories are for browsing. Only enabled adapters can deploy their declared sources; links do not install anything.</p>
    </section>
    {plugin && <section className="resource-section">
      <h4>Specifications & settings</h4>
      <DiscoverySchemaFields key={plugin.id} canManageKeys={admin} schema={plugin.schema} value={node.config} onChange={(config) => update({ config })} disabled={!editable} prefix={`node-${node.id}`} />
      {editable && <details className="resource-import">
        <summary>Import configuration values (JSON)</summary>
        <p className="muted">Paste a JSON object with fields from this adapter, for example values from your own configuration, OpenTofu variables or Ansible vars. This does not import modules, playbooks or secrets.</p>
        <div className="field"><label htmlFor="resource-file">Open a local JSON file</label>
          <input id="resource-file" type="file" accept=".json,application/json" onChange={async (e) => {
            const file = e.currentTarget.files?.[0];
            e.currentTarget.value = "";
            if (!file) return;
            setNotice(""); setImportError("");
            if (file.size > 65536) { setImportError("Configuration must be 64 KiB or less."); return; }
            try { setJson(await file.text()); }
            catch { setImportError("Could not read the configuration file."); }
          }} />
        </div>
        <label htmlFor="resource-json">Configuration JSON</label>
        <textarea id="resource-json" rows={5} maxLength={65536} value={json} onChange={(e) => setJson(e.target.value)} />
        {importError && <ErrorNotice message={importError} />}
        {notice && <p role="status">{notice}</p>}
        <button type="button" disabled={!json.trim()} onClick={() => {
          setNotice(""); setImportError("");
          try {
            const values = parseResourceVariables(json, plugin);
            update({ config: { ...node.config, ...values } });
            setJson(""); setNotice("Values copied to this resource. Validate the blueprint before saving.");
          } catch (e) { setImportError(e instanceof Error ? e.message : "Invalid configuration."); }
        }}>Apply values</button>
      </details>}
    </section>}
    <section className="resource-section resource-dependencies">
      <h4>Runs after</h4>
      <p className="muted">Select resources that must run before this one. Supported combinations are checked by the server.</p>
      {graph.nodes.length < 2 && <p className="muted">Add a second resource to define an order.</p>}
      {graph.nodes.filter((other) => other.id !== node.id).map((other) => {
        const checked = graph.edges.some((e) => e.target === node.id && e.source === other.id);
        const cyclic = !checked && wouldCycle(graph, other.id, node.id);
        return <label key={other.id}><input type="checkbox" checked={checked} disabled={!editable || cyclic} onChange={(e) => dependency(other.id, e.target.checked)} />{other.label}{cyclic ? " (would create a cycle)" : ""}</label>;
      })}
    </section>
    {editable && <button className="danger subtle full" onClick={remove}><Trash2 size={15} />Remove resource</button>}
  </aside>;
}
