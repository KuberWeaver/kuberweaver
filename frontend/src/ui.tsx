import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertCircle,
  Check,
  ChevronRight,
  LoaderCircle,
  RefreshCw,
  Package,
  Box,
  Server,
  Network,
  Layers,
  ShieldCheck,
  Cloud,
} from "lucide-react";
import { api } from "./api";
export const errorMessage = (e: unknown) =>
  e instanceof Error ? e.message : String(e);
export function useResource<T = any>(path: string, interval = 0) {
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((v) => v + 1), []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const load = async () => {
      try {
        const result = await api<T>(path);
        if (active) {
          setData(result);
          setError("");
        }
      } catch (e) {
        if (active) setError(errorMessage(e));
      } finally {
        if (active) setLoading(false);
      }
    };
    load();
    const timer = interval ? setInterval(load, interval) : undefined;
    return () => {
      active = false;
      if (timer) clearInterval(timer);
    };
  }, [path, interval, revision]);
  return { data, error, loading, refresh, setData };
}
export function Spinner() {
  return (
    <span className="loading">
      <LoaderCircle className="spin" size={17} /> Loading…
    </span>
  );
}
export function ErrorNotice({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="notice error" role="alert">
      <AlertCircle size={18} />
      <span>{message}</span>
      {retry && (
        <button className="text-button" onClick={retry}>
          <RefreshCw size={14} />
          Retry
        </button>
      )}
    </div>
  );
}
export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="notice">
      <ShieldCheck size={18} />
      <span>{children}</span>
    </div>
  );
}
export function Empty({
  title,
  children,
  icon = <Package size={27} />,
}: {
  title: string;
  children?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      <div>{children}</div>
    </div>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return (
    <span className={`badge ${tone}`}>
      {tone === "success" && <Check size={11} />} {children}
    </span>
  );
}
export function PluginIcon({
  name,
  size = 19,
}: {
  name?: string;
  size?: number;
}) {
  const key = (name || "").toLowerCase();
  const Icon = key.includes("nginx")
    ? Network
    : key.includes("apache")
      ? Server
      : key.includes("stack")
        ? Layers
        : key.includes("cloud")
          ? Cloud
          : key.includes("cisco") || key.includes("network")
            ? Network
            : key.includes("firewall") || key.includes("sense")
              ? ShieldCheck
              : Box;
  return <Icon size={size} />;
}
export function PageTitle({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow || "WORKSPACE"}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}
export function Details({
  data,
  title = "Technical details",
}: {
  data: unknown;
  title?: string;
}) {
  return (
    <details className="technical">
      <summary>
        {title}
        <ChevronRight size={14} />
      </summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}
export function ResourceBoundary({
  loading,
  error,
  retry,
  children,
}: {
  loading: boolean;
  error: string;
  retry: () => void;
  children: ReactNode;
}) {
  if (loading) return <Spinner />;
  if (error) return <ErrorNotice message={error} retry={retry} />;
  return <>{children}</>;
}
