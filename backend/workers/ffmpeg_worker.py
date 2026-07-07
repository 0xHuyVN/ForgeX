"""
Integrated queue worker.

Runs inside the FastAPI process, atomically claims waiting jobs, and dispatches
them into a bounded worker pool.
"""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from ..database import db_cursor, get_conn
from ..config import QUEUE_JOB_TIMEOUT_MINUTES
from ..services.pipeline_service import run_pipeline
from ..services.queue_manager import is_queue_paused, reset_stale_running, update_item_status


class QueueWorker:
    """Background worker that processes queue items with a bounded pool."""

    def __init__(self, max_workers: int = 2, poll_interval: float = 2.0):
        self.max_workers = max(1, int(max_workers or 1))
        self.poll_interval = poll_interval
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._active_count = 0
        self._active_jobs = {}
        self._timed_out_jobs = set()
        self._timeout_warned_jobs = set()
        self._executor = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="queue-job")
        reset_count = reset_stale_running()
        if reset_count:
            print(f"[Worker] Reset {reset_count} stale running job(s)")
        self._thread = threading.Thread(target=self._run, daemon=True, name="queue-dispatcher")
        self._thread.start()
        print(f"[Worker] Started (max_workers={self.max_workers}, poll={self.poll_interval}s)")

    def stop(self):
        self._running = False
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        print("[Worker] Stopping...")

    @property
    def is_alive(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active_count

    def _inc_active(self):
        with self._lock:
            self._active_count += 1

    def _dec_active(self, item_id=None):
        with self._lock:
            self._active_count = max(0, self._active_count - 1)
            if item_id is not None:
                self._active_jobs.pop(item_id, None)

    def _mark_active_job(self, item_id: int):
        with self._lock:
            self._active_jobs[item_id] = time.monotonic()

    def _fail_timed_out_jobs(self):
        timeout_seconds = max(1, int(QUEUE_JOB_TIMEOUT_MINUTES or 60)) * 60
        now = time.monotonic()
        timed_out = []
        with self._lock:
            for item_id, started_at in list(self._active_jobs.items()):
                if now - started_at > timeout_seconds and item_id not in self._timeout_warned_jobs:
                    timed_out.append(item_id)
                    self._timeout_warned_jobs.add(item_id)

        for item_id in timed_out:
            message = f"Queue job exceeded timeout of {QUEUE_JOB_TIMEOUT_MINUTES} minute(s); still waiting for the running task to finish"
            print(f"[Worker] {message}: {item_id}")
            try:
                with db_cursor() as cur:
                    cur.execute(
                        "INSERT INTO job_logs (queue_item_id, level, message) VALUES (?,?,?)",
                        (item_id, "warning", message),
                    )
            except Exception:
                pass

    def _current_error(self, item_id: int) -> str:
        try:
            with db_cursor() as cur:
                row = cur.execute("SELECT error FROM queue_items WHERE id=?", (item_id,)).fetchone()
                error = (row["error"] if row else "") or ""
                if error:
                    return error
                log = cur.execute(
                    "SELECT message FROM job_logs WHERE queue_item_id=? AND level='error' ORDER BY timestamp DESC, id DESC LIMIT 1",
                    (item_id,),
                ).fetchone()
                return (log["message"] if log else "") or ""
        except Exception:
            return ""

    def _claim_one(self):
        """Atomically claim the next waiting queue item."""
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute(
                """
                SELECT * FROM queue_items
                WHERE status='waiting'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                conn.commit()
                return None
            cur.execute(
                "UPDATE queue_items SET status='running', progress=0, error=NULL, updated_at=datetime('now','localtime') WHERE id=?",
                (row["id"],),
            )
            conn.commit()
            with db_cursor() as log_cur:
                log_cur.execute(
                    "INSERT INTO job_logs (queue_item_id, level, message) VALUES (?,?,?)",
                    (row["id"], "info", "[queue] Claimed by worker"),
                )
            return dict(row)
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[Worker] claim error: {e}")
            return None
        finally:
            cur.close()

    def _run_item(self, item: dict):
        self._mark_active_job(item["id"])
        try:
            print(f"[Worker] Processing item {item['id']}: {item['type']}")
            success = run_pipeline(item)
            if item["id"] in self._timed_out_jobs:
                update_item_status(item["id"], "failed", error=f"Queue job exceeded timeout of {QUEUE_JOB_TIMEOUT_MINUTES} minute(s)")
                success = False
            elif not success:
                error = self._current_error(item["id"])
                update_item_status(item["id"], "failed", error=error or "Pipeline returned error")
            print(f"[Worker] Item {item['id']} {'completed' if success else 'failed'}")
        except Exception as e:
            print(f"[Worker] Error processing item {item.get('id')}: {e}")
            try:
                update_item_status(item["id"], "failed", error=str(e))
            except Exception:
                pass
        finally:
            with self._lock:
                self._timed_out_jobs.discard(item.get("id"))
                self._timeout_warned_jobs.discard(item.get("id"))
            self._dec_active(item.get("id"))

    def _run(self):
        while self._running:
            try:
                self._fail_timed_out_jobs()
                if is_queue_paused():
                    time.sleep(self.poll_interval)
                    continue
                if self.active_count >= self.max_workers:
                    time.sleep(0.25)
                    continue

                item = self._claim_one()
                if item is None:
                    time.sleep(self.poll_interval)
                    continue

                self._inc_active()
                self._executor.submit(self._run_item, item)
            except Exception as e:
                print(f"[Worker] dispatcher error: {e}")
                time.sleep(2)


_worker: QueueWorker = None


def get_worker() -> QueueWorker:
    global _worker
    if _worker is None:
        _worker = QueueWorker()
    return _worker
