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
personality = "pragmatic"
""",
            encoding="utf-8",
        )

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
