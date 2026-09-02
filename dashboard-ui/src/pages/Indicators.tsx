import { useDataSelector } from "../store/DataProvider";

export default function Indicators() {
  const indicators = useDataSelector<any>((s) => s.indicators);
  const htf = useDataSelector<any>((s) => s.htf);
  if (!indicators) return (
    <div style={{ padding: "20px", color: "var(--text-muted)" }}>
      <div className="skeleton" style={{ width: "100%", height: "120px", marginBottom: "10px" }} />
      <div className="skeleton" style={{ width: "100%", height: "120px" }} />
      <div style={{ fontSize: "11px", marginTop: "8px" }}>Loading indicators...</div>
    </div>
  );

  const entries = Object.entries(indicators);
  const htfEntries = Object.entries(htf);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div className="lift animate-fade-in-up" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
          DEMA-ATR INDICATORS ({entries.length})
        </div>
        {entries.length === 0 ? (
          <div className="animate-fade-in-up" style={{ padding: "24px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No indicator data</div>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "110px 80px 80px 80px 50px 50px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)", position: "sticky", top: 0, zIndex: 1 }}>
              <span>Key</span><span style={{ textAlign: "right" }}>DEMA</span><span style={{ textAlign: "right" }}>ATR</span><span style={{ textAlign: "right" }}>Prev</span><span style={{ textAlign: "right" }}>Count</span><span>Init</span>
            </div>
            {entries.map(([key, data]: [string, any]) => (
              <div key={key} className="hover-row" style={{ display: "grid", gridTemplateColumns: "110px 80px 80px 80px 50px 50px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
                <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{key}</span>
                <span className="tabular-nums" style={{ color: "var(--text-primary)", textAlign: "right" }}>{data.dema_value?.toFixed(2) ?? "—"}</span>
                <span className="tabular-nums" style={{ color: "var(--text-primary)", textAlign: "right" }}>{data.atr_value?.toFixed(2) ?? "—"}</span>
                <span className="tabular-nums" style={{ color: "var(--text-muted)", textAlign: "right" }}>{data.prev_output?.toFixed(2) ?? "—"}</span>
                <span className="tabular-nums" style={{ color: "var(--text-secondary)", textAlign: "right" }}>{data.count}</span>
                <span style={{ color: data.initialized ? "var(--green)" : "var(--text-disabled)" }}>{data.initialized ? "YES" : "NO"}</span>
              </div>
            ))}
          </>
        )}
      </div>

      <div className="lift animate-fade-in-up" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
          HTF CONFIRMATION ({htfEntries.length})
        </div>
        {htfEntries.length === 0 ? (
          <div className="animate-fade-in-up" style={{ padding: "24px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No HTF data</div>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "110px 80px 80px 80px 50px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)", position: "sticky", top: 0, zIndex: 1 }}>
              <span>Key</span><span style={{ textAlign: "right" }}>Confirmed</span><span style={{ textAlign: "right" }}>Prev</span><span style={{ textAlign: "right" }}>Source</span><span>Init</span>
            </div>
            {htfEntries.map(([key, data]: [string, any]) => (
              <div key={key} className="hover-row" style={{ display: "grid", gridTemplateColumns: "110px 80px 80px 80px 50px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
                <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{key}</span>
                <span className="tabular-nums" style={{ color: "var(--text-primary)", textAlign: "right" }}>{data.last_confirmed_value?.toFixed(2) ?? "—"}</span>
                <span className="tabular-nums" style={{ color: "var(--text-muted)", textAlign: "right" }}>{data.prev_confirmed_value?.toFixed(2) ?? "—"}</span>
                <span style={{ color: "var(--text-muted)", fontSize: "9px", textAlign: "right" }}>{data.source_timestamp ? new Date(data.source_timestamp * 1000).toLocaleTimeString("en-IN", { hour12: false }) : "—"}</span>
                <span style={{ color: data.indicator?.initialized ? "var(--green)" : "var(--text-disabled)" }}>{data.indicator?.initialized ? "YES" : "NO"}</span>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
