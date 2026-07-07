import threading
from ..database import db_cursor

_job_state = threading.local()

def set_current_job_id(job_id: int):
    _job_state.job_id = job_id

def get_current_job_id() -> int:
    return getattr(_job_state, "job_id", None)

def job_log(level: str, message: str):
    # Clean and format the level and message
    lvl = (level or "info").lower()
    msg = str(message or "").strip()
    if not msg:
        return

    # Print to standard output/console (encoding-safe: a console that can't encode
    # a character e.g. cp1252 vs '→' must never crash the running pipeline).
    line = f"[JobLog] [{lvl.upper()}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        import sys
        enc = (getattr(sys.stdout, "encoding", None) or "ascii")
        sys.stdout.write(line.encode(enc, errors="replace").decode(enc, errors="replace") + "\n")
        sys.stdout.flush()

    job_id = get_current_job_id()
    if job_id:
        try:
            with db_cursor() as cur:
                cur.execute(
                    "INSERT INTO job_logs (queue_item_id, level, message) VALUES (?,?,?)",
                    (job_id, lvl, msg)
                )
        except Exception as e:
            print(f"[Logger Error] Failed to write to database: {e}", flush=True)
