"""Identity-bearing contracts for confirmatory R2 promotion authorities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, ClassVar, cast

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_readiness import EvidenceClass

CONFIRMATORY_F2_PROMOTION_CONTRACT = "qtrad-r2-confirmatory-f2-promotion-v1"
CONFIRMATORY_F2_PROMOTION_SCHEMA_VERSION = 1


def _identity(value: object) -> str:
    encoded = json.dumps(to_json_value(value), sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 identity")


def _utc(value: datetime, field: str) -> None:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise ValueError(f"{field} must be an aware UTC timestamp")


@dataclass(frozen=True, slots=True)
class ConfirmatoryF2Promotion:
    """Create-only authority over one receipt-authenticated confirmatory OOF run.

    Semantic fields bind the OOF receipt, parent authority, verifier/check set and the
    complete persisted F2 register. Runtime locators are physical paths only and are
    deliberately excluded from the promotion identity.
    """

    oof_id: str
    oof_closure_id: str
    oof_manifest_sha256: str
    oof_verification_id: str
    experiment_semantic_id: str
    foundation_semantic_id: str
    foundation_verification_id: str
    foundation_promotion_id: str | None
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    oof_verifier_contract: str
    oof_verifier_version: str
    oof_numerical_identity: str
    required_oof_checks: tuple[str, ...]
    evaluation_register_semantic_id: str
    evaluation_register_sha256: str
    evaluation_report_id: str
    confirmatory_data_ready: str
    inner_validation_rows_ready: str
    confirmatory_oof_ready: str
    authorized_by: str
    authorized_at: datetime
    runtime_locators: dict[str, str]
    promotion_id: str

    CONTRACT: ClassVar[str] = CONFIRMATORY_F2_PROMOTION_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = CONFIRMATORY_F2_PROMOTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value, field in (
            (self.oof_id, "OOF ID"),
            (self.oof_closure_id, "OOF closure ID"),
            (self.oof_manifest_sha256, "OOF manifest digest"),
            (self.oof_verification_id, "OOF verification ID"),
            (self.experiment_semantic_id, "experiment semantic ID"),
            (self.foundation_semantic_id, "foundation semantic ID"),
            (self.foundation_verification_id, "foundation verification ID"),
            (self.oof_numerical_identity, "OOF numerical identity"),
            (self.evaluation_register_semantic_id, "evaluation register identity"),
            (self.evaluation_register_sha256, "evaluation register digest"),
            (self.evaluation_report_id, "evaluation report ID"),
            (self.promotion_id, "promotion ID"),
        ):
            _sha256(value, field)
        if self.foundation_promotion_id is not None:
            _sha256(self.foundation_promotion_id, "foundation promotion ID")
        if not self.oof_verifier_contract or not self.oof_verifier_version:
            raise ValueError("F2 promotion requires an accepted OOF verifier")
        if not self.required_oof_checks or tuple(self.required_oof_checks) != tuple(
            dict.fromkeys(self.required_oof_checks)
        ):
            raise ValueError("F2 promotion checks must be non-empty and unique")
        if any(not item for item in self.required_oof_checks):
            raise ValueError("F2 promotion checks must be non-empty strings")
        for value, field in (
            (self.confirmatory_data_ready, "confirmatory readiness"),
            (self.inner_validation_rows_ready, "inner-validation readiness"),
            (self.confirmatory_oof_ready, "confirmatory OOF readiness"),
        ):
            if value != "READY":
                raise ValueError(f"{field} must be READY for promotion")
        if not self.authorized_by.strip():
            raise ValueError("F2 promotion authorization requires an operator")
        _utc(self.authorized_at, "F2 promotion authorization time")
        if set(self.runtime_locators) != {"oof_bundle", "oof_receipt"} or any(
            not value for value in self.runtime_locators.values()
        ):
            raise ValueError("F2 promotion runtime locators must name only its OOF and receipt")
        if self.promotion_id != _identity(self.semantic_json()):
            raise ValueError("F2 promotion ID does not authenticate its content")

    @classmethod
    def create(cls, **values: Any) -> ConfirmatoryF2Promotion:
        expected = {
            "oof_id",
            "oof_closure_id",
            "oof_manifest_sha256",
            "oof_verification_id",
            "experiment_semantic_id",
            "foundation_semantic_id",
            "foundation_verification_id",
            "foundation_promotion_id",
            "source_class",
            "evidence_class",
            "oof_verifier_contract",
            "oof_verifier_version",
            "oof_numerical_identity",
            "required_oof_checks",
            "evaluation_register_semantic_id",
            "evaluation_register_sha256",
            "evaluation_report_id",
            "confirmatory_data_ready",
            "inner_validation_rows_ready",
            "confirmatory_oof_ready",
            "authorized_by",
            "authorized_at",
            "runtime_locators",
        }
        if set(values) != expected:
            raise ValueError("F2 promotion create arguments are incomplete or unexpected")
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            **{
                key: (
                    value.value
                    if isinstance(value, (MarketDataSourceClass, EvidenceClass))
                    else list(value)
                    if key == "required_oof_checks"
                    else value.isoformat()
                    if isinstance(value, datetime)
                    else value
                )
                for key, value in values.items()
                if key != "runtime_locators"
            },
        }
        return cls(**values, promotion_id=_identity(semantic))

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "oof_id": self.oof_id,
            "oof_closure_id": self.oof_closure_id,
            "oof_manifest_sha256": self.oof_manifest_sha256,
            "oof_verification_id": self.oof_verification_id,
            "experiment_semantic_id": self.experiment_semantic_id,
            "foundation_semantic_id": self.foundation_semantic_id,
            "foundation_verification_id": self.foundation_verification_id,
            "foundation_promotion_id": self.foundation_promotion_id,
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "oof_verifier_contract": self.oof_verifier_contract,
            "oof_verifier_version": self.oof_verifier_version,
            "oof_numerical_identity": self.oof_numerical_identity,
            "required_oof_checks": list(self.required_oof_checks),
            "evaluation_register_semantic_id": self.evaluation_register_semantic_id,
            "evaluation_register_sha256": self.evaluation_register_sha256,
            "evaluation_report_id": self.evaluation_report_id,
            "confirmatory_data_ready": self.confirmatory_data_ready,
            "inner_validation_rows_ready": self.inner_validation_rows_ready,
            "confirmatory_oof_ready": self.confirmatory_oof_ready,
            "authorized_by": self.authorized_by,
            "authorized_at": self.authorized_at.isoformat(),
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {
            **self.semantic_json(),
            "runtime_locators": dict(self.runtime_locators),
            "promotion_id": self.promotion_id,
        }

    @classmethod
    def from_json(cls, value: object) -> ConfirmatoryF2Promotion:
        if not isinstance(value, dict):
            raise ValueError("F2 promotion must be a JSON object")
        raw = cast(dict[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "oof_id",
            "oof_closure_id",
            "oof_manifest_sha256",
            "oof_verification_id",
            "experiment_semantic_id",
            "foundation_semantic_id",
            "foundation_verification_id",
            "foundation_promotion_id",
            "source_class",
            "evidence_class",
            "oof_verifier_contract",
            "oof_verifier_version",
            "oof_numerical_identity",
            "required_oof_checks",
            "evaluation_register_semantic_id",
            "evaluation_register_sha256",
            "evaluation_report_id",
            "confirmatory_data_ready",
            "inner_validation_rows_ready",
            "confirmatory_oof_ready",
            "authorized_by",
            "authorized_at",
            "runtime_locators",
            "promotion_id",
        }
        if (
            set(raw) != expected
            or raw["contract"] != cls.CONTRACT
            or raw["schema_version"] != cls.SCHEMA_VERSION
        ):
            raise ValueError("F2 promotion contract is unsupported or has unknown fields")
        checks_value = raw["required_oof_checks"]
        locators_value = raw["runtime_locators"]
        if not isinstance(checks_value, list):
            raise ValueError("F2 promotion check set is malformed")
        checks_items = cast(list[object], checks_value)
        if not all(isinstance(item, str) for item in checks_items):
            raise ValueError("F2 promotion check set is malformed")
        if not isinstance(locators_value, dict):
            raise ValueError("F2 promotion runtime locators are malformed")
        locators_items = cast(dict[object, object], locators_value)
        if not all(
            isinstance(key, str) and isinstance(item, str) for key, item in locators_items.items()
        ):
            raise ValueError("F2 promotion runtime locators are malformed")
        checks = tuple(cast(list[str], checks_items))
        locators = cast(dict[str, str], locators_items)
        try:
            authorized_at = datetime.fromisoformat(str(raw["authorized_at"]))
        except ValueError as error:
            raise ValueError("F2 promotion authorization time is malformed") from error
        promotion = cls(
            oof_id=str(raw["oof_id"]),
            oof_closure_id=str(raw["oof_closure_id"]),
            oof_manifest_sha256=str(raw["oof_manifest_sha256"]),
            oof_verification_id=str(raw["oof_verification_id"]),
            experiment_semantic_id=str(raw["experiment_semantic_id"]),
            foundation_semantic_id=str(raw["foundation_semantic_id"]),
            foundation_verification_id=str(raw["foundation_verification_id"]),
            foundation_promotion_id=(
                None
                if raw["foundation_promotion_id"] is None
                else str(raw["foundation_promotion_id"])
            ),
            source_class=MarketDataSourceClass(str(raw["source_class"])),
            evidence_class=EvidenceClass(str(raw["evidence_class"])),
            oof_verifier_contract=str(raw["oof_verifier_contract"]),
            oof_verifier_version=str(raw["oof_verifier_version"]),
            oof_numerical_identity=str(raw["oof_numerical_identity"]),
            required_oof_checks=checks,
            evaluation_register_semantic_id=str(raw["evaluation_register_semantic_id"]),
            evaluation_register_sha256=str(raw["evaluation_register_sha256"]),
            evaluation_report_id=str(raw["evaluation_report_id"]),
            confirmatory_data_ready=str(raw["confirmatory_data_ready"]),
            inner_validation_rows_ready=str(raw["inner_validation_rows_ready"]),
            confirmatory_oof_ready=str(raw["confirmatory_oof_ready"]),
            authorized_by=str(raw["authorized_by"]),
            authorized_at=authorized_at,
            runtime_locators=locators,
            promotion_id=str(raw["promotion_id"]),
        )
        if promotion.as_json() != raw:
            raise ValueError("F2 promotion is not canonical")
        return promotion
