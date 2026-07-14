from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]
SCRIPTS = REPOSITORY_ROOT / "ops" / "capture"
RESEARCH_SCRIPTS = REPOSITORY_ROOT / "ops" / "research"


def test_capture_ingest_has_graceful_stop_contract() -> None:
    compose = (REPOSITORY_ROOT / "compose.capture.yaml").read_text()
    ingest = compose.split("  ingest:\n", maxsplit=1)[1].split("  api:\n", maxsplit=1)[0]

    assert "    stop_signal: SIGINT\n" in ingest
    assert "    stop_grace_period: 90s\n" in ingest


def test_capture_api_requires_a_stable_source_identity() -> None:
    compose = (REPOSITORY_ROOT / "compose.capture.yaml").read_text()
    api = compose.split("  api:\n", maxsplit=1)[1]

    assert "QTRAD_CAPTURE_SOURCE_ID: ${QTRAD_CAPTURE_SOURCE_ID:?" in api


def test_capture_database_is_available_only_on_an_explicit_loopback_port() -> None:
    compose = (REPOSITORY_ROOT / "compose.capture.yaml").read_text()
    database = compose.split("  db:\n", maxsplit=1)[1].split("  ingest:\n", maxsplit=1)[0]

    assert '      - "127.0.0.1:${QTRAD_DB_PORT:-15432}:5432"\n' in database
    assert "0.0.0.0" not in database


def test_storage_snapshot_writer_uid_matches_application_image() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text()
    script = (SCRIPTS / "storage-snapshot.sh").read_text()

    assert "useradd --create-home --uid 10001 qtrad" in dockerfile
    assert "readonly application_uid=10001" in script


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents)
    path.chmod(0o755)


def _run(script: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPTS / script), *args],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


def _run_research(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RESEARCH_SCRIPTS / script)],
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
  *"run --rm --no-deps ingest python -c"*)
    printf '%s\\n' '{{"name":"capture-v1","hash":"{universe_hash}"}}'
    ;;
  *"SELECT version_num FROM alembic_version"*) printf '0006\\n' ;;
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
        "QTRAD_CAPTURE_SOURCE_ID=oci-sydney-capture-1\n"
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
    assert manifest["schema"] == "qtrad-capture-backup-v2"
    assert manifest["capture_source_id"] == "oci-sydney-capture-1"
    assert manifest["universe_name"] == "capture-v1"
    assert manifest["universe_hash"] == universe_hash
    assert manifest["migration_version"] == "0006"
    identity = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    canonical = json.dumps(identity, separators=(",", ":"), sort_keys=True)
    assert (
        subprocess.run(
            ["sha256sum"], input=canonical, check=True, capture_output=True, text=True
        ).stdout.split()[0]
        == manifest["manifest_sha256"]
    )
    assert len(calls.read_text().splitlines()) == 3


def test_storage_snapshot_uses_pinned_one_shot_image_without_dependencies(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "docker-calls"
    evidence_dir = tmp_path / "evidence"
    inspector_image = "example.invalid/qtrad@sha256:" + "a" * 64
    capture_env = tmp_path / "capture.env"
    capture_env.write_text("QTRAD_DATABASE_PASSWORD=test-only\n")
    (tmp_path / "compose.capture.yaml").write_text("services: {}\n")
    _write_executable(
        fake_bin / "install",
        """#!/usr/bin/env bash
set -euo pipefail
mkdir -p "${!#}"
""",
    )
    for command in ("chown", "chmod"):
        _write_executable(fake_bin / command, "#!/usr/bin/env bash\nset -euo pipefail\n")
    _write_executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> '{calls}'
case "$*" in
  "image inspect {inspector_image}") ;;
  *"run --rm --no-deps --pull never"*)
    [[ "${{QTRAD_IMAGE:?}}" == '{inspector_image}' ]]
    [[ "$*" == *"-v {evidence_dir}:/evidence:Z"* ]]
    [[ "$*" == *"--output /evidence/storage-pinned-before.json"* ]]
    printf '{{"schema_version":2}}\\n' > '{evidence_dir}/storage-pinned-before.json'
    ;;
  *) exit 70 ;;
