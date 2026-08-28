"""Full 6-day live system simulator — exact replica of trading_engine flow."""
import json, datetime, time, bisect, sys
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, r"C:\Users\pc\Desktop\MCX-TRADER")
from core.timeframe_engine import Bar, BarState
from htf.backtest_style_htf import BacktestStyleHTFEngine
from indicators.dema_atr import DEMAATR
from strategies.types import StrategyState

with open("data/dhan_token.json") as f:
    TOKEN = json.load(f)["access_token"]
print("Token:", TOKEN[:30], "...")

def api_call(payload):
    r = requests.post("https://api.dhan.co/v2/charts/intraday", json=payload,
        headers={"access-token": TOKEN, "Content-Type": "application/json"}, timeout=30)
    return r.json()

# ============================================================
# CONFIG
# ============================================================
CAPITAL_PER_STRATEGY = 300000.0
MULTIPLIER = {"GOLDM": 10.0, "SILVERM": 5.0}
MARGIN_MODELS = {
    "GOLDM": {"slope": 0.125, "intercept": 126930.0},
    "SILVERM": {"slope": 0.0625, "intercept": 142900.0},
}
CHARGES = {
    "GOLDM": {"brokerage": 20.0, "stt_pct": 0.01, "exchange_pct": 0.0026, "sebi_pct": 0.0001},
    "SILVERM": {"brokerage": 20.0, "stt_pct": 0.01, "exchange_pct": 0.0026, "sebi_pct": 0.0001},
}

STRATEGIES = {
    "gold_01":   {"instrument": "GOLDM",   "fast_tf": "5m",  "fast_min": 5},
    "gold_02":   {"instrument": "GOLDM",   "fast_tf": "15m", "fast_min": 15},
    "silver_01": {"instrument": "SILVERM", "fast_tf": "15m", "fast_min": 15},
    "silver_02": {"instrument": "SILVERM", "fast_tf": "5m",  "fast_min": 5},
}

trading_days = [
    datetime.date(2026, 8, 21),
    datetime.date(2026, 8, 24),
    datetime.date(2026, 8, 25),
    datetime.date(2026, 8, 26),
    datetime.date(2026, 8, 27),
    datetime.date(2026, 8, 28),
]

from_date = datetime.date(2026, 8, 21)
to_date = datetime.date(2026, 8, 28)
session_open = "09:00"

# ============================================================
# FETCH DATA
# ============================================================
print("Fetching 7-day 5m data...")
j5_gold = api_call({"securityId": "563946", "exchangeSegment": "MCX_COMM", "instrument": "FUTCOM",
    "interval": "5", "fromDate": "%s 09:00:00" % from_date, "toDate": "%s 23:55:00" % to_date})
time.sleep(0.5)
j5_silver = api_call({"securityId": "483080", "exchangeSegment": "MCX_COMM", "instrument": "FUTCOM",
    "interval": "5", "fromDate": "%s 09:00:00" % from_date, "toDate": "%s 23:55:00" % to_date})
print("GOLDM: %d 5m candles, SILVERM: %d 5m candles" % (len(j5_gold["open"]), len(j5_silver["open"])))

def make_df(j5):
    df = pd.DataFrame({
        "timestamp": j5["timestamp"],
        "open": j5["open"], "high": j5["high"],
        "low": j5["low"], "close": j5["close"],
        "volume": j5.get("volume", [0]*len(j5["open"])),
    })
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return df.sort_values("datetime").reset_index(drop=True)

df_gold = make_df(j5_gold)
df_silver = make_df(j5_silver)

# ============================================================
# BUILD HTF ENGINES (same as live _warmup_from_rest)
# ============================================================
from datetime import timezone, timedelta

