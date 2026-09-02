import { useDataSelector } from "../store/DataProvider";
import { safeINR } from "../lib/utils";

export default function MarketData() {
  const marketData = useDataSelector<any>((s) => s.marketData);
  if (!marketData) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  const instruments = marketData.instruments ?? {};
  const adapterStats = marketData.adapter_stats ?? {};

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ display: "flex", gap: "20px", alignItems: "center", background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "8px 12px", fontSize: "10px", color: "var(--text-muted)" }}>
        <span>Feed: <span style={{ color: marketData.ws_connected ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{marketData.ws_connected ? "CONNECTED" : "DISCONNECTED"}</span></span>
        <span>Session ticks: <span style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{String(adapterStats.tick_count ?? 0)}</span></span>
        {adapterStats.error_count ? <span>Errors: <span style={{ color: "var(--red)" }}>{adapterStats.error_count}</span></span> : null}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
        {Object.entries(instruments).map(([name, data]: [string, any]) => {
          const isLive = data.ltp > 0;
          return (
            <div key={name} style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-primary)" }}>{name}</span>
                <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                  <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: isLive ? "var(--green)" : "var(--red)" }} />
                  <span style={{ fontSize: "9px", color: isLive ? "var(--green)" : "var(--red)" }}>{isLive ? "LIVE" : "NO DATA"}</span>
                </div>
              </div>
              <div style={{ fontSize: "28px", fontWeight: 700, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums", marginBottom: "12px" }}>
                {isLive ? safeINR(data.ltp) : "—"}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px", fontSize: "10px" }}>
                {[["Spread", data.spread > 0 ? `₹${data.spread}` : "—"], ["Ticks", String(data.tick_count ?? 0)], ["Last Update", data.timestamp > 0 ? new Date(data.timestamp * 1000).toLocaleTimeString("en-IN", { hour12: false }) : "—"]].map(([k, v]) => (
                  <div key={String(k)} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                    <span style={{ color: "var(--text-muted)" }}>{String(k)}</span>
                    <span style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{String(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
        {Object.keys(instruments).length === 0 && (
          <div style={{ gridColumn: "1/-1", padding: "40px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No market data available</div>
        )}
      </div>
    </div>
  );
}
