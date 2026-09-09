import { useState, useEffect } from "react";
import { api } from "../lib/api";
import { safeINR, pnlColor } from "../lib/utils";

const panelStyle: React.CSSProperties = {
  background: "var(--bg-panel)",
  border: "1px solid var(--border)",
  borderRadius: "8px",
  overflow: "hidden",
};

const panelHeader: React.CSSProperties = {
  padding: "10px 14px",
  borderBottom: "1px solid var(--border-subtle)",
  fontSize: "10px",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.8px",
  color: "var(--text-muted)",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
};

interface OptionOverview {
  status: string;
  open_count: number;
  today_count: number;
  today_pnl: number;
  total_pnl: number;
  win_rate: number;
  is_expiry_day: boolean;
  open_trades: any[];
}

interface OptionTrade {
  trade_id: string;
  underlying: string;
  expiry: string;
  strike: number;
  entry_time: string;
  entry_ce_premium: number;
  entry_pe_premium: number;
  entry_credit: number;
  quantity: number;
  margin: number;
  sl_amount: number;
  status: string;
  exit_time?: string;
  exit_ce_premium?: number;
  exit_pe_premium?: number;
  exit_pnl?: number;
  exit_reason?: string;
  pcr?: number;
  selection_reason?: string;
  spot_at_entry?: number;
  current_ce?: number;
  current_pe?: number;
  live_pnl?: number;
}

