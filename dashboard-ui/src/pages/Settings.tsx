import { useData } from "../store/DataProvider";

export default function Settings() {
  const { settings } = useData();
  if (!settings || Object.keys(settings).length === 0) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  const sections = [
    { key: "system", label: "System" },
    { key: "instruments", label: "Instruments" },
    { key: "strategies", label: "Strategies" },
    { key: "indicators", label: "Indicators" },
    { key: "risk", label: "Risk" },
    { key: "account", label: "Account" },
    { key: "paper_execution", label: "Paper Execution" },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
      {sections.map(({ key, label }) => {
        const data = settings[key];
        if (!data) return null;
        return (
          <div key={key} style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "12px" }}>
            <div style={{ fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "8px" }}>{label}</div>
            {typeof data === "object" && data !== null ? (
              Object.entries(data).map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", padding: "3px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                  <span style={{ color: "var(--text-muted)" }}>{k}</span>
                  <span style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums", marginLeft: "8px", overflow: "hidden", textOverflow: "ellipsis", maxWidth: "200px", whiteSpace: "nowrap", textAlign: "right" }}>
                    {typeof v === "object" ? JSON.stringify(v) : String(v ?? "")}
                  </span>
                </div>
              ))
            ) : (
              <div style={{ color: "var(--text-secondary)", fontSize: "10px" }}>{String(data)}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}
