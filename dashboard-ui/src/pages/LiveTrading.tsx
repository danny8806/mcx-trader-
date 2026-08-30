import { useData } from "../store/DataProvider";
import { formatINR, pnlColor, statusDot, formatTimestamp, safeNum, safeINR } from "../lib/utils";

const panelStyle: React.CSSProperties = { background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" };
const header: React.CSSProperties = { padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" };

export default function LiveTrading() {
  const { overview, strategies, positions, fills, goldOverview, silverOverview } = useData();
  if (!overview) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  const openPositions = positions.filter((p: any) => p.is_open);
  const goldStrats = strategies.filter((s: any) => s.instrument === "GOLDM");
  const silverStrats = strategies.filter((s: any) => s.instrument === "SILVERM");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
        {[{ name: "GOLDM", ltp: goldOverview?.ltp ?? 0, strats: goldStrats }, { name: "SILVERM", ltp: silverOverview?.ltp ?? 0, strats: silverStrats }].map(({ name, ltp, strats }) => (
          <div key={name} style={{ ...panelStyle, padding: "12px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-primary)" }}>{name}</span>
              <span style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
                {ltp > 0 ? safeINR(ltp) : "—"}
              </span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
              {strats.map((s: any) => (
                <div key={s.strategy_id} style={{ display: "flex", alignItems: "center", gap: "4px", padding: "3px 8px", background: "var(--bg-table-header)", borderRadius: "3px", fontSize: "9px" }}>
                  <span style={{ width: "4px", height: "4px", borderRadius: "50%", background: statusDot(s.state) }} />
                  <span style={{ color: "var(--text-secondary)" }}>{s.strategy_id}</span>
                  {s.position_side && <span style={{ color: s.position_side === "LONG" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{s.position_side}</span>}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div style={panelStyle}>
        <div style={{ ...header, display: "flex", justifyContent: "space-between" }}>
          <span>OPEN POSITIONS ({openPositions.length})</span>
          {openPositions.length > 0 && <span style={{ color: pnlColor(overview.unrealized_pnl), fontWeight: 600 }}>{formatINR(overview.unrealized_pnl)}</span>}
        </div>
        {openPositions.length === 0 ? (
          <div style={{ padding: "24px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No open positions</div>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "70px 100px 50px 40px 80px 50px 90px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)" }}>
              <span>Instrument</span><span>Strategy</span><span>Side</span><span>Qty</span><span>Entry</span><span>SL</span><span style={{ textAlign: "right" }}>P&L</span>
            </div>
            {openPositions.map((p: any) => (
              <div key={p.position_id} style={{ display: "grid", gridTemplateColumns: "70px 100px 50px 40px 80px 50px 90px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
                <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{p.instrument}</span>
                <span style={{ color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.strategy_id}</span>
                <span style={{ color: p.side === "LONG" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{p.side}</span>
                <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{p.quantity}</span>
                <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{safeINR(p.average_entry)}</span>
                <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{p.stop_price ? safeINR(p.stop_price) : "—"}</span>
                <span style={{ color: pnlColor(p.unrealized_pnl), fontWeight: 600, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
                  {p.unrealized_pnl >= 0 ? "+" : ""}₹{safeNum(p.unrealized_pnl).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                </span>
              </div>
            ))}
          </>
        )}
      </div>

      <div style={panelStyle}>
        <div style={header}>RECENT FILLS ({fills.length})</div>
        <div style={{ maxHeight: "200px", overflow: "auto" }}>
          {fills.length === 0 ? (
            <div style={{ padding: "20px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No fills</div>
          ) : (
            fills.slice(0, 20).map((f: any) => (
              <div key={f.fill_id} style={{ display: "flex", alignItems: "center", gap: "10px", padding: "4px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)" }}>
                <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums", width: "55px" }}>{formatTimestamp(f.timestamp)}</span>
                <span style={{ color: "var(--text-primary)", fontWeight: 500, width: "55px" }}>{f.instrument}</span>
                <span style={{ color: f.side === "BUY" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{f.side}</span>
                <span style={{ color: "var(--text-secondary)" }}>{f.quantity}</span>
                <span style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{safeINR(f.price)}</span>
                <div style={{ flex: 1 }} />
                <span style={{ color: "var(--text-muted)", fontSize: "9px" }}>{f.strategy_id}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
