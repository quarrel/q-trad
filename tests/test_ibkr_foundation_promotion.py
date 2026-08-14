from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import qtrad.runtime.ibkr_foundation as foundation_runtime
import qtrad.runtime.ibkr_foundation_promotion as promotion_runtime
import qtrad.runtime.provider_history as provider_history_runtime
from qtrad.application.ibkr_foundation import IBKRFoundationBuild, build_ibkr_foundation
from qtrad.application.r2_ibkr_historical import build_ibkr_historical_experiment
from qtrad.domain.ibkr_foundation import (
    IBKR_CONFIRMATORY_INSTRUMENTS,
    IBKRFoundationReadinessState,
    VerifiedIbkrFoundationPromotion,
)
from qtrad.domain.ibkr_historical import IbkrHistoricalRequest
from qtrad.domain.ibkr_results import IbkrHistoricalRequestResult
from qtrad.domain.r2_ibkr_historical import IBKRHistoricalAdapterIdentity
from qtrad.domain.r2_readiness import EvidenceClass
from qtrad.domain.research import ObservationDataset
from qtrad.runtime.ibkr_foundation import (
    _verify_ibkr_foundation_migration_v2 as verify_ibkr_foundation,
)
from qtrad.runtime.ibkr_foundation import (
    _write_ibkr_foundation_migration_v2 as write_ibkr_foundation,
)
from qtrad.runtime.ibkr_foundation_promotion import (
    _authenticate_ibkr_foundation_promotion_migration_v2 as authenticate_ibkr_foundation_promotion,
)
from qtrad.runtime.ibkr_foundation_promotion import (
    _create_ibkr_foundation_confirmatory_promotion_migration_v2 as _create_promotion,
)
from qtrad.runtime.ibkr_results import IbkrHistoricalResultStream
from qtrad.runtime.provider_history_v2 import (
    authenticate_provider_history_v2,
    provider_history_v2_verifier_sha256,
)
from qtrad.runtime.r2_bundles import verify_r2_oof_bundle
from tests.test_ibkr_foundation import _evaluate_session_aware_readiness
from tests.test_provider_history import (
    _provider_history_receipt,
    _published_provider_history,
)
from tests.test_r1_foundation import _config
from tests.test_r2_confirmatory import _build_confirmatory_fixture
from tests.test_r2_ibkr_historical import _foundation as _r2_foundation


def create_ibkr_foundation_confirmatory_promotion(
    *args: Any,
    **kwargs: Any,
) -> VerifiedIbkrFoundationPromotion:
    bundle = cast(Path, args[0] if args else kwargs["foundation"])
    kwargs.setdefault(
        "provider_history_receipt",
        _provider_history_receipt(bundle.parent / "provider" / "manifest.json"),
    )
    return _create_promotion(*args, **kwargs)


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
    provider_receipt = _provider_history_receipt(provider_manifest)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, 0, 30, tzinfo=UTC),
    )
    source = authenticate_provider_history_v2(
        provider_manifest,
        receipt=provider_receipt,
    )
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
        provider_history_receipt=provider_receipt,
        configuration=configuration,
    )
    receipt = tmp_path / "verification.json"
    verify_ibkr_foundation(
        bundle,
        provider_history_receipt=provider_receipt,
        receipt_output=receipt,
    )
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
    monkeypatch.setattr("qtrad.runtime.r2_verification.runtime_identities", no_replay)
    authenticated = authenticate_ibkr_foundation_promotion(
        bundle, receipt=receipt, promotion=promotion
    )
    assert authenticated.promotion_sha256 == authority.promotion_sha256

    r2_foundation = cast(IBKRFoundationBuild, _r2_foundation())
    adapter = IBKRHistoricalAdapterIdentity.create(
        foundation_bundle_id=authority.foundation_bundle_id,
        application_identity=_runtime()["application_identity"],
        image_identity=_runtime()["image_identity"],
    )
    experiment = build_ibkr_historical_experiment(
        r2_foundation,
        foundation_bundle_id=authority.foundation_bundle_id,
        adapter_identity=adapter,
        evidence_class=EvidenceClass.CONFIRMATORY,
        promotion_authority=authenticated,
    )
    assert experiment.evidence_class.value == "CONFIRMATORY"

    manufactured = object.__new__(VerifiedIbkrFoundationPromotion)
    object.__setattr__(manufactured, "_foundation_bundle_id", authority.foundation_bundle_id)
    object.__setattr__(manufactured, "_promotion_sha256", "0" * 64)
    with pytest.raises(ValueError, match="exact Stage 8 promotion attestation"):
        build_ibkr_historical_experiment(
            r2_foundation,
            foundation_bundle_id=authority.foundation_bundle_id,
            adapter_identity=adapter,
            evidence_class=EvidenceClass.CONFIRMATORY,
            promotion_authority=manufactured,
        )


