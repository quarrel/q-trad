"""Narrow compatibility repair for IG's pinned Lightstreamer Python client."""

from collections.abc import Awaitable
from importlib.metadata import version
from typing import Protocol, cast

from lightstreamer.client.com_lightstreamer_net import WsClientPy, ls_io_thread

_patched = False


class _DoneFuture(Protocol):
    def result(self) -> object: ...


class _ClosableResponse(Protocol):
    def close(self) -> Awaitable[object]: ...


def install_lightstreamer_compatibility() -> None:
    """Repair the 1.0.3 WebSocket disposal callback before creating a client.

    IG deploys Lightstreamer Server 7.3.3 and recommends Python client 1.0.3. That
    client registers an ``async def`` with ``Future.add_done_callback`` when a
    not-yet-connected WebSocket is disposed. The callback is never awaited, so
    the eventual socket is not closed and its I/O thread can keep the process
    alive. Later client releases require a newer server and cannot be substituted
    without an IG compatibility change.
    """

    global _patched
    if _patched:
        return
    installed = version("lightstreamer-client-lib")
    if installed != "1.0.3":
        raise RuntimeError(f"unsupported IG Lightstreamer client version: {installed}")
    WsClientPy.dispose = _dispose_ws_client
    _patched = True


def _dispose_ws_client(self: WsClientPy) -> None:
    if self.isCanceled:
        return
    self.isCanceled = True
    if self.cancellationToken.done():
        _close_response(self.cancellationToken.result().close())
        return

    def on_done_callback(future: object) -> None:
        try:
            response = cast(_ClosableResponse, cast(_DoneFuture, future).result())
        except BaseException:
            return
        _close_response(response.close())

    self.cancellationToken.add_done_callback(on_done_callback)


def _close_response(close_operation: Awaitable[object]) -> None:
    ls_io_thread.submit_coro(close_operation)
