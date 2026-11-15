import { useState, useEffect } from "react";
import type { FormEvent } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import {
  Workflow,
  LayoutDashboard,
  Package,
  Play,
  Network,
  Server,
  ShieldCheck,
  ChartNoAxesCombined,
  Stethoscope,
  History,
  Blocks,
  LogOut,
  Menu,
  X,
  ArrowRight,
  LockKeyhole,
  ChevronDown,
  Command,
  FlaskConical,
} from "lucide-react";
import { api, setCsrf, ApiError } from "./api";
import type { User, Plugin } from "./model";
import { Canvas } from "./Canvas";
import { Operations } from "./Operations";
import { AgentAccess } from "./AgentAccess";
import { Connections } from "./Connections";
import {
  Catalogue,
  Jobs,
  Infrastructure,
  Governance,
  FinOps,
  Diagnostics,
  Audit,
  Integrations,
} from "./pages";
import { ErrorNotice, Spinner, errorMessage, useResource } from "./ui";
const nav = [
  { id: "canvas", label: "Canvas", icon: LayoutDashboard },
  { id: "catalogue", label: "Catalogue", icon: Package },
  { id: "jobs", label: "Déploiements", icon: Play },
  { id: "infrastructure", label: "Infrastructure", icon: Network },
  { id: "connections", label: "Connexions", icon: Server },
  { id: "governance", label: "Gouvernance", icon: ShieldCheck },
  { id: "finops", label: "FinOps", icon: ChartNoAxesCombined },
  { id: "diagnostics", label: "Diagnostic", icon: Stethoscope },
  { id: "audit", label: "Audit", icon: History },
  { id: "integrations", label: "Intégrations", icon: Blocks },
  { id: "operations", label: "Opérations", icon: Command },
  { id: "agents", label: "Accès agents", icon: LockKeyhole },
];
export function App() {
  const [user, setUser] = useState<User | null>(null),
    [loading, setLoading] = useState(true),
    [bootError, setBootError] = useState(""),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setBootError("");
    api<{ user: User; csrf_token: string }>("/auth/me")
      .then((r) => {
        if (active) {
          setUser(r.user);
          setCsrf(r.csrf_token);
        }
      })
      .catch((e) => {
        if (active && (!(e instanceof ApiError) || e.status !== 401))
          setBootError(errorMessage(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [retry]);
  if (loading)
    return (
      <div className="boot">
        <Brand />
        <Spinner />
      </div>
    );
  if (!user)
    return (
      <Login
        error={bootError}
        onRetry={() => setRetry((v) => v + 1)}
        onLogin={(u) => setUser(u)}
      />
    );
  return (
    <Workspace
      user={user}
      onLogout={() => {
        setCsrf("");
        setUser(null);
      }}
    />
  );
}
function Brand() {
  return (
    <div className="brand">
      <span className="brand-symbol">
        <Workflow size={24} strokeWidth={1.7} />
      </span>
      <span>
        Kuber<span className="brand-light">Weaver</span>
        <small>INFRASTRUCTURE PLATFORM</small>
      </span>
    </div>
  );
}
function Login({
  error,
  onRetry,
  onLogin,
}: {
  error: string;
  onRetry: () => void;
  onLogin: (u: User) => void;
}) {
  const [busy, setBusy] = useState(false),
    [failure, setFailure] = useState("");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setFailure("");
    const values = new FormData(e.currentTarget);
    try {
      const r = await api<{ user: User; csrf_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username: values.get("username"),
          password: values.get("password"),
        }),
      });
      setCsrf(r.csrf_token);
      onLogin(r.user);
    } catch (e) {
      setFailure(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login">
      <aside className="login-story">
        <Brand />
        <div className="login-message">
          <div className="eyebrow">DE L’INTENTION À L’INFRASTRUCTURE</div>
          <h1>
            Composez.
            <br />
            Vérifiez.
            <br />
            <span>Déployez.</span>
          </h1>
          <p>
            Un noyau léger. Des extensions explicites.
            <br />
            Votre infrastructure, sous contrôle.
          </p>
          <div className="login-diagram">
            <div>
              <BoxMark />
              Blueprint
            </div>
            <span>→</span>
            <div>
              <ShieldCheck size={22} />
              Politiques
            </div>
            <span>→</span>
            <div>
              <Network size={22} />
              Laboratoire
            </div>
          </div>
        </div>
        <div className="login-foot">
          <FlaskConical size={16} /> Espace laboratoire · Opérations traçables
        </div>
      </aside>
      <main className="login-form">
        <div className="login-mobile-brand">
          <Brand />
        </div>
        <div className="login-card">
          <span className="login-lock">
            <LockKeyhole size={23} />
          </span>
          <h2>Bienvenue dans votre espace.</h2>
          <p>Connectez-vous pour accéder à la plateforme.</p>
          {error && <ErrorNotice message={error} retry={onRetry} />}
          <form onSubmit={submit}>
            <label htmlFor="username">Identifiant</label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              autoFocus
              required
              placeholder="Votre identifiant"
            />
            <label htmlFor="password">Mot de passe</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              placeholder="Votre mot de passe"
            />
            {failure && <ErrorNotice message={failure} />}
            <button className="primary login-submit" disabled={busy}>
              {busy ? "Connexion…" : "Se connecter"}
              <ArrowRight size={17} />
            </button>
          </form>
          <div className="login-hint">
            <ShieldCheck size={16} /> Accès nominatif · Droits définis par votre
            rôle
          </div>
        </div>
        <footer>
          KuberWeaver <span>Portail d’infrastructure modulaire</span>
        </footer>
      </main>
    </div>
  );
}
function BoxMark() {
  return <Command size={22} />;
}
function Workspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [page, setPage] = useState(
      () => window.location.hash.slice(1) || "canvas",
    ),
    [drawer, setDrawer] = useState(false),
    [logoutError, setLogoutError] = useState(""),
    [loggingOut, setLoggingOut] = useState(false);
  const plugins = useResource<Plugin[]>("/plugins");
  useEffect(() => {
    const sync = () => setPage(window.location.hash.slice(1) || "canvas");
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  const navigate = (id: string) => {
    setPage(id);
    window.location.hash = id;
    setDrawer(false);
  };
  async function logout() {
    setLoggingOut(true);
    try {
      await api("/auth/logout", { method: "POST" });
      onLogout();
    } catch (e) {
      setLogoutError(errorMessage(e));
    } finally {
      setLoggingOut(false);
    }
  }
  const current = nav.find((n) => n.id === page) || nav[0];
  return (
    <div className="shell">
      {drawer && (
        <button
          className="drawer-overlay"
          aria-label="Fermer la navigation"
          onClick={() => setDrawer(false)}
        />
      )}
      <aside className={`sidebar ${drawer ? "open" : ""}`}>
        <div className="sidebar-brand">
          <Brand />
          <button
            className="icon-button mobile-close"
            aria-label="Fermer le menu"
            onClick={() => setDrawer(false)}
          >
            <X size={20} />
          </button>
        </div>
        <div className="workspace-switch">
          <span className="workspace-avatar">L</span>
          <div>
            Laboratoire<small>Espace d’infrastructure</small>
          </div>
          <ChevronDown size={14} />
        </div>
        <div className="nav-caption">PLATEFORME</div>
        <nav aria-label="Navigation principale">
          {nav.map((n) => (
            <button
              key={n.id}
              className={`${page === n.id ? "active" : ""} ${n.id === "governance" ? "nav-separated" : ""}`}
              onClick={() => navigate(n.id)}
              aria-current={page === n.id ? "page" : undefined}
            >
              <n.icon size={18} />
              {n.label}
              {n.id === "integrations" && (
                <span className="nav-submark">+</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="kernel-label">
            <span /> Noyau modulaire
            <small>Capacités déclarées par les extensions</small>
          </div>
          <div className="user-block">
            <span className="user-avatar">
              {user.username.slice(0, 2).toUpperCase()}
            </span>
            <div>
              {user.username}
              <small>
                {user.role === "admin"
                  ? "Administrateur"
                  : user.role === "viewer"
                    ? "Lecture seule"
                    : "Opérateur"}
              </small>
            </div>
            <button
              className="icon-button"
              aria-label="Se déconnecter"
              title="Se déconnecter"
              disabled={loggingOut}
              onClick={logout}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-button mobile-menu"
              aria-label="Ouvrir le menu"
              onClick={() => setDrawer(true)}
            >
              <Menu size={22} />
            </button>
            <span className="breadcrumb-home">Plateforme</span>
            <span className="breadcrumb-slash">/</span>
            <strong>{current.label}</strong>
          </div>
          <div className="topbar-right">
            <span className="scope">
              <FlaskConical size={14} />
              Laboratoire
            </span>
            <span className="role-pill">{user.role}</span>
          </div>
        </header>
        <main className={`page page-${page}`}>
          {logoutError && <ErrorNotice message={logoutError} />}{" "}
          {page === "canvas" && (
            <ReactFlowProvider>
              <Canvas
                user={user}
                plugins={plugins.data || []}
                pluginsLoading={plugins.loading}
                pluginsError={plugins.error}
                retryPlugins={plugins.refresh}
                onJob={() => navigate("jobs")}
              />
            </ReactFlowProvider>
          )}
          {page === "catalogue" && (
            <Catalogue
              plugins={plugins.data || []}
              loading={plugins.loading}
              error={plugins.error}
              refresh={plugins.refresh}
              user={user}
              onCanvas={() => navigate("canvas")}
            />
          )}
          {page === "jobs" && <Jobs user={user} />}
          {page === "infrastructure" && <Infrastructure />}
          {page === "connections" && <Connections user={user} />}
          {page === "governance" && <Governance user={user} />}
          {page === "finops" && <FinOps />}
          {page === "diagnostics" && <Diagnostics user={user} />}
          {page === "audit" && <Audit />}
          {page === "integrations" && <Integrations user={user} />}
          {page === "operations" && <Operations user={user} />}
          {page === "agents" && <AgentAccess user={user} />}
          {!nav.some((n) => n.id === page) && (
            <div className="panel">
              Page introuvable.{" "}
              <button onClick={() => navigate("canvas")}>
                Revenir au canvas
              </button>
            </div>
          )}
        </main>
        <footer className="app-footer">
          <span>
            KuberWeaver <span className="footer-dot">·</span> Infrastructure par
            composition
          </span>
          <span>Laboratoire isolé · EUR / mois</span>
        </footer>
      </div>
    </div>
  );
}
