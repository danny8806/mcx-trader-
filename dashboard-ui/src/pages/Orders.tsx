import { useDataSelector } from "../store/DataProvider";
import { formatTimestamp } from "../lib/utils";

export default function Orders() {
  const orders = useDataSelector<any>((s) => s.orders);
  if (!orders) return (
    <div style={{ padding: "20px", color: "var(--text-muted)" }}>
      <div className="skeleton" style={{ width: "180px", height: "14px", marginBottom: "12px" }} />
      <div className="skeleton" style={{ width: "100%", height: "200px" }} />
      <div style={{ fontSize: "11px", marginTop: "8px" }}>Loading orders...</div>
    </div>
  );

  return (
    <div className="lift animate-fade-in-up" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
        ORDERS ({orders.length})
      </div>
      {orders.length === 0 ? (
        <div className="animate-fade-in-up" style={{ padding: "40px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No orders placed</div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "55px 90px 70px 100px 50px 40px 60px 70px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)", position: "sticky", top: 0, zIndex: 1 }}>
            <span>Time</span><span>ID</span><span>Instrument</span><span>Strategy</span><span>Side</span><span>Qty</span><span>Type</span><span>Status</span>
          </div>
          {orders.map((o: any) => (
            <div key={o.order_id} className="hover-row" style={{ display: "grid", gridTemplateColumns: "55px 90px 70px 100px 50px 40px 60px 70px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
              <span className="tabular-nums" style={{ color: "var(--text-muted)" }}>{formatTimestamp(o.created_at)}</span>
              <span style={{ color: "var(--text-muted)", fontFamily: "monospace", fontSize: "9px", overflow: "hidden", textOverflow: "ellipsis" }}>{o.order_id}</span>
              <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{o.instrument}</span>
              <span style={{ color: "var(--text-muted)" }}>{o.strategy_id}</span>
              <span style={{ color: o.side === "BUY" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{o.side}</span>
              <span className="tabular-nums" style={{ color: "var(--text-secondary)" }}>{o.quantity}</span>
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