def build_htf(df, inst_name):
    htf_engine = BacktestStyleHTFEngine()
    htf_engine.register(inst_name, "1h", 3, 6, 1.0, session_open)
    htf_engine.register(inst_name, "15m", 3, 6, 1.0, session_open)

    for tf, tf_min in [("1h", 60), ("15m", 15)]:
        d = df.copy()
        dt = d["datetime"]
        dates = dt.dt.date.astype(str)
        sess_start = pd.to_datetime(dates + " " + session_open)
        mins = ((dt - sess_start).dt.total_seconds() // 60).astype(int)
        d["_bucket"] = sess_start + pd.to_timedelta((mins // tf_min) * tf_min, unit="m")
        grouped = d.groupby("_bucket", sort=True).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).reset_index().rename(columns={"_bucket": "datetime"})

        bars = []
        for _, row in grouped.iterrows():
            bar_dt = row["datetime"]
            if bar_dt.tzinfo is None:
                bar_dt = bar_dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
            start_ts = bar_dt.timestamp()
            bars.append(Bar(instrument=inst_name, timeframe=tf,
                start_ts=start_ts, end_ts=start_ts + tf_min * 60,
                open=row["open"], high=row["high"], low=row["low"],
                close=row["close"], volume=int(row["volume"]), state=BarState.CLOSED))
        htf_engine.load_batch_htf(inst_name, tf, bars)
    return htf_engine

print("Building HTF engines...")
htf_gold = build_htf(df_gold, "GOLDM")
htf_silver = build_htf(df_silver, "SILVERM")

# ============================================================
# MARGIN & CHARGES
# ============================================================
def calc_margin(inst, price, qty):
    m = MARGIN_MODELS[inst]
    return qty * (m["slope"] * price + m["intercept"])

def calc_charges(inst, entry_price, exit_price, qty, side):
    c = CHARGES[inst]
    mult = MULTIPLIER[inst]
    notional = max(entry_price, exit_price) * qty * mult
    brokerage = c["brokerage"] * 2
    stt = notional * c["stt_pct"] / 100
    exchange = notional * c["exchange_pct"] / 100
    sebi = notional * c["sebi_pct"] / 100
    gst = (brokerage + exchange + sebi) * 0.18
    return brokerage + stt + exchange + sebi + gst

def calc_pnl(inst, entry, exit_p, qty, side):
    mult = MULTIPLIER[inst]
    if side == "LONG":
        gross = (exit_p - entry) * qty * mult
    else:
        gross = (entry - exit_p) * qty * mult
    charges = calc_charges(inst, entry, exit_p, qty, side)
    return gross, charges, gross - charges

# ============================================================
# STRATEGY SIMULATOR (exact live logic)
# ============================================================
class StrategySim:
    def __init__(self, name, inst, fast_tf, fast_min, capital):
        self.name = name
        self.inst = inst
        self.fast_tf = fast_tf
        self.fast_min = fast_min
        self.capital = capital
        self.available_margin = capital

        self.state = StrategyState.FLAT
        self.position_side = None
        self.stop_price = None
        self.pending_entry = None
        self.entry_price = None

        self._prev_close = None
        self._prev_htf = None
        self._prev_mid = None
        self._prev_high = None
        self._prev_low = None

        self.trades = []
        self.all_events = []
        self.bars_processed = 0

    def on_bar(self, bar, htf_mapped, mid_mapped):
        self.bars_processed += 1
        close = bar.close
        high = bar.high
        low = bar.low
        prev_close = self._prev_close if self._prev_close is not None else close
        prev_high = self._prev_high if self._prev_high is not None else high
        prev_low = self._prev_low if self._prev_low is not None else low
        htf_val = htf_mapped.htf_value
        prev_htf = self._prev_htf
        mid_val = mid_mapped.htf_value if mid_mapped else None

        self._prev_close = close
        self._prev_htf = htf_val
        self._prev_mid = mid_val
        self._prev_high = high
        self._prev_low = low

        if htf_val is None or prev_htf is None:
            return

        dt = datetime.datetime.fromtimestamp(bar.start_ts)
        t = dt.strftime("%Y-%m-%d %H:%M")

        # 1. Pending entry check
        if self.pending_entry is not None:
            self.pending_entry["bars"] += 1
            if self.pending_entry["bars"] >= 50:
                self.pending_entry = None
                self.state = StrategyState.FLAT
                self.position_side = None
            elif self.pending_entry["side"] == "LONG" and bar.high >= self.pending_entry["trigger"]:
                self._fill("LONG", bar.open, t, "entry")
            elif self.pending_entry["side"] == "SHORT" and bar.low <= self.pending_entry["trigger"]:
                self._fill("SHORT", bar.open, t, "entry")

        # 2. Stop loss
        if self.position_side == "LONG" and self.stop_price is not None:
            if bar.low <= self.stop_price:
                self._exit(self.stop_price, t, "stop_loss")
        elif self.position_side == "SHORT" and self.stop_price is not None:
            if bar.high >= self.stop_price:
                self._exit(self.stop_price, t, "stop_loss")

        # 3. Signal detection
        long_cross = close > htf_val and prev_close <= prev_htf and mid_val is not None and mid_val < htf_val
        short_cross = close < htf_val and prev_close >= prev_htf and mid_val is not None and mid_val > htf_val

        if self.state == StrategyState.FLAT:
            if long_cross:
                trigger = high
                sl = min(low, prev_low)
                self.pending_entry = {"side": "LONG", "trigger": trigger, "bars": 0, "sl": sl}
                self.stop_price = sl
                self.state = StrategyState.PENDING_LONG
                self.all_events.append((t, "LONG_SIGNAL", close, htf_val, mid_val))
            elif short_cross:
                trigger = low
                sl = max(high, prev_high)
                self.pending_entry = {"side": "SHORT", "trigger": trigger, "bars": 0, "sl": sl}
                self.stop_price = sl
                self.state = StrategyState.PENDING_SHORT
                self.all_events.append((t, "SHORT_SIGNAL", close, htf_val, mid_val))

        elif self.position_side == "SHORT" and long_cross:
            trigger = high
            sl = min(low, prev_low)
            self.pending_entry = {"side": "LONG", "trigger": trigger, "bars": 0, "sl": sl}
            self.stop_price = sl
            self.state = StrategyState.PENDING_LONG
            self.all_events.append((t, "REVERSE_TO_LONG", close, htf_val, mid_val))

        elif self.position_side == "LONG" and short_cross:
            trigger = low
            sl = max(high, prev_high)
            self.pending_entry = {"side": "SHORT", "trigger": trigger, "bars": 0, "sl": sl}
            self.stop_price = sl
            self.state = StrategyState.PENDING_SHORT
            self.all_events.append((t, "REVERSE_TO_SHORT", close, htf_val, mid_val))

    def _fill(self, side, price, t, reason):
        if self.position_side is not None:
            self._exit(self.stop_price, t, "reverse_fill")
        margin = calc_margin(self.inst, price, 1)
        if margin > self.available_margin:
            self.state = StrategyState.FLAT
            self.position_side = None
            self.stop_price = None
            self.pending_entry = None
            self.all_events.append((t, "MARGIN_BLOCKED", price, 0, 0))
            return
        self.available_margin -= margin
        self.position_side = side
        self.entry_price = price
        self.state = StrategyState.LONG_POSITION if side == "LONG" else StrategyState.SHORT_POSITION
        self.pending_entry = None
        self.all_events.append((t, "FILL_%s" % reason, price, 0, 0))

    def _exit(self, exit_price, t, reason):
        if self.position_side is None or self.entry_price is None:
            return
        gross, charges, net = calc_pnl(self.inst, self.entry_price, exit_price, 1, self.position_side)
        margin = calc_margin(self.inst, self.entry_price, 1)
        self.available_margin += margin
        self.trades.append({
            "entry_time": self._entry_time if hasattr(self, '_entry_time') else "",
            "exit_time": t,
            "side": self.position_side,
            "entry": self.entry_price,
            "exit": exit_price,
            "gross": gross,
            "charges": charges,
            "net": net,
            "reason": reason,
        })
        self.position_side = None
        self.entry_price = None
        self.stop_price = None
        self.pending_entry = None
        self.state = StrategyState.FLAT

    def _fill(self, side, price, t, reason):
        if self.position_side is not None:
            self._exit(self.stop_price, t, "reverse_fill")
        margin = calc_margin(self.inst, price, 1)
        if margin > self.available_margin:
            self.state = StrategyState.FLAT
            self.position_side = None
            self.stop_price = None
            self.pending_entry = None
            self.all_events.append((t, "MARGIN_BLOCKED", price, 0, 0))
            return
        self.available_margin -= margin
        self.position_side = side
        self.entry_price = price
        self._entry_time = t
        self.state = StrategyState.LONG_POSITION if side == "LONG" else StrategyState.SHORT_POSITION
        self.pending_entry = None
        self.all_events.append((t, "FILL_%s" % reason, price, 0, 0))

# ============================================================
# RUN SIMULATION
# ============================================================
print("\n" + "=" * 100)
print("FULL 6-DAY LIVE SYSTEM SIMULATION")
print("=" * 100)

all_strats = {}
for sname, scfg in STRATEGIES.items():
    inst = scfg["instrument"]
    htf = htf_gold if inst == "GOLDM" else htf_silver
    df = df_gold if inst == "GOLDM" else df_silver
    all_strats[sname] = StrategySim(sname, inst, scfg["fast_tf"], scfg["fast_min"], CAPITAL_PER_STRATEGY)

grand_total_net = 0
grand_total_trades = 0

for day in trading_days:
    print("\n" + "=" * 100)
    print("  %s" % day.strftime("%A %Y-%m-%d"))
    print("=" * 100)

    day_start = int(datetime.datetime.combine(day, datetime.time(9, 0)).timestamp())
    day_end = int(datetime.datetime.combine(day, datetime.time(23, 59)).timestamp())

    for sname, scfg in STRATEGIES.items():
        sim = all_strats[sname]
        inst = scfg["instrument"]
        fast_min = scfg["fast_min"]
        htf = htf_gold if inst == "GOLDM" else htf_silver
        df = df_gold if inst == "GOLDM" else df_silver

        # Build fast bars for this day
        if scfg["fast_tf"] == "5m":
            fast_df = df
        else:
            d = df.copy()
            dt = d["datetime"]
            dates = dt.dt.date.astype(str)
            sess_start = pd.to_datetime(dates + " " + session_open)
            mins = ((dt - sess_start).dt.total_seconds() // 60).astype(int)
            d["_bucket"] = sess_start + pd.to_timedelta((mins // 15) * 15, unit="m")
            fast_df = d.groupby("_bucket", sort=True).agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum",
            }).reset_index().rename(columns={"_bucket": "datetime"})

        day_signals = []
        for i in range(len(fast_df)):
            row = fast_df.iloc[i]
            bar_dt = row["datetime"]
            if bar_dt.tzinfo is None:
                bar_dt = bar_dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
            ts = bar_dt.timestamp()
            if ts < day_start or ts > day_end:
                continue

            bar = Bar(instrument=inst, timeframe=scfg["fast_tf"],
                start_ts=ts, end_ts=ts + fast_min * 60,
                open=row["open"], high=row["high"], low=row["low"],
                close=row["close"], volume=0, state=BarState.CLOSED)

            htf_mapped = htf.map_to_fast_bar(bar, scfg["fast_tf"])
            mid_mapped = htf.map_mid_to_fast_bar(bar, scfg["fast_tf"])

            events_before = len(sim.all_events)
            sim.on_bar(bar, htf_mapped, mid_mapped)
            for ev in sim.all_events[events_before:]:
                day_signals.append(ev)

        # Print day summary for this strategy
        if day_signals:
            for ev in day_signals:
                print("  %-12s %s  %-20s  close=%d  1H=%d  15m=%d" % (
                    sname, ev[0], ev[1], ev[2], ev[3], ev[4]))
        else:
            print("  %-12s %s  (no signals)" % (sname, day.strftime("%Y-%m-%d")))

# ============================================================
# FINAL TRADE LOG
# ============================================================
print("\n" + "=" * 100)
print("COMPLETE TRADE LOG (all 6 days)")
print("=" * 100)

for sname in STRATEGIES:
    sim = all_strats[sname]
    if sim.trades:
        print("\n--- %s (%s, %s) ---" % (sname, STRATEGIES[sname]["fast_tf"], STRATEGIES[sname]["instrument"]))
        print("%-20s %-20s %-6s %10s %10s %10s %8s %10s  %s" % (
            "ENTRY", "EXIT", "SIDE", "ENTRY$", "EXIT$", "GROSS", "CHARGES", "NET", "REASON"))
        total_net = 0
        total_charges = 0
        wins = 0
        losses = 0
        for t in sim.trades:
            total_net += t["net"]
            total_charges += t["charges"]
            if t["net"] > 0:
                wins += 1
            else:
                losses += 1
            print("%-20s %-20s %-6s %10.0f %10.0f %10.0f %8.0f %10.0f  %s" % (
                t["entry_time"], t["exit_time"], t["side"],
                t["entry"], t["exit"], t["gross"], t["charges"], t["net"], t["reason"]))
        n = len(sim.trades)
        win_rate = (wins / n * 100) if n > 0 else 0
        print("  Trades: %d  Wins: %d  Losses: %d  Win%%: %.0f%%  Total Net: %.0f  Charges: %.0f" % (
            n, wins, losses, win_rate, total_net, total_charges))
        grand_total_net += total_net
        grand_total_trades += len(sim.trades)
    else:
        print("\n--- %s: NO TRADES ---" % sname)

print("\n" + "=" * 100)
print("GRAND TOTAL")
print("=" * 100)
print("Total trades: %d" % grand_total_trades)
print("Total net P&L: Rs %.0f" % grand_total_net)
print("Per-strategy capital: Rs 3,00,000 x 4 = Rs 12,00,000")
print("=" * 100)
