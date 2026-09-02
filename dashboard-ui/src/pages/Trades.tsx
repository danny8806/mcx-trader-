import { useDataSelector } from "../store/DataProvider";
import { formatDT, formatINR, pnlColor, safeNum } from "../lib/utils";

export default function Trades() {
  const trades = useDataSelector<any[]>((s) => s.trades);
  if (!trades) return (
    <div style={{ padding: "20px", color: "var(--text-muted)" }}>
      <div className="skeleton" style={{ width: "200px", height: "14px", marginBottom: "12px" }} />
      <div className="skeleton" style={{ width: "100%", height: "200px" }} />
      <div style={{ fontSize: "11px", marginTop: "8px" }}>Loading trades...</div>
    </div>
  );

  return (
    <div className="lift animate-fade-in-up" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
        TRADEBOOK ({trades.length})
      </div>
      {trades.length === 0 ? (
        <div className="animate-fade-in-up" style={{ padding: "40px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No completed trades recorded</div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "150px 85px 60px 100px 50px 35px 80px 80px 80px 90px 90px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)", position: "sticky", top: 0, zIndex: 1 }}>
            <span>Entry</span><span>Exit</span><span>Instrument</span><span>Strategy</span><span>Side</span><span>Qty</span><span style={{ textAlign: "right" }}>Entry</span><span style={{ textAlign: "right" }}>Exit</span><span style={{ textAlign: "right" }}>Gross</span><span style={{ textAlign: "right" }}>Net P&L</span><span>Status</span>
          </div>
          {trades.map((t: any) => (
            <div key={t.trade_id} className="hover-row" style={{ display: "grid", gridTemplateColumns: "150px 85px 60px 100px 50px 35px 80px 80px 80px 90px 90px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
              <span className="tabular-nums" style={{ color: "var(--text-muted)" }}>{formatDT(t.entry_timestamp)}</span>
              <span className="tabular-nums" style={{ color: "var(--text-muted)" }}>{formatDT(t.exit_timestamp)}</span>
              <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{t.instrument}</span>
              <span style={{ color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.strategy_id}</span>
              <span style={{ color: t.side === "LONG" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{t.side}</span>
              <span className="tabular-nums" style={{ color: "var(--text-secondary)" }}>{t.quantity}</span>
              <span className="tabular-nums" style={{ color: "var(--text-secondary)", textAlign: "right" }}>₹{safeNum(t.entry_price).toLocaleString("en-IN")}</span>
              <span className="tabular-nums" style={{ color: "var(--text-secondary)", textAlign: "right" }}>₹{safeNum(t.exit_price).toLocaleString("en-IN")}</span>
              <span className="tabular-nums" style={{ color: pnlColor(Number(t.gross_pnl)), textAlign: "right" }}>{formatINR(safeNum(t.gross_pnl))}</span>
              <span className="tabular-nums" style={{ color: pnlColor(Number(t.net_pnl)), fontWeight: 600, textAlign: "right" }}>{formatINR(safeNum(t.net_pnl))}</span>
              <span style={{ color: t.status === "closed" ? "var(--text-muted)" : "var(--amber)", fontFamily: "monospace", fontSize: "9px" }}>{t.status}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
