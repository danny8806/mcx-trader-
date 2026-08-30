import { createContext, useContext, useEffect, useRef, useState, useCallback, type ReactNode } from "react";
import { api } from "../lib/api";

export type DataState = "loading" | "live" | "stale" | "empty" | "error";

interface DataContextType {
  connected: boolean;
  overview: any;
  goldOverview: any;
  silverOverview: any;
  strategies: any[];
  positions: any[];
  orders: any[];
  fills: any[];
  trades: any[];
  pnl: any;
  pnlByInstrument: Record<string, any>;
  risk: any;
  indicators: Record<string, any>;
  htf: Record<string, any>;
  healthComponents: any[];
  overallHealth: string;
  reconciliation: any;
  settings: any;
  audit: any[];
  alerts: any[];
  equityCurve: any[];
  marketData: any;
  wsEvents: any[];
  wsState: any;
  refresh: (key?: string) => void;
  lastError: string | null;
}

const DataContext = createContext<DataContextType>({} as DataContextType);

function sv<T>(setter: (v: T) => void, mounted: React.MutableRefObject<boolean>) {
  return (data: T) => { if (mounted.current) setter(data); };
}

function extractVal(obj: any, key: string): any {
  if (!obj || typeof obj !== "object") return undefined;
  const v = obj[key];
  if (v && typeof v === "object" && "value" in v) return v.value;
  return v;
}

