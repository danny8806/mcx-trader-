import { useData } from "../store/DataProvider";

export default function Reconciliation() {
  const { reconciliation } = useData();
  if (!reconciliation || Object.keys(reconciliation).length === 0) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  const stats: Record<string, any> = reconciliation.stats || {};
  const errors: string[] = reconciliation.errors || [];
  const warnings: string[] = reconciliation.warnings || [];
  const isConsistent = reconciliation.is_consistent === true;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-primary)" }}>
            Reconciliation — {(reconciliation.phase || "live").toUpperCase()}
          </div>
          <div style={{ fontSize: "9px", color: "var(--text-muted)", marginTop: "2px" }}>
            Compares orders, fills, positions, trades, P&L and account state across the persisted DB and in-memory engine
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: isConsistent ? "var(--green)" : "var(--red)" }} />
          <span style={{ fontSize: "10px", fontWeight: 600, color: isConsistent ? "var(--green)" : "var(--red)" }}>
            {isConsistent ? "CONSISTENT" : "INCONSISTENT"}
          </span>
        </div>
      </div>

      {Object.keys(stats).length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: "8px" }}>
          {Object.entries(stats).map(([k, v]) => (
            <div key={k} style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "10px 12px" }}>
              <div style={{ fontSize: "9px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>{k.replace(/_/g, " ")}</div>
              <div style={{ fontSize: "15px", fontWeight: 600, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{String(v)}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "12px" }}>
        <div style={{ fontSize: "11px", fontWeight: 600, color: "var(--text-primary)", marginBottom: "6px" }}>Errors ({errors.length})</div>
        {errors.length === 0 ? (
          <div style={{ fontSize: "10px", color: "var(--text-muted)" }}>No errors</div>
        ) : (
          errors.map((e: string, i: number) => (
            <div key={i} style={{ fontSize: "10px", color: "var(--red)", marginBottom: "4px" }}>• {e}</div>
          ))
        )}
      </div>

      <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "12px" }}>
        <div style={{ fontSize: "11px", fontWeight: 600, color: "var(--text-primary)", marginBottom: "6px" }}>Warnings ({warnings.length})</div>
        {warnings.length === 0 ? (
          <div style={{ fontSize: "10px", color: "var(--text-muted)" }}>No warnings</div>
        ) : (
          warnings.map((w: string, i: number) => (
            <div key={i} style={{ fontSize: "10px", color: "var(--amber)", marginBottom: "4px" }}>• {w}</div>
          ))
        )}
      </div>

      {reconciliation.timestamp && (
        <div style={{ fontSize: "9px", color: "var(--text-muted)", textAlign: "right" }}>
          Last checked: {typeof reconciliation.timestamp === "number" ? new Date((reconciliation.timestamp as number) * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Kolkata" }) : String(reconciliation.timestamp)}
        </div>
      )}
    </div>
  );
}