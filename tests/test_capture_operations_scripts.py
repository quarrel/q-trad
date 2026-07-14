from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]
SCRIPTS = REPOSITORY_ROOT / "ops" / "capture"


def test_capture_ingest_has_graceful_stop_contract() -> None:
    compose = (REPOSITORY_ROOT / "compose.capture.yaml").read_text()
    ingest = compose.split("  ingest:\n", maxsplit=1)[1].split("  api:\n", maxsplit=1)[0]

    assert "    stop_signal: SIGINT\n" in ingest
    assert "    stop_grace_period: 90s\n" in ingest


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents)
    path.chmod(0o755)


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPTS / script)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


def test_backup_writes_manifest_status_and_object_set(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "oci-calls"
    universe_hash = "a" * 64
    _write_executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  *"exec -T db pg_dump"*) printf 'archive' ;;
  *"exec -T db pg_restore --list"*) exit 0 ;;
  *"run --rm --no-deps ingest python -c"*) printf '%s\\n' '{universe_hash}' ;;
  *) printf 'unexpected docker call: %s\\n' "$*" >&2; exit 70 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "oci",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> '{calls}'
""",
    )
    capture_env = tmp_path / "capture.env"
    capture_env.write_text(
        "QTRAD_IMAGE=example.invalid/qtrad@sha256:" + "1" * 64 + "\n"
        "QTRAD_POSTGRES_IMAGE=postgres@sha256:" + "2" * 64 + "\n"
    )
    backup_dir = tmp_path / "backups"
    status_dir = tmp_path / "status"

    result = _run(
        "backup.sh",
        {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QTRAD_CAPTURE_ROOT": str(tmp_path),
            "QTRAD_CAPTURE_ENV": str(capture_env),
            "QTRAD_BACKUP_DIR": str(backup_dir),
            "QTRAD_STATUS_DIR": str(status_dir),
            "QTRAD_BACKUP_BUCKET": "capture-backups",
            "QTRAD_WEEKLY_BACKUP_DAY": "0",
        },
    )

    assert result.returncode == 0, result.stderr
    status = json.loads((status_dir / "backup-status.json").read_text())
    assert status["success"] is True
    assert status["universe_hash"] == universe_hash
    manifest = json.loads(next(backup_dir.glob("*.manifest.json")).read_text())
    assert manifest["schema"] == "qtrad-capture-backup-v1"
    assert manifest["universe_hash"] == universe_hash
    assert len(calls.read_text().splitlines()) == 3


def test_restore_verification_uses_manifest_pinned_postgres_image(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    object_store = tmp_path / "objects" / "daily"
    object_store.mkdir(parents=True)
    archive_name = "qtrad-capture-20260713T000000Z.dump"
    archive = object_store / archive_name
    archive.write_text("archive")
    digest = subprocess.run(
        ["sha256sum", str(archive)], check=True, capture_output=True, text=True
    ).stdout.split()[0]
    (object_store / f"{archive_name}.sha256").write_text(f"{digest}  {archive_name}\n")
    postgres_image = "postgres@sha256:" + "2" * 64
    (object_store / f"{archive_name}.manifest.json").write_text(
        json.dumps(
            {
                "schema": "qtrad-capture-backup-v1",
                "archive": archive_name,
                "sha256": digest,
                "universe_hash": "a" * 64,
                "postgres_image": postgres_image,
            }
        )
    )
    object_list = json.dumps(
        {
            "data": [
                {
                    "name": f"daily/{archive_name}",
                    "time-created": "2026-07-13T00:00:00Z",
                }
            ]
        }
    )
    _write_executable(
        fake_bin / "oci",
        f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *" object list "* ]]; then
  printf '%s\\n' '{object_list}'
elif [[ "$*" == *" object get "* ]]; then
  name=''
  file=''
  while (($#)); do
    case "$1" in
      --name) name=$2; shift 2 ;;
      --file) file=$2; shift 2 ;;
      *) shift ;;
    esac
  done
  cp '{tmp_path}/objects/'"$name" "$file"
else
  exit 70
fi
""",
    )
    docker_calls = tmp_path / "docker-calls"
    _write_executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> '{docker_calls}'
case "$*" in
  *"SELECT version_num FROM alembic_version"*) printf '0003\\n' ;;
  *"SELECT count(*) FROM canonical.events"*) printf '42\\n' ;;
esac
""",
    )
    status_dir = tmp_path / "status"

    result = _run(
        "restore-verify.sh",
        {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QTRAD_BACKUP_BUCKET": "capture-backups",
            "QTRAD_STATUS_DIR": str(status_dir),
        },
    )

    assert result.returncode == 0, result.stderr
    status = json.loads((status_dir / "restore-status.json").read_text())
    assert status["success"] is True
    assert status["canonical_event_count"] == 42
    assert postgres_image in docker_calls.read_text()


@pytest.mark.parametrize(
    ("ready", "expected_code"),
    [(True, 0), (False, 1)],
)
def test_healthwatch_publishes_metrics_and_fails_closed(
    tmp_path: Path, ready: bool, expected_code: int
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    response = {
        "ready": ready,
        "fresh_quote_count": 7,
        "global_position": 102,
        "checkpoint_position": 100,
    }
    _write_executable(
        fake_bin / "curl",
        f"""#!/usr/bin/env bash
set -euo pipefail
output=''
while (($#)); do
  case "$1" in
    --output) output=$2; shift 2 ;;
    *) shift ;;
  esac
done
printf '%s\\n' '{json.dumps(response)}' > "$output"
printf '%s' '{200 if ready else 503}'
""",
    )
    metrics_copy = tmp_path / "metrics.json"
    _write_executable(
        fake_bin / "oci",
        f"""#!/usr/bin/env bash
set -euo pipefail
for argument in "$@"; do
  if [[ "$argument" == file://* ]]; then cp "${{argument#file://}}" '{metrics_copy}'; fi
done
""",
    )
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    completed_at = subprocess.run(
        ["date", "--utc", "+%Y-%m-%dT%H:%M:%SZ"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = json.dumps({"success": True, "completed_at": completed_at})
    (status_dir / "backup-status.json").write_text(status)
    (status_dir / "restore-status.json").write_text(status)

    result = _run(
        "healthwatch.sh",
        {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QTRAD_OCI_METRIC_NAMESPACE": "qtrad_capture",
            "QTRAD_OCI_COMPARTMENT_ID": "ocid1.compartment.example",
            "QTRAD_OCI_TELEMETRY_ENDPOINT": "https://telemetry-ingestion.example.invalid",
            "QTRAD_STATUS_DIR": str(status_dir),
            "QTRAD_DATA_MOUNT": str(tmp_path),
        },
    )

    assert result.returncode == expected_code, result.stderr
    metrics = json.loads(metrics_copy.read_text())
    values = {metric["name"]: metric["datapoints"][0]["value"] for metric in metrics}
    assert values["collector_ready"] == int(ready)
    assert values["projection_lag_positions"] == 2
    assert values["restore_verified"] == 1