export default function OptionSelling() {
  const [overview, setOverview] = useState<OptionOverview | null>(null);
  const [trades, setTrades] = useState<OptionTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      const [ov, tr] = await Promise.all([
        api.optionOverview(),
        api.optionTrades(),
      ]);
      setOverview(ov);
      setTrades(tr.trades || []);
    } catch (e) {
      console.error("Option data fetch failed:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleAction = async (action: string) => {
    setActionLoading(action);
    try {
      if (action === "check") await api.optionCheck();
      else if (action === "recheck") await api.optionRecheck();
      else if (action === "exit") await api.optionExit();
      await fetchData();
    } catch (e) {
      console.error("Action failed:", e);
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        {[1, 2, 3].map((i) => (
          <div key={i} className="skeleton" style={{ height: "120px", borderRadius: "8px" }} />
        ))}
      </div>
    );
  }

  const ov = overview || { status: "UNKNOWN", open_count: 0, today_count: 0, today_pnl: 0, total_pnl: 0, win_rate: 0, is_expiry_day: false, open_trades: [] };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {/* MCX | OPTION SELLING Header */}
      <div className="split-grid-2">
        {/* MCX Side */}
        <div style={panelStyle}>
          <div style={panelHeader}>
            <span>MCX</span>
            <span style={{ fontSize: "9px", color: "var(--text-disabled)" }}>Futures Trading</span>
          </div>
          <div style={{ padding: "14px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
              <div>
                <div style={{ fontSize: "9px", color: "var(--text-muted)", textTransform: "uppercase" }}>Status</div>
                <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--green)", marginTop: "2px" }}>RUNNING</div>
              </div>
              <div>
                <div style={{ fontSize: "9px", color: "var(--text-muted)", textTransform: "uppercase" }}>Type</div>
                <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)", marginTop: "2px" }}>DEMA-ATR Futures</div>
              </div>
            </div>
          </div>
        </div>

        {/* OPTION SELLING Side */}
        <div style={panelStyle}>
          <div style={panelHeader}>
            <span>OPTION SELLING</span>
            <span style={{ fontSize: "9px", color: ov.is_expiry_day ? "var(--green)" : "var(--text-disabled)" }}>
              {ov.is_expiry_day ? "EXPIRY DAY" : "NON-EXPIRY"}
            </span>
          </div>
          <div style={{ padding: "14px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
              <div>
                <div style={{ fontSize: "9px", color: "var(--text-muted)", textTransform: "uppercase" }}>Status</div>
                <div style={{ fontSize: "13px", fontWeight: 600, color: ov.status === "RUNNING" ? "var(--green)" : "var(--text-muted)", marginTop: "2px" }}>{ov.status}</div>
              </div>
              <div>
                <div style={{ fontSize: "9px", color: "var(--text-muted)", textTransform: "uppercase" }}>Type</div>
                <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)", marginTop: "2px" }}>OI Strangle</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* P&L Split */}
      <div className="split-grid-2">
        <div style={panelStyle}>
          <div style={panelHeader}>MCX P&L</div>
          <div style={{ padding: "14px", textAlign: "center" }}>
            <div style={{ fontSize: "9px", color: "var(--text-muted)" }}>Today's P&L</div>
            <div style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-muted)", marginTop: "4px" }}>—</div>
            <div style={{ fontSize: "9px", color: "var(--text-muted)", marginTop: "4px" }}>See MCX Dashboard</div>
          </div>
        </div>

        <div style={panelStyle}>
          <div style={panelHeader}>OPTION SELLING P&L</div>
          <div style={{ padding: "14px", textAlign: "center" }}>
            <div style={{ fontSize: "9px", color: "var(--text-muted)" }}>Today's P&L</div>
            <div style={{ fontSize: "20px", fontWeight: 700, color: pnlColor(ov.today_pnl), marginTop: "4px" }}>
              {safeINR(ov.today_pnl)}
            </div>
            <div style={{ display: "flex", justifyContent: "center", gap: "16px", marginTop: "8px" }}>
              <div style={{ fontSize: "9px", color: "var(--text-muted)" }}>Total: <span style={{ color: pnlColor(ov.total_pnl) }}>{safeINR(ov.total_pnl)}</span></div>
              <div style={{ fontSize: "9px", color: "var(--text-muted)" }}>Win Rate: {ov.win_rate.toFixed(0)}%</div>
            </div>
          </div>
        </div>
      </div>

      {/* Open Trades */}
      <div style={panelStyle}>
        <div style={panelHeader}>
          <span>OPEN TRADES ({ov.open_count})</span>
          <div style={{ display: "flex", gap: "6px" }}>
            <button
              onClick={() => handleAction("check")}
              disabled={actionLoading === "check"}
              style={{ fontSize: "9px", padding: "3px 8px", borderRadius: "4px", border: "1px solid var(--border)", background: "var(--bg-panel-hover)", color: "var(--text-secondary)", cursor: "pointer" }}
            >
              {actionLoading === "check" ? "..." : "CHECK"}
            </button>
            <button
              onClick={() => handleAction("recheck")}
              disabled={actionLoading === "recheck"}
              style={{ fontSize: "9px", padding: "3px 8px", borderRadius: "4px", border: "1px solid var(--border)", background: "var(--bg-panel-hover)", color: "var(--text-secondary)", cursor: "pointer" }}
            >
              {actionLoading === "recheck" ? "..." : "RECHECK"}
            </button>
            <button
              onClick={() => handleAction("exit")}
              disabled={actionLoading === "exit"}
              style={{ fontSize: "9px", padding: "3px 8px", borderRadius: "4px", border: "1px solid var(--red)", background: "rgba(239,68,68,0.1)", color: "var(--red)", cursor: "pointer" }}
            >
              {actionLoading === "exit" ? "..." : "EXIT ALL"}
            </button>
          </div>
        </div>
        {ov.open_trades.length === 0 ? (
          <div style={{ padding: "20px", textAlign: "center", color: "var(--text-muted)", fontSize: "11px" }}>
            No open trades
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "80px 80px 80px 80px 1fr 100px 100px 100px", gap: "1px", background: "var(--border-subtle)" }}>
            <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>ID</div>
            <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Underlying</div>
            <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Strike</div>
            <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Expiry</div>
            <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Entry</div>
            <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Margin</div>
            <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Live P&L</div>
            <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Reason</div>
            {ov.open_trades.map((t: any, i: number) => (
              <>
                <div key={`id-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-primary)", background: "var(--bg-panel)" }}>{t.trade_id?.slice(-8)}</div>
                <div key={`u-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-primary)", background: "var(--bg-panel)" }}>{t.underlying}</div>
                <div key={`s-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-primary)", background: "var(--bg-panel)" }}>{t.strike}</div>
                <div key={`e-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-primary)", background: "var(--bg-panel)" }}>{t.expiry}</div>
                <div key={`en-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-secondary)", background: "var(--bg-panel)" }}>
                  CE: {t.entry_ce?.toFixed(1)} | PE: {t.entry_pe?.toFixed(1)}
                </div>
                <div key={`m-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-primary)", background: "var(--bg-panel)" }}>{safeINR(t.margin)}</div>
                <div key={`p-${i}`} style={{ padding: "6px 10px", fontSize: "10px", fontWeight: 600, color: pnlColor(t.live_pnl || 0), background: "var(--bg-panel)" }}>{safeINR(t.live_pnl || 0)}</div>
                <div key={`r-${i}`} style={{ padding: "6px 10px", fontSize: "9px", color: "var(--text-muted)", background: "var(--bg-panel)" }}>{t.selection_reason}</div>
              </>
            ))}
          </div>
        )}
      </div>

      {/* Trade History */}
      <div style={panelStyle}>
        <div style={panelHeader}>TRADE HISTORY</div>
        {trades.length === 0 ? (
          <div style={{ padding: "20px", textAlign: "center", color: "var(--text-muted)", fontSize: "11px" }}>
            No trades yet
          </div>
        ) : (
          <div style={{ maxHeight: "400px", overflowY: "auto" }}>
            <div style={{ display: "grid", gridTemplateColumns: "80px 70px 70px 70px 90px 90px 90px 70px 60px", gap: "1px", background: "var(--border-subtle)" }}>
              <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Date</div>
              <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Underlying</div>
              <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Strike</div>
              <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Entry</div>
              <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Exit</div>
              <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Margin</div>
              <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>P&L</div>
              <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Status</div>
              <div style={{ padding: "6px 10px", fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", background: "var(--bg-panel)" }}>Reason</div>
              {trades.slice(0, 50).map((t, i) => (
                <>
                  <div key={`d-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-secondary)", background: "var(--bg-panel)" }}>{t.entry_time?.slice(5, 10)}</div>
                  <div key={`u-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-primary)", background: "var(--bg-panel)" }}>{t.underlying}</div>
                  <div key={`s-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-primary)", background: "var(--bg-panel)" }}>{t.strike}</div>
                  <div key={`en-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-secondary)", background: "var(--bg-panel)" }}>{t.entry_time?.slice(11, 16)}</div>
                  <div key={`ex-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-secondary)", background: "var(--bg-panel)" }}>{t.exit_time?.slice(11, 16) || "—"}</div>
                  <div key={`m-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-primary)", background: "var(--bg-panel)" }}>{safeINR(t.margin)}</div>
                  <div key={`p-${i}`} style={{ padding: "6px 10px", fontSize: "10px", fontWeight: 600, color: pnlColor(t.exit_pnl || 0), background: "var(--bg-panel)" }}>{t.exit_pnl != null ? safeINR(t.exit_pnl) : "—"}</div>
                  <div key={`st-${i}`} style={{ padding: "6px 10px", fontSize: "10px", color: t.status === "OPEN" ? "var(--amber)" : "var(--text-muted)", background: "var(--bg-panel)" }}>{t.status}</div>
                  <div key={`r-${i}`} style={{ padding: "6px 10px", fontSize: "9px", color: "var(--text-muted)", background: "var(--bg-panel)" }}>{t.exit_reason || "—"}</div>
                </>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
