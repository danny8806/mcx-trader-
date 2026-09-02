import { useDataSelector } from "../store/DataProvider";

export default function Health() {
  const healthComponents = useDataSelector<any[]>((s) => s.healthComponents);
  const overallHealth = useDataSelector<string>((s) => s.overallHealth);
  if (!healthComponents) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  function formatName(name: string): string {
    return name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{
        background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px",
        padding: "12px", display: "flex", alignItems: "center", gap: "10px",
      }}>
        <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: overallHealth === "healthy" ? "var(--green)" : overallHealth === "degraded" ? "var(--amber)" : "var(--red)" }} />
        <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)" }}>
          System Status: {overallHealth.toUpperCase()}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "8px" }}>
        {healthComponents.map((c: any) => (
          <div key={c.name} style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "10px 12px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <span style={{ fontSize: "11px", fontWeight: 500, color: "var(--text-primary)" }}>{formatName(c.name)}</span>
              <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: c.status === "healthy" ? "var(--green)" : c.status === "degraded" ? "var(--amber)" : "var(--red)" }} />
                <span style={{ fontSize: "9px", color: c.status === "healthy" ? "var(--green)" : c.status === "degraded" ? "var(--amber)" : "var(--red)" }}>
                  {c.status?.toUpperCase()}
                </span>
              </div>
            </div>
            <div style={{ fontSize: "9px", color: "var(--text-muted)" }}>
              {c.errors > 0 && <div style={{ color: "var(--red)" }}>{c.errors} errors</div>}
              {c.uptime > 0 && <div>Uptime: {c.uptime < 60 ? `${c.uptime}s` : c.uptime < 3600 ? `${Math.floor(c.uptime / 60)}m` : `${Math.floor(c.uptime / 3600)}h`}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
