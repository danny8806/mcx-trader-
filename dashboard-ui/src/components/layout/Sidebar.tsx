import { NavLink } from "react-router-dom";
import {
  LayoutDashboard, Activity, Columns3, Briefcase,
  ShoppingCart, BookOpen, TrendingUp, Shield,
  Database, Gauge, GitCompare, Bell, Heart,
  Settings, FileText, Radio, Zap
} from "lucide-react";

const navItems = [
  { to: "/", icon: LayoutDashboard, label: "Overview" },
  { to: "/live", icon: Radio, label: "Live Trading" },
  { to: "/strategies", icon: Activity, label: "Strategies" },
  { to: "/matrix", icon: Columns3, label: "Strategy Matrix" },
  { to: "/positions", icon: Briefcase, label: "Positions" },
  { to: "/orders", icon: ShoppingCart, label: "Orders" },
  { to: "/trades", icon: BookOpen, label: "Trades" },
  { to: "/pnl", icon: TrendingUp, label: "P&L Analytics" },
  { to: "/risk", icon: Shield, label: "Risk" },
  { to: "/market-data", icon: Database, label: "Market Data" },
  { to: "/indicators", icon: Gauge, label: "Indicators" },
  { to: "/reconciliation", icon: GitCompare, label: "Reconciliation" },
  { to: "/alerts", icon: Bell, label: "Alerts" },
  { to: "/health", icon: Heart, label: "System Health" },
  { to: "/settings", icon: Settings, label: "Settings" },
  { to: "/audit", icon: FileText, label: "Audit Log" },
];

interface SidebarProps {
  connected: boolean;
}

export default function Sidebar({ connected }: SidebarProps) {
  return (
    <aside style={{
      width: "var(--sidebar-width)",
      height: "100vh",
      background: "var(--bg-sidebar)",
      borderRight: "1px solid var(--border)",
      display: "flex",
      flexDirection: "column",
      position: "fixed",
      left: 0,
      top: 0,
      zIndex: 40,
    }}>
      <div style={{ padding: "14px 14px 10px", borderBottom: "1px solid var(--border-subtle)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <div style={{
            width: "28px", height: "28px", borderRadius: "6px",
            background: "rgba(242,184,75,0.12)", display: "flex",
            alignItems: "center", justifyContent: "center",
          }}>
            <Zap style={{ width: "14px", height: "14px", color: "var(--amber)" }} />
          </div>
          <div>
            <div style={{ fontSize: "12px", fontWeight: 700, color: "var(--text-primary)", letterSpacing: "0.5px" }}>
              MCX TRADER
            </div>
            <div style={{ fontSize: "9px", color: "var(--text-muted)", marginTop: "1px" }}>
              Professional Trading Terminal
            </div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "10px" }}>
          <div style={{
            width: "6px", height: "6px", borderRadius: "50%",
            background: connected ? "var(--green)" : "var(--red)",
          }} />
          <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>
            {connected ? "Connected" : "Disconnected"}
          </span>
        </div>
      </div>

      <nav style={{ flex: 1, overflowY: "auto", padding: "6px 0" }}>
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            style={({ isActive }) => ({
              display: "flex",
              alignItems: "center",
              gap: "10px",
              padding: "0 14px",
              height: "34px",
              fontSize: "11px",
              fontWeight: isActive ? 500 : 400,
              color: isActive ? "var(--blue)" : "var(--text-secondary)",
              background: isActive ? "var(--bg-panel-active)" : "transparent",
              textDecoration: "none",
              borderLeft: isActive ? "2px solid var(--blue)" : "2px solid transparent",
              transition: "all 0.1s",
            })}
            onMouseEnter={(e) => {
              if (!e.currentTarget.style.background.includes("panel-active")) {
                e.currentTarget.style.background = "var(--bg-panel-hover)";
              }
            }}
            onMouseLeave={(e) => {
              if (!e.currentTarget.style.background.includes("panel-active")) {
                e.currentTarget.style.background = "transparent";
              }
            }}
          >
            <item.icon style={{ width: "15px", height: "15px", flexShrink: 0 }} />
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div style={{
        padding: "10px 14px",
        borderTop: "1px solid var(--border-subtle)",
        fontSize: "9px",
        color: "var(--text-disabled)",
      }}>
        <div style={{ fontWeight: 500 }}>MCX Trader</div>
        <div style={{ marginTop: "1px" }}>v2.0.0 • Paper Mode</div>
      </div>
    </aside>
  );
}
