"""Server deep-audit: trading.db + analytics.db + system_state.json + reconciliation.

Run inside the container with the live DB mounted at /app/data/db:
    docker exec mcx-trader python /app/_server_deep_audit.py
(or via one-off docker run with the data volume mounted).
Prints every integrity/consistency check and exits nonzero only if a hard error.
"""
from __future__ import annotations

import json
import sqlite3

TD = "/app/data/db/trading.db"
AD = "/app/data/db/analytics.db"
SS = "/app/data/db/system_state.json"


def main() -> int:
    hard_fail = False

    def _check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal hard_fail
        tag = "PASS" if ok else ("WARN" if detail.startswith("WARN") else "FAIL")
        if not ok:
            hard_fail = True
        print(f"  [{tag}] {label}  {detail}")

    # ---------------- trading.db ----------------
    print("=== trading.db ===")
    c = sqlite3.connect(TD)
    print("  tables:", [r[0] for r in c.execute(
        "select name from sqlite_master where type='table' order by name")])
    tc = c.execute("select count(*) from trades").fetchone()[0]
    oc = c.execute("select count(*) from orders").fetchone()[0]
    fc = c.execute("select count(*) from fills").fetchone()[0]
    ec = c.execute("select count(*) from events").fetchone()[0]
    sc = c.execute("select count(*) from account_snapshots").fetchone()[0]
    print(f"  rows: trades={tc} orders={oc} fills={fc} events={ec} snapshots={sc}")
    _check(tc == 27, "trades == 27", f"got {tc}")
    print("  integrity_check:", c.execute("pragma integrity_check").fetchone()[0])
    print("  journal_mode:", c.execute("pragma journal_mode").fetchone()[0])
    _check(c.execute("pragma integrity_check").fetchone()[0] == "ok", "pragma integrity_check")
    by_strat = c.execute(
        "select strategy_id,count(*) from trades group by strategy_id").fetchall()
    print("  trades by strategy:", by_strat)
    _check(dict(by_strat) == {"gold_01": 7, "gold_02": 4, "silver_01": 6, "silver_02": 10},
           "by-strategy counts", f"{by_strat}")
    n_open_db = c.execute("select count(*) from trades where status='open'").fetchone()[0]
    null_exit = c.execute("select count(*) from trades where exit_reason is null").fetchone()[0]
    print(f"  trades status='open'={n_open_db} exit_reason NULL={null_exit}")
    tot = c.execute("select sum(net_pnl) from trades").fetchone()[0]
    gross, charges = c.execute(
        "select sum(gross_pnl),sum(coalesce(charges,0)) from trades").fetchone()
    print(f"  realized: sum(net_pnl)={tot:.2f} sum(gross)={gross:.2f} sum(charges)={charges:.2f}")
    # referential integrity: fills.order_id -> orders.order_id
    orphan = c.execute("""select count(*) from fills f
                          left join orders o on o.order_id=f.order_id
                          where o.order_id is null""").fetchone()[0]
    _check(orphan == 0, "fills.order_id -> orders (no orphans)", f"orphans={orphan}")
    c.close()

    # ---------------- analytics.db ----------------
    print("\n=== analytics.db ===")
    a = sqlite3.connect(AD)
    print("  tables:", [r[0] for r in a.execute(
        "select name from sqlite_master where type='table' order by name")])
    for t in ("trades_analytics", "trade_legs", "trade_events", "trade_snapshots"):
        try:
            n = a.execute(f"select count(*) from {t}").fetchone()[0]
        except sqlite3.OperationalError:
            n = "N/A (table absent)"
        print(f"  {t}: {n}")
    print("  integrity_check:", a.execute("pragma integrity_check").fetchone()[0])
    print("  journal_mode:", a.execute("pragma journal_mode").fetchone()[0])
    _check(a.execute("pragma integrity_check").fetchone()[0] == "ok", "analytics integrity_check")
    _check(a.execute("select count(*) from trades_analytics").fetchone()[0] == 29,
           "trades_analytics == 29",
           f"got {a.execute('select count(*) from trades_analytics').fetchone()[0]}")
    _check(a.execute("select count(*) from trade_legs").fetchone()[0] == 56,
           "trade_legs == 56")
    _check(a.execute("select count(*) from trade_events").fetchone()[0] == 112,
           "trade_events == 112")
    print("  trades_analytics status breakdown:",
          a.execute("select status,count(*) from trades_analytics group by status").fetchall())
    print("  trades_analytics net_pnl by strategy:")
    anet = {}
    for sid, cnt, np_ in a.execute(
            "select strategy_id,count(*),round(sum(net_pnl),2) from trades_analytics group by strategy_id"):
        print(f"    {sid}: count={cnt} net={np_}")
        anet[sid] = (cnt, np_)
    # legs per trade -> should be even (entry+exit)
    bad_legs = a.execute("""select t.trade_id from trades_analytics t
                            left join trade_legs l on l.trade_id=t.trade_id
                            group by t.trade_id having count(l.leg_id)=0""").fetchall()
    _check(not bad_legs, "every analytics trade has >=1 leg", f"trades w/o legs={len(bad_legs)}")
    a.close()

    # ---------------- reconciliation trading.db vs analytics ----------------
    # trading.db `trades` stores every trade as closed (open positions live in
    # system_state.json). trades_analytics stores 27 CLOSED + 2 OPEN = 29.
    # So compare CLOSED-to-CLOSED, then open-analytics vs state open_positions.
    print("\n=== reconciliation: trading.db(closed) vs analytics(CLOSED) ===")
    c = sqlite3.connect(TD)
    a = sqlite3.connect(AD)
    persp = {r[0]: r[1] for r in c.execute(
        "select strategy_id,count(*) from trades group by strategy_id")}
    anap_closed = {r[0]: r[1] for r in a.execute(
        "select strategy_id,count(*) from trades_analytics where status='CLOSED' group by strategy_id")}
    anap_open = {r[0]: r[1] for r in a.execute(
        "select strategy_id,count(*) from trades_analytics where status='OPEN' group by strategy_id")}
    print("  trading.db(b) by strat:", persp)
    print("  analytics   CLOSED by strat:", anap_closed)
    print("  analytics   OPEN   by strat:", anap_open)
    allok = True
    for sid in sorted(set(persp) | set(anap_closed)):
        p, q = persp.get(sid, 0), anap_closed.get(sid, 0)
        ok = p == q
        allok = allok and ok
        print(f"    {sid}: trading.db={p} analytics_CLOSED={q} -> {'MATCH' if ok else 'MISMATCH'}")
    _check(allok, "trading.db(closed) vs analytics(CLOSED) match")
    c.close(); a.close()


    # ---------------- system_state.json ----------------
    print("\n=== system_state.json ===")
    st = json.load(open(SS, encoding="utf-8"))
    print("  top-level keys:", [k for k in st.keys()])
    ops = st.get("positions", {}).get("open_positions", {})
    print("  open_positions count:", len(ops))
    open_by = {}
    for pid, p in ops.items():
        sid_ = p.get("strategy_id")
        open_by[sid_] = p.get("side")
        print(f"    {sid_} {p.get('side')} stop={p.get('stop_price')}")
    _check(len(ops) == 2, "state open_positions == 2", f"got {len(ops)}")
    _check(set(open_by) == {"gold_01", "gold_02"}, "state open side == gold only",
           f"{open_by}")
    # strategy-state desync check (the bug we fixed)
    print("  strategies section:")
    inpos = []
    for sid_, s in st.get("strategies", {}).items():
        print(f"    {sid_}: state={s.get('state')} side={s.get('position_side')}")
        if s.get("position_side") is not None:
            inpos.append(sid_)
    print("  strategy in-position from state:", inpos)
    _check(set(inpos) == set(open_by), "strategies section matches open_positions (desync check)",
           f"state-inpos={inpos} open={set(open_by)}")

    print("\n=== reconciliation: analytics(OPEN) vs state open_positions ===")
    _check(sum(anap_open.values()) == len(ops),
           "analytics OPEN count == state open_positions count",
           f"analytics_open={sum(anap_open.values())} state_open={len(ops)}")
    _check(set(anap_open.keys()) == set(open_by),
           "analytics OPEN strategies == state open strategies",
           f"analytics_open={sorted(anap_open)} state_open={sorted(open_by)}")

    print("\n===== DEEP AUDIT SUMMARY =====")
    print("RESULT:", "FAIL (fix needed)" if hard_fail else "ALL CHECKS PASSED")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