def test_v2_promotion_replays_stage7_and_rejects_v1_verifier_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, v2_manifest = _published_provider_history(tmp_path)
    v2_receipt = _provider_history_receipt(v2_manifest)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, 0, 30, tzinfo=UTC),
    )
    requested = tuple(
        sorted(
            {
                *configuration.ordered_instruments,
                *(str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS),
            }
        )
    )
    source = authenticate_provider_history_v2(
        v2_manifest,
        receipt=v2_receipt,
        instrument_ids=requested,
        interval_start=configuration.required_observation_start,
        interval_end=configuration.required_observation_end,
    )
    build = build_ibkr_foundation(source, configuration)
    readiness, _gaps = _evaluate_session_aware_readiness()
    build = replace(build, readiness=readiness)
    monkeypatch.setattr(
        foundation_runtime,
        "build_ibkr_foundation",
        lambda *_args, **_kwargs: build,
    )
    bundle = tmp_path / "foundation-v2.json"
    write_ibkr_foundation(
        bundle,
        provider_manifest=v2_manifest,
        provider_history_receipt=v2_receipt,
        configuration=configuration,
    )
    foundation_receipt = tmp_path / "foundation-v2-receipt.json"
    verify_ibkr_foundation(
        bundle,
        provider_history_receipt=v2_receipt,
        receipt_output=foundation_receipt,
    )

    monkeypatch.setattr("qtrad.runtime.r2_verification.runtime_identities", _runtime)
    monkeypatch.setattr(promotion_runtime, "_require_detached_source", lambda: None)
    deep_replay_reached = False
    receipt_authenticated = False
    stage6_replay_reached = False
    original_verify = promotion_runtime.verify_provider_history_v2
    original_authenticate = promotion_runtime.authenticate_provider_history_v2
    original_iter_request_results = IbkrHistoricalResultStream.iter_request_results

    def record_receipt_authentication(path: Path, *, receipt: Path) -> object:
        nonlocal receipt_authenticated
        receipt_authenticated = True
        return original_authenticate(path, receipt=receipt)

    def record_stage6_replay(
        self: IbkrHistoricalResultStream,
        *,
        request_order: Sequence[IbkrHistoricalRequest] | None = None,
    ) -> Iterator[IbkrHistoricalRequestResult]:
        nonlocal stage6_replay_reached
        assert receipt_authenticated
        stage6_replay_reached = True
        yield from original_iter_request_results(self, request_order=request_order)

    def record_deep_replay(path: Path):
        nonlocal deep_replay_reached
        deep_replay_reached = True
        return original_verify(path)

    monkeypatch.setattr(
        promotion_runtime, "authenticate_provider_history_v2", record_receipt_authentication
    )
    monkeypatch.setattr(IbkrHistoricalResultStream, "iter_request_results", record_stage6_replay)
    monkeypatch.setattr(promotion_runtime, "verify_provider_history_v2", record_deep_replay)
    promotion = tmp_path / "promotion-v2.json"
    authority = create_ibkr_foundation_confirmatory_promotion(
        bundle,
        receipt=foundation_receipt,
        provider_history_receipt=v2_receipt,
        output=promotion,
        authorized_by="stage8-operator",
        authorized_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        authorization_reference="S8.4 v2 migration proof",
        workers=1,
    )
    assert deep_replay_reached
    assert stage6_replay_reached
    document = json.loads(promotion.read_bytes())
    assert document["stage7"]["provider_verifier_sha256"] == (provider_history_v2_verifier_sha256())
    assert (
        authenticate_ibkr_foundation_promotion(
            bundle,
            receipt=foundation_receipt,
            promotion=promotion,
        ).promotion_sha256
        == authority.promotion_sha256
    )

    document["stage7"]["provider_verifier_sha256"] = (
        provider_history_runtime.provider_history_verifier_sha256()
    )
    identity = dict(document)
    identity.pop("promotion_sha256")
    document["promotion_sha256"] = promotion_runtime._sha(identity)
    promotion.write_bytes(promotion_runtime._canonical_bytes(document) + b"\n")
    with pytest.raises(ValueError, match="stage7 binding changed"):
        authenticate_ibkr_foundation_promotion(
            bundle,
            receipt=foundation_receipt,
            promotion=promotion,
        )


def test_promoted_confirmatory_ibkr_oof_passes_generic_bundle_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    research_root = tmp_path / "research"
    research_root.mkdir()
    bundle, receipt, _build = _foundation(research_root, monkeypatch, qualifying=True)
    promotion = research_root / "promotion.json"
    _promote(bundle, receipt, promotion, monkeypatch)

    oof_path = _build_confirmatory_fixture(
        tmp_path,
        compact=True,
        replay_foundation_path=bundle,
        foundation_receipt_path=receipt,
        foundation_promotion_path=promotion,
    )[0]

    verified = verify_r2_oof_bundle(oof_path)
    assert verified.evidence_class is EvidenceClass.CONFIRMATORY
    assert verified.source_class.value == "IBKR_HISTORICAL_RESEARCH"


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
    monkeypatch.setattr(promotion_runtime, "_verify_ibkr_foundation_migration_v2", replay_reached)
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


def test_new_promotion_rejects_legacy_provider_contract_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, receipt, _build = _foundation(tmp_path, monkeypatch, qualifying=True)
    output = tmp_path / "promotion.json"
    original_bindings = promotion_runtime._bindings

    def legacy_bindings(*args: Any, **kwargs: Any) -> tuple[Any, Path, str]:
        bindings, provider_path, _contract = original_bindings(*args, **kwargs)
        return (
            bindings,
            provider_path,
            "qtrad-provider-historical-observations-v1",
        )

    monkeypatch.setattr(promotion_runtime, "_bindings", legacy_bindings)

    with pytest.raises(
        ValueError,
        match="new confirmatory promotions require provider-history v2",
    ):
        _promote(bundle, receipt, output, monkeypatch)

    assert not output.exists()
