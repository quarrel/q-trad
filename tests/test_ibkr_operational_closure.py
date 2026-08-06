from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from qtrad.application import ibkr_historical as historical


def test_commit_metadata_is_used_when_git_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = "a" * 40
    (tmp_path / ".qtrad-commit").write_text(f"{commit}\n", encoding="ascii")

    def unavailable(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("git is not installed")

    monkeypatch.setattr(historical.subprocess, "run", unavailable)
    assert historical._derive_qtrad_commit(tmp_path, require_clean=True) == commit


def test_commit_metadata_rejects_non_sha1_identity(tmp_path: Path) -> None:
    (tmp_path / ".qtrad-commit").write_text("not-a-commit\n", encoding="ascii")
    with pytest.raises(RuntimeError, match="lowercase SHA-1"):
        historical._read_build_commit(tmp_path)


def test_configured_image_digest_normalizes_full_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    digest = "sha256:" + "b" * 64
    monkeypatch.setenv("QTRAD_IMAGE_DIGEST", f"syd.ocir.io/example/qtrad-ibkr@{digest}")
    assert historical.configured_image_digest() == digest


def test_configured_image_digest_accepts_bare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    digest = "sha256:" + "c" * 64
    monkeypatch.setenv("QTRAD_IMAGE_DIGEST", digest)
    assert historical.configured_image_digest() == digest


def test_configured_image_digest_rejects_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QTRAD_IMAGE_DIGEST", "syd.ocir.io/example/qtrad-ibkr:latest")
    with pytest.raises(RuntimeError, match="sha256 digest"):
        historical.configured_image_digest()


def test_commit_metadata_is_used_from_image_root_when_source_tree_is_nested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = "b" * 40
    source_root = tmp_path / "app" / "src"
    source_root.mkdir(parents=True)
    (tmp_path / "app" / ".qtrad-commit").write_text(f"{commit}\n", encoding="ascii")

    def unavailable(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("git is not installed")

    monkeypatch.setattr(historical.subprocess, "run", unavailable)
    assert historical._derive_qtrad_commit(source_root, require_clean=True) == commit
