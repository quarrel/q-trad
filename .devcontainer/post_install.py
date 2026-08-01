#!/usr/bin/env python3
"""Configure persistent, container-local development state."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

MCP_SERVER_NAMES = ("context7", "tilth")
RETIRED_MCP_SERVER_NAMES = ("github", "token-optimizer")


def ensure_owned_directories() -> None:
    uid = os.getuid()
    gid = os.getgid()
    paths = (
        Path("/commandhistory"),
        Path.home() / ".cache",
        Path.home() / ".codex",
        Path("/workspace/.venv"),
    )
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        if path.stat().st_uid == uid and path.stat().st_gid == gid:
            continue
        subprocess.run(
            ["sudo", "chown", "-R", f"{uid}:{gid}", str(path)],
            check=True,
        )


def configure_history() -> None:
    history = Path("/commandhistory/.bash_history")
    history.touch(exist_ok=True)
    profile = Path.home() / ".bashrc"
    declaration = "export HISTFILE=/commandhistory/.bash_history"
    current = profile.read_text(encoding="utf-8") if profile.exists() else ""
    if declaration not in current:
        with profile.open("a", encoding="utf-8") as file:
            file.write(f"\n{declaration}\n")


def configure_codex() -> None:
    config = Path.home() / ".codex" / "config.toml"
    if not config.exists():
        config.write_text(
            """# Docker is the outer isolation boundary for this Dev Container.
approval_policy = "never"
sandbox_mode = "danger-full-access"
personality = "pragmatic"
""",
            encoding="utf-8",
        )

    host_agents = Path("/workspace/.devcontainer/local/AGENTS.md")
    if host_agents.is_file() and host_agents.stat().st_size:
        shutil.copyfile(host_agents, Path.home() / ".codex" / "AGENTS.md")


def run_codex_mcp(*arguments: str) -> None:
    """Run one Codex MCP configuration command without a shell."""
    subprocess.run(["codex", "mcp", *arguments], check=True)


def set_mcp_server_setting(
    server_name: str,
    setting_name: str,
    toml_value: str,
    expected_value: object,
) -> None:
    """Atomically add one reviewed setting to a CLI-created MCP server section."""
    config = Path.home() / ".codex" / "config.toml"
    current = config.read_text(encoding="utf-8")
    header = f"[mcp_servers.{server_name}]"
    lines = current.splitlines(keepends=True)
    try:
        section_start = next(
            index for index, line in enumerate(lines) if line.rstrip("\r\n") == header
        )
    except StopIteration as error:
        raise RuntimeError(f"missing MCP server section: {server_name}") from error

    section_end = next(
        (
            index
            for index in range(section_start + 1, len(lines))
            if lines[index].lstrip().startswith("[")
        ),
        len(lines),
    )
    expected_line = f"{setting_name} = {toml_value}"
    existing = [
        line.strip()
        for line in lines[section_start + 1 : section_end]
        if line.partition("=")[0].strip() == setting_name
    ]
    if existing and existing != [expected_line]:
        raise RuntimeError(f"unexpected MCP setting {setting_name} for {server_name}")
    if not existing:
        lines.insert(section_end, f"{expected_line}\n")

    updated = "".join(lines)
    parsed = tomllib.loads(updated)
    if parsed["mcp_servers"][server_name][setting_name] != expected_value:
        raise RuntimeError(f"failed to configure MCP setting {setting_name} for {server_name}")

    mode = config.stat().st_mode & 0o777
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config.parent,
            prefix=".config.toml.",
            delete=False,
        ) as temporary:
            temporary.write(updated)
            temporary_name = temporary.name
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, config)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def forward_mcp_environment_variable(server_name: str, variable_name: str) -> None:
    """Add Codex's parent-environment pass-through setting without persisting a secret."""
    set_mcp_server_setting(
        server_name,
        "env_vars",
        f'["{variable_name}"]',
        [variable_name],
    )


def configure_mcp_servers() -> None:
    """Replace project MCP registrations sequentially and verify safe identities."""
    missing_environment = [name for name in ("CONTEXT7_API_KEY",) if not os.environ.get(name)]
    if missing_environment:
        missing = ", ".join(missing_environment)
        raise RuntimeError(f"required MCP environment is missing: {missing}")

    configured = json.loads(
        subprocess.run(
            ["codex", "mcp", "list", "--json"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    configured_names = {server["name"] for server in configured}
    for name in (*MCP_SERVER_NAMES, *RETIRED_MCP_SERVER_NAMES):
        if name in configured_names:
            run_codex_mcp("remove", name)

    # Context7 reads the inherited environment variable. Do not persist its value in
    # config.toml or in a process argument that a diagnostic command can render.
    run_codex_mcp("add", "context7", "--", "npx", "-y", "@upstash/context7-mcp")
    # `codex mcp add --env` accepts only literal KEY=VALUE pairs. Add the
    # documented env_vars pass-through after the CLI has created the section.
    forward_mcp_environment_variable("context7", "CONTEXT7_API_KEY")

    run_codex_mcp(
        "add",
        "tilth",
        "--",
        "/opt/codex-install/node_modules/.bin/tilth",
        "--mcp",
        "--edit",
    )

    verified = json.loads(
        subprocess.run(
            ["codex", "mcp", "list", "--json"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    by_name = {server["name"]: server for server in verified}
    if not set(MCP_SERVER_NAMES).issubset(by_name):
        raise RuntimeError("one or more required MCP servers were not registered")

    context7 = by_name["context7"]["transport"]
    if (
        context7["command"] != "npx"
        or context7["args"]
        != [
            "-y",
            "@upstash/context7-mcp",
        ]
        or context7["env_vars"] != ["CONTEXT7_API_KEY"]
    ):
        raise RuntimeError("Context7 MCP registration does not match the reviewed command")

    tilth = by_name["tilth"]["transport"]
    if tilth["command"] != "/opt/codex-install/node_modules/.bin/tilth" or tilth["args"] != [
        "--mcp",
        "--edit",
    ]:
        raise RuntimeError("Tilth MCP registration does not match the reviewed command")


def main() -> None:
    print("[post_install] configuring q-trad Dev Container", file=sys.stderr)
    ensure_owned_directories()
    configure_history()
    configure_codex()
    configure_mcp_servers()
    Path("tmp").mkdir(exist_ok=True)
    print("[post_install] complete", file=sys.stderr)


if __name__ == "__main__":
    main()
