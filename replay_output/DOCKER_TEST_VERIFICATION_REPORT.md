# Docker Full Test — VPS Verification Report

**Date:** 2026-09-05 20:20 IST
**Executed by:** opencode (MCX-TRADER workspace) via SSH to production VPS
**Host:**

| Item | Value |
|------|-------|
| VPS | `200.234.44.93` |
| OS | Linux (root, 2 vCPU, 7.8 GiB RAM) |
| Docker Engine | **29.1.3** (server + client) |
| Local Docker | Not installed (no CLI, no Docker Desktop, no WSL) — Docker run executed remotely on the VPS |

---

## 1. Scope of "full Docker test"

Per the `tests/new_architecture/test_docker.py` contract, the Docker test has two halves:

1. **Static contract checks** (6 tests) — Dockerfile multi-stage structure, EXPOSE/CMD, healthcheck route
   (`/api/health`), entrypoint `dashboard/run.py`, compose DB volume + env file. These run everywhere.
2. **Runtime smoke + full suite** — `docker build` from current repo, container boot, `/api/health`
   probe, and the full 1270-test regression suite executed **inside the built container**.

Because the local Windows host has no Docker, the runtime was executed on the VPS where Docker 29.1.3
is available. A **dedicated isolated build context** was used:
`/home/jadhavdnyaneshwar701/mcx-trader-dockertest` — the live container `mcx-trader`
(port 8000) and its DB volume were **never touched**.

---

## 2. Build result

```
docker build -t mcx-trader:fulltest .   (from current repo, 574 files synced)
Successfully built 5f60a76ff61a  ->  after events/ fix: c6dda966747e
```

- Stage 1: Node 24-alpine, npm ci + `npm run build` — OK
- Stage 2: python:3.14-slim, pip install requirements — OK (fastapi 0.141.1, starlette 1.6.0, httpx 0.28.1)
- 38 stages total

### ⚠️ Critical bug found and fixed during the build test

The **first** build from the current repo* booted the container but the engine failed to initialise:

```
[Lifespan] Starting...
Could not initialize TradingEngine: No module named 'events'
```

**Root cause:** current code (`trading_engine.py:36`, `strategies/instance.py:12`,
`data/native_streams.py:7`) imports the `events.*` package, but `Dockerfile` only copied
`*.py config core data strategies htf indicators execution portfolio persistence notifications
monitoring analytics reconciliation dashboard` — the `events/` directory was never copied.

> The **live** container (34 h, healthy, `engine:true`) is the **older** image built before the
> `events/` refactor, so it is unaffected. A fresh deploy from the then-current Dockerfile would have
> silently started with a **disabled trading engine**.

**Fix applied** — `Dockerfile` now includes:

```dockerfile
COPY events/ ./events/
```

Rebuild result: container reports `"engine":true` in `/api/health` and boots cleanly.

---

## 3. Runtime smoke (throwaway container, port 8021)

```
docker run -d --name mcx-trader-fulltest ... -p 8021:8000 mcx-trader:fulltest python dashboard/run.py
Up 12 seconds (health: starting) -> health: healthy after ~30s

GET http://localhost:8021/api/health  -> 200
{"status":"ok","engine":true,"persistence":true,"ws_connections":0,
 "event_bus":{"total_events":0,"counts":{},"subscribers":{},"wildcard_subscribers":0},
 "timestamp":"2026-09-05T20:11:30+00:00"}
```

- Engine init: ✅ `engine:true` after the fix (was `engine:false` before)
- Persistence: ✅
- Container `docker ps`: `Up (healthy)` — Dockerfile HEALTHCHECK passes
- Port 8021 used to avoid colliding with the live production `:8000`

---

## 4. Full regression suite inside the container

Run inside a fresh container with the repo mounted at `/app`, pytest 9.1.1 installed ad hoc
(pytest is not part of `requirements.txt` / the image — see §6 note):

```
python -m pytest tests -q -p no:cacheprovider --tb=short
```

**Result: `1226 passed, 44 skipped, 0 failed` in 48.69 s**

| Metric | Container (VPS, py3.14/fastapi 0.141.1) | Local (Win, py3.14/fastapi 0.136.3) |
|--------|-----------------|-----------------|
| Passed | 1226 | 1226 |
| Skipped | 44 | 44 |
| Failed | 0 | 0 |
| Collection | 1270 | 1270 |

### Test-fix applied during the run

Two WS-route tests failed only on the newer Starlette in the container:
`_IncludedRouter` objects in `app.routes` have no `.path`.

- `tests/fresh_audit/test_comprehensive.py::TestWebSocket::test_ws_endpoint_exists`
- `tests/fresh_audit/test_whole_project.py::TestWebSocketAndMisc::test_websocket_endpoint_exists`

**Fix** — attribute-safe route scan (works on Starlette 1.3.1 and 1.6.0):

```python
routes = [getattr(r, "path", None) for r in app.routes]
```

Both pass on the local install AND in the container.

---

## 5. Static docker contract tests (also executed in-container)

- `test_dockerfile_exists_and_multistage` — ✅
- `test_dockerfile_exposes_and_cmd` — ✅ (EXPOSE 8000, CMD ["python","dashboard/run.py"])
- `test_dockerfile_healthcheck_route_exists` — ✅ (`/api/health` present in Dockerfile + server)
- `test_startup_entrypoint_exists` — ✅
- `test_compose_persists_db_volume` — ✅ (compose mounts `/app/data/db` as named volume)
- `test_compose_env_file_referenced` — ✅ (`mcx-trader.env` present)
- `test_docker_runtime_smoke` (`docker version`) — ✅ verified on the VPS host: server 29.1.3

---

## 6. Findings / notes

1. **Not part of the image:** `tests/` is not copied by the Dockerfile and pytest is not in
   `requirements.txt`. The container test run mounted `tests/` via `-v` and installed pytest inside
   the throwaway container. This matches the design (tests are a dev-time concern), but if in-image
   testing is desired, add a dev target.
2. **45 skipped** behaviour identical to local: live-server-gated tests (port 8000 probes) skip in the
   container (no server), as designed.
3. **Ancillary external data:** `tests/fresh_audit/test_backtest_vs_live_crossover.py` imports
   `core/dema_mtf.py` + `data_mcx/GOLDM_5m_mcx.csv` from a Windows-only absolute path
   (`C:\Users\pc\Desktop\nifty dema backtest\project`). Those two files were provisioned on the VPS
   (literal path furniture) so collection succeeded; no code change needed.
4. **Cleanup:** throwaway containers (`mcx-trader-fulltest`, `mcx-trader-testrun`) removed. The live
   `mcx-trader` container remains untouched and healthy (`engine:true`, 3 ws_connections).

---

## 7. Verdict

**PASS** — Full Docker build, container boot, healthcheck, and all 1270 tests behave identically in
the Linux/Docker environment (1226 passed / 44 skipped / 0 failed). The Docker test additionally
caught and fixed a real packaging regression: **Dockerfile omitted `events/`, which would have shipped
a container with a disabled trading engine**.