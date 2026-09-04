const API_BASE = "";

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export const api = {
  health: () => fetchJSON<any>("/api/health"),
  overview: () => fetchJSON<any>("/api/overview"),
  overviewInstrument: (inst: string) => fetchJSON<any>(`/api/overview/${inst}`),
  strategies: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return fetchJSON<any>(`/api/strategies${qs}`);
  },
  strategy: (id: string) => fetchJSON<any>(`/api/strategies/${id}`),
  strategyParams: (id: string) => fetchJSON<any>(`/api/strategies/${id}/parameters`),
  controlStrategy: (id: string, action: string) => postJSON<any>(`/api/strategies/${id}/control`, { action }),
  positions: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return fetchJSON<any>(`/api/positions${qs}`);
  },
  position: (id: string) => fetchJSON<any>(`/api/positions/${id}`),
  orders: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return fetchJSON<any>(`/api/orders${qs}`);
  },
  fills: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return fetchJSON<any>(`/api/fills${qs}`);
  },
  trades: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return fetchJSON<any>(`/api/trades${qs}`);
  },
  trade: (id: string) => fetchJSON<any>(`/api/trades/${id}`),
  pnl: () => fetchJSON<any>("/api/pnl"),
  pnlInstrument: (inst: string) => fetchJSON<any>(`/api/pnl/${inst}`),
  equityCurve: () => fetchJSON<any>("/api/equity-curve"),
  marketData: () => fetchJSON<any>("/api/market-data"),
  marketDataInstrument: (inst: string) => fetchJSON<any>(`/api/market-data/${inst}`),
  risk: () => fetchJSON<any>("/api/risk"),
  healthSystem: () => fetchJSON<any>("/api/health/system"),
  indicators: () => fetchJSON<any>("/api/indicators"),
  indicatorsInstrument: (inst: string) => fetchJSON<any>(`/api/indicators/${inst}`),
  htf: () => fetchJSON<any>("/api/htf"),
  htfInstrument: (inst: string) => fetchJSON<any>(`/api/htf/${inst}`),
  alerts: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return fetchJSON<any>(`/api/alerts${qs}`);
  },
  reconciliation: () => fetchJSON<any>("/api/reconciliation"),
  orphanScan: () => fetchJSON<any>("/api/trades/orphan-scan"),
  lifecycleReconcile: () => fetchJSON<any>("/api/trades/lifecycle-reconcile"),
  settings: () => fetchJSON<any>("/api/settings"),
  refreshSettings: () => postJSON<any>("/api/settings/refresh"),
  audit: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return fetchJSON<any>(`/api/audit${qs}`);
  },
  replayStatus: () => fetchJSON<any>("/api/replay/status"),
};
