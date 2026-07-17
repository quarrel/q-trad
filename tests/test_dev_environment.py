from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_dev_container_separates_persistent_and_test_postgres() -> None:
    compose = (REPOSITORY_ROOT / ".devcontainer" / "compose.devcontainer.yaml").read_text()
    devcontainer = (REPOSITORY_ROOT / ".devcontainer" / "devcontainer.json").read_text()

    assert (
        "QTRAD_DATABASE_URL: postgresql+asyncpg://qtrad:qtrad-dev-only@db:5432/qtrad_dev" in compose
    )
    assert "QTRAD_TEST_POSTGRES_HOST: test-db" in compose
    assert "  test-db:\n" in compose
    assert "    tmpfs:\n      - /var/lib/postgresql\n" in compose
    assert (
        '"postStartCommand": "uv run alembic upgrade head && codex remote-control start"'
        in devcontainer
    )
    assert "/var/run/docker.sock" not in compose


def test_dev_container_has_one_image_codex_bootstrap() -> None:
    dockerfile = (REPOSITORY_ROOT / ".devcontainer" / "Dockerfile").read_text()
    package = json.loads(
        (REPOSITORY_ROOT / ".devcontainer" / "codex-install" / "package.json").read_text()
    )

    assert "@openai/codex" not in package["dependencies"]
    assert (
        "npm install --global --prefix /opt/codex-latest --ignore-scripts "
        "@openai/codex@latest" in dockerfile
    )
    assert "ln -s /opt/codex-latest/bin/codex /usr/local/bin/codex" in dockerfile


def test_dev_verification_refuses_a_remote_database_host() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "QTRAD_TEST_POSTGRES_HOST": "collector.example.invalid",
            "QTRAD_TEST_POSTGRES_USER": "test",
            "QTRAD_TEST_POSTGRES_PASSWORD": "test",
        }
    )

    result = subprocess.run(
        [REPOSITORY_ROOT / "ops" / "dev" / "verify.sh"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr == "refusing non-local PostgreSQL test host: collector.example.invalid\n"