esac
""",
    )

    result = _run(
        "storage-snapshot.sh",
        {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QTRAD_CAPTURE_ROOT": str(tmp_path),
            "QTRAD_CAPTURE_ENV": str(capture_env),
            "QTRAD_STORAGE_EVIDENCE_DIR": str(evidence_dir),
            "QTRAD_STORAGE_INSPECTOR_IMAGE": inspector_image,
        },
        "pinned-before",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(evidence_dir / "storage-pinned-before.json")
    assert len(calls.read_text().splitlines()) == 2

    repeated = _run(
        "storage-snapshot.sh",
        {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QTRAD_CAPTURE_ROOT": str(tmp_path),
            "QTRAD_CAPTURE_ENV": str(capture_env),
            "QTRAD_STORAGE_EVIDENCE_DIR": str(evidence_dir),
            "QTRAD_STORAGE_INSPECTOR_IMAGE": inspector_image,
        },
        "pinned-before",
    )
    assert repeated.returncode != 0
    assert len(calls.read_text().splitlines()) == 2


@pytest.mark.parametrize(
    ("label", "image"),
    [
        ("../escape", "example.invalid/qtrad@sha256:" + "a" * 64),
        ("pinned-before", "example.invalid/qtrad:latest"),
    ],
)
def test_storage_snapshot_rejects_unsafe_identity_before_docker(
    tmp_path: Path,
    label: str,
    image: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_called = tmp_path / "docker-called"
    _write_executable(
        fake_bin / "docker",
        f"#!/usr/bin/env bash\nset -euo pipefail\ntouch '{docker_called}'\n",
    )
    capture_env = tmp_path / "capture.env"
    capture_env.write_text("QTRAD_DATABASE_PASSWORD=test-only\n")
    (tmp_path / "compose.capture.yaml").write_text("services: {}\n")

    result = _run(
        "storage-snapshot.sh",
        {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QTRAD_CAPTURE_ROOT": str(tmp_path),
            "QTRAD_CAPTURE_ENV": str(capture_env),
            "QTRAD_STORAGE_EVIDENCE_DIR": str(tmp_path / "evidence"),
            "QTRAD_STORAGE_INSPECTOR_IMAGE": image,
        },
        label,
    )

    assert result.returncode != 0
    assert not docker_called.exists()


def _qualification_environment(
    tmp_path: Path,
    *,
    now: str,
    include_pre_candidate_run: bool = False,
    include_completed_run: bool = False,
    ready: bool = True,
) -> tuple[dict[str, str], Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls"
    output = tmp_path / "qualification.json"
    image = "example.invalid/qtrad@sha256:" + "1" * 64
    source_id = "oci-sydney-capture-1"
    configuration_hash = "a" * 64
    compose_file = tmp_path / "compose.capture.yaml"
    compose_file.write_text("services: {}\n")
    descriptor_sha = subprocess.run(
        ["sha256sum", str(compose_file)], check=True, capture_output=True, text=True
    ).stdout.split()[0]
    capture_env = tmp_path / "capture.env"
    capture_env.write_text(
        f"QTRAD_IMAGE={image}\n"
        f"QTRAD_CAPTURE_SOURCE_ID={source_id}\n"
        "QTRAD_DATABASE_PASSWORD=test-only\n"
    )
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    (status_dir / "backup-status.json").write_text(
        json.dumps({"success": True, "completed_at": "2026-07-17T03:30:00Z"})
    )
    (status_dir / "restore-status.json").write_text(
        json.dumps({"success": True, "completed_at": "2026-07-17T03:00:00Z"})
    )
    readiness = {
        "ready": ready,
        "reasons": [] if ready else ["IG adapter is not healthy"],
        "expected_instruments": 7,
        "fresh_quote_count": 7,
        "global_position": 500_001,
        "checkpoint_position": 500_001,
        "checkpoint_updated_at": "2026-07-17T04:05:30.123456+00:00",
        "configuration_hash": configuration_hash,
    }
    system = {
        "counts": {"raw_messages": 500_000, "canonical_events": 500_001},
        "adapter_health": [
            {
                "adapter_name": "ig-market-data",
                "environment": "IG_DEMO",
                "status": "HEALTHY",
                "detail": (
                    "state=CONNECTED; subscriptions=7/7; updates=7/7; "
                    "reconnects=2; dropped_records=0; provider_operations=0"
                ),
            }
        ],
        "projection_checkpoints": [],
        "quotas": [],
    }
    runs = [
        {
            "run_id": "00000000-0000-0000-0000-000000000003",
            "kind": "INGESTION",
            "status": "RUNNING",
            "started_at": "2026-07-15T00:00:00.123456+00:00",
            "configuration_hash": configuration_hash,
            "detail": {},
        },
        {
            "run_id": "00000000-0000-0000-0000-000000000002",
            "kind": "INGESTION",
            "status": "STOPPED",
            "started_at": "2026-07-14T06:00:00+00:00",
            "configuration_hash": configuration_hash,
            "detail": {"adapter_health": "state=STOPPED; dropped_records=0"},
        },
        {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "kind": "INGESTION",
            "status": "STOPPED",
            "started_at": "2026-07-14T03:05:33+00:00",
            "configuration_hash": configuration_hash,
            "detail": {"adapter_health": "state=STOPPED; dropped_records=0"},
        },
    ]
    if include_pre_candidate_run:
        runs.append(
            {
                "run_id": "00000000-0000-0000-0000-000000000000",
                "kind": "INGESTION",
                "status": "RUNNING",
                "started_at": "2026-07-13T00:00:00+00:00",
                "configuration_hash": configuration_hash,
                "detail": {},
            }
        )
    if include_completed_run:
        runs.append(
            {
                "run_id": "00000000-0000-0000-0000-000000000004",
                "kind": "INGESTION",
                "status": "COMPLETED",
                "started_at": "2026-07-16T00:00:00+00:00",
                "configuration_hash": configuration_hash,
                "detail": {},
            }
        )
    responses = {
        "/health/ready": readiness,
        "/api/v1/system": system,
        "/api/v1/runs": runs,
        "/api/v1/gaps": [],
    }
    response_case = "\n".join(
        f"  *{path}) printf '%s\\n' '{json.dumps(value)}' > \"$output\"; "
        f"http_code={'503' if path == '/health/ready' and not ready else '200'} ;;"
        for path, value in responses.items()
    )
    _write_executable(
        fake_bin / "curl",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "curl $*" >> '{calls}'
output=''
url=''
http_code=200
while (($#)); do
  case "$1" in
    --output) output=$2; shift 2 ;;
    http://*) url=$1; shift ;;
    *) shift ;;
  esac
done
case "$url" in
{response_case}
  *) exit 70 ;;
esac
printf '%s' "$http_code"
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "systemctl $*" >> '{calls}'
case "$1" in
  is-active) printf 'active\n' ;;
  show) printf 'success\n' ;;
  *) exit 70 ;;
esac
""",
    )
    mount_evidence = json.dumps(
        {
            "filesystems": [
                {
                    "target": str(tmp_path),
                    "source": "/dev/sdb",
                    "fstype": "xfs",
                    "options": "rw,relatime",
                }
            ]
        }
    )
    _write_executable(
        fake_bin / "findmnt",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "findmnt $*" >> '{calls}'
printf '%s\n' '{mount_evidence}'
""",
    )
    compose_services = [
        {"Service": "db", "State": "running", "Health": "healthy"},
        {"Service": "ingest", "State": "running", "Health": ""},
        {"Service": "api", "State": "running", "Health": "healthy"},
    ]
    _write_executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "docker $*" >> '{calls}'
case "$*" in
  *"exec -T db psql"*) printf '0003\n' ;;
  *"ps --format json"*) printf '%s\n' '{json.dumps(compose_services)}' ;;
  *) exit 70 ;;
esac
""",
    )
    environment = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "QTRAD_CAPTURE_ROOT": str(tmp_path),
        "QTRAD_CAPTURE_ENV": str(capture_env),
        "QTRAD_STATUS_DIR": str(status_dir),
        "QTRAD_DATA_MOUNT": str(tmp_path),
        "QTRAD_QUALIFICATION_START": "2026-07-14T03:05:33Z",
        "QTRAD_QUALIFICATION_NOT_BEFORE_END": "2026-07-17T03:05:33Z",
        "QTRAD_QUALIFICATION_NOW": now,
        "QTRAD_QUALIFICATION_IMAGE": image,
        "QTRAD_QUALIFICATION_DESCRIPTOR_COMMIT": "8" * 40,
        "QTRAD_QUALIFICATION_DESCRIPTOR_SHA256": descriptor_sha,
        "QTRAD_QUALIFICATION_SOURCE_ID": source_id,
        "QTRAD_QUALIFICATION_CONFIGURATION_HASH": configuration_hash,
        "QTRAD_QUALIFICATION_MIGRATION": "0003",
    }
    return environment, output, calls


