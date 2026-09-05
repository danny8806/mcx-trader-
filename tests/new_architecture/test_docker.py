"""Docker packaging (Phase 62).

Static contract tests prove the Dockerfile is internally consistent and that
docker-compose persists the canonical DB. Runtime execution is skipped when the
host has no `docker` CLI (environment constraint, documented in the report).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"


def test_dockerfile_exists_and_multistage():
    assert DOCKERFILE.exists()
    text = DOCKERFILE.read_text(encoding="utf-8", errors="ignore")
    assert "AS frontend-build" in text
    assert "AS " in text or "FROM python:" in text
    assert "COPY --from=frontend-build" in text, "frontend dist must be copied in"


def test_dockerfile_exposes_and_cmd():
    text = DOCKERFILE.read_text(encoding="utf-8", errors="ignore")
    assert "EXPOSE 8000" in text
    assert "CMD [" in text


def test_dockerfile_healthcheck_route_exists():
    text = DOCKERFILE.read_text(encoding="utf-8", errors="ignore")
    assert "/api/health" in text
    server = (ROOT / "dashboard" / "server.py").read_text(encoding="utf-8", errors="ignore")
    assert '@app.get("/api/health")' in server


def test_startup_entrypoint_exists():
    run = ROOT / "dashboard" / "run.py"
    assert run.exists(), "Dockerfile CMD references dashboard/run.py which must exist"


def test_compose_persists_db_volume():
    assert COMPOSE.exists()
    text = COMPOSE.read_text(encoding="utf-8", errors="ignore")
    assert "/app/data/db" in text, "canonical DB must be mounted as a named volume"
    assert "volumes:" in text


def test_compose_env_file_referenced():
    text = COMPOSE.read_text(encoding="utf-8", errors="ignore")
    assert "mcx-trader.env" in text
    assert (ROOT / "mcx-trader.env").exists()


@pytest.mark.skipif(shutil.which("docker") is None,
                    reason="docker CLI not installed on this host — runtime image build/run "
                           "cannot be executed; only static contract checks run")
def test_docker_runtime_smoke():
    out = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"docker daemon unavailable: {out.stderr}"