"""Identity and bounded materialisation regressions for causal datasets."""

import builtins
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

import pytest

from qtrad.domain import foundation as foundation_domain
from qtrad.domain import research as research_domain
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.foundation import (
    PanelDataset,
    PanelRow,
    PanelStatus,
)
from qtrad.domain.market_data import DataQuality, PriceBasis
from qtrad.domain.research import ObservationDataset, ObservationRow


def _legacy_digest(payload: object) -> str:
    canonical = to_json_value(payload)
    return sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _observation_row(
    position: int,
    *,
    interval_end: datetime | None = None,
    instrument_id: str = "fx:aud-usd",
) -> ObservationRow:
    end = interval_end or datetime(2026, 7, 1, 12, position, tzinfo=UTC)
    persisted = end + timedelta(seconds=1)
    return ObservationRow(
        event_id=uuid4(),
        stream_id=f"market-bar:{instrument_id}:MID",
        stream_version=position,
        event_type="MarketBarClosed",
        event_time=end,
        received_at=persisted,
        persisted_at=persisted,
        global_position=position,
        instrument_id=instrument_id,
        basis=PriceBasis.MID,
        interval_start=end - timedelta(minutes=1),
        interval_end=end,
        open=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.00"),
        close=Decimal("100.50"),
        sample_count=1,
        revision=1,
        provenance=research_domain.BarProvenance.QUOTE_DERIVED,
        quality=research_domain.DataQuality.HEALTHY,
        source_provider="ig",
        source_environment="demo",
        source_external_id="AUDUSD",
    )


def _observation_configuration() -> Mapping[str, JsonValue]:
    return cast(
        Mapping[str, JsonValue],
        {
            "unicode": "café / 東京",
            "decimal": Decimal("1.2300"),
            "timestamp": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            "nested": {"b": Decimal("2.00"), "a": "first"},
        },
    )


def _panel_row(
    position: int,
    *,
    instrument_id: str = "fx:aud-usd",
    decision_time: datetime | None = None,
) -> PanelRow:
    decision = decision_time or datetime(2026, 7, 1, 12, position, tzinfo=UTC)
    interval_end = decision - timedelta(minutes=1)
    return PanelRow(
        decision_time=decision,
        instrument_id=instrument_id,
        basis=PriceBasis.MID,
        feature_data_asof=decision,
        latest_feature_bar_end=interval_end,
        status=PanelStatus.OBSERVED,
        audit_disposition=None,
        selected_event_id=uuid4(),
        selected_stream_version=position,
        selected_global_position=position,
        selected_availability_time=decision - timedelta(seconds=1),
        selected_revision=1,
        interval_start=interval_end - timedelta(minutes=1),
        interval_end=interval_end,
        open=Decimal("100.00"),
        high=Decimal("101.00"),
        low=Decimal("99.00"),
        close=Decimal("100.50"),
        sample_count=1,
        quality=DataQuality.HEALTHY,
    )


def test_observation_identity_matches_legacy_payload_with_unicode_decimal_and_timestamp() -> None:
    rows = (_observation_row(1), _observation_row(2))
    configuration = _observation_configuration()
    source_dataset_ids = ("source-µ",)
    selection_policies = cast(Mapping[str, JsonValue], {"policy": "café"})
    dataset = ObservationDataset.create(
        rows,
        configuration=configuration,
        source_dataset_ids=source_dataset_ids,
        selection_policies=selection_policies,
    )

    expected = _legacy_digest(
        {
            "contract": research_domain.OBSERVATION_DATASET_CONTRACT,
            "schema_version": research_domain.OBSERVATION_SCHEMA_VERSION,
            "configuration": configuration,
            "source_dataset_ids": list(source_dataset_ids),
            "selection_policies": selection_policies,
            "rows": [row.as_json() for row in sorted(rows, key=ObservationRow.semantic_key)],
        }
    )
    assert dataset.dataset_id == expected
    assert (
        research_domain.observation_dataset_id(
            dataset.rows,
            configuration=configuration,
            source_dataset_ids=source_dataset_ids,
            selection_policies=selection_policies,
        )
        == expected
    )


def test_panel_identity_matches_legacy_payload_and_roundtrips() -> None:
    rows = (_panel_row(1, instrument_id="fx:éur-usd"), _panel_row(2, instrument_id="fx:aud-usd"))
    dataset = PanelDataset.create(
        rows,
        observation_dataset_id="observation-µ",
        foundation_configuration_id="foundation-東京",
    )
    expected = _legacy_digest(
        {
            "contract": foundation_domain.PANEL_DATASET_CONTRACT,
            "schema_version": 1,
            "observation_dataset_id": "observation-µ",
            "foundation_configuration_id": "foundation-東京",
            "rows": [row.as_json() for row in sorted(rows, key=foundation_domain._panel_key)],
        }
    )
    assert dataset.dataset_id == expected
    rebuilt = PanelDataset.create(
        dataset.rows,
        observation_dataset_id=dataset.observation_dataset_id,
        foundation_configuration_id=dataset.foundation_configuration_id,
    )
    assert rebuilt == dataset


