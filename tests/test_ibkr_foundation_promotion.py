from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import qtrad.runtime.ibkr_foundation as foundation_runtime
import qtrad.runtime.ibkr_foundation_promotion as promotion_runtime
import qtrad.runtime.provider_history as provider_history_runtime
from qtrad.application.ibkr_foundation import IBKRFoundationBuild, build_ibkr_foundation
from qtrad.application.r2_ibkr_historical import build_ibkr_historical_experiment
from qtrad.domain.ibkr_foundation import (
    IBKRFoundationReadinessState,
    VerifiedIbkrFoundationPromotion,
)
from qtrad.domain.r2_ibkr_historical import IBKRHistoricalAdapterIdentity
from qtrad.domain.r2_readiness import EvidenceClass
from qtrad.domain.research import ObservationDataset
from qtrad.runtime.ibkr_foundation import verify_ibkr_foundation, write_ibkr_foundation
from qtrad.runtime.ibkr_foundation_promotion import (
    authenticate_ibkr_foundation_promotion,
    create_ibkr_foundation_confirmatory_promotion,
)
from qtrad.runtime.provider_history import read_provider_history_source_evidence
from tests.test_ibkr_foundation import _evaluate_session_aware_readiness
from tests.test_provider_history import _published_provider_history
from tests.test_r1_foundation import _config
from tests.test_r2_ibkr_historical import _foundation as _r2_foundation


def _runtime() -> dict[str, str]:
    commit = "a" * 40
    image = "sha256:" + "b" * 64
    return {
        "application_identity": f"q-trad+git:{commit}+image:{image}",
        "image_identity": image,
        "python_identity": "fixture-python",
        "numpy_identity": "fixture-numpy",
        "sklearn_identity": "fixture-sklearn",
    }


def _foundation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    qualifying: bool,
) -> tuple[Path, Path, IBKRFoundationBuild]:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, 0, 30, tzinfo=UTC),
    )
    source = read_provider_history_source_evidence(provider_manifest)
    build = build_ibkr_foundation(source, configuration)
    if qualifying:
        readiness, _gaps = _evaluate_session_aware_readiness()
        build = replace(build, readiness=readiness)
        monkeypatch.setattr(
            foundation_runtime,
            "build_ibkr_foundation",
            lambda *_args, **_kwargs: build,
        )
    bundle = tmp_path / "foundation.json"
    write_ibkr_foundation(
        bundle,
        provider_manifest=provider_manifest,
        configuration=configuration,
    )
    receipt = tmp_path / "verification.json"
    verify_ibkr_foundation(bundle, receipt_output=receipt)
    return bundle, receipt, build


