from fastapi import APIRouter, HTTPException
import json


from ..models.schemas import (
    AIHashtagRequest,
    AIProjectRequest,
    AISceneDetectRequest,
    AISummaryRequest,
    AITitleRequest,
)
from ..services.queue_manager import add_queue_item
from ..services.path_guard import http_safe_media_input
from ..services.secure_settings import protect_setting, reveal_setting

router = APIRouter()


@router.post("/scene-detect")
def scene_detect(data: AISceneDetectRequest):
    video_path = str(http_safe_media_input(data.video_path, field="video path"))
    item_id = add_queue_item(
        data.project_id,
        "scene_detect",
        video_path,
        {"threshold": data.threshold},
        priority=1,
    )
    return {"id": item_id, "message": "Da dua tien trinh tach phan canh vao hang doi", "project_id": data.project_id}


@router.post("/summary")
def summarize(data: AISummaryRequest):
    item_id = add_queue_item(
        data.project_id,
        "ai_task",
        "",
        {"task": "summary", "text": data.text, "max_length": data.max_length, "engine": data.engine},
        priority=1,
    )
    return {"id": item_id, "message": "Da dua tien trinh tao summary vao hang doi", "project_id": data.project_id}


@router.post("/recap")
def recap(data: AIProjectRequest):
    video_path = str(http_safe_media_input(data.video_path, field="video path")) if data.video_path else ""
    item_id = add_queue_item(
        data.project_id,
        "ai_recap",
        video_path,
        {"text": data.text or "", "style": "review", "language": "vi"},
        priority=1,
    )
    return {"id": item_id, "message": "Da dua tien trinh tao recap vao hang doi", "project_id": data.project_id}


@router.post("/characters")
def characters(data: AIProjectRequest):
    if not data.video_path:
        raise HTTPException(400, "Yeu cau cung cap video_path")
    video_path = str(http_safe_media_input(data.video_path, field="video path"))
    item_id = add_queue_item(data.project_id, "ai_task", video_path, {"task": "characters"}, priority=1)
    return {"id": item_id, "message": "Da dua tien trinh nhan dien nhan vat vao hang doi", "project_id": data.project_id}


@router.post("/speakers")
def speakers(data: AIProjectRequest):
    if not data.video_path:
        raise HTTPException(400, "Yeu cau cung cap video_path")
    video_path = str(http_safe_media_input(data.video_path, field="video path"))
    item_id = add_queue_item(data.project_id, "ai_task", video_path, {"task": "speakers"}, priority=1)
    return {"id": item_id, "message": "Da dua tien trinh nhan dien speaker vao hang doi", "project_id": data.project_id}


@router.post("/title")
def title_gen(data: AITitleRequest):
    video_path = str(http_safe_media_input(data.video_path, field="video path")) if data.video_path else ""
    item_id = add_queue_item(
        data.project_id,
        "ai_task",
        video_path,
        {"task": "title", "style": data.style},
        priority=1,
    )
    return {"id": item_id, "message": "Da dua tien trinh tao title vao hang doi", "project_id": data.project_id}


@router.post("/titles")
def titles_gen(data: AITitleRequest):
    return title_gen(data)


@router.post("/hashtags")
def hashtags(data: AIHashtagRequest):
    item_id = add_queue_item(
        data.project_id,
        "ai_task",
        "",
        {"task": "hashtags", "text": data.text, "count": data.count},
        priority=1,
    )
    return {"id": item_id, "message": "Da dua tien trinh tao hashtag vao hang doi", "project_id": data.project_id}


@router.get("/providers/presets")
def ai_provider_presets():
    from ..services.ai_provider_service import provider_presets
    return provider_presets()


@router.post("/providers/test")
def test_ai_provider_endpoint(data: dict):
    from ..services.ai_provider_service import test_ai_provider
    return test_ai_provider(data)


