import { useData } from "../store/DataProvider";
import { formatINR, formatPct, pnlColor, statusDot, formatTimestamp, safeNum, safeINR } from "../lib/utils";

const panelStyle: React.CSSProperties = {
  background: "var(--bg-panel)",
  border: "1px solid var(--border)",
  borderRadius: "6px",
  overflow: "hidden",
};

const panelHeader: React.CSSProperties = {
  padding: "8px 12px",
  borderBottom: "1px solid var(--border-subtle)",
  fontSize: "10px",
  fontWeight: 600,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
};

function MetricCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "6px", padding: "10px 12px" }}>
      <div style={{ fontSize: "9px", fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "4px" }}>
        {label}
      </div>
      <div style={{ fontSize: "20px", fontWeight: 700, color: color ?? "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: "10px", color: "var(--text-muted)", marginTop: "2px" }}>{sub}</div>}
    </div>
  );
}

function MarketCard({ name, ltp, status: _status, overview }: { name: string; ltp: number; status: string; overview: any }) {
  const isLive = ltp > 0;
  return (
    <div style={{ ...panelStyle, padding: "12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
        <span style={{ fontSize: "11px", fontWeight: 600, color: "var(--text-primary)" }}>{name}</span>
        <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
          <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: isLive ? "var(--green)" : "var(--red)" }} />
          <span style={{ fontSize: "9px", color: isLive ? "var(--green)" : "var(--red)" }}>
            {isLive ? "LIVE" : "NO DATA"}
          </span>
        </div>
      </div>
      <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums", marginBottom: "8px" }}>
        {isLive ? safeINR(ltp) : "—"}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px", fontSize: "10px" }}>
        {overview?.strategies?.map((s: any) => (
          <div key={s.strategy_id} style={{ display: "flex", alignItems: "center", gap: "4px", padding: "3px 6px", background: "var(--bg-table-header)", borderRadius: "3px" }}>
            <span style={{ width: "4px", height: "4px", borderRadius: "50%", background: statusDot(s.status) }} />
            <span style={{ color: "var(--text-secondary)", fontSize: "9px" }}>{s.strategy_id}</span>
            {s.position_side && (
              <span style={{ color: s.position_side === "LONG" ? "var(--green)" : "var(--red)", fontSize: "9px", fontWeight: 600 }}>
                {s.position_side}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function StrategyRow({ s }: { s: any }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "120px 40px 40px 55px 55px 1fr 70px",
      gap: "8px",
      alignItems: "center",
      padding: "5px 12px",
      fontSize: "10px",
      borderBottom: "1px solid var(--border-subtle)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
        <span style={{ width: "4px", height: "4px", borderRadius: "50%", background: statusDot(s.state) }} />
        <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{s.strategy_id}</span>
      </div>
      <span style={{ color: "var(--text-muted)" }}>{s.fast_timeframe}</span>
      <span style={{ color: "var(--text-muted)" }}>{s.htf_timeframe}</span>
      <span style={{
        color: s.position_side === "LONG" ? "var(--green)" : s.position_side === "SHORT" ? "var(--red)" : "var(--text-muted)",
        fontWeight: s.position_side ? 600 : 400,
      }}>
        {s.position_side ?? "FLAT"}
      </span>
      <span style={{ color: "var(--text-secondary)" }}>{s.trade_count}</span>
      <span style={{ color: "var(--text-muted)" }}>{(s.win_rate * 100).toFixed(0)}%</span>
      <span style={{ color: pnlColor(s.realized_net), fontWeight: 600, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
        {formatINR(s.realized_net)}
      </span>
    </div>
  );
}

function PositionRow({ p }: { p: any }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "70px 100px 50px 40px 80px 50px 90px",
      gap: "8px",
      alignItems: "center",
      padding: "5px 12px",
      fontSize: "10px",
      borderBottom: "1px solid var(--border-subtle)",
    }}>
      <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{p.instrument}</span>
      <span style={{ color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.strategy_id}</span>
      <span style={{ color: p.side === "BUY" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{p.side}</span>
      <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{p.quantity}</span>
      <span style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>{safeINR(p.average_entry)}</span>
      <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>{p.stop_price ? safeINR(p.stop_price) : "—"}</span>
      <span style={{ color: pnlColor(p.unrealized_pnl), fontWeight: 600, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
        {p.unrealized_pnl >= 0 ? "+" : ""}₹{safeNum(p.unrealized_pnl).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
      </span>
    </div>
  );
}

function FillRow({ f }: { f: any }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: "10px",
      padding: "4px 12px", fontSize: "10px",
      borderBottom: "1px solid var(--border-subtle)",
    }}>
      <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums", width: "55px" }}>
        {formatTimestamp(f.timestamp)}
      </span>
      <span style={{ color: "var(--text-primary)", fontWeight: 500, width: "55px" }}>{f.instrument}</span>
      <span style={{ color: f.side === "BUY" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>{f.side}</span>
      <span style={{ color: "var(--text-secondary)" }}>{f.quantity}</span>
      <span style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{safeINR(f.price)}</span>
      <div style={{ flex: 1 }} />
      <span style={{ color: "var(--text-muted)", fontSize: "9px" }}>{f.strategy_id}</span>
    </div>
  );
}

export default function Overview() {
  const { overview, goldOverview, silverOverview, strategies, positions, fills, connected } = useData();

  if (!overview) {
    return (
      <div style={{ padding: "20px", color: "var(--text-muted)" }}>
        <div style={{ fontSize: "11px" }}>Loading dashboard...</div>
      </div>
    );
  }

  const goldStrats = strategies.filter((s: any) => s.instrument === "GOLDM");
  const silverStrats = strategies.filter((s: any) => s.instrument === "SILVERM");
  const openPositions = positions.filter((p: any) => p.is_open);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {overview.kill_switch && (
        <div style={{
          background: "var(--red-muted)", border: "1px solid var(--red)",
          borderRadius: "6px", padding: "8px 12px", fontSize: "11px",
          color: "var(--red)", display: "flex", alignItems: "center", gap: "8px",
        }}>
          <span>⚠</span> KILL SWITCH ACTIVE — All trading halted
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(8, 1fr)", gap: "8px" }}>
        <MetricCard label="STARTING CAPITAL" value={safeINR(overview.starting_capital)} />
        <MetricCard
          label="NET P&L"
          value={formatINR(overview.total_net_pnl)}
          sub={formatPct(overview.starting_capital > 0 ? (overview.total_net_pnl / overview.starting_capital) * 100 : 0)}
          color={pnlColor(overview.total_net_pnl)}
        />
        <MetricCard label="REALIZED" value={formatINR(overview.realized_pnl)} color={pnlColor(overview.realized_pnl)} />
        <MetricCard label="UNREALIZED" value={formatINR(overview.unrealized_pnl)} color={pnlColor(overview.unrealized_pnl)} />
        <MetricCard label="TODAY P&L" value={formatINR(overview.today_pnl)} color={pnlColor(overview.today_pnl)} />
        <MetricCard label="MARGIN USED" value={safeINR(overview.margin_used)} sub={`Avail: ${safeINR(overview.available_margin)}`} />
        <MetricCard label="OPEN POSITIONS" value={String(overview.open_positions_count)} />
        <MetricCard label="ACTIVE STRATEGIES" value={String(overview.active_strategies_count)} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
        <MarketCard name="GOLD MINI (GOLDM)" ltp={goldOverview?.ltp ?? 0} status={connected ? "live" : "down"} overview={goldOverview} />
        <MarketCard name="SILVER MINI (SILVERM)" ltp={silverOverview?.ltp ?? 0} status={connected ? "live" : "down"} overview={silverOverview} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
        <div style={panelStyle}>
          <div style={panelHeader}>GOLD STRATEGIES ({goldStrats.length})</div>
          <div style={{ maxHeight: "200px", overflow: "auto" }}>
            {goldStrats.map((s: any) => <StrategyRow key={s.strategy_id} s={s} />)}
            {goldStrats.length === 0 && <div style={{ padding: "16px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No strategies</div>}
          </div>
        </div>
        <div style={panelStyle}>
          <div style={panelHeader}>SILVERM STRATEGIES ({silverStrats.length})</div>
          <div style={{ maxHeight: "200px", overflow: "auto" }}>
            {silverStrats.map((s: any) => <StrategyRow key={s.strategy_id} s={s} />)}
            {silverStrats.length === 0 && <div style={{ padding: "16px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No strategies</div>}
          </div>
        </div>
      </div>

      <div style={panelStyle}>
        <div style={panelHeader}>OPEN POSITIONS ({openPositions.length})</div>
        {openPositions.length === 0 ? (
          <div style={{ padding: "20px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No open positions — waiting for signals</div>
        ) : (
          <div>
            {openPositions.map((p: any) => <PositionRow key={p.position_id} p={p} />)}
          </div>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
        <div style={panelStyle}>
          <div style={panelHeader}>RECENT FILLS</div>
          <div style={{ maxHeight: "180px", overflow: "auto" }}>
            {fills.length === 0 ? (
              <div style={{ padding: "16px", textAlign: "center", color: "var(--text-muted)", fontSize: "10px" }}>No fills yet</div>
            ) : (
              fills.slice(0, 10).map((f: any) => <FillRow key={f.fill_id} f={f} />)
            )}
          </div>
        </div>
        <div style={panelStyle}>
          <div style={panelHeader}>SYSTEM STATUS</div>
          <div style={{ padding: "12px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px", fontSize: "10px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                <span style={{ color: "var(--text-muted)" }}>Connection</span>
                <span style={{ color: connected ? "var(--green)" : "var(--red)" }}>{connected ? "LIVE" : "DOWN"}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                <span style={{ color: "var(--text-muted)" }}>Execution</span>
                <span style={{ color: "var(--blue)" }}>PAPER</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                <span style={{ color: "var(--text-muted)" }}>Strategies</span>
                <span style={{ color: "var(--text-primary)" }}>{strategies.length}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                <span style={{ color: "var(--text-muted)" }}>Positions</span>
                <span style={{ color: "var(--text-primary)" }}>{openPositions.length}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                <span style={{ color: "var(--text-muted)" }}>Fills</span>
                <span style={{ color: "var(--text-primary)" }}>{fills.length}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                <span style={{ color: "var(--text-muted)" }}>Kill Switch</span>
                <span style={{ color: overview.kill_switch ? "var(--red)" : "var(--green)" }}>{overview.kill_switch ? "ON" : "OFF"}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
