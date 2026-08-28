import { useData } from "../store/DataProvider";
import { formatTimestamp, safeINR } from "../lib/utils";

export default function Trades() {
  const { fills } = useData();
  if (!fills) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  return (
    <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
        TRADES ({fills.length})
      </div>
      {fills.length === 0 ? (
        <div style={{ padding: "40px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No trades recorded</div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "55px 90px 70px 100px 50px 40px 90px 40px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)" }}>
            <span>Time</span><span>ID</span><span>Instrument</span><span>Strategy</span><span>Side</span><span>Qty</span><span>Price</span><span>Mult</span>
          </div>
          {fills.map((f: any) => (
            <div key={f.fill_id} style={{ display: "grid", gridTemplateColumns: "55px 90px 70px 100px 50px 40px 90px 40px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
              <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{formatTimestamp(f.timestamp)}</span>
              <span style={{ color: "var(--text-muted)", fontFamily: "monospace", fontSize: "9px", overflow: "hidden", textOverflow: "ellipsis" }}>{f.fill_id}</span>
              <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{f.instrument}</span>
              <span style={{ color: "var(--text-muted)" }}>{f.strategy_id}</span>
              <span style={{ color: f.side === "BUY" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{f.side}</span>
              <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{f.quantity}</span>
              <span style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{safeINR(f.price)}</span>
              <span style={{ color: "var(--text-muted)" }}>×{f.multiplier}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