def test_qualification_evidence_is_bounded_hash_verified_and_read_only(tmp_path: Path) -> None:
    environment, output, calls = _qualification_environment(tmp_path, now="2026-07-17T04:05:33Z")

    result = _run("qualification-evidence.sh", environment, str(output))

    assert result.returncode == 0, result.stderr
    evidence = json.loads(output.read_text())
    assert output.stat().st_mode & 0o777 == 0o600
    assert evidence["schema"] == "qtrad-capture-qualification-v1"
    assert evidence["automatic_checks_passed"] is True
    assert len(evidence["release"]["evidence_tool_sha256"]) == 64
    assert evidence["qualification_decision"] == "PENDING_OPERATOR_REVIEW"
    assert evidence["operator_reviews"] == {
        "candidate_gap_classification": "NOT_REQUIRED",
        "container_log_history": "REQUIRED",
        "monitoring_history": "REQUIRED",
        "active_market_storage_interval": "REQUIRED",
    }
    identity = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
    canonical = json.dumps(identity, separators=(",", ":"), sort_keys=True)
    assert (
        subprocess.run(
            ["sha256sum"], input=canonical, check=True, capture_output=True, text=True
        ).stdout.split()[0]
        == evidence["evidence_sha256"]
    )
    recorded_calls = calls.read_text()
    assert "exec -T db psql" in recorded_calls
    assert "SELECT version_num FROM alembic_version" in recorded_calls
    assert " ps --format json" in recorded_calls
    assert "findmnt --json --target" in recorded_calls
    assert " up " not in recorded_calls
    assert " restart " not in recorded_calls
    assert " stop " not in recorded_calls

    repeated = _run("qualification-evidence.sh", environment, str(output))
    assert repeated.returncode != 0


