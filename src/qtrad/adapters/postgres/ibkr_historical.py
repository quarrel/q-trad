"""PostgreSQL state store for IBKR historical execution."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from qtrad.domain.ibkr_execution import (
    MAX_IBKR_PLAN_BYTES,
    IbkrAttemptStatus,
    IbkrHistoricalAttempt,
    IbkrHistoricalAttemptOutcome,
    IbkrHistoricalCallback,
    IbkrHistoricalCallbackKind,
    IbkrHistoricalCallbackRecord,
    IbkrHistoricalIncomplete,
    IbkrPlanRegistrationStatus,
    IbkrPublicationStatus,
    IbkrRequestStatus,
    IbkrTerminalDisposition,
    callback_payload,
    ibkr_historical_plan_bytes,
    ibkr_historical_plan_bytes_sha256,
)
from qtrad.domain.ibkr_historical import IbkrHistoricalPlan, IbkrHistoricalRequestKind
from qtrad.domain.time import require_utc
from qtrad.ports.ibkr_historical import IbkrHistoricalExecutionStore


class PostgresIbkrHistoricalExecutionStore(IbkrHistoricalExecutionStore):
    """Durable Stage 3 state machine backed by PostgreSQL transactions."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def register_ibkr_historical_plan(
        self,
        plan: IbkrHistoricalPlan,
        *,
        plan_bytes: bytes | None,
        registered_at: datetime,
    ) -> IbkrPlanRegistrationStatus:
        require_utc(registered_at, "IBKR historical plan registered_at")
        encoded = plan_bytes if plan_bytes is not None else ibkr_historical_plan_bytes(plan)
        if not encoded or len(encoded) > MAX_IBKR_PLAN_BYTES:
            raise ValueError("IBKR historical plan bytes are empty or exceed their bound")
        try:
            parsed = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("IBKR historical plan bytes are not valid JSON") from error
        if parsed != plan.as_json_value():
            raise ValueError("IBKR historical plan bytes do not match the authenticated plan")
        bytes_hash = ibkr_historical_plan_bytes_sha256(encoded)
        plan_payload = plan.as_json_value()
        expected_requests = {request.request_sha256 for request in plan.requests}

        async with self._engine.begin() as connection:
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO ops.ibkr_historical_plans (
                        plan_sha256, plan_bytes, plan_bytes_sha256, plan_payload,
                        registered_at, publication_status
                    ) VALUES (
                        :plan_sha256, :plan_bytes, :plan_bytes_sha256,
                        CAST(:plan_payload AS jsonb), :registered_at, :publication_status
                    )
                    ON CONFLICT (plan_sha256) DO NOTHING
                    RETURNING plan_sha256
                    """
                ),
                {
                    "plan_sha256": plan.plan_sha256,
                    "plan_bytes": encoded,
                    "plan_bytes_sha256": bytes_hash,
                    "plan_payload": _json_text(plan_payload),
                    "registered_at": registered_at,
                    "publication_status": IbkrPublicationStatus.PENDING.value,
                },
            )
            if inserted.scalar_one_or_none() is None:
                existing = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT plan_bytes, plan_bytes_sha256, plan_payload
                            FROM ops.ibkr_historical_plans
                            WHERE plan_sha256 = :plan_sha256
                            """
                            ),
                            {"plan_sha256": plan.plan_sha256},
                        )
                    )
                    .mappings()
                    .one()
                )
                existing_bytes = _bytes_value(existing["plan_bytes"], "stored IBKR plan bytes")
                if (
                    existing_bytes != encoded
                    or existing["plan_bytes_sha256"] != bytes_hash
                    or existing["plan_payload"] != plan_payload
                ):
                    raise RuntimeError(
                        "registered IBKR plan identity conflicts with its immutable bytes"
                    )
                existing_requests = {
                    str(row["request_sha256"])
                    for row in (
                        await connection.execute(
                            text(
                                """
                                SELECT request_sha256
                                FROM ops.ibkr_historical_requests
                                WHERE plan_sha256 = :plan_sha256
                                """
                            ),
                            {"plan_sha256": plan.plan_sha256},
                        )
                    ).mappings()
                }
                if existing_requests != expected_requests:
                    raise RuntimeError(
                        "registered IBKR plan request closure differs from its immutable plan"
                    )
                return IbkrPlanRegistrationStatus.ALREADY_REGISTERED

            for request in plan.requests:
                await connection.execute(
                    text(
                        """
                        INSERT INTO ops.ibkr_historical_requests (
                            plan_sha256, request_sha256, request_payload, instrument_id,
                            request_kind, interval_start, interval_end, status,
                            attempt_count, publication_status
                        ) VALUES (
                            :plan_sha256, :request_sha256, CAST(:request_payload AS jsonb),
                            :instrument_id, :request_kind, :interval_start, :interval_end,
                            :status, 0, :publication_status
                        )
                        """
                    ),
                    {
                        "plan_sha256": plan.plan_sha256,
                        "request_sha256": request.request_sha256,
                        "request_payload": _json_text(request.as_json_value()),
                        "instrument_id": str(request.instrument_id),
                        "request_kind": request.kind.value,
                        "interval_start": request.interval_start,
                        "interval_end": request.interval_end,
                        "status": IbkrRequestStatus.PENDING.value,
                        "publication_status": IbkrPublicationStatus.PENDING.value,
                    },
                )
        return IbkrPlanRegistrationStatus.REGISTERED

    async def recover_ibkr_historical_execution(
        self,
        *,
        plan_sha256: str,
        recovered_at: datetime,
        maximum_attempts: int,
    ) -> Sequence[IbkrHistoricalAttemptOutcome]:
        _require_sha256(plan_sha256, "IBKR recovery plan hash")
        _require_maximum_attempts(maximum_attempts)
        require_utc(recovered_at, "IBKR recovery time")
        outcomes: list[IbkrHistoricalAttemptOutcome] = []
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT attempt_id
                        FROM ops.ibkr_historical_attempts
                        WHERE plan_sha256 = :plan_sha256
                          AND status = :status
                        ORDER BY attempt_ordinal, attempt_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "plan_sha256": plan_sha256,
                        "status": IbkrAttemptStatus.STARTED.value,
                    },
                )
            ).mappings()
            attempt_ids = [cast(UUID, row["attempt_id"]) for row in rows]
            for attempt_id in attempt_ids:
                try:
                    outcomes.append(
                        await self._finalize_attempt_locked(
                            connection, attempt_id, completed_at=recovered_at
                        )
                    )
                except IbkrHistoricalIncomplete:
                    outcomes.append(
                        await self._invalidate_attempt_locked(
                            connection,
                            attempt_id,
                            invalidated_at=recovered_at,
                            maximum_attempts=maximum_attempts,
                        )
                    )
        return tuple(outcomes)

    async def pending_ibkr_historical_requests(self, plan_sha256: str) -> tuple[str, ...]:
        _require_sha256(plan_sha256, "IBKR pending plan hash")
        async with self._engine.connect() as connection:
            plan_exists = (
                await connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM ops.ibkr_historical_plans
                            WHERE plan_sha256 = :plan_sha256
                        )
                        """
                    ),
                    {"plan_sha256": plan_sha256},
                )
            ).scalar_one()
            if not bool(plan_exists):
                raise RuntimeError("IBKR historical plan is not registered")
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT request_sha256
                        FROM ops.ibkr_historical_requests
                        WHERE plan_sha256 = :plan_sha256
                          AND status = :status
                        ORDER BY request_sha256
                        """
                    ),
                    {
                        "plan_sha256": plan_sha256,
                        "status": IbkrRequestStatus.PENDING.value,
                    },
                )
            ).scalars()
            return tuple(str(value) for value in rows)

    async def start_ibkr_historical_attempt(
        self,
        *,
        plan_sha256: str,
        request_sha256: str,
        connection_generation: int,
        provider_request_id: int,
        started_at: datetime,
        maximum_attempts: int,
    ) -> IbkrHistoricalAttempt | None:
        _require_sha256(plan_sha256, "IBKR attempt plan hash")
        _require_sha256(request_sha256, "IBKR attempt request hash")
        _require_positive(connection_generation, "IBKR attempt connection generation")
        _require_positive(provider_request_id, "IBKR provider request ID")
        _require_maximum_attempts(maximum_attempts)
        require_utc(started_at, "IBKR attempt start time")

        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT request_sha256, status, attempt_count
                        FROM ops.ibkr_historical_requests
                        WHERE plan_sha256 = :plan_sha256
                          AND request_sha256 = :request_sha256
                        FOR UPDATE
                        """
                        ),
                        {
                            "plan_sha256": plan_sha256,
                            "request_sha256": request_sha256,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise RuntimeError("IBKR planned request is not registered")
            if str(row["status"]) != IbkrRequestStatus.PENDING.value:
                return None
            attempt_ordinal = int(row["attempt_count"]) + 1
            if attempt_ordinal > maximum_attempts:
                raise RuntimeError("IBKR planned request exceeded its retry budget")
            attempt_id = uuid4()
            await connection.execute(
                text(
                    """
                    INSERT INTO ops.ibkr_historical_attempts (
                        attempt_id, plan_sha256, request_sha256, attempt_ordinal,
                        provider_request_id, connection_generation, started_at, status
                    ) VALUES (
                        :attempt_id, :plan_sha256, :request_sha256, :attempt_ordinal,
                        :provider_request_id, :connection_generation, :started_at, :status
                    )
                    """
                ),
                {
                    "attempt_id": attempt_id,
                    "plan_sha256": plan_sha256,
                    "request_sha256": request_sha256,
                    "attempt_ordinal": attempt_ordinal,
                    "provider_request_id": provider_request_id,
                    "connection_generation": connection_generation,
                    "started_at": started_at,
                    "status": IbkrAttemptStatus.STARTED.value,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE ops.ibkr_historical_requests
                    SET status = :status, attempt_count = :attempt_count
                    WHERE plan_sha256 = :plan_sha256
                      AND request_sha256 = :request_sha256
                    """
                ),
                {
                    "status": IbkrRequestStatus.IN_FLIGHT.value,
                    "attempt_count": attempt_ordinal,
                    "plan_sha256": plan_sha256,
                    "request_sha256": request_sha256,
                },
            )
        return IbkrHistoricalAttempt(
            attempt_id=attempt_id,
            plan_sha256=plan_sha256,
            request_sha256=request_sha256,
            attempt_ordinal=attempt_ordinal,
            provider_request_id=provider_request_id,
            connection_generation=connection_generation,
            started_at=started_at,
            status=IbkrAttemptStatus.STARTED,
        )

    async def append_ibkr_historical_callback(
        self,
        *,
        attempt_id: UUID,
        callback: IbkrHistoricalCallback,
    ) -> IbkrHistoricalCallbackRecord:
        payload = callback_payload(callback.payload)
        async with self._engine.begin() as connection:
            attempt = await self._attempt_for_update(connection, attempt_id)
            if attempt is None:
                raise RuntimeError("IBKR callback targets an unknown attempt")
            sequence = int(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT COALESCE(MAX(sequence), 0) + 1
                            FROM ops.ibkr_historical_callbacks
                            WHERE attempt_id = :attempt_id
                            """
                        ),
                        {"attempt_id": attempt_id},
                    )
                ).scalar_one()
            )
            closure_eligible = (
                str(attempt["status"]) == IbkrAttemptStatus.STARTED.value
                and int(attempt["connection_generation"]) == callback.connection_generation
            )
            callback_id = int(
                (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO ops.ibkr_historical_callbacks (
                                attempt_id, connection_generation, sequence, callback_kind,
                                received_at, payload, closure_eligible
                            ) VALUES (
                                :attempt_id, :connection_generation, :sequence, :callback_kind,
                                :received_at, CAST(:payload AS jsonb), :closure_eligible
                            )
                            RETURNING callback_id
                            """
                        ),
                        {
                            "attempt_id": attempt_id,
                            "connection_generation": callback.connection_generation,
                            "sequence": sequence,
                            "callback_kind": callback.kind.value,
                            "received_at": callback.received_at,
                            "payload": _json_text(payload),
                            "closure_eligible": closure_eligible,
                        },
                    )
                ).scalar_one()
            )
            if callback.kind is IbkrHistoricalCallbackKind.COMPLETION:
                accepted_row_count = int(
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT COUNT(*)
                                FROM ops.ibkr_historical_callbacks
                                WHERE attempt_id = :attempt_id
                                  AND sequence < :sequence
                                  AND callback_kind = :callback_kind
                                  AND closure_eligible
                                """
                            ),
                            {
                                "attempt_id": attempt_id,
                                "sequence": sequence,
                                "callback_kind": IbkrHistoricalCallbackKind.MIDPOINT_BAR.value,
                            },
                        )
                    ).scalar_one()
                )
                schedule_interval_count = int(
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT COUNT(*)
                                FROM ops.ibkr_historical_callbacks
                                WHERE attempt_id = :attempt_id
                                  AND sequence < :sequence
                                  AND callback_kind = :callback_kind
                                  AND closure_eligible
                                """
                            ),
                            {
                                "attempt_id": attempt_id,
                                "sequence": sequence,
                                "callback_kind": IbkrHistoricalCallbackKind.SCHEDULE.value,
                            },
                        )
                    ).scalar_one()
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO ops.ibkr_historical_completion_markers (
                            attempt_id, connection_generation, sequence, completed_at,
                            accepted_row_count, schedule_interval_count,
                            closure_eligible, payload
                        ) VALUES (
                            :attempt_id, :connection_generation, :sequence, :completed_at,
                            :accepted_row_count, :schedule_interval_count,
                            :closure_eligible, CAST(:payload AS jsonb)
                        )
                        """
                    ),
                    {
                        "attempt_id": attempt_id,
                        "connection_generation": callback.connection_generation,
                        "sequence": sequence,
                        "completed_at": callback.received_at,
                        "accepted_row_count": accepted_row_count,
                        "schedule_interval_count": schedule_interval_count,
                        "closure_eligible": closure_eligible,
                        "payload": _json_text(payload),
                    },
                )
        return IbkrHistoricalCallbackRecord(
            callback_id=callback_id,
            attempt_id=attempt_id,
            connection_generation=callback.connection_generation,
            sequence=sequence,
            kind=callback.kind,
            received_at=callback.received_at,
            payload=payload,
            closure_eligible=closure_eligible,
        )

    async def finalize_ibkr_historical_attempt(
        self,
        *,
        attempt_id: UUID,
        completed_at: datetime,
    ) -> IbkrHistoricalAttemptOutcome:
        require_utc(completed_at, "IBKR attempt completion time")
        async with self._engine.begin() as connection:
            return await self._finalize_attempt_locked(
                connection, attempt_id, completed_at=completed_at
            )

    async def fail_ibkr_historical_attempt(
        self,
        *,
        attempt_id: UUID,
        failed_at: datetime,
        disposition: str,
        detail: str,
        retryable: bool,
        maximum_attempts: int,
    ) -> IbkrHistoricalAttemptOutcome:
        require_utc(failed_at, "IBKR attempt failure time")
        _require_maximum_attempts(maximum_attempts)
        if not detail or len(detail) > 2_000:
            raise ValueError("IBKR attempt failure detail must be bounded")
        try:
            parsed_disposition = IbkrTerminalDisposition(disposition)
        except ValueError as error:
            raise ValueError("IBKR attempt failure disposition is unsupported") from error
        if parsed_disposition is IbkrTerminalDisposition.SUCCEEDED:
            raise ValueError("IBKR attempt failure cannot use SUCCEEDED disposition")

        async with self._engine.begin() as connection:
            row = await self._attempt_for_update(connection, attempt_id)
            if row is None:
                raise RuntimeError("IBKR failure targets an unknown attempt")
            if str(row["status"]) != IbkrAttemptStatus.STARTED.value:
                return _attempt_outcome_from_row(row)

            retry = retryable and int(row["attempt_ordinal"]) < maximum_attempts
            attempt_status = (
                IbkrAttemptStatus.RETRYABLE_FAILURE if retry else IbkrAttemptStatus.TERMINAL_FAILURE
            )
            final_disposition = (
                parsed_disposition
                if retry
                else (
                    IbkrTerminalDisposition.RETRY_LIMIT_EXHAUSTED
                    if retryable
                    else parsed_disposition
                )
            )
            request_status = IbkrRequestStatus.PENDING if retry else IbkrRequestStatus.TERMINAL
            await connection.execute(
                text(
                    """
                    UPDATE ops.ibkr_historical_attempts
                    SET status = :status, terminal_at = :terminal_at,
                        terminal_disposition = :terminal_disposition, detail = :detail
                    WHERE attempt_id = :attempt_id
                    """
                ),
                {
                    "status": attempt_status.value,
                    "terminal_at": _not_before(failed_at, cast(datetime, row["started_at"])),
                    "terminal_disposition": final_disposition.value,
                    "detail": detail,
                    "attempt_id": attempt_id,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE ops.ibkr_historical_requests
                    SET status = :status,
                        selected_attempt_id = CASE
                            WHEN :status = :terminal_status THEN :attempt_id
                            ELSE selected_attempt_id
                        END
                    WHERE plan_sha256 = :plan_sha256
                      AND request_sha256 = :request_sha256
                    """
                ),
                {
                    "status": request_status.value,
                    "terminal_status": IbkrRequestStatus.TERMINAL.value,
                    "attempt_id": attempt_id,
                    "plan_sha256": row["plan_sha256"],
                    "request_sha256": row["request_sha256"],
                },
            )
            updated = await self._attempt_for_update(connection, attempt_id)
            if updated is None:
                raise RuntimeError("IBKR attempt disappeared after failure transition")
            return _attempt_outcome_from_row(updated)

    async def invalidate_ibkr_historical_attempts(
        self,
        *,
        plan_sha256: str,
        connection_generation: int,
        invalidated_at: datetime,
        maximum_attempts: int,
    ) -> Sequence[IbkrHistoricalAttemptOutcome]:
        _require_sha256(plan_sha256, "IBKR invalidation plan hash")
        _require_positive(connection_generation, "IBKR invalidation connection generation")
        _require_maximum_attempts(maximum_attempts)
        require_utc(invalidated_at, "IBKR invalidation time")
        outcomes: list[IbkrHistoricalAttemptOutcome] = []
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT attempt_id
                        FROM ops.ibkr_historical_attempts
                        WHERE plan_sha256 = :plan_sha256
                          AND connection_generation = :connection_generation
                          AND status = :status
                        ORDER BY attempt_ordinal, attempt_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "plan_sha256": plan_sha256,
                        "connection_generation": connection_generation,
                        "status": IbkrAttemptStatus.STARTED.value,
                    },
                )
            ).mappings()
            for row in rows:
                attempt_id = cast(UUID, row["attempt_id"])
                try:
                    outcome = await self._finalize_attempt_locked(
                        connection,
                        attempt_id,
                        completed_at=invalidated_at,
                    )
                except IbkrHistoricalIncomplete:
                    outcome = await self._invalidate_attempt_locked(
                        connection,
                        attempt_id,
                        invalidated_at=invalidated_at,
                        maximum_attempts=maximum_attempts,
                    )
                outcomes.append(outcome)
        return tuple(outcomes)

    async def mark_ibkr_historical_request_published(
        self,
        *,
        plan_sha256: str,
        request_sha256: str,
        result_sha256: str,
        published_at: datetime,
    ) -> None:
        _require_sha256(plan_sha256, "IBKR publication plan hash")
        _require_sha256(request_sha256, "IBKR publication request hash")
        _require_sha256(result_sha256, "IBKR result hash")
        require_utc(published_at, "IBKR publication time")
        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT status, publication_status, result_sha256
                        FROM ops.ibkr_historical_requests
                        WHERE plan_sha256 = :plan_sha256
                          AND request_sha256 = :request_sha256
                        FOR UPDATE
                        """
                        ),
                        {
                            "plan_sha256": plan_sha256,
                            "request_sha256": request_sha256,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise RuntimeError("IBKR publication targets an unknown request")
            if str(row["status"]) != IbkrRequestStatus.SUCCEEDED.value:
                raise RuntimeError("IBKR publication requires a successful request")
            if str(row["publication_status"]) == IbkrPublicationStatus.PUBLISHED.value:
                if row["result_sha256"] != result_sha256:
                    raise RuntimeError(
                        "IBKR publication identity conflicts with its immutable result"
                    )
                return
            await connection.execute(
                text(
                    """
                    UPDATE ops.ibkr_historical_requests
                    SET publication_status = :publication_status,
                        result_sha256 = :result_sha256, published_at = :published_at
                    WHERE plan_sha256 = :plan_sha256
                      AND request_sha256 = :request_sha256
                    """
                ),
                {
                    "publication_status": IbkrPublicationStatus.PUBLISHED.value,
                    "result_sha256": result_sha256,
                    "published_at": published_at,
                    "plan_sha256": plan_sha256,
                    "request_sha256": request_sha256,
                },
            )
            unpublished = (
                await connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM ops.ibkr_historical_requests
                            WHERE plan_sha256 = :plan_sha256
                              AND publication_status = :publication_status
                        )
                        """
                    ),
                    {
                        "plan_sha256": plan_sha256,
                        "publication_status": IbkrPublicationStatus.PENDING.value,
                    },
                )
            ).scalar_one()
            if not bool(unpublished):
                await connection.execute(
                    text(
                        """
                        UPDATE ops.ibkr_historical_plans
                        SET publication_status = :publication_status,
                            published_at = :published_at
                        WHERE plan_sha256 = :plan_sha256
                        """
                    ),
                    {
                        "publication_status": IbkrPublicationStatus.PUBLISHED.value,
                        "published_at": published_at,
                        "plan_sha256": plan_sha256,
                    },
                )

    async def _finalize_attempt_locked(
        self,
        connection: AsyncConnection,
        attempt_id: UUID,
        *,
        completed_at: datetime,
    ) -> IbkrHistoricalAttemptOutcome:
        row = await self._attempt_for_update(connection, attempt_id)
        if row is None:
            raise RuntimeError("IBKR completion targets an unknown attempt")
        if str(row["status"]) != IbkrAttemptStatus.STARTED.value:
            return _attempt_outcome_from_row(row)

        marker = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT marker_id, attempt_id, connection_generation, sequence,
                           completed_at, accepted_row_count, schedule_interval_count,
                           closure_eligible, payload
                    FROM ops.ibkr_historical_completion_markers
                    WHERE attempt_id = :attempt_id
                      AND connection_generation = :connection_generation
                      AND closure_eligible
                    ORDER BY sequence
                    LIMIT 1
                    """
                    ),
                    {
                        "attempt_id": attempt_id,
                        "connection_generation": row["connection_generation"],
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if marker is None:
            raise IbkrHistoricalIncomplete("IBKR attempt has no eligible completion marker")

        error_count = int(
            (
                await connection.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM ops.ibkr_historical_callbacks
                        WHERE attempt_id = :attempt_id
                          AND sequence < :sequence
                          AND callback_kind = :callback_kind
                          AND closure_eligible
                        """
                    ),
                    {
                        "attempt_id": attempt_id,
                        "sequence": marker["sequence"],
                        "callback_kind": IbkrHistoricalCallbackKind.ERROR.value,
                    },
                )
            ).scalar_one()
        )
        request_kind = IbkrHistoricalRequestKind(str(row["request_kind"]))
        if error_count:
            request_status = IbkrRequestStatus.TERMINAL
            attempt_status = IbkrAttemptStatus.TERMINAL_FAILURE
            disposition = IbkrTerminalDisposition.PROVIDER_REJECTED
            detail = "provider emitted an error callback before completion"
        elif (
            request_kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
            and int(marker["accepted_row_count"]) == 0
        ):
            request_status = IbkrRequestStatus.TERMINAL
            attempt_status = IbkrAttemptStatus.TERMINAL_FAILURE
            disposition = IbkrTerminalDisposition.NO_DATA_RETURNED
            detail = "provider completed the exact bar request without an accepted row"
        else:
            request_status = IbkrRequestStatus.SUCCEEDED
            attempt_status = IbkrAttemptStatus.SUCCEEDED
            disposition = IbkrTerminalDisposition.SUCCEEDED
            detail = None

        terminal_at = _not_before(completed_at, cast(datetime, row["started_at"]))
        await connection.execute(
            text(
                """
                UPDATE ops.ibkr_historical_attempts
                SET status = :status, terminal_at = :terminal_at,
                    terminal_disposition = :terminal_disposition, detail = :detail
                WHERE attempt_id = :attempt_id
                """
            ),
            {
                "status": attempt_status.value,
                "terminal_at": terminal_at,
                "terminal_disposition": disposition.value,
                "detail": detail,
                "attempt_id": attempt_id,
            },
        )
        await connection.execute(
            text(
                """
                UPDATE ops.ibkr_historical_requests
                SET status = :status, selected_attempt_id = :attempt_id
                WHERE plan_sha256 = :plan_sha256
                  AND request_sha256 = :request_sha256
                """
            ),
            {
                "status": request_status.value,
                "attempt_id": attempt_id,
                "plan_sha256": row["plan_sha256"],
                "request_sha256": row["request_sha256"],
            },
        )
        updated = await self._attempt_for_update(connection, attempt_id)
        if updated is None:
            raise RuntimeError("IBKR attempt disappeared after completion transition")
        return _attempt_outcome_from_row(updated)

    async def _invalidate_attempt_locked(
        self,
        connection: AsyncConnection,
        attempt_id: UUID,
        *,
        invalidated_at: datetime,
        maximum_attempts: int,
    ) -> IbkrHistoricalAttemptOutcome:
        row = await self._attempt_for_update(connection, attempt_id)
        if row is None:
            raise RuntimeError("IBKR invalidation targets an unknown attempt")
        if str(row["status"]) != IbkrAttemptStatus.STARTED.value:
            return _attempt_outcome_from_row(row)
        exhausted = int(row["attempt_ordinal"]) >= maximum_attempts
        attempt_status = (
            IbkrAttemptStatus.TERMINAL_FAILURE if exhausted else IbkrAttemptStatus.INVALIDATED
        )
        request_status = IbkrRequestStatus.TERMINAL if exhausted else IbkrRequestStatus.PENDING
        disposition = IbkrTerminalDisposition.RETRY_LIMIT_EXHAUSTED if exhausted else None
        terminal_at = _not_before(invalidated_at, cast(datetime, row["started_at"]))
        await connection.execute(
            text(
                """
                UPDATE ops.ibkr_historical_attempts
                SET status = :status, terminal_at = :terminal_at,
                    terminal_disposition = :terminal_disposition, detail = :detail
                WHERE attempt_id = :attempt_id
                """
            ),
            {
                "status": attempt_status.value,
                "terminal_at": terminal_at,
                "terminal_disposition": disposition.value if disposition else None,
                "detail": "connection generation disconnected before attempt completion",
                "attempt_id": attempt_id,
            },
        )
        await connection.execute(
            text(
                """
                UPDATE ops.ibkr_historical_requests
                SET status = :status,
                    selected_attempt_id = CASE
                        WHEN :status = :terminal_status THEN :attempt_id
                        ELSE selected_attempt_id
                    END
                WHERE plan_sha256 = :plan_sha256
                  AND request_sha256 = :request_sha256
                """
            ),
            {
                "status": request_status.value,
                "terminal_status": IbkrRequestStatus.TERMINAL.value,
                "attempt_id": attempt_id,
                "plan_sha256": row["plan_sha256"],
                "request_sha256": row["request_sha256"],
            },
        )
        updated = await self._attempt_for_update(connection, attempt_id)
        if updated is None:
            raise RuntimeError("IBKR attempt disappeared after invalidation transition")
        return _attempt_outcome_from_row(updated)

    @staticmethod
    async def _attempt_for_update(
        connection: AsyncConnection, attempt_id: UUID
    ) -> Mapping[str, Any] | None:
        result = await connection.execute(
            text(
                """
                SELECT a.attempt_id, a.plan_sha256, a.request_sha256,
                       a.attempt_ordinal, a.provider_request_id,
                       a.connection_generation, a.started_at, a.status,
                       a.terminal_at, a.terminal_disposition, a.detail,
                       r.status AS request_status, r.request_kind
                FROM ops.ibkr_historical_attempts AS a
                JOIN ops.ibkr_historical_requests AS r
                  ON r.plan_sha256 = a.plan_sha256
                 AND r.request_sha256 = a.request_sha256
                WHERE a.attempt_id = :attempt_id
                FOR UPDATE
                """
            ),
            {"attempt_id": attempt_id},
        )
        return cast(Mapping[str, Any] | None, result.mappings().one_or_none())


