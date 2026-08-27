import { useData } from "../store/DataProvider";
import { formatTimestamp } from "../lib/utils";

export default function Orders() {
  const { orders } = useData();
  if (!orders) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  return (
    <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
        ORDERS ({orders.length})
      </div>
      {orders.length === 0 ? (
        <div style={{ padding: "40px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No orders placed</div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "55px 90px 70px 100px 50px 40px 60px 70px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)" }}>
            <span>Time</span><span>ID</span><span>Instrument</span><span>Strategy</span><span>Side</span><span>Qty</span><span>Type</span><span>Status</span>
          </div>
          {orders.map((o: any) => (
            <div key={o.order_id} style={{ display: "grid", gridTemplateColumns: "55px 90px 70px 100px 50px 40px 60px 70px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
              <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{formatTimestamp(o.created_at)}</span>
              <span style={{ color: "var(--text-muted)", fontFamily: "monospace", fontSize: "9px", overflow: "hidden", textOverflow: "ellipsis" }}>{o.order_id}</span>
              <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{o.instrument}</span>
              <span style={{ color: "var(--text-muted)" }}>{o.strategy_id}</span>
              <span style={{ color: o.side === "BUY" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{o.side}</span>
              <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{o.quantity}</span>
              <span style={{ color: "var(--text-muted)" }}>{o.order_type}</span>
              <span style={{
                color: o.state === "FILLED" ? "var(--green)" : o.state === "CANCELLED" ? "var(--text-muted)" : o.state === "REJECTED" ? "var(--red)" : "var(--amber)",
                fontWeight: 600, fontSize: "9px",
              }}>{o.state}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
