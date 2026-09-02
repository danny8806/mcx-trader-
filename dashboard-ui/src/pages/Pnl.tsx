import { useDataSelector } from "../store/DataProvider";
import { formatINR, pnlColor } from "../lib/utils";
import EquityCurveChart from "../components/EquityCurveChart";

export default function Pnl() {
  const pnl = useDataSelector<any>((s) => s.pnl);
  const pnlByInstrument = useDataSelector<Record<string, any>>((s) => s.pnlByInstrument);
  const equityCurve = useDataSelector<any[]>((s) => s.equityCurve);
  if (!pnl) return (
    <div style={{ padding: "20px", color: "var(--text-muted)" }}>
      <div className="skeleton" style={{ width: "100%", height: "52px", marginBottom: "8px" }} />
      <div className="skeleton" style={{ width: "100%", height: "200px", marginBottom: "10px" }} />
      <div className="skeleton" style={{ width: "100%", height: "120px" }} />
      <div style={{ fontSize: "11px", marginTop: "8px" }}>Loading P&L data...</div>
    </div>
  );

  const instruments = Object.entries(pnlByInstrument);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div className="metric-grid">
        {[
          ["NET P&L", pnl.net_pnl],
          ["REALIZED", pnl.realized_pnl],
          ["UNREALIZED", pnl.unrealized_pnl],
          ["CHARGES", pnl.charges],
          ["EQUITY", pnl.equity],
        ].map(([label, val]) => (
          <div key={String(label)} className="lift animate-fade-in-up" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px", padding: "10px 12px" }}>
            <div style={{ fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "4px" }}>{String(label)}</div>
            <div className="tabular-nums" style={{ fontSize: "20px", fontWeight: 700, color: pnlColor(Number(val)) }}>
              {formatINR(Number(val))}
            </div>
          </div>
        ))}
      </div>

      <div className="lift animate-fade-in-up" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
          EQUITY CURVE
        </div>
        <div style={{ padding: "12px" }}>
          <EquityCurveChart points={equityCurve ?? []} />
        </div>
      </div>

      <div className="lift animate-fade-in-up" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
          BY INSTRUMENT
        </div>
        {instruments.length === 0 ? (
          <div className="animate-fade-in-up" style={{ padding: "24px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No instrument data</div>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "80px 1fr 1fr 1fr 60px 50px 50px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)", position: "sticky", top: 0, zIndex: 1 }}>
              <span>Instrument</span><span style={{ textAlign: "right" }}>Gross</span><span style={{ textAlign: "right" }}>Charges</span><span style={{ textAlign: "right" }}>Net</span><span style={{ textAlign: "right" }}>Trades</span><span style={{ textAlign: "right" }}>Wins</span><span style={{ textAlign: "right" }}>Win%</span>
            </div>
            {instruments.map(([inst, data]: [string, any]) => (
              <div key={inst} className="hover-row" style={{ display: "grid", gridTemplateColumns: "80px 1fr 1fr 1fr 60px 50px 50px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
                <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{inst}</span>
                <span className="tabular-nums" style={{ color: pnlColor(data.realized_gross), textAlign: "right" }}>{formatINR(data.realized_gross)}</span>
                <span className="tabular-nums" style={{ color: "var(--text-muted)", textAlign: "right" }}>{formatINR(data.realized_charges)}</span>
                <span className="tabular-nums" style={{ color: pnlColor(data.realized_net), fontWeight: 600, textAlign: "right" }}>{formatINR(data.realized_net)}</span>
                <span className="tabular-nums" style={{ color: "var(--text-secondary)", textAlign: "right" }}>{data.trade_count}</span>
                <span className="tabular-nums" style={{ color: "var(--green)", textAlign: "right" }}>{data.wins}</span>
                <span className="tabular-nums" style={{ color: "var(--text-secondary)", textAlign: "right" }}>{(data.win_rate * 100).toFixed(1)}%</span>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
