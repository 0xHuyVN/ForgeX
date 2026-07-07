from fastapi import APIRouter, HTTPException

from ..database import db_cursor
from ..services.queue_manager import add_queue_item

router = APIRouter()


@router.post("/download")
def batch_download(data: dict):
    urls = data.get("urls", [])
    if not urls or not isinstance(urls, list):
        raise HTTPException(400, "urls phai la danh sach khong trong")

    items = []
    for url in urls:
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO downloads (url, platform, status) VALUES (?,?,?)",
                (url, data.get("platform", "auto"), "waiting"),
            )
            dl_id = cur.lastrowid
        item_id = add_queue_item(
            data.get("project_id", 0),
            "download",
            "",
            {
                "download_id": dl_id,
                "url": url,
                "quality": data.get("quality", "best"),
                "cookie_file": data.get("cookie_file"),
                "proxy": data.get("proxy"),
                "output_dir": data.get("output_dir"),
                "platform": data.get("platform", "auto"),
            },
        )
        items.append({"url": url, "id": dl_id, "queue_id": item_id})
    return {"message": f"Da dua {len(items)} luot tai ve vao hang doi", "items": items}


@router.post("/urls")
def batch_urls(data: dict):
    urls = data.get("urls", [])
    if not urls:
        raise HTTPException(400, "Yeu cau cung cap urls")
    item_ids = []
    for url in urls:
        item_id = add_queue_item(data.get("project_id", 0), "download", "", {"url": url, "quality": data.get("quality", "best")})
        item_ids.append(item_id)
    return {"message": f"Da dua {len(item_ids)} URL vao hang doi", "ids": item_ids}
