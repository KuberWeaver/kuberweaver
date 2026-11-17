const states: Record<string, { label: string; tone: string }> = {
  configured: { label: "Configured", tone: "neutral" },
  connected: { label: "Connected", tone: "success" },
  live: { label: "Available", tone: "success" },
  ready: { label: "Ready", tone: "success" },
  available: { label: "Available", tone: "success" },
  loaded: { label: "Enabled", tone: "success" },
  enabled: { label: "Enabled", tone: "success" },
  active: { label: "Active", tone: "success" },
  disabled: { label: "Disabled", tone: "neutral" },
  unconfigured: { label: "Not configured", tone: "neutral" },
  planned: { label: "Planned", tone: "neutral" },
  degraded: { label: "Degraded", tone: "warning" },
  error: { label: "Error", tone: "danger" },
  unknown: { label: "Status not specified", tone: "neutral" },
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