def test_direct_constructors_reject_forged_ids_order_and_duplicates() -> None:
    observation_rows = (_observation_row(1), _observation_row(2))
    observation = ObservationDataset.create(observation_rows, configuration={})
    with pytest.raises(ValueError, match="observation dataset ID"):
        ObservationDataset(
            rows=observation.rows,
            configuration=observation.configuration,
            source_dataset_ids=observation.source_dataset_ids,
            selection_policies=observation.selection_policies,
            dataset_id="0" * 64,
            _verified=True,
        )
    with pytest.raises(ValueError, match="canonical semantic ordering"):
        ObservationDataset(
            rows=tuple(reversed(observation.rows)),
            configuration=observation.configuration,
            source_dataset_ids=observation.source_dataset_ids,
            selection_policies=observation.selection_policies,
            dataset_id=observation.dataset_id,
            _verified=True,
        )
    with pytest.raises(ValueError, match="unique semantic keys"):
        ObservationDataset(
            rows=(observation.rows[0], observation.rows[0]),
            configuration=observation.configuration,
            source_dataset_ids=observation.source_dataset_ids,
            selection_policies=observation.selection_policies,
            dataset_id=observation.dataset_id,
            _verified=True,
        )

    panel_rows = (_panel_row(1), _panel_row(2))
    panel = PanelDataset.create(
        panel_rows,
        observation_dataset_id="observation",
        foundation_configuration_id="foundation",
    )
    with pytest.raises(ValueError, match="panel dataset ID"):
        PanelDataset(
            rows=panel.rows,
            observation_dataset_id=panel.observation_dataset_id,
            foundation_configuration_id=panel.foundation_configuration_id,
            dataset_id="0" * 64,
            _verified=True,
        )
    with pytest.raises(ValueError, match="deterministic ordering"):
        PanelDataset(
            rows=tuple(reversed(panel.rows)),
            observation_dataset_id=panel.observation_dataset_id,
            foundation_configuration_id=panel.foundation_configuration_id,
            dataset_id=panel.dataset_id,
            _verified=True,
        )
    with pytest.raises(ValueError, match="unique semantic keys"):
        PanelDataset(
            rows=(panel.rows[0], panel.rows[0]),
            observation_dataset_id=panel.observation_dataset_id,
            foundation_configuration_id=panel.foundation_configuration_id,
            dataset_id=panel.dataset_id,
            _verified=True,
        )


def test_create_uses_one_identity_hash_and_no_full_row_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation_rows = (_observation_row(1), _observation_row(2))
    observation_identity_calls = 0
    observation_row_calls = 0
    observation_identity = research_domain._observation_dataset_identity
    observation_json = research_domain._canonical_json_bytes

    def count_observation_identity(
        rows: tuple[ObservationRow, ...],
        *,
        configuration: Mapping[str, JsonValue],
        source_dataset_ids: tuple[str, ...],
        selection_policies: Mapping[str, JsonValue],
    ) -> str:
        nonlocal observation_identity_calls
        observation_identity_calls += 1
        return observation_identity(
            rows,
            configuration=configuration,
            source_dataset_ids=source_dataset_ids,
            selection_policies=selection_policies,
        )

    def count_observation_json(value: object) -> bytes:
        if isinstance(value, dict) and "rows" in value:
            raise AssertionError("observation identity materialised a full rows payload")
        return observation_json(value)

    original_observation_as_json = ObservationRow.as_json

    def count_observation_row(self: ObservationRow) -> dict[str, JsonValue]:
        nonlocal observation_row_calls
        observation_row_calls += 1
        return original_observation_as_json(self)

    monkeypatch.setattr(
        research_domain, "_observation_dataset_identity", count_observation_identity
    )
    monkeypatch.setattr(research_domain, "_canonical_json_bytes", count_observation_json)
    monkeypatch.setattr(ObservationRow, "as_json", count_observation_row)
    ObservationDataset.create(observation_rows, configuration={})
    assert observation_identity_calls == 1
    assert observation_row_calls == len(observation_rows)

    panel_rows = (_panel_row(1), _panel_row(2))
    panel_identity_calls = 0
    panel_row_calls = 0
    panel_identity = foundation_domain._panel_dataset_identity
    panel_json = foundation_domain._json_bytes

    def count_panel_identity(
        rows: tuple[PanelRow, ...],
        *,
        observation_dataset_id: str,
        foundation_configuration_id: str,
    ) -> str:
        nonlocal panel_identity_calls
        panel_identity_calls += 1
        return panel_identity(
            rows,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=foundation_configuration_id,
        )

    def count_panel_json(value: object) -> bytes:
        if isinstance(value, dict) and "rows" in value:
            raise AssertionError("panel identity materialised a full rows payload")
        return panel_json(value)

    original_panel_as_json = PanelRow.as_json

    def count_panel_row(self: PanelRow) -> dict[str, JsonValue]:
        nonlocal panel_row_calls
        panel_row_calls += 1
        return original_panel_as_json(self)

    monkeypatch.setattr(foundation_domain, "_panel_dataset_identity", count_panel_identity)
    monkeypatch.setattr(foundation_domain, "_json_bytes", count_panel_json)
    monkeypatch.setattr(PanelRow, "as_json", count_panel_row)
    PanelDataset.create(
        panel_rows,
        observation_dataset_id="observation",
        foundation_configuration_id="foundation",
    )
    assert panel_identity_calls == 1
    assert panel_row_calls == len(panel_rows)


def test_create_only_sorts_once_after_linear_constructor_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original_sorted = cast(Callable[..., list[object]], builtins.sorted)

    def count_sorted(
        iterable: Iterable[object],
        *,
        key: Callable[[object], Any] | None = None,
        reverse: bool = False,
    ) -> list[object]:
        nonlocal calls
        calls += 1
        if key is None:
            return original_sorted(iterable, reverse=reverse)
        return original_sorted(iterable, key=key, reverse=reverse)

    monkeypatch.setattr(builtins, "sorted", count_sorted)
    ObservationDataset.create((_observation_row(2), _observation_row(1)), configuration={})
    assert calls == 1
