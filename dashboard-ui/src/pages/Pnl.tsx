import { useDataSelector } from "../store/DataProvider";
import { formatINR, pnlColor } from "../lib/utils";
import EquityCurveChart from "../components/EquityCurveChart";

export default function Pnl() {
  const pnl = useDataSelector<any>((s) => s.pnl);
  const pnlByInstrument = useDataSelector<Record<string, any>>((s) => s.pnlByInstrument);
  const equityCurve = useDataSelector<any[]>((s) => s.equityCurve);
  if (!pnl) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  const instruments = Object.entries(pnlByInstrument);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "8px" }}>
        {[
          ["NET P&L", pnl.net_pnl],
          ["REALIZED", pnl.realized_pnl],
          ["UNREALIZED", pnl.unrealized_pnl],
          ["CHARGES", pnl.charges],
          ["EQUITY", pnl.equity],
        ].map(([label, val]) => (
          <div key={String(label)} style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "10px 12px" }}>
            <div style={{ fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "4px" }}>{String(label)}</div>
            <div style={{ fontSize: "20px", fontWeight: 700, color: pnlColor(Number(val)), fontVariantNumeric: "tabular-nums" }}>
              {formatINR(Number(val))}
            </div>
          </div>
        ))}
      </div>

      <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" }}>
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
          EQUITY CURVE
        </div>
        <div style={{ padding: "12px" }}>
          <EquityCurveChart points={equityCurve ?? []} />
        </div>
      </div>

      <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" }}>
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
          BY INSTRUMENT
        </div>
        {instruments.length === 0 ? (
          <div style={{ padding: "24px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No instrument data</div>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "80px 1fr 1fr 1fr 60px 50px 50px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)" }}>
              <span>Instrument</span><span style={{ textAlign: "right" }}>Gross</span><span style={{ textAlign: "right" }}>Charges</span><span style={{ textAlign: "right" }}>Net</span><span style={{ textAlign: "right" }}>Trades</span><span style={{ textAlign: "right" }}>Wins</span><span style={{ textAlign: "right" }}>Win%</span>
            </div>
            {instruments.map(([inst, data]: [string, any]) => (
              <div key={inst} style={{ display: "grid", gridTemplateColumns: "80px 1fr 1fr 1fr 60px 50px 50px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
                <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{inst}</span>
                <span style={{ color: pnlColor(data.realized_gross), fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{formatINR(data.realized_gross)}</span>
                <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{formatINR(data.realized_charges)}</span>
                <span style={{ color: pnlColor(data.realized_net), fontWeight: 600, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{formatINR(data.realized_net)}</span>
                <span style={{ color: "var(--text-secondary)", textAlign: "right" }}>{data.trade_count}</span>
                <span style={{ color: "var(--green)", textAlign: "right" }}>{data.wins}</span>
                <span style={{ color: "var(--text-secondary)", textAlign: "right" }}>{(data.win_rate * 100).toFixed(1)}%</span>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
