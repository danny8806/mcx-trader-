import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { formatINR, pnlColor, safeNum, safeINR } from "../lib/utils";

type View = "open" | "closed" | "all";

export default function Positions() {
  const [view, setView] = useState<View>("open");
  const [list, setList] = useState<any[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({ open: 0, closed: 0, all: 0 });
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [o, c, a] = await Promise.all([
          api.positions({ status: "open" }),
          api.positions({ status: "closed" }),
          api.positions({ status: "all" }),
        ]);
        if (mounted.current) {
          setCounts({
            open: o?.count ?? 0,
            closed: c?.count ?? 0,
            all: a?.count ?? 0,
          });
        }
      } catch { /* ignore */ }
    })();
  }, []);

  useEffect(() => {
    let timer: number;
    const load = async () => {
      try {
        const d = await api.positions({ status: view }) as any;
        if (mounted.current) setList(Array.isArray(d?.positions) ? d.positions : []);
      } catch { /* ignore */ }
    };
    load();
    timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [view]);

  if (!list) return <div style={{ padding: "20px", color: "var(--text-muted)" }}>Loading...</div>;

  const tabs: { key: View; label: string }[] = [
    { key: "open", label: `Open (${counts.open})` },
    { key: "closed", label: `Closed (${counts.closed})` },
    { key: "all", label: `All (${counts.all})` },
  ];

  return (
    <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
          POSITIONS ({list.length})
        </span>
        <div style={{ display: "flex", gap: "4px" }}>
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setView(t.key)}
              style={{
                padding: "3px 10px", fontSize: "9px", borderRadius: "4px", cursor: "pointer",
                background: view === t.key ? "var(--accent)" : "transparent",
                color: view === t.key ? "#fff" : "var(--text-muted)",
                border: "1px solid var(--border)", fontWeight: 600,
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {list.length === 0 ? (
        <div style={{ padding: "40px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>
          {view === "open" ? "No open positions — all flat" : "No positions in this view"}
        </div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "70px 110px 50px 40px 80px 80px 55px 70px 60px 90px", gap: "8px", padding: "5px 12px", fontSize: "9px", color: "var(--text-disabled)", textTransform: "uppercase", borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-table-header)" }}>
            <span>Instrument</span><span>Strategy</span><span>Side</span><span>Qty</span><span>Entry</span><span>LTP/Exit</span><span>SL</span><span>Margin</span><span>Status</span><span style={{ textAlign: "right" }}>P&L</span>
          </div>
          {list.map((p: any) => {
            const closed = p.status === "closed";
            const lastExit = (p.exit_fills ?? []).length ? (p.exit_fills[p.exit_fills.length - 1] as any).price : null;
            const mark = closed ? lastExit : p.current_mark;
            const pnl = closed ? p.realized_pnl : p.unrealized_pnl;
            return (
              <div key={p.position_id} style={{ display: "grid", gridTemplateColumns: "70px 110px 50px 40px 80px 80px 55px 70px 60px 90px", gap: "8px", padding: "5px 12px", fontSize: "10px", borderBottom: "1px solid var(--border-subtle)", alignItems: "center" }}>
                <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{p.instrument}</span>
                <span style={{ color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.strategy_id}</span>
                <span style={{ color: p.side === "LONG" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{p.side}</span>
                <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{p.quantity}</span>
                <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{safeINR(p.average_entry)}</span>
                <span style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{mark ? safeINR(mark) : "—"}</span>
                <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{p.stop_price ? safeINR(p.stop_price) : "—"}</span>
                <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{formatINR(p.margin, false)}</span>
                <span style={{ color: closed ? "var(--text-muted)" : "var(--amber)", fontFamily: "monospace", fontSize: "9px" }}>{closed ? (p.exit_reason || "closed") : "open"}</span>
                <span style={{ color: pnlColor(pnl), fontWeight: 600, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
                  {pnl >= 0 ? "+" : ""}₹{safeNum(pnl).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                </span>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}