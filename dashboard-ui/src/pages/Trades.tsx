import { useState } from "react";
import { useDataSelector } from "../store/DataProvider";
import { formatDT, formatINR, pnlColor, safeNum } from "../lib/utils";

interface Trade {
  trade_id: string;
  strategy_id: string;
  instrument: string;
  entry_side: string;
  entry_action: string;
  entry_event_type: string;
  entry_signal_id: string;
  entry_trigger_price: number;
  entry_price: number;
  entry_timestamp: number | string;
  stop_loss_price: number;
  position_id: string;
  quantity: number;
  multiplier: number;
  pending_order_id: string;
  pending_status: string;
  entry_order_id: string;
  entry_fill_id: string;
  exit_signal_id: string;
  exit_order_id: string;
  exit_fill_id: string;
  exit_type: string;
  exit_action: string;
  exit_event_type: string;
  exit_reason: string;
  exit_price: number;
  exit_timestamp: number | string;
  gross_pnl: number;
  charges: number;
  net_pnl: number;
  status: string;
}

function TradeRow({ trade, isExpanded, onToggle }: {
  trade: Trade;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const statusColor = trade.status === "CLOSED" ? "var(--text-muted)" :
    trade.status === "OPEN" ? "var(--green)" :
    trade.status === "PENDING" ? "var(--amber)" : "var(--text-muted)";

  return (
    <>
      <div
        className="hover-row"
        onClick={onToggle}
        style={{
          display: "grid",
          gridTemplateColumns: "150px 70px 60px 100px 50px 35px 80px 80px 80px 90px 90px 60px",
          gap: "8px",
          padding: "5px 12px",
          fontSize: "10px",
          borderBottom: "1px solid var(--border-subtle)",
          alignItems: "center",
          cursor: "pointer",
        }}
      >
        <span className="tabular-nums" style={{ color: "var(--text-muted)" }}>
          {formatDT(trade.entry_timestamp)}
        </span>
        <span className="tabular-nums" style={{ color: "var(--text-muted)" }}>
          {formatDT(trade.exit_timestamp)}
        </span>
        <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>
          {trade.instrument}
        </span>
        <span style={{
          color: "var(--text-muted)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>
          {trade.strategy_id}
        </span>
        <span style={{
          color: trade.entry_side === "LONG" ? "var(--green)" : "var(--red)",
          fontWeight: 600,
        }}>
          {trade.entry_side}
        </span>
        <span className="tabular-nums" style={{ color: "var(--text-secondary)" }}>
          {trade.quantity}
        </span>
        <span className="tabular-nums" style={{ color: "var(--text-secondary)", textAlign: "right" }}>
          ₹{safeNum(trade.entry_price).toLocaleString("en-IN")}
        </span>
        <span className="tabular-nums" style={{ color: "var(--text-secondary)", textAlign: "right" }}>
          ₹{safeNum(trade.exit_price).toLocaleString("en-IN")}
        </span>
        <span className="tabular-nums" style={{ color: pnlColor(Number(trade.gross_pnl)), textAlign: "right" }}>
          {formatINR(safeNum(trade.gross_pnl))}
        </span>
        <span className="tabular-nums" style={{
          color: pnlColor(Number(trade.net_pnl)),
          fontWeight: 600,
          textAlign: "right",
        }}>
          {formatINR(safeNum(trade.net_pnl))}
        </span>
        <span style={{
          color: statusColor,
          fontFamily: "monospace",
          fontSize: "9px",
        }}>
          {trade.status}
        </span>
        <span style={{ color: "var(--text-muted)", fontSize: "8px" }}>
          {isExpanded ? "▼" : "▶"}
        </span>
      </div>

      {isExpanded && (
        <div style={{
          padding: "8px 12px 8px 24px",
          borderBottom: "1px solid var(--border-subtle)",
          background: "var(--bg-table-header)",
          fontSize: "9px",
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: "8px",
        }}>
          {/* Entry Signal */}
          <div>
            <div style={{ color: "var(--text-disabled)", marginBottom: "4px", fontWeight: 600 }}>ENTRY SIGNAL</div>
            <div style={{ color: "var(--text-primary)" }}>{trade.entry_signal_id ? trade.entry_signal_id.slice(0, 12) + "..." : "—"}</div>
            <div style={{ color: "var(--text-muted)" }}>Event: {trade.entry_event_type || "—"}</div>
            <div style={{ color: "var(--text-muted)" }}>Action: {trade.entry_action || "—"}</div>
            <div style={{ color: "var(--text-muted)" }}>Trigger: ₹{safeNum(trade.entry_trigger_price).toLocaleString("en-IN")}</div>
          </div>

          {/* Position */}
          <div>
            <div style={{ color: "var(--text-disabled)", marginBottom: "4px", fontWeight: 600 }}>POSITION</div>
            <div style={{ color: "var(--text-primary)" }}>ID: {trade.position_id ? trade.position_id.slice(0, 12) + "..." : "—"}</div>
            <div style={{ color: "var(--text-muted)" }}>Fill: {trade.entry_fill_id ? trade.entry_fill_id.slice(0, 12) + "..." : "—"}</div>
            <div style={{ color: "var(--text-muted)" }}>Order: {trade.entry_order_id ? trade.entry_order_id.slice(0, 12) + "..." : "—"}</div>
            <div style={{ color: "var(--text-muted)" }}>SL: ₹{safeNum(trade.stop_loss_price).toLocaleString("en-IN")}</div>
          </div>

          {/* Exit */}
          <div>
            <div style={{ color: "var(--text-disabled)", marginBottom: "4px", fontWeight: 600 }}>EXIT</div>
            <div style={{ color: trade.exit_type ? "var(--text-primary)" : "var(--text-muted)" }}>
              {trade.exit_type || "—"}
            </div>
            <div style={{ color: "var(--text-muted)" }}>
              Reason: {trade.exit_reason || "—"}
            </div>
            <div style={{ color: "var(--text-muted)" }}>
              Signal: {trade.exit_signal_id ? trade.exit_signal_id.slice(0, 12) + "..." : "— (SL/No signal)"}
            </div>
            <div style={{ color: "var(--text-muted)" }}>
              Exit Fill: {trade.exit_fill_id ? trade.exit_fill_id.slice(0, 12) + "..." : "—"}
            </div>
          </div>

          {/* P&L */}
          <div>
            <div style={{ color: "var(--text-disabled)", marginBottom: "4px", fontWeight: 600 }}>P&L</div>
            <div style={{ color: pnlColor(Number(trade.gross_pnl)) }}>
              Gross: {formatINR(safeNum(trade.gross_pnl))}
            </div>
            <div style={{ color: "var(--text-muted)" }}>
              Charges: {formatINR(safeNum(trade.charges))}
            </div>
            <div style={{ color: pnlColor(Number(trade.net_pnl)), fontWeight: 600 }}>
              Net: {formatINR(safeNum(trade.net_pnl))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default function Trades() {
  const trades = useDataSelector<any[]>((s) => s.trades);
  const [expandedTradeId, setExpandedTradeId] = useState<string | null>(null);

  if (!trades) return (
    <div style={{ padding: "20px", color: "var(--text-muted)" }}>
      <div className="skeleton" style={{ width: "200px", height: "14px", marginBottom: "12px" }} />
      <div className="skeleton" style={{ width: "100%", height: "200px" }} />
      <div style={{ fontSize: "11px", marginTop: "8px" }}>Loading trades...</div>
    </div>
  );

  return (
    <div className="lift animate-fade-in-up" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "8px", overflow: "hidden" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: "10px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
        TRADEBOOK ({trades.length}) — FULL LIFECYCLE
      </div>
      {trades.length === 0 ? (
        <div className="animate-fade-in-up" style={{ padding: "40px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No completed trades recorded</div>
      ) : (
        <>
          <div style={{
            display: "grid",
            gridTemplateColumns: "150px 70px 60px 100px 50px 35px 80px 80px 80px 90px 90px 60px",
            gap: "8px",
            padding: "5px 12px",
            fontSize: "9px",
            color: "var(--text-disabled)",
            textTransform: "uppercase",
            borderBottom: "1px solid var(--border-subtle)",
            background: "var(--bg-table-header)",
            position: "sticky",
            top: 0,
            zIndex: 1,
          }}>
            <span>Entry</span>
            <span>Exit</span>
            <span>Instrument</span>
            <span>Strategy</span>
            <span>Side</span>
            <span>Qty</span>
            <span style={{ textAlign: "right" }}>Entry</span>
            <span style={{ textAlign: "right" }}>Exit</span>
            <span style={{ textAlign: "right" }}>Gross</span>
            <span style={{ textAlign: "right" }}>Net P&L</span>
            <span>Status</span>
            <span></span>
          </div>
          {trades.map((t: any) => (
            <TradeRow
              key={t.trade_id}
              trade={t}
              isExpanded={expandedTradeId === t.trade_id}
              onToggle={() => setExpandedTradeId(expandedTradeId === t.trade_id ? null : t.trade_id)}
            />
          ))}
        </>
      )}
    </div>
  );
}
