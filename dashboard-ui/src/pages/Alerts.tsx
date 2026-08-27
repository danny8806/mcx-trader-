import { useData } from "../store/DataProvider";
import { formatTimestamp } from "../lib/utils";

export default function Alerts() {
  const { alerts } = useData();
  if (!alerts) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  return (
    <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
        ALERTS ({alerts.length})
      </div>
      {alerts.length === 0 ? (
        <div style={{ padding: "40px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No alerts — system operating normally</div>
      ) : (
        <div style={{ maxHeight: "500px", overflow: "auto" }}>
          {alerts.map((a: any, i: number) => (
            <div key={a.id ?? i} style={{ display: "flex", alignItems: "flex-start", gap: "10px", padding: "8px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)" }}>
              <span style={{
                width: "5px", height: "5px", borderRadius: "50%", marginTop: "4px", flexShrink: 0,
                background: a.severity === "critical" ? "var(--red)" : a.severity === "warning" ? "var(--amber)" : "var(--text-muted)",
              }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "2px" }}>
                  <span style={{ fontWeight: 500, color: "var(--text-primary)" }}>{a.type}</span>
                  {a.severity && (
                    <span style={{
                      fontSize: "8px", padding: "1px 4px", borderRadius: "2px", fontWeight: 600,
                      background: a.severity === "critical" ? "var(--red-muted)" : a.severity === "warning" ? "var(--amber-muted)" : "var(--bg-table-header)",
                      color: a.severity === "critical" ? "var(--red)" : a.severity === "warning" ? "var(--amber)" : "var(--text-muted)",
                    }}>{a.severity}</span>
                  )}
                </div>
                {a.data && <p style={{ color: "var(--text-secondary)", fontSize: "9px" }}>{typeof a.data === "string" ? a.data : JSON.stringify(a.data)}</p>}
              </div>
              <span style={{ color: "var(--text-muted)", fontSize: "9px", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>{formatTimestamp(a.timestamp)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
