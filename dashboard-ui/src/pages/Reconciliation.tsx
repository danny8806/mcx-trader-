import { useData } from "../store/DataProvider";
import { formatTimestamp } from "../lib/utils";

export default function Reconciliation() {
  const { reconciliation } = useData();
  if (!reconciliation || Object.keys(reconciliation).length === 0) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  const sections = [
    { key: "market_data", label: "Market Data" },
    { key: "execution", label: "Execution" },
    { key: "position", label: "Positions" },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {sections.map(({ key, label }) => {
        const section = reconciliation[key];
        if (!section) return null;
        return (
          <div key={key} style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "12px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-primary)" }}>{label}</span>
              <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: section.status === "ok" || section.status === "healthy" ? "var(--green)" : "var(--amber)" }} />
                <span style={{ fontSize: "9px", color: "var(--text-muted)" }}>{section.status?.toUpperCase()}</span>
              </div>
            </div>
            {section.message && <p style={{ fontSize: "10px", color: "var(--text-secondary)" }}>{section.message}</p>}
            {section.last_run && <p style={{ fontSize: "9px", color: "var(--text-muted)", marginTop: "4px" }}>Last: {formatTimestamp(section.last_run)}</p>}
          </div>
        );
      })}
      {reconciliation.timestamp && (
        <div style={{ fontSize: "9px", color: "var(--text-muted)", textAlign: "right" }}>
          Last checked: {formatTimestamp(reconciliation.timestamp)}
        </div>
      )}
    </div>
  );
}
