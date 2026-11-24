"""Host-owned notification delivery, isolated from the deployment consumer."""
from __future__ import annotations
import threading
from .providers import resolve_provider


class NotificationDispatcher:
    def __init__(self, app, interval=5.0):
        self.app = app
        self.interval = interval
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self._run, name='kuberweaver-notifications', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            try:
                provider = resolve_provider(self.app, 'notifications')
                retry = getattr(provider, 'retry_pending', None)
                if callable(retry):
                    retry(limit=10)
            except Exception:
                # Durable provider state records delivery failure. Raw exceptions
                # may include secret endpoints and must never enter log output.
                pass
            self.stop.wait(self.interval)

    def close(self):
        self.stop.set()
        if self.thread:
            # Transport operations are bounded; retain the process ownership
            # lock until any in-flight delivery has actually returned.
            self.thread.join()
