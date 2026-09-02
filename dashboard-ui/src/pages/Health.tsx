import { useDataSelector } from "../store/DataProvider";

export default function Health() {
  const healthComponents = useDataSelector<any[]>((s) => s.healthComponents);
  const overallHealth = useDataSelector<string>((s) => s.overallHealth);
  if (!healthComponents) return (
    <div style={{ padding: "20px", color: "var(--text-muted)" }}>
      <div className="skeleton" style={{ width: "250px", height: "36px", marginBottom: "12px" }} />
      <div className="skeleton" style={{ width: "100%", height: "120px" }} />
      <div style={{ fontSize: "11px", marginTop: "8px" }}>Loading health data...</div>
    </div>
  );

  function formatName(name: string): string {
    return name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div className="lift animate-fade-in-up" style={{
        background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px",
        padding: "12px", display: "flex", alignItems: "center", gap: "10px",
      }}>
        <span
          className={overallHealth === "healthy" ? "animate-pulse-dot" : ""}
          style={{ width: "8px", height: "8px", borderRadius: "50%", background: overallHealth === "healthy" ? "var(--green)" : overallHealth === "degraded" ? "var(--amber)" : "var(--red)", ["--dot" as any]: overallHealth === "healthy" ? "var(--green)" : overallHealth === "degraded" ? "var(--amber)" : "var(--red)" }}
        />
        <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)" }}>
          System Status: {(overallHealth ?? "unknown").toUpperCase()}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "8px" }}>
        {healthComponents.map((c: any) => {
          const isHealthy = c.status === "healthy";
          return (
            <div key={c.name} className="lift animate-fade-in-up" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px", padding: "10px 12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                <span style={{ fontSize: "11px", fontWeight: 500, color: "var(--text-primary)" }}>{formatName(c.name)}</span>
                <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                  <span
                    className={isHealthy ? "animate-pulse-dot" : ""}
                    style={{ width: "5px", height: "5px", borderRadius: "50%", background: c.status === "healthy" ? "var(--green)" : c.status === "degraded" ? "var(--amber)" : "var(--red)", ["--dot" as any]: c.status === "healthy" ? "var(--green)" : c.status === "degraded" ? "var(--amber)" : "var(--red)" }}
                  />
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
          );
        })}
      </div>
    </div>
  );
}