def _promote(
    bundle: Path,
    receipt: Path,
    output: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> VerifiedIbkrFoundationPromotion:
    monkeypatch.setattr("qtrad.runtime.r2_verification.runtime_identities", _runtime)
    monkeypatch.setattr(promotion_runtime, "_require_detached_source", lambda: None)
    return create_ibkr_foundation_confirmatory_promotion(
        bundle,
        receipt=receipt,
        output=output,
        authorized_by="stage8-operator",
        authorized_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        authorization_reference="S8.4 approved operational handoff",
        workers=1,
    )


def test_qualifying_promotion_is_create_only_and_authenticates_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, receipt, _build = _foundation(tmp_path, monkeypatch, qualifying=True)
    promotion = tmp_path / "promotion.json"
    authority = _promote(bundle, receipt, promotion, monkeypatch)

    assert authority.foundation_bundle_id
    document = json.loads(promotion.read_bytes())
    assert document["contract"] == "qtrad-ibkr-foundation-confirmatory-promotion-v1"
    assert document["profile"] == "CONFIRMATORY"
    assert document["evidence_class"] == "CONFIRMATORY"
    assert document["readiness"]["state"] == IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY

    promotion_bytes = promotion.read_bytes()
    second_promotion = tmp_path / "promotion-2.json"
    _promote(bundle, receipt, second_promotion, monkeypatch)
    assert second_promotion.read_bytes() == promotion_bytes
    with pytest.raises(FileExistsError):
        _promote(bundle, receipt, promotion, monkeypatch)
    assert promotion.read_bytes() == promotion_bytes

    def no_replay(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("promotion authentication performed semantic replay")

    monkeypatch.setattr(foundation_runtime, "verify_ibkr_foundation", no_replay)
    monkeypatch.setattr(foundation_runtime, "build_ibkr_foundation", no_replay)
    monkeypatch.setattr(foundation_runtime, "_read_child_rows", no_replay)
    monkeypatch.setattr(
        provider_history_runtime, "read_provider_history_source_evidence", no_replay
    )
    monkeypatch.setattr(provider_history_runtime, "_read_parquet_rows", no_replay)
    monkeypatch.setattr("qtrad.runtime.r2_verification.runtime_identities", no_replay)
    authenticated = authenticate_ibkr_foundation_promotion(
        bundle, receipt=receipt, promotion=promotion
    )
    assert authenticated.promotion_sha256 == authority.promotion_sha256

    experiment = build_ibkr_historical_experiment(
        cast(IBKRFoundationBuild, _r2_foundation()),
        foundation_bundle_id=authority.foundation_bundle_id,
        adapter_identity=IBKRHistoricalAdapterIdentity.create(
            foundation_bundle_id=authority.foundation_bundle_id,
            application_identity=_runtime()["application_identity"],
            image_identity=_runtime()["image_identity"],
        ),
        evidence_class=EvidenceClass.CONFIRMATORY,
        promotion_authority=authenticated,
    )
    assert experiment.evidence_class.value == "CONFIRMATORY"


def test_promotion_fails_closed_before_replay_and_rejects_nonqualifying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, receipt, _build = _foundation(tmp_path, monkeypatch, qualifying=False)
    output = tmp_path / "promotion.json"

    def replay_reached(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("data-scale replay reached")

    monkeypatch.setattr(
        "qtrad.runtime.r2_verification.runtime_identities",
        lambda: (_ for _ in ()).throw(ValueError("dirty source tree")),
    )
    monkeypatch.setattr(promotion_runtime, "verify_ibkr_foundation", replay_reached)
    with pytest.raises(ValueError, match="dirty source tree"):
        create_ibkr_foundation_confirmatory_promotion(
            bundle,
            receipt=receipt,
            output=output,
            authorized_by="operator",
            authorized_at=datetime(2026, 8, 12, tzinfo=UTC),
            authorization_reference="approval",
        )
    assert not output.exists()

    monkeypatch.setattr("qtrad.runtime.r2_verification.runtime_identities", _runtime)
    monkeypatch.setattr(
        promotion_runtime,
        "_require_detached_source",
        lambda: (_ for _ in ()).throw(RuntimeError("attached source tree")),
    )
    with pytest.raises(RuntimeError, match="attached source tree"):
        create_ibkr_foundation_confirmatory_promotion(
            bundle,
            receipt=receipt,
            output=output,
            authorized_by="operator",
            authorized_at=datetime(2026, 8, 12, tzinfo=UTC),
            authorization_reference="approval",
        )
    assert not output.exists()

    monkeypatch.setattr("qtrad.runtime.r2_verification.runtime_identities", _runtime)
    monkeypatch.setattr(promotion_runtime, "_require_detached_source", lambda: None)
    with pytest.raises(ValueError, match="nonqualifying"):
        create_ibkr_foundation_confirmatory_promotion(
            bundle,
            receipt=receipt,
            output=output,
            authorized_by="operator",
            authorized_at=datetime(2026, 8, 12, tzinfo=UTC),
            authorization_reference="approval",
        )
    assert not output.exists()


def test_promotion_rejects_masquerade_root_changes_and_verifier_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, receipt, _build = _foundation(tmp_path, monkeypatch, qualifying=True)
    promotion = tmp_path / "promotion.json"
    _promote(bundle, receipt, promotion, monkeypatch)

    with pytest.raises(ValueError):
        authenticate_ibkr_foundation_promotion(bundle, receipt=receipt, promotion=receipt)

    foundation_document = json.loads(bundle.read_bytes())
    provider = bundle.parent / foundation_document["provider_history_manifest"]
    provider_document = json.loads(provider.read_bytes())
    stage6_result = provider.parent / provider_document["source_result"]["path"]
    for root in (stage6_result, provider, bundle):
        root_bytes = root.read_bytes()
        root.write_bytes(root_bytes + b"\n")
        with pytest.raises(ValueError):
            authenticate_ibkr_foundation_promotion(bundle, receipt=receipt, promotion=promotion)
        root.write_bytes(root_bytes)

    original = promotion.read_bytes()
    for section in ("stage6", "stage7", "stage8"):
        document = json.loads(original)
        key = next(iter(document[section]))
        document[section][key] = "0" * 64
        identity = dict(document)
        identity.pop("promotion_sha256")
        document["promotion_sha256"] = promotion_runtime._sha(identity)
        promotion.write_bytes(promotion_runtime._canonical_bytes(document) + b"\n")
        with pytest.raises(ValueError, match=f"{section} binding changed"):
            authenticate_ibkr_foundation_promotion(bundle, receipt=receipt, promotion=promotion)
    promotion.write_bytes(original)

    monkeypatch.setattr(promotion_runtime, "_PROMOTION_VERIFIER_VERSION", 2)
    with pytest.raises(ValueError, match="verifier is not accepted"):
        authenticate_ibkr_foundation_promotion(bundle, receipt=receipt, promotion=promotion)
