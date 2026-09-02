import { useDataSelector } from "../store/DataProvider";
import { formatINR, pnlColor, statusDot, formatTimestamp, safeNum, safeINR } from "../lib/utils";

const panelStyle: React.CSSProperties = { background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" };
const header: React.CSSProperties = { padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" };

export default function LiveTrading() {
  const overview = useDataSelector<any>((s) => s.overview);
  const strategies = useDataSelector<any[]>((s) => s.strategies);
  const positions = useDataSelector<any[]>((s) => s.positions);
  const fills = useDataSelector<any[]>((s) => s.fills);
  const goldOverview = useDataSelector<any>((s) => s.goldOverview);
  const silverOverview = useDataSelector<any>((s) => s.silverOverview);
  if (!overview) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  const openPositions = positions.filter((p: any) => p.is_open);
  const goldStrats = strategies.filter((s: any) => s.instrument === "GOLDM");
  const silverStrats = strategies.filter((s: any) => s.instrument === "SILVERM");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div className="split-grid-2">
        {[{ name: "GOLDM", ltp: goldOverview?.ltp ?? 0, strats: goldStrats }, { name: "SILVERM", ltp: silverOverview?.ltp ?? 0, strats: silverStrats }].map(({ name, ltp, strats }) => (
          <div key={name} className="lift animate-fade-in-up" style={{ ...panelStyle, padding: "12px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <span style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", fontWeight: 600, color: "var(--text-primary)" }}>
                {name}
                <span className={ltp > 0 ? "animate-pulse-dot" : ""} style={{ width: "5px", height: "5px", borderRadius: "50%", background: ltp > 0 ? "var(--green)" : "var(--red)", ["--dot" as any]: ltp > 0 ? "var(--green)" : "var(--red)" }} />
              </span>
              <span className="tabular-nums" style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-primary)" }}>
                {ltp > 0 ? safeINR(ltp) : "—"}
              </span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
              {strats.map((s: any) => (
                <div key={s.strategy_id} style={{ display: "flex", alignItems: "center", gap: "4px", padding: "3px 8px", background: "var(--bg-table-header)", borderRadius: "4px", fontSize: "9px" }}>
                  <span style={{ width: "4px", height: "4px", borderRadius: "50%", background: statusDot(s.state) }} />
                  <span style={{ color: "var(--text-secondary)" }}>{s.strategy_id}</span>
                  {s.position_side && <span style={{ color: s.position_side === "LONG" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{s.position_side}</span>}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="lift" style={panelStyle}>
        <div style={{ ...header, display: "flex", justifyContent: "space-between" }}>
          <span>OPEN POSITIONS ({openPositions.length})</span>
          {openPositions.length > 0 && <span style={{ color: pnlColor(overview.unrealized_pnl), fontWeight: 600 }}>{formatINR(overview.unrealized_pnl)}</span>}
        </div>
        {openPositions.length === 0 ? (
          <div className="animate-fade-in-up" style={{ padding: "24px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No open positions — all flat</div>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "70px 100px 50px 40px 80px 50px 90px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)", position: "sticky" }}>
              <span>Instrument</span><span>Strategy</span><span>Side</span><span>Qty</span><span>Entry</span><span>SL</span><span style={{ textAlign: "right" }}>P&L</span>
            </div>
            {openPositions.map((p: any) => (
              <div key={p.position_id} className="hover-row" style={{ display: "grid", gridTemplateColumns: "70px 100px 50px 40px 80px 50px 90px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
                <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{p.instrument}</span>
                <span style={{ color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.strategy_id}</span>
                <span style={{ color: p.side === "LONG" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{p.side}</span>
                <span className="tabular-nums" style={{ color: "var(--text-secondary)" }}>{p.quantity}</span>
                <span className="tabular-nums" style={{ color: "var(--text-secondary)" }}>{safeINR(p.average_entry)}</span>
                <span className="tabular-nums" style={{ color: "var(--text-muted)" }}>{p.stop_price ? safeINR(p.stop_price) : "—"}</span>
                <span className="tabular-nums" style={{ color: pnlColor(p.unrealized_pnl), fontWeight: 600, textAlign: "right" }}>
                  {p.unrealized_pnl >= 0 ? "+" : ""}₹{safeNum(p.unrealized_pnl).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                </span>
              </div>
            ))}
          </>
        )}
      </div>

      <div className="lift" style={panelStyle}>
        <div style={header}>RECENT FILLS ({fills.length})</div>
        <div style={{ maxHeight: "200px", overflow: "auto" }}>
          {fills.length === 0 ? (
            <div className="animate-fade-in-up" style={{ padding: "20px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No fills yet</div>
          ) : (
            fills.slice(0, 20).map((f: any) => (
              <div key={f.fill_id} className="hover-row" style={{ display: "flex", alignItems: "center", gap: "10px", padding: "4px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)" }}>
                <span className="tabular-nums" style={{ color: "var(--text-muted)", width: "55px" }}>{formatTimestamp(f.timestamp)}</span>
                <span style={{ color: "var(--text-primary)", fontWeight: 500, width: "55px" }}>{f.instrument}</span>
                <span style={{ color: f.side === "BUY" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{f.side}</span>
                <span style={{ color: "var(--text-secondary)" }}>{f.quantity}</span>
                <span className="tabular-nums" style={{ color: "var(--text-primary)" }}>{safeINR(f.price)}</span>
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
