import { useData } from "../store/DataProvider";
import { formatINR, pnlColor, safeNum, safeINR } from "../lib/utils";

export default function Positions() {
  const { positions } = useData();
  if (!positions) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;
  const open = positions.filter((p: any) => p.is_open);

  return (
    <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
        OPEN POSITIONS ({open.length})
      </div>
      {open.length === 0 ? (
        <div style={{ padding: "40px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No open positions — all flat</div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "70px 110px 50px 40px 80px 80px 50px 70px 90px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)" }}>
            <span>Instrument</span><span>Strategy</span><span>Side</span><span>Qty</span><span>Entry</span><span>LTP</span><span>SL</span><span>Margin</span><span style={{ textAlign: "right" }}>P&L</span>
          </div>
          {open.map((p: any) => (
            <div key={p.position_id} style={{ display: "grid", gridTemplateColumns: "70px 110px 50px 40px 80px 80px 50px 70px 90px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
              <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{p.instrument}</span>
              <span style={{ color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.strategy_id}</span>
              <span style={{ color: p.side === "BUY" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{p.side}</span>
              <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{p.quantity}</span>
              <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{safeINR(p.average_entry)}</span>
              <span style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{safeINR(p.current_mark)}</span>
              <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{p.stop_price ? safeINR(p.stop_price) : "—"}</span>
              <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{formatINR(p.margin, false)}</span>
              <span style={{ color: pnlColor(p.unrealized_pnl), fontWeight: 600, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
                {p.unrealized_pnl >= 0 ? "+" : ""}₹{safeNum(p.unrealized_pnl).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
