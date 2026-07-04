#!/usr/bin/env python3
"""Configure persistent, container-local development state."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


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
""",
            encoding="utf-8",
        )

    current = config.read_text(encoding="utf-8")
    sections: list[str] = []
    if "[mcp_servers.tilth]" not in current:
        sections.append(
            """
[mcp_servers.tilth]
command = "/opt/codex-install/node_modules/.bin/tilth"
args = ["--mcp"]

[mcp_servers.tilth.tools.tilth_search]
approval_mode = "approve"

[mcp_servers.tilth.tools.tilth_read]
approval_mode = "approve"

[mcp_servers.tilth.tools.tilth_diff]
approval_mode = "approve"

[mcp_servers.tilth.tools.tilth_files]
approval_mode = "approve"

[mcp_servers.tilth.tools.tilth_grok]
approval_mode = "approve"

[mcp_servers.tilth.tools.tilth_deps]
approval_mode = "approve"
"""
        )
    if "[mcp_servers.context7]" not in current:
        sections.append(
            """
[mcp_servers.context7]
url = "https://mcp.context7.com/mcp"
"""
        )
    if sections:
        with config.open("a", encoding="utf-8") as file:
            file.write("".join(sections))

    host_agents = Path("/workspace/.devcontainer/local/AGENTS.md")
    if host_agents.is_file() and host_agents.stat().st_size:
        shutil.copyfile(host_agents, Path.home() / ".codex" / "AGENTS.md")


def main() -> None:
    print("[post_install] configuring q-trad Dev Container", file=sys.stderr)
    ensure_owned_directories()
    configure_history()
    configure_codex()
    Path("tmp").mkdir(exist_ok=True)
    print("[post_install] complete", file=sys.stderr)


if __name__ == "__main__":
    main()