def _attempt_outcome_from_row(row: Mapping[str, Any]) -> IbkrHistoricalAttemptOutcome:
    status = IbkrAttemptStatus(str(row["status"]))
    terminal_disposition = row["terminal_disposition"]
    disposition = (
        IbkrTerminalDisposition(str(terminal_disposition))
        if terminal_disposition is not None
        else None
    )
    attempt = IbkrHistoricalAttempt(
        attempt_id=cast(UUID, row["attempt_id"]),
        plan_sha256=str(row["plan_sha256"]),
        request_sha256=str(row["request_sha256"]),
        attempt_ordinal=int(row["attempt_ordinal"]),
        provider_request_id=int(row["provider_request_id"]),
        connection_generation=int(row["connection_generation"]),
        started_at=cast(datetime, row["started_at"]),
        status=status,
        terminal_at=cast(datetime | None, row["terminal_at"]),
        terminal_disposition=disposition,
        detail=cast(str | None, row["detail"]),
    )
    request_status = IbkrRequestStatus(str(row["request_status"]))
    outcome_disposition = (
        IbkrTerminalDisposition.SUCCEEDED
        if request_status is IbkrRequestStatus.SUCCEEDED
        else disposition
        if request_status is IbkrRequestStatus.TERMINAL
        else None
    )
    return IbkrHistoricalAttemptOutcome(
        attempt=attempt,
        request_status=request_status,
        disposition=outcome_disposition,
    )


def _json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _bytes_value(value: object, field: str) -> bytes:
    if isinstance(value, memoryview):
        return value.tobytes()
    if not isinstance(value, bytes):
        raise TypeError(f"{field} did not return bytes")
    return value


def _not_before(value: datetime, lower_bound: datetime) -> datetime:
    return max(value, lower_bound)


def _require_positive(value: int, field: str) -> None:
    if value <= 0:
        raise ValueError(f"{field} must be positive")


def _require_maximum_attempts(value: int) -> None:
    if value <= 0 or value > 6:
        raise ValueError("IBKR maximum attempts must be between one and six")


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")


__all__ = ["PostgresIbkrHistoricalExecutionStore"]
