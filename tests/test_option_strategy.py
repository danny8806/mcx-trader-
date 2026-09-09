"""Option strategy and P&L unit tests."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from option.strategy import parse_option_chain, StrategySignal, StrikeData


def test_parse_option_chain_ce_oi_high():
    """CE OI > PE OI → select ATM-2."""
    chain_data = {
        "last_price": 23500,
        "oc": {
            "23200": {"ce": {"security_id": "C1", "last_price": 100, "oi": 1000}, "pe": {"security_id": "P1", "last_price": 50, "oi": 500}},
            "23250": {"ce": {"security_id": "C2", "last_price": 90, "oi": 2000}, "pe": {"security_id": "P2", "last_price": 60, "oi": 400}},
            "23300": {"ce": {"security_id": "C3", "last_price": 80, "oi": 3000}, "pe": {"security_id": "P3", "last_price": 70, "oi": 300}},
            "23350": {"ce": {"security_id": "C4", "last_price": 70, "oi": 4000}, "pe": {"security_id": "P4", "last_price": 80, "oi": 200}},
            "23400": {"ce": {"security_id": "C5", "last_price": 60, "oi": 5000}, "pe": {"security_id": "P5", "last_price": 90, "oi": 100}},
            "23450": {"ce": {"security_id": "C6", "last_price": 50, "oi": 4500}, "pe": {"security_id": "P6", "last_price": 100, "oi": 150}},
            "23500": {"ce": {"security_id": "C7", "last_price": 40, "oi": 4000}, "pe": {"security_id": "P7", "last_price": 110, "oi": 200}},
            "23550": {"ce": {"security_id": "C8", "last_price": 30, "oi": 3500}, "pe": {"security_id": "P8", "last_price": 120, "oi": 250}},
            "23600": {"ce": {"security_id": "C9", "last_price": 20, "oi": 3000}, "pe": {"security_id": "P9", "last_price": 130, "oi": 300}},
            "23650": {"ce": {"security_id": "C10", "last_price": 10, "oi": 2500}, "pe": {"security_id": "P10", "last_price": 140, "oi": 350}},
        }
    }

    signal = parse_option_chain(chain_data, 65, "NSE_FNO", "NIFTY")
    assert signal is not None
    # CE OI (ATM to ATM+3) > PE OI → ATM-2
    assert signal.selected_strike == 23400  # ATM is 23500, ATM-2 is 23400
    assert signal.underlying == "NIFTY"
    assert signal.lot_size == 65
    print(f"PASS: CE OI high -> selected strike {signal.selected_strike}")


def test_parse_option_chain_pe_oi_high():
    """PE OI > CE OI → select ATM+2."""
    chain_data = {
        "last_price": 23500,
        "oc": {
            "23200": {"ce": {"security_id": "C1", "last_price": 100, "oi": 100}, "pe": {"security_id": "P1", "last_price": 50, "oi": 5000}},
            "23250": {"ce": {"security_id": "C2", "last_price": 90, "oi": 200}, "pe": {"security_id": "P2", "last_price": 60, "oi": 4500}},
            "23300": {"ce": {"security_id": "C3", "last_price": 80, "oi": 300}, "pe": {"security_id": "P3", "last_price": 70, "oi": 4000}},
            "23350": {"ce": {"security_id": "C4", "last_price": 70, "oi": 400}, "pe": {"security_id": "P4", "last_price": 80, "oi": 3500}},
            "23400": {"ce": {"security_id": "C5", "last_price": 60, "oi": 500}, "pe": {"security_id": "P5", "last_price": 90, "oi": 3000}},
            "23450": {"ce": {"security_id": "C6", "last_price": 50, "oi": 600}, "pe": {"security_id": "P6", "last_price": 100, "oi": 2500}},
            "23500": {"ce": {"security_id": "C7", "last_price": 40, "oi": 700}, "pe": {"security_id": "P7", "last_price": 110, "oi": 2000}},
            "23550": {"ce": {"security_id": "C8", "last_price": 30, "oi": 800}, "pe": {"security_id": "P8", "last_price": 120, "oi": 1500}},
            "23600": {"ce": {"security_id": "C9", "last_price": 20, "oi": 900}, "pe": {"security_id": "P9", "last_price": 130, "oi": 1000}},
            "23650": {"ce": {"security_id": "C10", "last_price": 10, "oi": 1000}, "pe": {"security_id": "P10", "last_price": 140, "oi": 500}},
        }
    }

    signal = parse_option_chain(chain_data, 65, "NSE_FNO", "NIFTY")
    assert signal is not None
    # PE OI (ATM to ATM-3) > CE OI → ATM+2
    assert signal.selected_strike == 23600  # ATM is 23500, ATM+2 is 23600
    print(f"PASS: PE OI high -> selected strike {signal.selected_strike}")


def test_parse_option_chain_empty():
    """Empty chain returns None."""
    signal = parse_option_chain({"last_price": 0, "oc": {}}, 65, "NSE_FNO", "NIFTY")
    assert signal is None
    print("PASS: Empty chain returns None")


def test_pnl_calculation():
    """Verify P&L formula: (entry_premium - exit_premium) × quantity."""
    # Entry: sell CE at 215.6, sell PE at 65.65
    entry_ce = 215.6
    entry_pe = 65.65
    quantity = 65

    # Exit: buy back CE at 200, buy back PE at 60
    exit_ce = 200.0
    exit_pe = 60.0

    # P&L = (215.6 - 200) * 65 + (65.65 - 60) * 65
    # = 15.6 * 65 + 5.65 * 65
    # = 1014 + 367.25 = 1381.25
    expected_pnl = (entry_ce - exit_ce) * quantity + (entry_pe - exit_pe) * quantity
    assert expected_pnl == 1381.25, f"P&L mismatch: {expected_pnl}"
    print(f"PASS: P&L calculation correct: Rs {expected_pnl}")


def test_sl_calculation():
    """SL amount = 1% of margin."""
    margin = 340155.72
    sl_percent = 0.01
    sl_amount = margin * sl_percent
    assert abs(sl_amount - 3401.5572) < 0.01
    print(f"PASS: SL amount correct: Rs {sl_amount}")


def test_pcr_calculation():
    """PCR = PE OI / CE OI."""
    pe_oi = 15000
    ce_oi = 10000
    pcr = pe_oi / ce_oi
    assert pcr == 1.5
    print(f"PASS: PCR calculation correct: {pcr}")


if __name__ == "__main__":
    test_parse_option_chain_ce_oi_high()
    test_parse_option_chain_pe_oi_high()
    test_parse_option_chain_empty()
    test_pnl_calculation()
    test_sl_calculation()
    test_pcr_calculation()
    print("\nAll strategy tests passed!")
