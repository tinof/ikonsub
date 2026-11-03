from __future__ import annotations

import threading
import time
from itertools import pairwise

from PySubtrans.Helpers.TestCases import DummyTranslationClient, LoggedTestCase
from PySubtrans.SettingsType import SettingsType


class TranslationClientRateLimitTests(LoggedTestCase):
    """Validate that request pacing is shared by all threads using one client."""

    def _make_client(self, rate_limit: float) -> DummyTranslationClient:
        return DummyTranslationClient(
            SettingsType({"instructions": "Verify pacing.", "rate_limit": rate_limit})
        )

    def _start_times(self, client: DummyTranslationClient, threads: int) -> list[float]:
        starts: list[float] = []
        lock = threading.Lock()

        def worker() -> None:
            client._wait_for_rate_limit()
            with lock:
                starts.append(time.monotonic())

        workers = [threading.Thread(target=worker) for _ in range(threads)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(timeout=5.0)
        return sorted(starts)

    def test_parallel_requests_share_the_rate_limit(self) -> None:
        """Three threads on one client start at least 60 / rate_limit seconds apart."""
        client = self._make_client(rate_limit=600.0)  # 0.1 s between request starts
        starts = self._start_times(client, threads=3)
        self.assertLoggedEqual("started", 3, len(starts))
        gaps = [later - earlier for earlier, later in pairwise(starts)]
        self.assertLoggedTrue(
            "gaps respect the interval", all(gap >= 0.09 for gap in gaps), input_value=gaps
        )

    def test_no_rate_limit_does_not_wait(self) -> None:
        """Without a rate limit requests start immediately."""
        client = self._make_client(rate_limit=0.0)
        began = time.monotonic()
        self._start_times(client, threads=3)
        self.assertLoggedTrue("no waiting", time.monotonic() - began < 0.5)

    def test_abort_wakes_waiting_requests(self) -> None:
        """Aborting releases a thread that waits for its rate-limit slot."""
        client = self._make_client(rate_limit=6.0)  # 10 s between request starts
        client._wait_for_rate_limit()  # takes the first slot

        waiter = threading.Thread(target=client._wait_for_rate_limit)
        began = time.monotonic()
        waiter.start()
        time.sleep(0.05)
        client.AbortTranslation()
        waiter.join(timeout=5.0)

        self.assertLoggedFalse("waiter finished", waiter.is_alive())
        self.assertLoggedTrue("woke early", time.monotonic() - began < 2.0)
