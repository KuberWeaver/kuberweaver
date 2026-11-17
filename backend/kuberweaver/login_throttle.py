"""Bounded sliding-window limiter for failed logins.

Two windows are tracked: one per client and account pair so a single account
cannot be guessed at, and one per client so one source cannot spread guesses
across many accounts. Buckets hold failure timestamps only; a successful login
clears the pair, and the dictionary is capped so unauthenticated input cannot
grow it without bound.
"""
from __future__ import annotations

import time


class LoginThrottle:
    def __init__(self, *, account_limit=10, client_limit=30, window=60.0, max_keys=1024,
                 clock=time.monotonic):
        self.account_limit = account_limit
        self.client_limit = client_limit
        self.window = window
        self.max_keys = max_keys
        self.clock = clock
        self._buckets = {}

    def _recent(self, key, current):
        stamps = self._buckets.get(key)
        if not stamps:
            return []
        kept = [stamp for stamp in stamps if current - stamp < self.window]
        if kept:
            self._buckets[key] = kept
        else:
            self._buckets.pop(key, None)
        return kept

    def retry_after(self, client, username):
        """Seconds to wait before another attempt, zero when not throttled."""
        current = self.clock()
        waits = []
        for key, limit in ((('account', client, username), self.account_limit),
                           (('client', client), self.client_limit)):
            stamps = self._recent(key, current)
            if limit and len(stamps) >= limit:
                waits.append(self.window - (current - stamps[0]))
        return max(waits) if waits else 0.0

    def failure(self, client, username):
        current = self.clock()
        for key in (('account', client, username), ('client', client)):
            stamps = self._recent(key, current)
            stamps.append(current)
            self._buckets[key] = stamps
        if len(self._buckets) > self.max_keys:
            self._forget_oldest()

    def success(self, client, username):
        self._buckets.pop(('account', client, username), None)

    def _forget_oldest(self):
        excess = len(self._buckets) - self.max_keys
        ordered = sorted(self._buckets, key=lambda key: self._buckets[key][-1])
        for key in ordered[:excess]:
            self._buckets.pop(key, None)
