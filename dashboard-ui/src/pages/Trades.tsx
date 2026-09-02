import { useDataSelector } from "../store/DataProvider";
import { formatDT, formatINR, pnlColor, safeNum } from "../lib/utils";

export default function Trades() {
  const trades = useDataSelector<any[]>((s) => s.trades);
  if (!trades) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  return (
    <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
        TRADEBOOK ({trades.length})
      </div>
      {trades.length === 0 ? (
        <div style={{ padding: "40px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No completed trades recorded</div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "150px 85px 60px 100px 50px 35px 80px 80px 80px 90px 90px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)" }}>
            <span>Entry</span><span>Exit</span><span>Instrument</span><span>Strategy</span><span>Side</span><span>Qty</span><span style={{ textAlign: "right" }}>Entry</span><span style={{ textAlign: "right" }}>Exit</span><span style={{ textAlign: "right" }}>Gross</span><span style={{ textAlign: "right" }}>Net P&L</span><span>Status</span>
          </div>
          {trades.map((t: any) => (
            <div key={t.trade_id} style={{ display: "grid", gridTemplateColumns: "150px 85px 60px 100px 50px 35px 80px 80px 80px 90px 90px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
              <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{formatDT(t.entry_timestamp)}</span>
              <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{formatDT(t.exit_timestamp)}</span>
              <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{t.instrument}</span>
              <span style={{ color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.strategy_id}</span>
              <span style={{ color: t.side === "LONG" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{t.side}</span>
              <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{t.quantity}</span>
              <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums", textAlign: "right" }}>₹{safeNum(t.entry_price).toLocaleString("en-IN")}</span>
              <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums", textAlign: "right" }}>₹{safeNum(t.exit_price).toLocaleString("en-IN")}</span>
              <span style={{ color: pnlColor(Number(t.gross_pnl)), fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{formatINR(safeNum(t.gross_pnl))}</span>
              <span style={{ color: pnlColor(Number(t.net_pnl)), fontWeight: 600, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{formatINR(safeNum(t.net_pnl))}</span>
              <span style={{ color: t.status === "closed" ? "var(--text-muted)" : "var(--amber)", fontFamily: "monospace", fontSize: "9px" }}>{t.status}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}