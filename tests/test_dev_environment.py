from __future__ import annotations

import json
import os
import runpy
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

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
        '"postStartCommand": "uv run alembic upgrade head && codex remote-control stop '
        '&& codex remote-control start"' in devcontainer
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


def test_dev_container_includes_github_cli() -> None:
    dockerfile = (REPOSITORY_ROOT / ".devcontainer" / "Dockerfile").read_text()

    assert "\n        gh \\\n" in dockerfile


def test_post_install_adds_reviewed_github_actions_toolset_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        '[mcp_servers.github]\nurl = "https://api.githubcopilot.com/mcp/"\n',
        encoding="utf-8",
    )
    config.chmod(0o600)
    monkeypatch.setenv("HOME", str(tmp_path))
    namespace = runpy.run_path(str(REPOSITORY_ROOT / ".devcontainer" / "post_install.py"))
    setter = cast(Callable[[str, str, str, object], None], namespace["set_mcp_server_setting"])

    setter(
        "github",
        "http_headers",
        '{ "X-MCP-Toolsets" = "default,actions" }',
        {"X-MCP-Toolsets": "default,actions"},
    )
    setter(
        "github",
        "http_headers",
        '{ "X-MCP-Toolsets" = "default,actions" }',
        {"X-MCP-Toolsets": "default,actions"},
    )

    assert config.read_text(encoding="utf-8").count("X-MCP-Toolsets") == 1
    assert config.stat().st_mode & 0o777 == 0o600


def test_post_install_rejects_conflicting_mcp_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        '[mcp_servers.github]\nhttp_headers = { "X-MCP-Toolsets" = "default" }\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    namespace = runpy.run_path(str(REPOSITORY_ROOT / ".devcontainer" / "post_install.py"))
    setter = cast(Callable[[str, str, str, object], None], namespace["set_mcp_server_setting"])

    with pytest.raises(RuntimeError, match="unexpected MCP setting http_headers for github"):
        setter(
            "github",
            "http_headers",
            '{ "X-MCP-Toolsets" = "default,actions" }',
            {"X-MCP-Toolsets": "default,actions"},
        )


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
