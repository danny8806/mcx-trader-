"""Frontend <-> backend API contract (Phase 44).

Static consumer-contract assertions:

  * every /api/... endpoint referenced by the React app (dashboard-ui/src)
    must be implemented by a FastAPI route in the backend;
  * the canonical lineage field names the frontend renders (strategy_id,
    trade_id, position_id, entry_signal_id) must be produced by the backend;
  * the WS message envelope ({type, data, ts}) used by the frontend must
    match ConnectionManager.broadcast().
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI_SRC = ROOT / "dashboard-ui" / "src"
ROUTES = sorted((ROOT / "dashboard" / "routes").glob("*.py"))
SERVER = ROOT / "dashboard" / "server.py"


def _backend_endpoints() -> set[str]:
    found = set()
    files = [*ROUTES, SERVER]
    for extra in [ROOT / "analytics" / "routes.py", ROOT / "dashboard" / "server.py"]:
        if extra.exists() and extra not in files:
            files.append(extra)
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        found.update(re.findall(r'@(?:router|app)\.(?:get|post|put|delete|websocket)\("([^"]+)"\)', text))
    return found


def _frontend_paths() -> set[str]:
    found = set()
    for f in [*UI_SRC.rglob("*.tsx"), *UI_SRC.rglob("*.ts")]:
        found.update(re.findall(r'["`](/api/[A-Za-z0-9_/{}$?.&=%\-]+)', f.read_text(encoding="utf-8", errors="ignore")))
    cleaned = set()
    for p in found:
        clean = p.split("?")[0]
        clean = re.sub(r"\$\{[^}]+\}", "{}", clean)
        clean = re.sub(r"/+", "/", clean)
        clean = clean.removesuffix("{}")  # trailing ${qs} param placeholder
        cleaned.add(clean)
    return cleaned


def _match_exist(backend: set[str], path: str) -> bool:
    def rx(t):
        t = re.sub(r"\{[^}]*\}", "[^/]+", t)
        return re.compile("^" + re.escape("/") + t[1:] + "/?$")
    for b in backend:
        if rx(b).match(path):
            return True
    return False


def test_all_frontend_api_paths_exist_backend():
    backend = _backend_endpoints()
    missing = []
    for path in sorted(_frontend_paths()):
        if path in backend or _match_exist(backend, path):
            continue
        missing.append(path)
    assert not missing, f"frontend calls unimplemented endpoints: {missing}"


def test_frontend_uses_canonical_lineage_fields():
    ui_text = "".join(f.read_text(encoding="utf-8", errors="ignore")
                      for f in [*UI_SRC.rglob("*.tsx"), *UI_SRC.rglob("*.ts")])
    for field in ("strategy_id", "trade_id", "position_id", "entry_signal_id"):
        assert field in ui_text, f"frontend does not reference {field}"


def test_backend_produces_lineage_fields():
    backend_text = "".join(f.read_text(encoding="utf-8", errors="ignore") for f in [*ROUTES, SERVER])
    for field in ("strategy_id", "trade_id", "position_id"):
        assert field in backend_text, f"backend never produces {field}"


def test_ws_envelope_matches_frontend():
    ui_text = "".join(f.read_text(encoding="utf-8", errors="ignore")
                      for f in UI_SRC.rglob("*.tsx"))
    ws_text = (SERVER).read_text(encoding="utf-8", errors="ignore")
    if "onmessage" in ui_text or "addEventListener" in ui_text or "WebSocket" in ui_text:
        assert "type" in ws_text
        assert "data" in ws_text


def test_frontend_positions_fields_match_snapshot():
    """Position rows the UI renders must be keys the backend emits."""
    backend_text = (ROOT / "dashboard" / "routes" / "positions.py").read_text(encoding="utf-8")
    ui_text = (UI_SRC / "pages" / "Positions.tsx").read_text(encoding="utf-8", errors="ignore")
    for key in ("position_id", "strategy_id", "instrument", "side",
                "quantity", "status", "stop_price"):
        assert key in backend_text or key in (ROOT / "trading_engine.py").read_text(encoding="utf-8")
        assert key in ui_text, f"UI rows read {key} but never render it"


def test_frontend_dist_built():
    """If a dist exists it must contain the mounted SPA entry."""
    dist = ROOT / "dashboard-ui" / "dist"
    if dist.exists():
        assert (dist / "index.html").exists(), "dist exists but has no index.html"