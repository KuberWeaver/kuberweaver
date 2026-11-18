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
  Bot,
  Command,
  Wrench,
  Store,
  PackageCheck,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api, setCsrf, ApiError } from "./api";
import type { User, Plugin } from "./model";
import { Canvas } from "./Canvas";
import { Operations } from "./Operations";
import { AgentAccess } from "./AgentAccess";
import { Keys } from "./Keys";
import { Connections } from "./Connections";
import { EngineeringStudio } from "./Studio";
import { MyServices, ServiceOfferings } from "./Offerings";
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
import { Empty, ErrorNotice, Spinner, errorMessage, useResource } from "./ui";
import { ThemeToggle } from "./ThemeToggle";
import {
  defaultPageId,
  navigationFor,
  pageFor,
  roleCanSee,
} from "./navigation";
import type { PageId } from "./navigation";

const pageIcons: Record<PageId, LucideIcon> = {
  canvas: LayoutDashboard,
  catalogue: Package,
  studio: Wrench,
  offerings: Store,
  services: PackageCheck,
  jobs: Play,
  infrastructure: Network,
  connections: Server,
  keys: LockKeyhole,
  governance: ShieldCheck,
  finops: ChartNoAxesCombined,
  diagnostics: Stethoscope,
  audit: History,
  integrations: Blocks,
  operations: Command,
  agents: Bot,
};
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
          <div className="eyebrow">INFRASTRUCTURE WORKSPACE</div>
          <h1>
            Compose.
            <br />
            Verify.
            <br />
            <span>Deploy.</span>
          </h1>
          <p>
            Blueprints, policies and deployments
            <br />
            in one auditable workspace.
          </p>
          <div className="login-diagram">
            <div>
              <BoxMark />
              Blueprint
            </div>
            <span>→</span>
            <div>
              <ShieldCheck size={22} />
              Policies
            </div>
            <span>→</span>
            <div>
              <Network size={22} />
              Workspace
            </div>
          </div>
        </div>
        <div className="login-foot">
          <ShieldCheck size={16} /> Infrastructure workspace · Auditable operations
        </div>
      </aside>
      <main className="login-form">
        <div className="login-theme">
          <ThemeToggle />
        </div>
        <div className="login-mobile-brand">
          <Brand />
        </div>
        <div className="login-card">
          <span className="login-lock">
            <LockKeyhole size={23} />
          </span>
          <h2>Welcome to your workspace.</h2>
          <p>Sign in to access the platform.</p>
          {error && <ErrorNotice message={error} retry={onRetry} />}
          <form onSubmit={submit}>
            <label htmlFor="username">Username</label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              autoFocus
              required
              placeholder="Your username"
            />
            <label htmlFor="password">Password</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              placeholder="Your password"
            />
            {failure && <ErrorNotice message={failure} />}
            <button className="primary login-submit" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
              <ArrowRight size={17} />
            </button>
          </form>
          <div className="login-hint">
            <ShieldCheck size={16} /> Individual access · Role-based permissions
          </div>
        </div>
        <footer>
          KuberWeaver <span>Modular infrastructure portal</span>
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
      () => window.location.hash.slice(1) || defaultPageId,
    ),
    [drawer, setDrawer] = useState(false),
    [logoutError, setLogoutError] = useState(""),
    [loggingOut, setLoggingOut] = useState(false);
  const plugins = useResource<Plugin[]>("/plugins");
  useEffect(() => {
    const sync = () => setPage(window.location.hash.slice(1) || defaultPageId);
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  const navigate = (id: PageId) => {
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
  const current = pageFor(page);
  const permitted = !!current && roleCanSee(current, user.role);
  return (
    <div className="shell">
      {drawer && (
        <button
          className="drawer-overlay"
          aria-label="Close navigation"
          onClick={() => setDrawer(false)}
        />
      )}
      <aside className={`sidebar ${drawer ? "open" : ""}`}>
        <div className="sidebar-brand">
          <Brand />
          <button
            className="icon-button mobile-close"
            aria-label="Close menu"
            onClick={() => setDrawer(false)}
          >
            <X size={20} />
          </button>
        </div>
        <nav aria-label="Main navigation">
          {navigationFor(user.role).map((group) => (
            <div className="nav-group" key={group.id}>
              <div className="nav-caption">{group.label}</div>
              {group.pages.map((item) => {
                const Icon = pageIcons[item.id];
                return (
                  <button
                    key={item.id}
                    className={page === item.id ? "active" : ""}
                    onClick={() => navigate(item.id)}
                    aria-current={page === item.id ? "page" : undefined}
                  >
                    <Icon size={18} />
                    {item.label}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="kernel-label">
            <span /> Modular core
            <small>Capabilities declared by extensions</small>
          </div>
          <div className="user-block">
            <span className="user-avatar">
              {user.username.slice(0, 2).toUpperCase()}
            </span>
            <div>
              {user.username}
              <small>
                {user.role === "admin"
                  ? "Administrator"
                  : user.role === "viewer"
                    ? "Read only"
                    : "Operator"}
              </small>
            </div>
            <button
              className="icon-button"
              aria-label="Sign out"
              title="Sign out"
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
              aria-label="Open menu"
              onClick={() => setDrawer(true)}
            >
              <Menu size={22} />
            </button>
            <span className="breadcrumb-home">Platform</span>
            <span className="breadcrumb-slash">/</span>
            <strong>{current?.label || "Page not found"}</strong>
          </div>
          <div className="topbar-right">
            <ThemeToggle />
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
              onOfferings={() => navigate("offerings")}
            />
          )}
          {permitted && page === "studio" && (
            <EngineeringStudio
              user={user}
              onOpenDeployments={() => navigate("jobs")}
            />
          )}
          {page === "offerings" && (
            <ServiceOfferings
              user={user}
              onOpenServices={() => navigate("services")}
              onOpenCatalogue={() => navigate("catalogue")}
            />
          )}
          {page === "services" && (
            <MyServices
              onOpenDeployments={() => navigate("jobs")}
              onOpenCatalogue={() => navigate("offerings")}
            />
          )}
          {page === "jobs" && (
            <Jobs user={user} onCreate={() => navigate("canvas")} />
          )}
          {page === "infrastructure" && <Infrastructure />}
          {page === "keys" && <Keys user={user} />}
          {page === "connections" && <Connections user={user} />}
          {page === "governance" && <Governance user={user} />}
          {page === "finops" && <FinOps />}
          {page === "diagnostics" && <Diagnostics user={user} />}
          {page === "audit" && <Audit />}
          {page === "integrations" && <Integrations user={user} />}
          {page === "operations" && <Operations user={user} />}
          {page === "agents" && <AgentAccess user={user} />}
          {!current && (
            <div className="panel">
              <Empty
                title="Page not found"
                icon={<Blocks size={27} />}
                action={
                  <button onClick={() => navigate(defaultPageId)}>
                    Return to canvas
                  </button>
                }
              >
                The address does not match any portal page.
              </Empty>
            </div>
          )}
          {current && !permitted && (
            <div className="panel">
              <Empty
                title="Page not available"
                icon={<ShieldCheck size={27} />}
                action={
                  <button onClick={() => navigate("services")}>
                    Open my services
                  </button>
                }
              >
                Your role does not include {current.label}. Ask an administrator for
                access.
              </Empty>
            </div>
          )}
        </main>
        <footer className="app-footer">
          <span>
            KuberWeaver <span className="footer-dot">·</span> Infrastructure by composition
          </span>
          <span>EUR / month</span>
        </footer>
      </div>
    </div>
  );
}
