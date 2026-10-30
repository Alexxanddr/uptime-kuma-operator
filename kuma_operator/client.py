"""One serialized Socket.IO session and bounded remote operations."""

import logging
import threading
from contextlib import contextmanager
from uptime_kuma_api import UptimeKumaApi


class KumaManager:
    def __init__(self, settings, factory=UptimeKumaApi):
        self.settings = settings
        self.factory = factory
        self.api = None
        self.lock = threading.RLock()

    def _close(self):
        api, self.api = self.api, None
        if api is not None:
            try:
                api.disconnect()
            except Exception:
                logging.getLogger(__name__).warning("Kuma disconnect failed")

    def close(self):
        with self.lock:
            self._close()

    @contextmanager
    def session(self):
        with self.lock:
            try:
                if self.api is not None and hasattr(self.api, "sio") and not self.api.sio.connected:
                    self._close()
                if self.api is None:
                    self.api = self.factory(self.settings.url, timeout=self.settings.timeout)
                    if hasattr(self.api, "sio"):
                        # Socket.IO reconnect does not repeat Kuma authentication.
                        # Rebuild the whole authenticated session instead.
                        self.api.sio.reconnection = False
                    self.api.login(self.settings.username, self.settings.password)
                yield self.api
            except Exception:
                self._close()
                raise
