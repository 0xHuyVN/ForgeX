from fastapi import APIRouter, HTTPException

from ..database import db_cursor
from ..services.queue_manager import add_queue_item

router = APIRouter()


@router.post("/")
def start_download(data: dict):
    url = data.get("url", "")
    if not url:
        raise HTTPException(400, "Yeu cau cung cap url")

    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO downloads (url, platform, status) VALUES (?,?,?)",
            (url, data.get("platform") or "auto", "waiting"),
        )
        dl_id = cur.lastrowid

    params = {
        "download_id": dl_id,
        "url": url,
        "quality": data.get("quality", "best"),
        "cookie_file": data.get("cookie_file"),
        "proxy": data.get("proxy"),
        "output_dir": data.get("output_dir"),
        "platform": data.get("platform", "auto"),
    }
    try:
        item_id = add_queue_item(data.get("project_id", 0), "download", "", params)
    except ValueError:
        item_id = add_queue_item(0, "download", "", params)
    return {"id": dl_id, "queue_id": item_id, "message": "Da dua tien trinh tai ve vao hang doi"}


@router.get("/")
def list_downloads():
    with db_cursor() as cur:
        rows = cur.execute("SELECT * FROM downloads ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


@router.get("/{dl_id}")
def get_download(dl_id: int):
    with db_cursor() as cur:
        row = cur.execute("SELECT * FROM downloads WHERE id=?", (dl_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Khong tim thay luot tai xuong")
        return dict(row)


@router.post("/{dl_id}/cancel")
def cancel_download(dl_id: int):
    with db_cursor() as cur:
        cur.execute("UPDATE downloads SET status='cancelled' WHERE id=?", (dl_id,))
    return {"message": "Da huy tai xuong"}
