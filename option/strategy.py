"""OI-based option selling strategy. Logic from option_selling_strategy.py."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StrikeData:
    strike: float
    ce_sec: str | None = None
    ce_ltp: float = 0.0
    ce_oi: int = 0
    pe_sec: str | None = None
    pe_ltp: float = 0.0
    pe_oi: int = 0


@dataclass
class StrategySignal:
    underlying: str
    spot: float
    expiry: str
    atm: float
    selected_strike: float
    ce_ltp: float
    pe_ltp: float
    ce_sec: str
    pe_sec: str
    ce_oi: int
    pe_oi: int
    pcr: float
    selection_reason: str
    margin: float
    lot_size: int
    exchange: str


def parse_option_chain(chain_data: dict, lot_size: int, exchange: str, underlying: str) -> StrategySignal | None:
    """Parse option chain and generate signal based on OI analysis.

    Returns StrategySignal if OI threshold is met, None otherwise.
    """
    spot = chain_data.get("last_price", 0)
    if not spot:
        return None

    oc = chain_data.get("oc", {})
    strikes = []

    for strike_str, opts in oc.items():
        strike = float(strike_str)
        ce = opts.get("ce", {})
        pe = opts.get("pe", {})
        strikes.append(StrikeData(
            strike=strike,
            ce_sec=ce.get("security_id"),
            ce_ltp=ce.get("last_price", 0),
            ce_oi=ce.get("oi", 0),
            pe_sec=pe.get("security_id"),
            pe_ltp=pe.get("last_price", 0),
            pe_oi=pe.get("oi", 0),
        ))

    if not strikes:
        return None

    strikes.sort(key=lambda x: x.strike)

    # Find ATM
    atm = min(strikes, key=lambda x: abs(x.strike - spot))
    atm_idx = strikes.index(atm)

    # Need at least 4 strikes above and below ATM
    if atm_idx < 3 or atm_idx >= len(strikes) - 3:
        return None

    # OI Analysis: ATM to ATM+3 OTM for CE, ATM to ATM-3 OTM for PE
    ce_oi_sum = sum(strikes[atm_idx + i].ce_oi for i in range(4))
    pe_oi_sum = sum(strikes[atm_idx - i].pe_oi for i in range(4))

    # PCR
    pcr = pe_oi_sum / ce_oi_sum if ce_oi_sum > 0 else 0

    # Strike selection based on OI
    if ce_oi_sum > pe_oi_sum:
        selected = strikes[atm_idx - 2]
        selection_reason = f"CE OI ({ce_oi_sum:,}) > PE OI ({pe_oi_sum:,}) -> ATM-2"
    else:
        selected = strikes[atm_idx + 2]
        selection_reason = f"PE OI ({pe_oi_sum:,}) > CE OI ({ce_oi_sum:,}) -> ATM+2"

    return StrategySignal(
        underlying=underlying,
        spot=spot,
        expiry="",  # filled by caller
        atm=atm.strike,
        selected_strike=selected.strike,
        ce_ltp=selected.ce_ltp,
        pe_ltp=selected.pe_ltp,
        ce_sec=selected.ce_sec,
        pe_sec=selected.pe_sec,
        ce_oi=ce_oi_sum,
        pe_oi=pe_oi_sum,
        pcr=pcr,
        selection_reason=selection_reason,
        margin=0,  # filled by caller
        lot_size=lot_size,
        exchange=exchange,
    )
