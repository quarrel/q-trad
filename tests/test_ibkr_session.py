import pytest

from qtrad.adapters.ibkr.pacing import IbkrPacingPolicy, IbkrRequestReservation
from qtrad.adapters.ibkr.session import (
    IbkrRecoveryAction,
    IbkrSession,
    IbkrSessionState,
    IbkrSubscription,
    IbkrSystemCode,
)


def _connected_session() -> IbkrSession:
    session = IbkrSession()
    session.begin_connection()
    session.mark_socket_connected()
    session.mark_handshake()
    session.mark_server_time()
    return session


def test_handshake_requires_server_time_before_connected() -> None:
    session = IbkrSession()
    generation = session.begin_connection()
    session.mark_socket_connected()
    session.mark_handshake()

    assert generation == 1
    assert session.state == IbkrSessionState.WAITING_SERVER_TIME
    session.mark_server_time()
    assert session.state == IbkrSessionState.CONNECTED


def test_generation_and_exact_resubscription_after_data_loss() -> None:
    session = _connected_session()
    session.register_subscriptions(
        (
            IbkrSubscription("fx:eur-usd", 10, "LIVE", ("BID", "ASK")),
            IbkrSubscription("index:spx", 20, "LIVE", ("BID", "ASK")),
        )
    )
    session.mark_subscription_active("fx:eur-usd", generation=1)
    session.mark_subscription_active("index:spx", generation=1)

    decision = session.on_system_message(IbkrSystemCode.UPSTREAM_DISCONNECTED, now=100.0)
    assert decision.action == IbkrRecoveryAction.NONE
    decision = session.on_system_message(
        IbkrSystemCode.UPSTREAM_RESTORED_DATA_LOST,
        now=101.0,
    )

    assert decision.resubscribe is True
    assert decision.action == IbkrRecoveryAction.NONE
    assert session.snapshot().active_subscriptions == 0
    assert session.snapshot().recovery_epoch == 1
    repeated = session.on_system_message(IbkrSystemCode.UPSTREAM_RESTORED_DATA_LOST)
    assert repeated.resubscribe is False
    assert repeated.reason_code == "IBKR_RESUBSCRIPTION_ALREADY_REQUESTED"


def test_data_maintained_does_not_request_resubscription() -> None:
    session = _connected_session()
    session.register_subscriptions((IbkrSubscription("fx:eur-usd", 10, "LIVE", ("BID", "ASK")),))
    session.mark_subscription_active("fx:eur-usd", generation=1)

    session.on_system_message(IbkrSystemCode.UPSTREAM_DISCONNECTED, now=100.0)
    decision = session.on_system_message(
        IbkrSystemCode.UPSTREAM_RESTORED_DATA_MAINTAINED,
        now=101.0,
    )

    assert decision.resubscribe is False
    assert decision.revalidate_server_time is True
    assert decision.action == IbkrRecoveryAction.NONE
    session.mark_server_time()
    assert session.snapshot().state == IbkrSessionState.SUBSCRIBING


def test_unknown_global_and_request_errors_are_isolated() -> None:
    session = _connected_session()

    request_decision = session.on_system_message(9999, request_id=4)
    global_decision = session.on_system_message(9999)

    assert request_decision.reason_code == "REQUEST_FAILED"
    assert global_decision.action == IbkrRecoveryAction.NONE
    assert global_decision.reason_code == "UNKNOWN_GLOBAL_CODE_9999"
    assert session.state == IbkrSessionState.DEGRADED


def test_farm_disconnect_recovers_and_inactive_is_not_failure() -> None:
    session = _connected_session()

    disconnected = session.on_system_message(IbkrSystemCode.SECURITY_DEFINITION_FARM_DISCONNECTED)
    inactive = session.on_system_message(IbkrSystemCode.HISTORICAL_FARM_INACTIVE)
    recovered = session.on_system_message(IbkrSystemCode.SECURITY_DEFINITION_FARM_CONNECTED)

    assert disconnected.reason_code == "SECURITY_DEFINITION_FARM_DISCONNECTED"
    assert inactive.action == IbkrRecoveryAction.NONE
    assert recovered.reason_code == "SECURITY_DEFINITION_FARM_CONNECTED"
    assert session.snapshot().state == IbkrSessionState.CONNECTED


def test_gateway_escalation_is_bounded() -> None:
    session = IbkrSession()
    session.on_system_message(IbkrSystemCode.UPSTREAM_DISCONNECTED, now=0.0)
    session.mark_reconnect_failed()
    session.mark_reconnect_failed()

    decision = session.poll(now=300.0)
    assert decision.action == IbkrRecoveryAction.RESTART_GATEWAY

    cooldown = session.poll(now=301.0)
    assert cooldown.reason_code == "GATEWAY_RESTART_COOLDOWN"


def test_old_generation_callbacks_are_not_accepted() -> None:
    session = _connected_session()
    session.register_subscriptions((IbkrSubscription("fx:eur-usd", 10, "LIVE", ("BID", "ASK")),))
    session.begin_connection()
    session.mark_socket_connected()
    session.mark_handshake()
    session.mark_server_time()
    session.mark_subscription_active("fx:eur-usd", generation=1)

    snapshot = session.snapshot()
    assert snapshot.active_subscriptions == 0
    assert "SUPERSEDED_GENERATION" in snapshot.reason_codes


def test_reconnect_delay_is_full_jitter_with_cap() -> None:
    session = IbkrSession()

    assert session.reconnect_delay(0, 0.5) == 0.5
    assert session.reconnect_delay(8, 0.5) == 15.0
    with pytest.raises(ValueError):
        session.reconnect_delay(-1, 0.5)


def test_historical_pacing_covers_duplicate_and_weighted_windows() -> None:
    policy = IbkrPacingPolicy()
    recent = [
        IbkrRequestReservation(float(index), "historical", "10", str(index), 2)
        for index in range(30)
    ]
    duplicate = IbkrRequestReservation(30.0, "historical", "10", "29", 1)

    assert policy.wait_seconds(duplicate, recent) == pytest.approx(570.0)
    assert policy.reserve(duplicate, recent) == pytest.approx(570.0)
