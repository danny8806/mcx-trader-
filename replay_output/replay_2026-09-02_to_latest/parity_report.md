# Replay Parity Report

Reference: `core/dema_mtf.py`

- DEMA/ATR: reference implementation used
- HTF mapping: native completed-bar mapping used
- Lookahead: no future bars used
- Entry: strict later-bar breakout, fill at breakout open
- Stop loss: close of bar crossing SL
- Reversal: exit at next bar open

Existing database trades were read only after replay and did not drive results.
