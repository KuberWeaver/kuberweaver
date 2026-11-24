import threading
from types import SimpleNamespace

from backend.extensions import ExtensionRegistry


def test_notification_dispatch_runs_outside_caller_thread_and_stops():
    from backend.notification_dispatcher import NotificationDispatcher
    owner = threading.get_ident()
    calls = []
    done = threading.Event()
    class Delivery:
        def send(self, payload): pass
        def retry_pending(self, *, limit):
            calls.append((threading.get_ident(), limit))
            done.set()
    registry = ExtensionRegistry()
    registry.register('notifications', 'chosen', Delivery())
    app = SimpleNamespace(state=SimpleNamespace(registry=registry, provider_config_error=None, provider_bindings={'notifications': 'chosen'}))
    dispatcher = NotificationDispatcher(app, interval=0.02)
    dispatcher.start()
    try:
        assert done.wait(2)
    finally:
        dispatcher.close()
    assert calls and calls[0][0] != owner and calls[0][1] == 10
    assert not dispatcher.thread.is_alive()
