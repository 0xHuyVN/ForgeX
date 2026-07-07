import queue
import threading
import json


class EventBus:
    """Thread-safe pub/sub event bus using queue.Queue for cross-thread signaling."""

    def __init__(self):
        self._queues: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def register(self) -> queue.Queue:
        q = queue.Queue(maxsize=100)
        with self._lock:
            self._queues.add(q)
        return q

    def unregister(self, q: queue.Queue):
        with self._lock:
            self._queues.discard(q)

    def publish(self, event_type: str, data: dict = None):
        payload = json.dumps({"type": event_type, "data": data})
        with self._lock:
            for q in list(self._queues):
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    pass


event_bus = EventBus()
