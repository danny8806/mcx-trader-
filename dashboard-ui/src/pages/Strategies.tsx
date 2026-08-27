import { useData } from "../store/DataProvider";
import { formatINR, pnlColor, statusDot } from "../lib/utils";

const panelStyle: React.CSSProperties = { background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" };

export default function Strategies() {
  const { strategies } = useData();
  if (!strategies) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "10px" }}>
      {strategies.map((s: any) => (
        <div key={s.strategy_id} style={{ ...panelStyle, padding: "12px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: statusDot(s.state) }} />
              <span style={{ fontSize: "11px", fontWeight: 600, color: "var(--text-primary)" }}>{s.strategy_id}</span>
            </div>
            <span style={{
              fontSize: "9px", padding: "2px 6px", borderRadius: "3px", fontWeight: 600,
              background: s.position_side === "LONG" ? "var(--green-muted)" : s.position_side === "SHORT" ? "var(--red-muted)" : "var(--bg-table-header)",
              color: s.position_side === "LONG" ? "var(--green)" : s.position_side === "SHORT" ? "var(--red)" : "var(--text-muted)",
            }}>
              {s.position_side ?? s.state?.toUpperCase() ?? "UNKNOWN"}
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "3px", fontSize: "10px" }}>
            {[["Instrument", s.instrument], ["Fast TF", s.fast_timeframe], ["HTF", s.htf_timeframe], ["Qty", s.quantity],
              ["Trades", s.trade_count], ["Win%", `${(s.win_rate * 100).toFixed(1)}%`],
              ["P&L", formatINR(s.realized_net)]].map(([k, v]) => (
              <div key={String(k)} style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-muted)" }}>{String(k)}</span>
                <span style={{ color: k === "P&L" ? pnlColor(Number(String(v).replace(/[+₹,]/g, ""))) : "var(--text-primary)", fontVariantNumeric: "tabular-nums", fontWeight: k === "P&L" ? 600 : 400 }}>{String(v)}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      {strategies.length === 0 && <div style={{ gridColumn: "1/-1", padding: "40px", textAlign: "center", color: "var(--text-muted)" }}>No strategies loaded</div>}
    </div>
  );
}
