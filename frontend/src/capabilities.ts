const states: Record<string, { label: string; tone: string }> = {
  configured: { label: "Configuré", tone: "neutral" },
  connected: { label: "Connecté", tone: "success" },
  live: { label: "Disponible", tone: "success" },
  ready: { label: "Prêt", tone: "success" },
  available: { label: "Disponible", tone: "success" },
  loaded: { label: "Activée", tone: "success" },
  enabled: { label: "Activée", tone: "success" },
  active: { label: "Active", tone: "success" },
  disabled: { label: "Désactivée", tone: "neutral" },
  unconfigured: { label: "Non configuré", tone: "neutral" },
  planned: { label: "Planifié", tone: "neutral" },
  degraded: { label: "Dégradé", tone: "warning" },
  error: { label: "Erreur", tone: "danger" },
  unknown: { label: "État non précisé", tone: "neutral" },
};
export function capabilityState(item: {
  status?: string;
  state?: string;
  enabled?: boolean;
}) {
  const state =
    item.status ||
    item.state ||
    (item.enabled === true
      ? "enabled"
      : item.enabled === false
        ? "disabled"
        : "unknown");
  return states[state] || { label: state, tone: "neutral" };
}