@pytest.mark.parametrize(
    ("now", "include_pre_candidate_run", "include_completed_run", "ready", "failed_check"),
    [
        ("2026-07-17T03:05:32Z", False, False, True, "now_at_or_after_end"),
        ("2026-07-17T04:05:33Z", True, False, True, "pre_candidate_runs_reconciled"),
        ("2026-07-17T04:05:33Z", False, True, True, "no_candidate_unexpected_statuses"),
        ("2026-07-17T04:05:33Z", False, False, False, "readiness_http_200"),
    ],
)
def test_qualification_evidence_fails_closed_with_reviewable_output(
    tmp_path: Path,
    now: str,
    include_pre_candidate_run: bool,
    include_completed_run: bool,
    ready: bool,
    failed_check: str,
) -> None:
    environment, output, _ = _qualification_environment(
        tmp_path,
        now=now,
        include_pre_candidate_run=include_pre_candidate_run,
        include_completed_run=include_completed_run,
        ready=ready,
    )

    result = _run("qualification-evidence.sh", environment, str(output))

    assert result.returncode != 0
    evidence = json.loads(output.read_text())
    assert evidence["automatic_checks_passed"] is False
    assert evidence["automatic_checks"][failed_check] is False


@pytest.mark.parametrize("manifest_version", [1, 2])
def test_restore_verification_uses_manifest_pinned_postgres_image(
    tmp_path: Path, manifest_version: int
) -> None:
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
    manifest_identity = {
        "schema": f"qtrad-capture-backup-v{manifest_version}",
        "created_at": "2026-07-13T00:00:00Z",
        "archive": archive_name,
        "sha256": digest,
        "database": "qtrad_capture",
        "capture_source_id": "oci-sydney-capture-1",
        "universe_name": "capture-v1",
        "universe_hash": "a" * 64,
        "capture_image": "example.invalid/qtrad@sha256:" + "1" * 64,
        "postgres_image": postgres_image,
    }
    if manifest_version == 2:
        manifest_identity["migration_version"] = "0006"
        canonical_manifest = json.dumps(manifest_identity, separators=(",", ":"), sort_keys=True)
        manifest_identity["manifest_sha256"] = subprocess.run(
            ["sha256sum"], input=canonical_manifest, check=True, capture_output=True, text=True
        ).stdout.split()[0]
    else:
        manifest_identity.pop("capture_source_id")
        manifest_identity.pop("universe_name")
    (object_store / f"{archive_name}.manifest.json").write_text(json.dumps(manifest_identity))
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
  *"SELECT version_num FROM alembic_version"*) printf '0006\\n' ;;
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
            "QTRAD_EXPECTED_V1_MIGRATION_VERSION": "0006",
        },
    )

    assert result.returncode == 0, result.stderr
    status = json.loads((status_dir / "restore-status.json").read_text())
    assert status["success"] is True
    assert status["canonical_event_count"] == 42
    assert status["migration_version"] == "0006"
    assert status["manifest_schema"] == f"qtrad-capture-backup-v{manifest_version}"
    assert postgres_image in docker_calls.read_text()


