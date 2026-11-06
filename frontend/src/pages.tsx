import { useState, useEffect } from "react";
import { ReactFlow, Background, Controls, MarkerType } from "@xyflow/react";
import {
  Plus,
  Search,
  RefreshCw,
  ShieldCheck,
  CheckCircle2,
  XCircle,
  RotateCcw,
  Terminal,
  Clock,
  Network,
  GitBranch,
  LockKeyhole,
  Activity,
  Info,
  Package,
  Save,
  ChevronRight,
  ChartNoAxesCombined,
} from "lucide-react";
import type { User, Plugin, Job } from "./model";
import { money, date, statuses, mayEdit, canRollback } from "./model";
import { api } from "./api";
import { capabilityState } from "./capabilities";
import { DeploymentCost } from "./DeploymentCost";
import { ProviderConfiguration } from "./ProviderConfiguration";
import {
  useResource,
  PageTitle,
  ResourceBoundary,
  Empty,
  Badge,
  PluginIcon,
  Notice,
  Details,
  ErrorNotice,
  Spinner,
  errorMessage,
} from "./ui";

export function Catalogue({
  plugins,
  loading,
  error,
  refresh,
  user,
  onCanvas,
}: {
  plugins: Plugin[];
  loading: boolean;
  error: string;
  refresh: () => void;
  user: User;
  onCanvas: () => void;
}) {
  const [query, setQuery] = useState(""),
    [category, setCategory] = useState("all"),
    [selected, setSelected] = useState<Plugin | null>(null);
  const categories = [...new Set(plugins.map((p) => p.category))];
  const filtered = plugins.filter(
    (p) =>
      (category === "all" || p.category === category) &&
      `${p.name} ${p.description}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <>
      <PageTitle
        eyebrow="EXTENSIONS & COMPOSANTS"
        title="Un catalogue, pas une limite."
        description="Des capacités déclarées par les plugins. Un formulaire généré depuis chaque schéma."
      />
      <Notice>
        Seules les extensions exécutables sur une cible présente dans le
        laboratoire peuvent être ajoutées au canvas.
      </Notice>
      <div className="list-toolbar">
        <div className="search-input">
          <Search size={16} />
          <input
            aria-label="Rechercher dans le catalogue"
            placeholder="Rechercher une extension…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <label className="inline-label">
          Catégorie
          <select
            aria-label="Filtrer par catégorie"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="all">Toutes les catégories</option>
            {categories.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <button
          className="icon-button"
          aria-label="Actualiser le catalogue"
          onClick={refresh}
        >
          <RefreshCw size={17} />
        </button>
      </div>
      <ResourceBoundary loading={loading} error={error} retry={refresh}>
        <div className="catalogue-grid">
          {filtered.map((p) => (
            <article
              className={`catalogue-card ${p.capability === "planned" ? "planned" : ""}`}
              key={p.id}
            >
              <div className="catalogue-card-top">
                <span className="plugin-icon large">
                  <PluginIcon name={p.name} size={26} />
                </span>
                <Badge tone={p.capability === "live" ? "success" : "neutral"}>
                  {p.capability === "live"
                    ? "Disponible dans le lab"
                    : "Planifié"}
                </Badge>
              </div>
              <span className="eyebrow">{p.category}</span>
              <h2>
                {p.name}
                <small>v{p.version}</small>
              </h2>
              <p>{p.description}</p>
              <div className="catalogue-meta">
                <span>{p.engine || "Provider non configuré"}</span>
                <strong>
                  {money(p.price_monthly)}
                  <small> / mois</small>
                </strong>
              </div>
              {p.capability === "planned" && (
                <div className="planned-note">
                  <LockKeyhole size={13} />
                  Cible absente du laboratoire · non exécutable
                </div>
              )}
              <div className="catalogue-actions">
                <button onClick={() => setSelected(p)}>
                  Manifeste
                  <ChevronRight size={14} />
                </button>
                <button
                  className="primary"
                  disabled={!mayEdit(user.role) || p.capability !== "live"}
                  onClick={() => {
                    sessionStorage.setItem("kuberweaver.add", p.id);
                    onCanvas();
                  }}
                >
                  <Plus size={15} />
                  Ajouter
                </button>
              </div>
            </article>
          ))}
        </div>
        {!filtered.length && (
          <Empty title="Aucune extension correspondante">
            Modifiez votre recherche ou actualisez le registre.
          </Empty>
        )}
      </ResourceBoundary>
      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label={`Manifeste ${selected.name}`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="panel-title">
              <h2>{selected.name}</h2>
              <button
                onClick={() => setSelected(null)}
                aria-label="Fermer le manifeste"
              >
                Fermer
              </button>
            </div>
            <p>{selected.description}</p>
            <Details title="Schéma du formulaire" data={selected.schema} />
            <Details
              title="Ressources, sécurité et exécuteur"
              data={{
                resources: selected.resources,
                security: selected.security,
                executor: selected.executor,
                defaults: selected.defaults,
              }}
            />
          </section>
        </div>
      )}
    </>
  );
}

function JobStatus({ status }: { status: string }) {
  return (
    <Badge
      tone={
        status === "succeeded" || status === "rolled_back"
          ? "success"
          : status === "failed" ||
              status === "rollback_failed" ||
              status === "rejected"
            ? "danger"
            : status === "running" || status === "rolling_back"
              ? "info"
              : status === "pending_approval"
                ? "warning"
                : "neutral"
      }
    >
      {statuses[status] || status}
    </Badge>
  );
}
export function Jobs({ user }: { user: User }) {
  const jobs = useResource<Job[]>("/jobs", 2000);
  const [selected, setSelected] = useState<string | null>(() =>
    sessionStorage.getItem("kuberweaver.job"),
  );
  return (
    <>
      <PageTitle
        eyebrow="ORCHESTRATION & GITOPS"
        title="Déploiements"
        description="De l’approbation au rollback : chaque étape laisse une trace."
        actions={
          <button onClick={jobs.refresh}>
            <RefreshCw size={15} />
            Actualiser
          </button>
        }
      />
      <ResourceBoundary
        loading={jobs.loading}
        error={jobs.error}
        retry={jobs.refresh}
      >
        {!jobs.data?.length ? (
          <Empty
            title="Aucun déploiement pour le moment"
            icon={<GitBranch size={28} />}
          >
            Validez un blueprint sur le canvas puis soumettez-le à approbation.
          </Empty>
        ) : (
          <div className="jobs-layout">
            <section className="panel jobs-list">
              <div className="panel-title">
                <h3>Historique des demandes</h3>
                <Badge>{jobs.data.length}</Badge>
              </div>
              {jobs.data.map((j) => (
                <button
                  key={j.id}
                  className={`job-row ${selected === j.id ? "selected" : ""}`}
                  onClick={() => {
                    setSelected(j.id);
                    sessionStorage.setItem("kuberweaver.job", j.id);
                  }}
                >
                  <div className="job-row-title">
                    <strong>
                      {j.name || j.graph?.name || j.id.slice(0, 8)}
                    </strong>
                    <JobStatus status={j.status} />
                  </div>
                  <div className="job-row-meta">
                    <span>{j.environment}</span>
                    <span>{date(j.created_at)}</span>
                  </div>
                  <small>{j.created_by}</small>
                </button>
              ))}
            </section>
            {selected ? (
              <JobDetail id={selected} user={user} onChange={jobs.refresh} />
            ) : (
              <section className="panel">
                <Empty title="Sélectionnez une demande">
                  Consultez ses phases, ses événements et son état GitOps.
                </Empty>
              </section>
            )}
          </div>
        )}
      </ResourceBoundary>
    </>
  );
}
function JobDetail({
  id,
  user,
  onChange,
}: {
  id: string;
  user: User;
  onChange: () => void;
}) {
  const r = useResource<Job>(`/jobs/${encodeURIComponent(id)}`, 2000),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  useEffect(() => {
    setError("");
    setMessage("");
  }, [id]);
  async function action(name: string) {
    if (
      name === "rollback" &&
      !window.confirm("Restaurer la version précédente de ce déploiement ?")
    )
      return;
    setBusy(name);
    setError("");
    setMessage("");
    try {
      await api(`/jobs/${encodeURIComponent(id)}/${name}`, { method: "POST" });
      const exact = await api<Job>(`/jobs/${encodeURIComponent(id)}`);
      r.setData(exact);
      onChange();
      setMessage(
        name === "approve"
          ? "Demande approuvée. Suivi du déploiement en cours."
          : name === "reject"
            ? "Demande refusée."
            : "Restauration demandée. Suivi en cours.",
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  const j = r.data;
  const groups = [...new Set((j?.events || []).map((e) => e.phase))];
  return (
    <section className="panel job-detail">
      <ResourceBoundary
        loading={r.loading && !j}
        error={r.error}
        retry={r.refresh}
      >
        {j && (
          <>
            <div className="panel-title">
              <div>
                <div className="eyebrow">DEMANDE {j.id.slice(0, 8)}</div>
                <h2>{j.name || j.graph?.name || "Déploiement"}</h2>
              </div>
              <JobStatus status={j.status} />
            </div>
            <dl className="definition-grid">
              <div>
                <dt>Environnement</dt>
                <dd>{j.environment}</dd>
              </div>
              <div>
                <dt>Demandé par</dt>
                <dd>{j.created_by}</dd>
              </div>
              <div>
                <dt>Approbation</dt>
                <dd>{j.approved_by || "Non approuvé"}</dd>
              </div>
              <div>
                <dt>Commit GitOps</dt>
                <dd className="mono">
                  {j.git_commit ? (
                    <span title={j.git_commit}>
                      {j.git_commit.slice(0, 12)}
                    </span>
                  ) : (
                    "Pas de commit publié"
                  )}
                </dd>
              </div>
            </dl>
            {error && <ErrorNotice message={error} />}{" "}
            {message && (
              <div className="notice success" role="status">
                <CheckCircle2 size={16} />
                {message}
              </div>
            )}
            {j.error && (
              <ErrorNotice
                message={
                  typeof j.error === "string"
                    ? j.error
                    : JSON.stringify(j.error)
                }
              />
            )}
            <DeploymentCost cost={j.cost} />
            {j.status === "pending_approval" && (
              <div className="approval-box">
                <ShieldCheck size={22} />
                <div>
                  <strong>Une approbation est requise</strong>
                  <p>
                    {user.role === "admin"
                      ? "Vérifiez le blueprint et les règles avant de poursuivre."
                      : "Un administrateur doit approuver cette demande."}
                  </p>
                </div>
                {user.role === "admin" && (
                  <div className="approval-actions">
                    <button
                      className="danger"
                      disabled={!!busy}
                      onClick={() => action("reject")}
                    >
                      <XCircle size={15} />
                      Refuser
                    </button>
                    <button
                      className="primary"
                      disabled={!!busy}
                      onClick={() => action("approve")}
                    >
                      <CheckCircle2 size={15} />
                      {busy === "approve" ? "Approbation…" : "Approuver"}
                    </button>
                  </div>
                )}
              </div>
            )}
            <h3 className="section-heading">
              <Activity size={16} />
              Phases observées
            </h3>
            {!groups.length ? (
              <p className="muted">
                Aucune phase exécutée. Les événements apparaîtront ici.
              </p>
            ) : (
              <div className="execution-phases">
                {groups.map((phase, i) => {
                  const events = j.events.filter((e) => e.phase === phase);
                  const last = events[events.length - 1];
                  return (
                    <div key={phase}>
                      <span
                        className={`phase-number ${last.level === "error" ? "phase-error" : ""}`}
                      >
                        {i + 1}
                      </span>
                      <strong>{phase}</strong>
                      <small>
                        {events.length} événement{events.length > 1 ? "s" : ""}
                      </small>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="logs-heading">
              <h3>
                <Terminal size={16} />
                Journal d’exécution
              </h3>
              <span>
                <Clock size={12} />
                Actualisation toutes les 2 s
              </span>
            </div>
            <div
              className="log-console"
              role="log"
              aria-label="Journal d’exécution"
            >
              {!j.events?.length ? (
                <span className="log-empty">
                  En attente du premier événement…
                </span>
              ) : (
                j.events.map((e, i) => (
                  <div className={`log-line ${e.level}`} key={e.id || i}>
                    <time>{date(e.time)}</time>
                    <span className="log-phase">[{e.phase}]</span>
                    <span>{e.message}</span>
                  </div>
                ))
              )}
            </div>
            <Details title="Blueprint demandé" data={j.graph} />
            {j.result && (
              <Details title="Résultat de l’exécution" data={j.result} />
            )}{" "}
            {user.role === "admin" &&
              canRollback(j) && (
                <div className="rollback-box">
                  <div>
                    <strong>Restaurer l’état précédent</strong>
                    <p>
                      Le rollback est journalisé et appliqué au périmètre de
                      cette demande.
                    </p>
                  </div>
                  <button
                    className="danger"
                    disabled={!!busy}
                    onClick={() => action("rollback")}
                  >
                    <RotateCcw size={15} />
                    {busy === "rollback" ? "Demande…" : "Rollback manuel"}
                  </button>
                </div>
              )}
          </>
        )}
      </ResourceBoundary>
    </section>
  );
}

export function Infrastructure() {
  const r = useResource("/infrastructure");
  const data = r.data;
  const nodes = (data?.nodes || []).map((n: any, i: number) => ({
    id: n.id || n.node_id,
    position: n.position || {
      x: typeof n.x === 'number' ? n.x : 50 + (i % 3) * 240,
      y: typeof n.y === 'number' ? n.y : 50 + Math.floor(i / 3) * 150,
    },
    data: {
      label: (
        <div className="infra-node">
          <Network size={20} />
          <strong>{n.name}</strong>
          <span>
            {n.type || n.node_type} · {n.status}
          </span>
        </div>
      ),
    },
    style: {
      background: "#fff",
      border: "1px solid #b8cbd0",
      borderRadius: 9,
      padding: 14,
      width: 200,
    },
  }));
  const links = (data?.links || [])
    .map((l: any, i: number) => ({
      id: l.id || l.link_id || `link-${i}`,
      source: l.source || l.nodes?.[0]?.node_id,
      target: l.target || l.nodes?.[1]?.node_id,
      markerEnd: { type: MarkerType.ArrowClosed },
    }))
    .filter((l: any) => l.source && l.target);
  return (
    <>
      <PageTitle
        eyebrow="OBSERVABILITÉ DU LABORATOIRE"
        title="Infrastructure réelle"
        description="La topologie GNS3 observée, distincte de l’état désiré de vos blueprints."
        actions={
          <button onClick={r.refresh}>
            <RefreshCw size={15} />
            Actualiser la topologie
          </button>
        }
      />
      <Notice>
        Lecture seule : cette vue ne crée, ne démarre et ne modifie aucun
        équipement GNS3.
      </Notice>
      <ResourceBoundary loading={r.loading} error={r.error} retry={r.refresh}>
        {data && (
          <>
            <div className="infra-meta panel">
              <div>
                <span className="eyebrow">SOURCE</span>
                <strong>{data.source || "GNS3"}</strong>
              </div>
              <div>
                <span className="eyebrow">PROJET</span>
                <strong className="mono">
                  {data.project_id || "Non configuré"}
                </strong>
              </div>
              <div>
                <span className="eyebrow">SERVEUR</span>
                <strong>{data.server_url || "Non configuré"}</strong>
              </div>
              <Badge>
                {nodes.length} nœud{nodes.length !== 1 ? "s" : ""}
              </Badge>
            </div>
            {data.errors?.map((e: any, i: number) => (
              <ErrorNotice
                key={i}
                message={
                  typeof e === "string" ? e : e.message || JSON.stringify(e)
                }
              />
            ))}
            {!nodes.length ? (
              <Empty
                title="Aucun équipement observable"
                icon={<Network size={28} />}
              >
                Vérifiez la configuration du provider GNS3 et l’accès au projet
                du laboratoire.
              </Empty>
            ) : (
              <>
                <section
                  className="infra-flow panel"
                  aria-label="Topologie GNS3"
                >
                  <ReactFlow
                    nodes={nodes}
                    edges={links}
                    fitView
                    nodesDraggable={false}
                    nodesConnectable={false}
                    deleteKeyCode={null}
                    proOptions={{ hideAttribution: true }}
                  >
                    <Background color="#d7dfe4" gap={22} />
                    <Controls showInteractive={false} />
                  </ReactFlow>
                </section>
                <div className="table-wrap panel">
                  <table>
                    <thead>
                      <tr>
                        <th>Équipement</th>
                        <th>Type</th>
                        <th>État observé</th>
                        <th>Identifiant</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.nodes.map((n: any) => (
                        <tr key={n.id || n.node_id}>
                          <td>
                            <strong>{n.name}</strong>
                          </td>
                          <td>{n.type || n.node_type}</td>
                          <td>
                            <Badge
                              tone={
                                n.status === "started" ? "success" : "neutral"
                              }
                            >
                              {n.status}
                            </Badge>
                          </td>
                          <td className="mono">{n.id || n.node_id}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </ResourceBoundary>
    </>
  );
}

function SettingFields({
  value,
  onChange,
  disabled,
  path = "policy",
}: {
  value: Record<string, any>;
  onChange: (v: Record<string, any>) => void;
  disabled: boolean;
  path?: string;
}) {
  return (
    <div className="settings-fields">
      {Object.entries(value).map(([key, v]) => {
        const id = `${path}-${key}`;
        const label = key.replaceAll("_", " ");
        return (
          <div className="setting-row" key={key}>
            <div>
              <label htmlFor={id}>{label}</label>
              <small>
                {Array.isArray(v)
                  ? "Liste autorisée · une valeur par ligne"
                  : typeof v === "number"
                    ? "Valeur numérique appliquée côté serveur"
                    : typeof v === "boolean"
                      ? "Règle appliquée à la validation des plans"
                      : "Configuration de la plateforme"}
              </small>
            </div>
            <div>
              {Array.isArray(v) ? (
                <textarea
                  id={id}
                  disabled={disabled}
                  rows={Math.min(5, Math.max(2, v.length))}
                  value={v.join("\n")}
                  onChange={(e) =>
                    onChange({ ...value, [key]: e.target.value.split("\n") })
                  }
                />
              ) : typeof v === "boolean" ? (
                <label className="toggle">
                  <input
                    id={id}
                    type="checkbox"
                    checked={v}
                    disabled={disabled}
                    onChange={(e) =>
                      onChange({ ...value, [key]: e.target.checked })
                    }
                  />
                  {v ? "Activé" : "Désactivé"}
                </label>
              ) : typeof v === "object" && v !== null ? (
                <SettingFields
                  value={v}
                  onChange={(next) => onChange({ ...value, [key]: next })}
                  disabled={disabled}
                  path={id}
                />
              ) : (
                <input
                  id={id}
                  disabled={disabled}
                  type={typeof v === "number" ? "number" : "text"}
                  step="any"
                  value={v ?? ""}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      [key]:
                        typeof v === "number"
                          ? Number(e.target.value)
                          : e.target.value,
                    })
                  }
                />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
export function Governance({ user }: { user: User }) {
  const r = useResource("/settings");
  const [draft, setDraft] = useState<any>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  useEffect(() => {
    if (r.data)
      setDraft(
        structuredClone({ policies: r.data.policies, pricing: r.data.pricing }),
      );
  }, [r.data]);
  async function save() {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const clean = (v: any): any =>
        Array.isArray(v)
          ? v
              .map((x) => (typeof x === "string" ? x.trim() : x))
              .filter((x) => x !== "")
          : v && typeof v === "object"
            ? Object.fromEntries(
                Object.entries(v).map(([k, val]) => [k, clean(val)]),
              )
            : v;
      await api("/settings", {
        method: "PATCH",
        body: JSON.stringify(clean(draft)),
      });
      const exact = await api("/settings");
      r.setData(exact);
      setSaved(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="POLITIQUES DE PLATEFORME"
        title="Des garde-fous, par défaut."
        description="Budget, images, exposition réseau et cibles : des règles explicites, contrôlées côté serveur."
        actions={
          user.role === "admin" ? (
            <button
              className="primary"
              disabled={busy || !draft}
              onClick={save}
            >
              <Save size={16} />
              {busy ? "Enregistrement…" : "Enregistrer les règles"}
            </button>
          ) : undefined
        }
      />
      <Notice>
        {user.role === "admin"
          ? "Les changements s’appliquent aux prochaines validations. Les listes autorisées sont saisies une valeur par ligne."
          : "Consultation des règles uniquement. Leur modification est réservée aux administrateurs."}
      </Notice>
      {error && <ErrorNotice message={error} />}{" "}
      {saved && (
        <div className="notice success" role="status">
          <CheckCircle2 size={17} />
          Paramètres enregistrés et relus depuis le serveur.
        </div>
      )}
      <ResourceBoundary loading={r.loading} error={r.error} retry={r.refresh}>
        {draft && (
          <>
            <section className="panel settings-panel">
              <div className="panel-title">
                <h2>
                  <ShieldCheck size={19} />
                  Politiques d’approbation
                </h2>
                <Badge>Contrôles serveur</Badge>
              </div>
              <SettingFields
                value={draft.policies || {}}
                onChange={(policies) => {
                  setSaved(false);
                  setDraft({ ...draft, policies });
                }}
                disabled={user.role !== "admin"}
              />
            </section>
            <section className="panel settings-panel">
              <div className="panel-title">
                <h2>
                  <ChartNoAxesCombined size={19} />
                  Tarification du laboratoire
                </h2>
                <Badge>EUR / mois</Badge>
              </div>
              <SettingFields
                value={draft.pricing || {}}
                onChange={(pricing) => {
                  setSaved(false);
                  setDraft({ ...draft, pricing });
                }}
                disabled={user.role !== "admin"}
              />
            </section>
          </>
        )}
      </ResourceBoundary>
    </>
  );
}

export function FinOps() {
  const r = useResource("/finops");
  const d = r.data;
  return (
    <>
      <PageTitle
        eyebrow="COÛTS & OPTIMISATION"
        title="La visibilité avant la dépense."
        description="Estimations du laboratoire basées sur les ressources déclarées et les tarifs configurés."
        actions={
          <button onClick={r.refresh}>
            <RefreshCw size={15} />
            Actualiser
          </button>
        }
      />
      <ResourceBoundary loading={r.loading} error={r.error} retry={r.refresh}>
        {d && (
          <>
            <div className="finops-summary">
              <section className="panel">
                <span className="eyebrow">TOTAL MENSUEL ESTIMÉ</span>
                <div className="finops-total">
                  {money(d.monthly_total)}
                  <small>/ mois</small>
                </div>
                <p className="muted">
                  Estimation en {d.currency || "EUR"}. Pas une facture
                  fournisseur.
                </p>
              </section>
              <section className="panel">
                <h3>Un coût compréhensible</h3>
                <p>
                  Les tarifs sont administrables dans Gouvernance. Les coûts
                  externes non collectés ne sont pas inclus.
                </p>
                <div className="notice">
                  <Info size={16} />
                  Aucune donnée de facturation cloud n’est simulée.
                </div>
              </section>
            </div>
            <section className="panel">
              <div className="panel-title">
                <h2>Répartition des coûts</h2>
                <Badge>{d.currency || "EUR"}</Badge>
              </div>
              {!d.breakdown?.length ? (
                <Empty title="Aucun coût à répartir">
                  Les ressources déployées apparaîtront ici.
                </Empty>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Ressource</th>
                        <th>Quantité</th>
                        <th>Estimation mensuelle</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.breakdown.map((row: any, i: number) => (
                        <tr key={i}>
                          <td>
                            {row.name ||
                              row.label ||
                              row.plugin_id ||
                              row.resource ||
                              row.category ||
                              `Ressource ${i + 1}`}
                          </td>
                          <td>{row.quantity ?? row.count ?? "—"}</td>
                          <td>
                            <strong>
                              {money(
                                row.monthly ??
                                  row.cost ??
                                  row.total ??
                                  row.amount,
                              )}
                            </strong>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <section className="panel recommendations">
              <h2>Recommandations</h2>
              {!d.recommendations?.length ? (
                <p className="muted">
                  Aucune recommandation issue des règles à ce stade.
                </p>
              ) : (
                d.recommendations.map((rec: any, i: number) => (
                  <div className="recommendation" key={i}>
                    <Info size={18} />
                    <div>
                      <strong>
                        {typeof rec === "string"
                          ? rec
                          : rec.title || rec.message}
                      </strong>
                      {rec.description && <p>{rec.description}</p>}
                      {rec.savings_monthly != null && (
                        <small>
                          Économie potentielle : {money(rec.savings_monthly)} /
                          mois
                        </small>
                      )}
                    </div>
                  </div>
                ))
              )}
              <Details data={d.pricing} title="Barème de calcul" />
            </section>
          </>
        )}
      </ResourceBoundary>
    </>
  );
}

export function Diagnostics({ user }: { user?: User } = {}) {
  const r = useResource("/diagnostics");
  const d = r.data;
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState("");
  async function runAnalysis() {
    if (running) return;
    setRunning(true); setRunError("");
    try { r.setData(await api("/diagnostics/run", { method: "POST" })); }
    catch (e) { setRunError(errorMessage(e)); }
    finally { setRunning(false); }
  }
  return (
    <>
      <PageTitle
        eyebrow="ANALYSE EN LECTURE SEULE"
        title="Diagnostic du laboratoire"
        description="Des constats expliqués. Aucune remédiation automatique, aucune modification de l’infrastructure."
        actions={
          <><button onClick={r.refresh} disabled={running}><RefreshCw size={15} />Actualiser le résultat</button>
          <button onClick={runAnalysis} disabled={running || user?.role === "viewer"}>{running ? "Analyse en cours…" : "Lancer une analyse explicite"}</button></>
        }
      />
      <div className="diagnostics-mode panel">
        <span className="plugin-icon">
          <Activity size={22} />
        </span>
        <div>
          <h3>{d?.mode === "llm" ? "Diagnostic IA" : "Moteur de règles"}</h3>
          <p>{d?.mode === "llm" ? `Fournisseur : ${d.provider || "llm"} · Modèle : ${d.model || "non communiqué"} · Exécution explicite uniquement.` : "Diagnostic déterministe · Pas de remédiation automatique."}</p>
          {d?.mode === "llm" && d.status === "not_run" && <p>Aucune analyse IA exécutée. Le chargement de cette page ne déclenche aucun appel modèle.</p>}
          {d?.generated_at && <small>Résultat produit : {date(d.generated_at)}</small>}
        </div>
        <Badge>Lecture seule</Badge>
      </div>
      {runError && <ErrorNotice message={runError} />}
      <ResourceBoundary loading={r.loading} error={r.error} retry={r.refresh}>
        {d && (
          <>
            {d.summary && (
              <section className="panel diagnostic-summary">
                <h3>Synthèse</h3>
                {typeof d.summary === "string" ? (
                  <p>{d.summary}</p>
                ) : (
                  <Details data={d.summary} title="Synthèse du fournisseur sélectionné" />
                )}
              </section>
            )}
            <section className="panel">
              <div className="panel-title">
                <h2>Constats</h2>
                <Badge>{d.findings?.length || 0}</Badge>
              </div>
              {!d.findings?.length ? (
                <Empty
                  title="Aucun constat remonté"
                  icon={<ShieldCheck size={28} />}
                >
                  Aucun constat n’est disponible dans ce résultat. Une analyse non exécutée ne prouve pas l’absence de problème ; aucun diagnostic ne constitue une garantie de sécurité exhaustive.
                </Empty>
              ) : (
                d.findings.map((f: any, i: number) => (
                  <article className="finding" key={f.id || i}>
                    <div>
                      <Badge
                        tone={
                          ["critical", "error", "high"].includes(f.severity)
                            ? "danger"
                            : f.severity === "warning"
                              ? "warning"
                              : "info"
                        }
                      >
                        {f.severity || "information"}
                      </Badge>
                      <h3>{f.title || f.code || "Constat"}</h3>
                    </div>
                    <p>{f.message || f.description}</p>
                    {f.resource && <small>Ressource : {f.resource}</small>}
                    {Array.isArray(f.citations) && f.citations.length > 0 && <p className="muted">Sources : {f.citations.map((c: any) => `${c.job_id} / événement ${c.event_id}`).join(", ")}</p>}
                    {f.recommendation && (
                      <div className="notice">
                        <Info size={15} />
                        {f.recommendation}
                      </div>
                    )}
                  </article>
                ))
              )}
            </section>
          </>
        )}
      </ResourceBoundary>
    </>
  );
}

export function Audit() {
  const r = useResource<any[]>("/audit");
  const [query, setQuery] = useState("");
  const entries = (r.data || []).filter((e) =>
    JSON.stringify(e).toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <>
      <PageTitle
        eyebrow="TRAÇABILITÉ"
        title="Journal d’audit"
        description="Qui a fait quoi, sur quelle ressource et à quel moment."
        actions={
          <button onClick={r.refresh}>
            <RefreshCw size={15} />
            Actualiser
          </button>
        }
      />
      <div className="list-toolbar">
        <div className="search-input">
          <Search size={15} />
          <input
            aria-label="Rechercher dans l’audit"
            placeholder="Acteur, action, ressource…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      <ResourceBoundary loading={r.loading} error={r.error} retry={r.refresh}>
        {!entries.length ? (
          <Empty title="Aucun événement d’audit">
            Les opérations de la plateforme seront tracées ici.
          </Empty>
        ) : (
          <div className="panel table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Acteur</th>
                  <th>Action</th>
                  <th>Ressource & détail</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.id}>
                    <td className="nowrap">{date(e.time)}</td>
                    <td>
                      <strong>{e.actor}</strong>
                    </td>
                    <td>
                      <Badge>{e.action}</Badge>
                    </td>
                    <td>
                      <span className="mono">{e.resource || "—"}</span>
                      {e.detail && (
                        <details>
                          <summary>Détail de l’événement</summary>
                          <pre>
                            {typeof e.detail === "string"
                              ? e.detail
                              : JSON.stringify(e.detail, null, 2)}
                          </pre>
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ResourceBoundary>
    </>
  );
}

export function Integrations({ user }: { user: User }) {
  const r = useResource<any[]>("/integrations");
  const extensions = useResource<any>("/extensions");
  const registry = Array.isArray(extensions.data)
    ? extensions.data
    : extensions.data?.extensions || extensions.data?.registry || [];
  return (
    <>
      <PageTitle
        eyebrow="NOYAU & FOURNISSEURS"
        title="Une plateforme qui s’étend."
        description="Les providers relient le noyau aux systèmes externes. Leur état est explicite, jamais supposé."
        actions={
          <button
            onClick={() => {
              r.refresh();
              extensions.refresh();
            }}
          >
            <RefreshCw size={15} />
            Actualiser les états
          </button>
        }
      />
      <Notice>
        Une capacité planifiée ou désactivée n’est pas une connexion active. La
        configuration des secrets et des endpoints se fait côté serveur.
      </Notice>
      <ResourceBoundary loading={r.loading} error={r.error} retry={r.refresh}>
        <div className="integrations-grid">
          {r.data?.map((integration: any, i: number) => {
            const state = capabilityState(integration);
            return (
              <section
                className="panel integration-card"
                key={integration.id || i}
              >
                <div className="panel-title">
                  <span className="plugin-icon">
                    <PluginIcon
                      name={integration.name || integration.id}
                      size={23}
                    />
                  </span>
                  <Badge tone={state.tone}>{state.label}</Badge>
                </div>
                <h2>{integration.name || integration.id}</h2>
                <p>
                  {integration.description ||
                    integration.reason ||
                    "Aucune description fournie par le provider."}
                </p>
                {integration.message && <p>{integration.message}</p>}
                {integration.reason && integration.description && (
                  <div className="notice">
                    <Info size={15} />
                    {integration.reason}
                  </div>
                )}
                <dl className="integration-details">
                  {integration.provider && (
                    <div>
                      <dt>Provider</dt>
                      <dd>{integration.provider}</dd>
                    </div>
                  )}
                  {integration.endpoint && (
                    <div>
                      <dt>Endpoint</dt>
                      <dd className="mono">{integration.endpoint}</dd>
                    </div>
                  )}
                  {integration.enabled !== undefined && (
                    <div>
                      <dt>Capacité</dt>
                      <dd>{integration.enabled ? "Activée" : "Désactivée"}</dd>
                    </div>
                  )}
                </dl>
              </section>
            );
          })}
        </div>
        {!r.data?.length && (
          <Empty title="Aucun provider déclaré">
            Le serveur ne retourne aucune intégration configurée.
          </Empty>
        )}
      </ResourceBoundary>
      <ProviderConfiguration user={user} />
      <section className="panel extension-panel">
        <div className="panel-title">
          <h2>
            <Package size={19} />
            Registre des extensions
          </h2>
          <Badge>Noyau extensible</Badge>
        </div>
        <p className="muted">
          L’activation des extensions définit les capacités disponibles dans ce
          portail.
        </p>
        {extensions.loading ? (
          <Spinner />
        ) : extensions.error ? (
          <div className="notice">
            <Info size={17} />
            <span>État du registre non disponible. {extensions.error}</span>
          </div>
        ) : Array.isArray(registry) && registry.length ? (
          <div className="extension-list">
            {registry.map((e: any, i: number) => (
              <div className="extension-row" key={e.id || i}>
                <span className="plugin-icon">
                  <Package size={18} />
                </span>
                <div>
                  <strong>{e.name || e.id}</strong>
                  <small>
                    {e.description || e.reason || e.version || e.module}
                  </small>
                </div>
                <Badge tone={capabilityState(e).tone}>
                  {capabilityState(e).label}
                </Badge>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">Aucune extension optionnelle installée. Les providers du noyau restent déclarés ci-dessous.</p>
        )}
        {extensions.data?.config_errors?.map((err: unknown, i: number) => <ErrorNotice key={i} message={typeof err === 'string' ? err : JSON.stringify(err)}/>)}
        {extensions.data?.providers?.length > 0 && <Details data={extensions.data.providers} title="Providers enregistrés dans le noyau"/>}
      </section>
    </>
  );
}
