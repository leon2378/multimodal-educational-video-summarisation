"""GPU memory, sampled while a benchmark runs: the whole card, as NVML reports it, so it counts
every process (another container's model included)."""

import threading
import time
from types import TracebackType


def used_bytes() -> int | None:
    """Memory in use on GPU 0, or None without an NVIDIA GPU."""
    try:
        import pynvml

        pynvml.nvmlInit()
        return int(pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0)).used)
    except Exception:
        return None


class PeakMemory:
    """Peak GPU memory in use during a `with` block, sampled every 50 ms."""

    def __init__(self, interval_s: float = 0.05) -> None:
        self.interval_s = interval_s
        self.baseline: int | None = None
        self.peak: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "PeakMemory":
        self.baseline = used_bytes()
        self.peak = self.baseline
        if self.baseline is not None:
            self._thread = threading.Thread(target=self._sample, daemon=True)
            self._thread.start()
        return self

    def _sample(self) -> None:
        while not self._stop.is_set():
            used = used_bytes()
            if used is not None and (self.peak is None or used > self.peak):
                self.peak = used
            time.sleep(self.interval_s)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    @property
    def added_mb(self) -> float | None:
        """Peak above what was in use when the block started."""
        if self.peak is None or self.baseline is None:
            return None
        return (self.peak - self.baseline) / 2**20