@pytest.mark.parametrize("manifest_version", [1, 2])
def test_research_snapshot_import_is_verified_non_overwriting_and_evidenced(
    tmp_path: Path, manifest_version: int
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    archive_name = "qtrad-capture-20260713T000000Z.dump"
    archive = tmp_path / archive_name
    archive.write_text("archive")
    archive_sha = subprocess.run(
        ["sha256sum", str(archive)], check=True, capture_output=True, text=True
    ).stdout.split()[0]
    checksum = tmp_path / f"{archive_name}.sha256"
    checksum.write_text(f"{archive_sha}  {archive_name}\n")
    universe_hash = "a" * 64
    manifest_identity = {
        "schema": f"qtrad-capture-backup-v{manifest_version}",
        "created_at": "2026-07-13T00:00:00Z",
        "archive": archive_name,
        "sha256": archive_sha,
        "database": "qtrad_capture",
        "capture_source_id": "oci-sydney-capture-1",
        "universe_name": "capture-v1",
        "universe_hash": universe_hash,
        "capture_image": "example.invalid/qtrad@sha256:" + "1" * 64,
        "postgres_image": "postgres@sha256:" + "2" * 64,
    }
    if manifest_version == 2:
        manifest_identity["migration_version"] = "0006"
        canonical_manifest = json.dumps(manifest_identity, separators=(",", ":"), sort_keys=True)
        manifest_identity["manifest_sha256"] = subprocess.run(
            ["sha256sum"], input=canonical_manifest, check=True, capture_output=True, text=True
        ).stdout.split()[0]
    else:
        manifest_identity.pop("capture_source_id")
        manifest_identity.pop("universe_name")
    manifest = tmp_path / f"{archive_name}.manifest.json"
    manifest.write_text(json.dumps(manifest_identity))
    calls = tmp_path / "calls"
    for command in ("createdb", "dropdb"):
        _write_executable(
            fake_bin / command,
            f"#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' '{command} $*' >> '{calls}'\n",
        )
    _write_executable(
        fake_bin / "pg_restore",
        f"#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' 'pg_restore $*' >> '{calls}'\n",
    )
    _write_executable(
        fake_bin / "psql",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "psql $*" >> '{calls}'
case "$*" in
  *"SELECT datname FROM pg_database"*) ;;
  *"SELECT (SELECT version_num"*) printf '0006|12|11\\n' ;;
esac
""",
    )
    evidence = tmp_path / "import.json"
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "QTRAD_SNAPSHOT_ARCHIVE": str(archive),
        "QTRAD_SNAPSHOT_CHECKSUM": str(checksum),
        "QTRAD_SNAPSHOT_MANIFEST": str(manifest),
        "QTRAD_RESEARCH_DATABASE": "qtrad_research_capture_20260713",
        "QTRAD_RESEARCH_IMPORT_EVIDENCE": str(evidence),
        "QTRAD_EXPECTED_CAPTURE_SOURCE_ID": "oci-sydney-capture-1",
        "QTRAD_EXPECTED_UNIVERSE_HASH": universe_hash,
        "QTRAD_EXPECTED_V1_MIGRATION_VERSION": "0006",
    }

    result = _run_research("import-capture-snapshot.sh", env)

    assert result.returncode == 0, result.stderr
    imported = json.loads(evidence.read_text())
    assert imported["schema"] == "qtrad-research-snapshot-import-v1"
    assert imported["capture_source_id"] == "oci-sydney-capture-1"
    assert imported["source_manifest_schema"] == f"qtrad-capture-backup-v{manifest_version}"
    assert imported["raw_message_count"] == 12
    assert imported["canonical_event_count"] == 11
    assert imported["import_sha256"]
    assert "createdb" in calls.read_text()
    assert "dropdb" not in calls.read_text()

    repeated = _run_research("import-capture-snapshot.sh", env)
    assert repeated.returncode != 0

    _write_executable(
        fake_bin / "pg_restore",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "pg_restore $*" >> '{calls}'
[[ "$*" == *"--list"* ]]
""",
    )
    failed_env = {
        **env,
        "QTRAD_RESEARCH_DATABASE": "qtrad_research_failed_20260713",
        "QTRAD_RESEARCH_IMPORT_EVIDENCE": str(tmp_path / "failed-import.json"),
    }
    failed = _run_research("import-capture-snapshot.sh", failed_env)
    assert failed.returncode != 0
    assert "dropdb" in calls.read_text()


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