export function DataProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [overview, setOverview] = useState<any>(null);
  const [goldOverview, setGoldOverview] = useState<any>(null);
  const [silverOverview, setSilverOverview] = useState<any>(null);
  const [strategies, setStrategies] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [orders, setOrders] = useState<any[]>([]);
  const [fills, setFills] = useState<any[]>([]);
  const [trades, setTrades] = useState<any[]>([]);
  const [pnl, setPnl] = useState<any>(null);
  const [pnlByInstrument, setPnlByInstrument] = useState<Record<string, any>>({});
  const [risk, setRisk] = useState<any>(null);
  const [indicators, setIndicators] = useState<Record<string, any>>({});
  const [htf, setHtf] = useState<Record<string, any>>({});
  const [healthComponents, setHealthComponents] = useState<any[]>([]);
  const [overallHealth, setOverallHealth] = useState("unknown");
  const [reconciliation, setReconciliation] = useState<any>(null);
  const [settings, setSettings] = useState<any>(null);
  const [audit, setAudit] = useState<any[]>([]);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [equityCurve, setEquityCurve] = useState<any[]>([]);
  const [marketData, setMarketData] = useState<any>(null);
  const [wsEvents, setWsEvents] = useState<any[]>([]);
  const [wsState, setWsState] = useState<any>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const timersRef = useRef<Record<string, number>>({});
  const mountedRef = useRef(true);
  const wsActiveRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const safe = useCallback(<T,>(setter: (v: T) => void) => sv(setter, mountedRef), []);

  const fetchOverview = useCallback(async () => {
    try {
      const d = await api.overview() as any;
      if (wsActiveRef.current) return;
      safe(setOverview)({
        total_equity: extractVal(d, "total_equity") ?? 0,
        starting_capital: extractVal(d, "starting_capital") ?? 0,
        today_pnl: extractVal(d, "today_pnl") ?? 0,
        total_net_pnl: extractVal(d, "total_net_pnl") ?? 0,
        realized_pnl: extractVal(d, "realized_pnl") ?? 0,
        unrealized_pnl: extractVal(d, "unrealized_pnl") ?? 0,
        margin_used: extractVal(d, "margin_used") ?? 0,
        available_margin: extractVal(d, "available_margin") ?? 0,
        open_positions_count: extractVal(d, "open_positions_count") ?? 0,
        active_orders_count: extractVal(d, "active_orders_count") ?? 0,
        active_strategies_count: extractVal(d, "active_strategies_count") ?? 0,
        kill_switch: extractVal(d, "kill_switch") ?? false,
      });
    } catch (e: any) { setLastError(`overview: ${e?.message || e}`); }
  }, [safe]);

  const fetchGoldOverview = useCallback(async () => {
    try { safe(setGoldOverview)(await api.overviewInstrument("GOLDM")); } catch (e: any) { setLastError(`gold: ${e?.message || e}`); }
  }, [safe]);

  const fetchSilverOverview = useCallback(async () => {
    try { safe(setSilverOverview)(await api.overviewInstrument("SILVERM")); } catch (e: any) { setLastError(`silver: ${e?.message || e}`); }
  }, [safe]);

  const fetchStrategies = useCallback(async () => {
    try {
      const d = await api.strategies() as any;
      if (wsActiveRef.current) return;
      safe(setStrategies)(Array.isArray(d?.strategies) ? d.strategies : []);
    } catch (e: any) { setLastError(`strategies: ${e?.message || e}`); }
  }, [safe]);

  const fetchPositions = useCallback(async () => {
    try {
      const d = await api.positions() as any;
      if (wsActiveRef.current) return;
      safe(setPositions)(Array.isArray(d?.positions) ? d.positions : []);
    } catch (e: any) { setLastError(`positions: ${e?.message || e}`); }
  }, [safe]);

  const fetchOrders = useCallback(async () => {
    try {
      const d = await api.orders() as any;
      safe(setOrders)(Array.isArray(d?.orders) ? d.orders : []);
    } catch (e: any) { setLastError(`orders: ${e?.message || e}`); }
  }, [safe]);

  const fetchFills = useCallback(async () => {
    try {
      const d = await api.fills() as any;
      safe(setFills)(Array.isArray(d?.fills) ? d.fills : []);
    } catch (e: any) { setLastError(`fills: ${e?.message || e}`); }
  }, [safe]);

  const fetchTrades = useCallback(async () => {
    try {
      const d = await api.trades() as any;
      safe(setTrades)(Array.isArray(d?.trades) ? d.trades : []);
    } catch (e: any) { setLastError(`trades: ${e?.message || e}`); }
  }, [safe]);

  const fetchPnl = useCallback(async () => {
    try {
      const d = await api.pnl() as any;
      safe(setPnl)(d?.portfolio ?? null);
      safe(setPnlByInstrument)(d?.by_instrument ?? {});
    } catch (e: any) { setLastError(`pnl: ${e?.message || e}`); }
  }, [safe]);

  const fetchRisk = useCallback(async () => {
    try { safe(setRisk)(await api.risk()); } catch (e: any) { setLastError(`risk: ${e?.message || e}`); }
  }, [safe]);

  const fetchIndicators = useCallback(async () => {
    try {
      const d = await api.indicators() as any;
      safe(setIndicators)(d?.indicators ?? d ?? {});
    } catch (e: any) { setLastError(`indicators: ${e?.message || e}`); }
  }, [safe]);

  const fetchHtf = useCallback(async () => {
    try {
      const d = await api.htf() as any;
      safe(setHtf)(d?.htf ?? d ?? {});
    } catch (e: any) { setLastError(`htf: ${e?.message || e}`); }
  }, [safe]);

  const fetchHealth = useCallback(async () => {
    try {
      const d = await api.healthSystem() as any;
      safe(setHealthComponents)(d?.components ?? []);
      safe(setOverallHealth)(d?.overall ?? "unknown");
    } catch (e: any) { setLastError(`health: ${e?.message || e}`); }
  }, [safe]);

  const fetchReconciliation = useCallback(async () => {
    try { safe(setReconciliation)(await api.reconciliation()); } catch (e: any) { setLastError(`reconciliation: ${e?.message || e}`); }
  }, [safe]);

  const fetchSettings = useCallback(async () => {
    try { safe(setSettings)(await api.settings()); } catch (e: any) { setLastError(`settings: ${e?.message || e}`); }
  }, [safe]);

  const fetchAudit = useCallback(async () => {
    try {
      const d = await api.audit() as any;
      safe(setAudit)(d?.entries ?? []);
    } catch (e: any) { setLastError(`audit: ${e?.message || e}`); }
  }, [safe]);

  const fetchAlerts = useCallback(async () => {
    try {
      const d = await api.alerts() as any;
      safe(setAlerts)(d?.alerts ?? []);
    } catch (e: any) { setLastError(`alerts: ${e?.message || e}`); }
  }, [safe]);

  const fetchEquityCurve = useCallback(async () => {
    try {
      const d = await api.equityCurve() as any;
      const pts = (d?.equity_curve ?? []).map((r: any) => {
        const ts = typeof r.timestamp === "number" ? r.timestamp : ((Date.parse(r.timestamp) / 1000) || 0);
        return { timestamp: ts, equity: Number(r.equity ?? 0) };
      }).sort((a: any, b: any) => a.timestamp - b.timestamp);
      safe(setEquityCurve)(pts);
    } catch (e: any) { setLastError(`equity: ${e?.message || e}`); }
  }, [safe]);

  const fetchMarketData = useCallback(async () => {
    try { safe(setMarketData)(await api.marketData()); } catch (e: any) { setLastError(`market: ${e?.message || e}`); }
  }, [safe]);

  const refresh = useCallback((key?: string) => {
    const map: Record<string, () => void> = {
      overview: fetchOverview, goldOverview: fetchGoldOverview, silverOverview: fetchSilverOverview,
      strategies: fetchStrategies, positions: fetchPositions, orders: fetchOrders,
      fills: fetchFills, trades: fetchTrades, pnl: fetchPnl, risk: fetchRisk, indicators: fetchIndicators,
      htf: fetchHtf, health: fetchHealth, reconciliation: fetchReconciliation,
      settings: fetchSettings, audit: fetchAudit, alerts: fetchAlerts,
      equityCurve: fetchEquityCurve, marketData: fetchMarketData,
    };
    if (key && map[key]) map[key]();
    else Object.values(map).forEach(fn => fn());
  }, [fetchOverview, fetchGoldOverview, fetchSilverOverview, fetchStrategies, fetchPositions, fetchOrders, fetchFills, fetchTrades, fetchPnl, fetchRisk, fetchIndicators, fetchHtf, fetchHealth, fetchReconciliation, fetchSettings, fetchAudit, fetchAlerts, fetchEquityCurve, fetchMarketData]);

  useEffect(() => {
    fetchOverview(); fetchGoldOverview(); fetchSilverOverview();
    fetchStrategies(); fetchPositions(); fetchOrders(); fetchFills();
    fetchTrades();
    fetchPnl(); fetchRisk(); fetchIndicators(); fetchHtf();
    fetchHealth(); fetchReconciliation(); fetchSettings();
    fetchAudit(); fetchAlerts(); fetchEquityCurve(); fetchMarketData();
  }, []);

  useEffect(() => {
    timersRef.current = {
      overview: window.setInterval(fetchOverview, 3000),
      gold: window.setInterval(fetchGoldOverview, 3000),
      silver: window.setInterval(fetchSilverOverview, 3000),
      strategies: window.setInterval(fetchStrategies, 3000),
      positions: window.setInterval(fetchPositions, 2000),
      orders: window.setInterval(fetchOrders, 3000),
      fills: window.setInterval(fetchFills, 3000),
      trades: window.setInterval(fetchTrades, 5000),
      pnl: window.setInterval(fetchPnl, 5000),
      risk: window.setInterval(fetchRisk, 3000),
      indicators: window.setInterval(fetchIndicators, 5000),
      htf: window.setInterval(fetchHtf, 5000),
      health: window.setInterval(fetchHealth, 10000),
      audit: window.setInterval(fetchAudit, 10000),
      alerts: window.setInterval(fetchAlerts, 5000),
      equityCurve: window.setInterval(fetchEquityCurve, 10000),
      marketData: window.setInterval(fetchMarketData, 2000),
    };
    return () => { Object.values(timersRef.current).forEach(clearInterval); timersRef.current = {}; };
  }, [fetchOverview, fetchGoldOverview, fetchSilverOverview, fetchStrategies, fetchPositions, fetchOrders, fetchFills, fetchTrades, fetchPnl, fetchRisk, fetchIndicators, fetchHtf, fetchHealth, fetchAudit, fetchAlerts, fetchEquityCurve, fetchMarketData]);

  useEffect(() => {
    let reconnectTimer: number;
    function connect() {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      wsRef.current = ws;
      ws.onopen = () => {
        ws.send(JSON.stringify({ action: "subscribe", channels: ["all"] }));
        if (mountedRef.current) setConnected(true);
      };
      ws.onclose = () => {
        wsActiveRef.current = false;
        if (mountedRef.current) setConnected(false);
        reconnectTimer = window.setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === "engine_state") {
            const s = msg.data;
            wsActiveRef.current = true;
            safe(setWsState)(s);
            if (s?.account) {
              setOverview((prev: any) => prev ? {
                ...prev,
                total_equity: s.account.equity ?? prev.total_equity,
                starting_capital: s.account.starting_capital ?? prev.starting_capital,
                realized_pnl: s.account.realized_pnl ?? prev.realized_pnl,
                unrealized_pnl: s.account.unrealized_pnl ?? prev.unrealized_pnl,
                margin_used: s.account.used_margin ?? prev.margin_used,
              } : prev);
            }
            if (s?.strategies) {
              const list = Object.entries(s.strategies).map(([name, snap]: [string, any]) => ({
                strategy_id: snap.strategy_id ?? name,
                instrument: snap.instrument ?? "",
                fast_timeframe: snap.fast_timeframe ?? "",
                htf_timeframe: snap.htf_timeframe ?? "",
                quantity: snap.quantity ?? 1,
                enabled: snap.enabled ?? true,
                state: snap.state ?? "unknown",
                position_side: snap.position_side,
                stop_price: snap.stop_price,
                pending_entry: snap.pending_entry,
                bars_processed: snap.bars_processed ?? 0,
                trade_count: snap.trade_count ?? 0,
                wins: snap.wins ?? 0,
                losses: snap.losses ?? 0,
                win_rate: snap.win_rate ?? 0,
                realized_net: snap.realized_net ?? 0,
                realized_gross: snap.realized_gross ?? 0,
              }));
              safe(setStrategies)(list);
            }
            if (s?.positions) {
              const openPos = s.positions?.open_positions ?? {};
              safe(setPositions)(Object.values(openPos) as any[]);
            }
          }
          if (msg.type === "events") {
            const events = msg.data ?? [];
            safe(setWsEvents)((prev: any[]) => [...events, ...prev].slice(0, 200));
          }
        } catch (e: any) { setLastError(`ws: ${e?.message || e}`); }
      };
    }
    connect();
    return () => { clearTimeout(reconnectTimer); wsRef.current?.close(); };
  }, [safe]);

  return (
    <DataContext.Provider value={{
      connected, overview, goldOverview, silverOverview, strategies, positions,
      orders, fills, trades, pnl, pnlByInstrument, risk, indicators, htf,
      healthComponents, overallHealth, reconciliation, settings, audit,
      alerts, equityCurve, marketData, wsEvents, wsState, refresh, lastError,
    }}>
      {children}
    </DataContext.Provider>
  );
}

export function useData() {
  return useContext(DataContext);
}
