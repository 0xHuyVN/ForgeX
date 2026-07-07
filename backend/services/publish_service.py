import os
from pathlib import Path
from ..config import EXPORTS_DIR
from ..database import db_cursor


class PublishCredentialError(RuntimeError):
    """Raised when a real publish target is configured but credentials are absent."""


def preflight_publish_credentials(platform: str) -> dict:
    """Return sanitized credential readiness for a publish platform."""
    platform = (platform or "").lower()
    if platform == "youtube":
        token_file = Path("data/tokens/youtube_token.json")
        return {
            "platform": "youtube",
            "ready": token_file.exists(),
            "required": ["data/tokens/youtube_token.json"],
        }
    if platform == "tiktok":
        return {
            "platform": "tiktok",
            "ready": bool(os.environ.get("TIKTOK_API_KEY", "").strip()),
            "required": ["TIKTOK_API_KEY"],
        }
    if platform == "facebook":
        return {
            "platform": "facebook",
            "ready": bool(os.environ.get("FACEBOOK_ACCESS_TOKEN", "").strip()),
            "required": ["FACEBOOK_ACCESS_TOKEN"],
        }
    return {"platform": platform, "ready": False, "required": ["known platform"]}


def require_publish_credentials(platform: str):
    state = preflight_publish_credentials(platform)
    if not state["ready"]:
        missing = ", ".join(state["required"])
        raise PublishCredentialError(f"BLOCKED_CREDENTIALS: {platform} requires {missing}")


def publish_youtube(video_path: str, title: str, description: str = "", privacy: str = "private", category: str = "22", project_id: int = 0):
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        token_file = Path("data/tokens/youtube_token.json")
        if not token_file.exists():
            raise PublishCredentialError("BLOCKED_CREDENTIALS: youtube requires data/tokens/youtube_token.json")
        creds = Credentials.from_authorized_user_file(str(token_file))
        service = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {"title": title, "description": description, "categoryId": category},
            "status": {"privacyStatus": privacy},
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute()
        with db_cursor() as cur:
            cur.execute("INSERT INTO exports (project_id, input_path, output_path, format, status) VALUES (?,?,?,?,?)",
                        (project_id, video_path, f"https://youtu.be/{response['id']}", "youtube", "published"))
        return {"success": True, "url": f"https://youtu.be/{response['id']}", "id": response["id"]}
    except ImportError:
        return {"success": False, "error": "YouTube publish requires google-api-python-client and google-auth. Install dependencies and authenticate first."}
    except Exception as e:
        return {"success": False, "error": str(e)}


def publish_tiktok(video_path: str, title: str, description: str = "", project_id: int = 0):
    try:
        import requests
        api_key = os.environ.get("TIKTOK_API_KEY", "")
        if not api_key:
            raise PublishCredentialError("BLOCKED_CREDENTIALS: tiktok requires TIKTOK_API_KEY")
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://open-api.tiktok.com/video/upload/",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"video": f},
                data={"title": title, "description": description},
                timeout=300,
            )
        result = resp.json()
        with db_cursor() as cur:
            cur.execute("INSERT INTO exports (project_id, input_path, output_path, format, status) VALUES (?,?,?,?,?)",
                        (project_id, video_path, result.get("data", {}).get("share_url", ""), "tiktok", "published"))
        return {"success": True, "url": result.get("data", {}).get("share_url", "")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def publish_facebook(video_path: str, title: str, description: str = "", page_id: str = "me", project_id: int = 0):
    try:
        import requests
        token = os.environ.get("FACEBOOK_ACCESS_TOKEN", "")
        if not token:
            raise PublishCredentialError("BLOCKED_CREDENTIALS: facebook requires FACEBOOK_ACCESS_TOKEN")
        url = f"https://graph.facebook.com/v18.0/{page_id}/videos"
        with open(video_path, "rb") as f:
            resp = requests.post(
                url,
                params={"access_token": token, "title": title, "description": description},
                files={"source": f},
                timeout=300,
            )
        result = resp.json()
        video_id = result.get("id", "")
        with db_cursor() as cur:
            cur.execute("INSERT INTO exports (project_id, input_path, output_path, format, status) VALUES (?,?,?,?,?)",
                        (project_id, video_path, f"https://fb.watch/{video_id}" if video_id else "", "facebook", "published"))
        return {"success": True, "id": video_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _publish_ffmpeg(video_path: str, title: str, platform: str, project_id: int = 0):
    out_dir = EXPORTS_DIR / "publish"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"{platform}_{Path(video_path).stem}.mp4")
    cmd = ["-i", video_path, "-c", "copy", "-y", out_path]
    from .ffmpeg_utils import run_ffmpeg
    run_ffmpeg(cmd)
    with db_cursor() as cur:
        cur.execute("INSERT INTO exports (project_id, input_path, output_path, format, status) VALUES (?,?,?,?,?)",
                    (project_id, video_path, out_path, platform, "exported"))
    return {"success": True, "output": out_path, "message": f"Đã chuẩn bị video cho {platform}. Yêu cầu API token để tải lên trực tiếp."}


def list_published():
    with db_cursor() as cur:
        rows = cur.execute("SELECT * FROM exports WHERE format IN ('youtube','tiktok','facebook') ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