@router.post("/cookies/check")
def check_cookies_endpoint(data: dict):
    provider = data.get("provider")
    use_playwright = data.get("use_playwright", False)
    
    if provider not in ("chatgpt", "gemini"):
        raise HTTPException(status_code=400, detail="Provider must be 'chatgpt' or 'gemini'")
        
    from ..database import db_cursor
    from ..services.cookie_checker import verify_cookie_status
    
    # Load cookie JSON from settings table
    with db_cursor() as cur:
        key = f"{provider}_cookies"
        row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        
    if not row or not row["value"]:
        return {"status": "empty", "message": f"Chưa có cookie cho {provider}."}
        
    try:
        cookies_list = json.loads(reveal_setting(key, row["value"]))
    except Exception:
        return {"status": "error", "message": "Dữ liệu cookie không đúng định dạng JSON."}
        
    res = verify_cookie_status(provider, cookies_list, use_playwright=use_playwright)
    return res


@router.post("/cookies/grab")
def grab_cookies_endpoint(data: dict):
    browser = data.get("browser", "chrome")
    provider = data.get("provider", "all")
    consent = bool(data.get("consent"))
    
    if browser not in ("chrome", "edge"):
        raise HTTPException(status_code=400, detail="Browser must be 'chrome' or 'edge'")
    if provider not in ("chatgpt", "gemini", "all"):
        raise HTTPException(status_code=400, detail="Provider must be 'chatgpt', 'gemini', or 'all'")
    if not consent:
        raise HTTPException(
            status_code=403,
            detail="COOKIE_CONSENT_REQUIRED: user must explicitly consent before browser cookies are read",
        )
        
    from ..services.cookie_grabber import grab_browser_cookies
    from ..database import db_cursor
    
    domains_map = {
        "chatgpt": ["chatgpt.com", "openai.com"],
        "gemini": ["gemini.google.com", "google.com"]
    }
    
    grabbed = {}
    
    with db_cursor() as cur:
        if provider in ("chatgpt", "all"):
            chatgpt_cookies = grab_browser_cookies(browser, domains_map["chatgpt"])
            if chatgpt_cookies:
                cur.execute(
                    "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("chatgpt_cookies", protect_setting("chatgpt_cookies", json.dumps(chatgpt_cookies))),
                )
            grabbed["chatgpt"] = len(chatgpt_cookies)
            
        if provider in ("gemini", "all"):
            gemini_cookies = grab_browser_cookies(browser, domains_map["gemini"])
            if gemini_cookies:
                cur.execute(
                    "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("gemini_cookies", protect_setting("gemini_cookies", json.dumps(gemini_cookies))),
                )
            grabbed["gemini"] = len(gemini_cookies)
            
    return {
        "status": "success",
        "message": f"Đã tự động lấy cookie từ {browser.capitalize()}",
        "grabbed": grabbed
    }


@router.delete("/cookies/{provider}")
def delete_cookies_endpoint(provider: str):
    if provider not in ("chatgpt", "gemini", "all"):
        raise HTTPException(status_code=400, detail="Provider must be 'chatgpt', 'gemini', or 'all'")
    keys = ["chatgpt_cookies", "gemini_cookies"] if provider == "all" else [f"{provider}_cookies"]
    from ..database import db_cursor
    with db_cursor() as cur:
        for key in keys:
            cur.execute("DELETE FROM settings WHERE key=?", (key,))
    return {"status": "success", "deleted": keys}


@router.post("/chat-browser")
def chat_browser_endpoint(data: dict):
    provider = data.get("provider")
    prompt = data.get("prompt")
    
    if not provider or not prompt:
        raise HTTPException(status_code=400, detail="Provider and prompt are required")
        
    if provider not in ("chatgpt", "gemini"):
        raise HTTPException(status_code=400, detail="Provider must be 'chatgpt' or 'gemini'")
        
    from ..database import db_cursor
    from ..services.browser_chat_service import run_chatgpt_automation, run_gemini_automation
    
    # Load cookies
    with db_cursor() as cur:
        key = f"{provider}_cookies"
        row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        
    if not row or not row["value"]:
        raise HTTPException(status_code=400, detail=f"Chưa có cookie cho {provider}. Vui lòng lấy cookie trước.")
        
    try:
        cookies_list = json.loads(reveal_setting(key, row["value"]))
    except Exception:
        raise HTTPException(status_code=400, detail="Dữ liệu cookie không đúng định dạng JSON.")
        
    try:
        if provider == "chatgpt":
            result = run_chatgpt_automation(prompt, cookies_list)
        else:
            result = run_gemini_automation(prompt, cookies_list)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
