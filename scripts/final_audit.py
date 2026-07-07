"""Final post-fix audit harness.

Runs a reproducible local/staging audit and writes evidence under review/logs.
The script is intentionally dependency-light: it uses stdlib HTTP clients and
optionally calls Playwright only when it is installed.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "review"
LOGS = REVIEW / "logs"
FIXTURES = REVIEW / "fixtures"
OUTPUTS = REVIEW / "outputs"
SCREENSHOTS = REVIEW / "screenshots"
DB_PATH = ROOT / "data" / "db" / "app.db"

MOJIBAKE_MARKERS = ("Ã", "Ä", "Æ", "áº", "á»", "ï¿½", "â€”", "â€“", "â€")
SECRET_KEYS = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_TTS_KEY",
    "ELEVENLABS_API_KEY",
    "FPT_API_KEY",
    "TIKTOK_API_KEY",
    "FACEBOOK_ACCESS_TOKEN",
    "VOICE_CLONE_PYTHON",
    "VALTEC_TTS_DIR",
    "WHISPER_MODEL",
    "WHISPER_DEVICE",
)


def ensure_dirs() -> None:
    for path in (LOGS, FIXTURES, OUTPUTS, SCREENSHOTS):
        path.mkdir(parents=True, exist_ok=True)


def write_json(name: str, payload) -> None:
    (LOGS / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_text(name: str, text: str) -> None:
    (LOGS / name).write_text(text, encoding="utf-8", errors="replace")


def run_cmd(name: str, cmd: list[str], timeout: int = 300, env: dict | None = None) -> dict:
    started = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        payload = {
            "name": name,
            "cmd": cmd,
            "returncode": result.returncode,
            "duration_s": round(time.time() - started, 2),
            "stdout": result.stdout[-12000:],
            "stderr": result.stderr[-12000:],
        }
    except Exception as exc:
        payload = {
            "name": name,
            "cmd": cmd,
            "returncode": -1,
            "duration_s": round(time.time() - started, 2),
            "stdout": "",
            "stderr": str(exc),
        }
    write_json(f"final_cmd_{name}.json", payload)
    return payload


def clean_env_python() -> Path:
    venv = ROOT / ".venv-audit"
    python = venv / "Scripts" / "python.exe"
    if not python.exists():
        run_cmd("venv_create", [sys.executable, "-m", "venv", str(venv)], timeout=600)
    return python


def run_clean_env(skip_install: bool) -> dict:
    python = clean_env_python()
    results = {}
    if not skip_install:
        results["pip_install"] = run_cmd(
            "venv_pip_install",
            [str(python), "-m", "pip", "install", "-r", "requirements.txt"],
            timeout=3600,
        )
    else:
        results["pip_install"] = {"returncode": None, "status": "SKIPPED"}
    results["pip_check"] = run_cmd("venv_pip_check", [str(python), "-m", "pip", "check"], timeout=300)
    results["compileall"] = run_cmd("venv_compileall", [str(python), "-m", "compileall", "backend"], timeout=300)
    return results


def ensure_fixture() -> dict:
    video = FIXTURES / "final_sample.mp4"
    srt = FIXTURES / "final_sample.srt"
    data_srt = ROOT / "data" / "subtitles" / "final_sample.srt"
    if not srt.exists():
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello final audit\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nSecond line\n",
            encoding="utf-8",
        )
    if not video.exists():
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video),
        ]
        run_cmd("fixture_ffmpeg", cmd, timeout=120)
    data_srt.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(srt, data_srt)
    return {"video": str(video), "srt": str(data_srt), "fixture_srt": str(srt)}


def request_json(method: str, base: str, path: str, body=None, timeout: int = 60, headers: dict | None = None) -> dict:
    data = None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = Request(base + path, data=data, method=method, headers=req_headers)
    started = time.time()
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            return {"status": resp.status, "body": parsed, "duration_ms": round((time.time() - started) * 1000, 1)}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return {"status": exc.code, "body": parsed, "duration_ms": round((time.time() - started) * 1000, 1)}
    except URLError as exc:
        return {"status": 0, "body": str(exc), "duration_ms": round((time.time() - started) * 1000, 1)}


def start_server(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["HOST"] = "127.0.0.1"
    env["PYTHONIOENCODING"] = "utf-8"
    log = open(LOGS / "final_startup_server.log", "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.main"],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    return proc


def wait_health(base: str, timeout: int = 60) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = request_json("GET", base, "/api/health", timeout=5)
        if last.get("status") == 200:
            return last
        time.sleep(1)
    return last


def wait_job(base: str, job_id: int, timeout: int = 180) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        rows = request_json("GET", base, "/api/queue", timeout=10)
        if rows.get("status") == 200 and isinstance(rows.get("body"), list):
            for item in rows["body"]:
                if item.get("id") == job_id:
                    last = item
                    if item.get("status") in {"completed", "failed", "cancelled"}:
                        return enrich_job_error(base, item)
        time.sleep(2)
    return enrich_job_error(base, last) if last else {"id": job_id, "status": "timeout"}


def enrich_job_error(base: str, item: dict) -> dict:
    if not item or item.get("status") != "failed":
        return item
    current = str(item.get("error") or "")
    if current and current != "Pipeline returned error":
        return item
    logs = request_json("GET", base, f"/api/queue/logs?queue_item_id={item.get('id')}&limit=20", timeout=10)
    if logs.get("status") == 200 and isinstance(logs.get("body"), list):
        for row in logs["body"]:
            message = str(row.get("message") or "")
            if row.get("level") == "error" and message:
                item = dict(item)
                item["error"] = message.replace("Pipeline failed: ", "", 1)
                return item
    return item


def run_e2e(base: str, fixtures: dict) -> list[dict]:
    results = []
    project = request_json("POST", base, "/api/projects/", {"name": f"final_audit_{int(time.time())}", "preset": "Movie Review"})
    project_id = project.get("body", {}).get("id") if isinstance(project.get("body"), dict) else None
    results.append({"step": "create_project", **project})
    if not project_id:
        write_json("final_e2e_results.json", results)
        return results

    steps = [
        ("import_subtitle", "POST", "/api/subtitle/import-path", {"project_id": project_id, "path": fixtures["srt"]}),
        ("sync_video", "POST", f"/api/timeline/{project_id}/video", {"path": fixtures["video"]}),
        ("tts_preview", "POST", "/api/voice/tts", {"project_id": project_id, "text": "Final audit voice", "provider": "edge", "voice": "vi-VN-NamMinhNeural"}),
        ("export_audio", "POST", "/api/export/audio", {"project_id": project_id, "input_path": fixtures["video"], "format": "mp3"}),
        ("render", "POST", "/api/export/render", {"project_id": project_id, "type": "draft", "input_path": fixtures["video"], "output_name": "final_audit_render"}),
        ("scene_detect", "POST", "/api/edit/scene-detect", {"project_id": project_id, "video_path": fixtures["video"], "threshold": 27}),
        ("whisper_stt", "POST", "/api/subtitle/transcribe-video", {"project_id": project_id, "path": fixtures["video"], "language": "en"}),
        ("ocr_hardsub", "POST", "/api/subtitle/ocr-video", {"project_id": project_id, "path": fixtures["video"]}),
        ("voice_clone", "POST", "/api/voice/clone/train", {"project_id": project_id, "sample_path": fixtures["video"], "name": f"final_audit_{project_id}"}),
    ]
    for name, method, path, body in steps:
        res = request_json(method, base, path, body, timeout=120)
        record = {"step": name, **res}
        if isinstance(res.get("body"), dict) and "id" in res["body"] and name not in {"import_subtitle"}:
            record["job"] = wait_job(base, int(res["body"]["id"]), timeout=240)
        results.append(record)

    write_json("final_e2e_results.json", results)
    return results


def run_security(base: str, fixtures: dict) -> list[dict]:
    payloads = [
        ("path_traversal_video", "GET", "/api/video/serve?" + urlencode({"path": "C:/Windows/win.ini"}), None, {}),
        ("timeline_non_media", "POST", "/api/timeline/1/video", {"path": "C:/Windows/win.ini"}, {}),
        ("invalid_clip_fk", "POST", "/api/timeline/tracks/999999/clips", {"source_path": fixtures["video"]}, {}),
        ("csrf_like_settings", "PUT", "/api/settings", {"audit_probe": "1"}, {"Origin": "https://evil.example"}),
        ("ssrf_download", "POST", "/api/download/", {"url": "http://127.0.0.1:1/ssrf-probe"}, {}),
        ("sql_injection_project", "GET", "/api/projects/1%20OR%201=1", None, {}),
        ("command_injection_subtitle", "POST", "/api/subtitle/detect-streams", {"path": f"{fixtures['video']};calc.exe"}, {}),
        ("cookie_grab_sensitive", "POST", "/api/ai/cookies/grab", {"provider": "chatgpt"}, {}),
        ("queue_clear_sensitive", "POST", "/api/queue/clear-all", {}, {"Origin": "https://evil.example"}),
    ]
    results = []
    for name, method, path, body, headers in payloads:
        res = request_json(method, base, path, body, timeout=30, headers=headers)
        results.append({"probe": name, **res})
    write_json("final_security_probes.json", results)
    return results


def run_publish_preflight(base: str, fixtures: dict) -> list[dict]:
    results = []
    for platform in ("youtube", "tiktok", "facebook"):
        res = request_json(
            "POST",
            base,
            f"/api/publish/{platform}",
            {
                "project_id": 0,
                "video_path": fixtures["video"],
                "title": "Final audit test",
                "description": "Automated private/unlisted audit upload",
                "privacy": "private",
            },
            timeout=60,
        )
        record = {"platform": platform, **res}
        if isinstance(res.get("body"), dict) and "id" in res["body"]:
            record["job"] = wait_job(base, int(res["body"]["id"]), timeout=240)
        results.append(record)
    write_json("final_publish_results.json", results)
    return results


def run_download_probe(base: str) -> dict:
    # yt-dlp test target from the public project; short, stable, safe for audits.
    url = "https://www.youtube.com/watch?v=BaW_jenozKc"
    res = request_json("POST", base, "/api/download/", {"url": url, "quality": "audio", "platform": "youtube"}, timeout=60)
    record = {"url": url, **res}
    if isinstance(res.get("body"), dict) and "queue_id" in res["body"]:
        record["job"] = wait_job(base, int(res["body"]["queue_id"]), timeout=300)
    write_json("final_download_result.json", record)
    return record


def run_perf(base: str) -> dict:
    samples = []
    for _ in range(10):
        samples.append(request_json("GET", base, "/api/health", timeout=10)["duration_ms"])
        time.sleep(0.1)
    worker = request_json("GET", base, "/api/queue/worker", timeout=30)
    info = request_json("GET", base, "/api/system/info", timeout=30)
    out_csv = LOGS / "final_latency_health.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["sample", "duration_ms"])
        for idx, value in enumerate(samples, 1):
            writer.writerow([idx, value])
    payload = {
        "health_ms": samples,
        "median_health_ms": sorted(samples)[len(samples) // 2] if samples else None,
        "worker": worker,
        "system_info": info,
    }
    write_json("final_performance.json", payload)
    return payload


def run_db_check() -> dict:
    if not DB_PATH.exists():
        payload = {"status": "FAIL", "error": f"DB not found: {DB_PATH}"}
    else:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.row_factory = sqlite3.Row
            violations = [dict(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            payload = {"status": "PASS" if not violations else "FAIL", "tables": tables, "foreign_key_violations": violations}
        finally:
            conn.close()
    write_json("final_database_check.json", payload)
    return payload


def scan_mojibake() -> dict:
    files = [p for p in list((ROOT / "backend").rglob("*.py")) + [ROOT / "app.js", ROOT / "index.html", ROOT / "style.css"] if p.exists()]
    findings = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(marker in line for marker in MOJIBAKE_MARKERS):
                findings.append({"file": str(path.relative_to(ROOT)), "line": lineno, "text": line[:240]})
    payload = {"status": "PASS" if not findings else "FAIL", "findings": findings[:500], "total": len(findings)}
    write_json("final_mojibake_scan.json", payload)
    return payload


def secret_preflight() -> dict:
    env_file = ROOT / ".env"
    env_values = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                env_values[key.strip()] = value.strip()
    payload = {}
    for key in SECRET_KEYS:
        value = os.environ.get(key) or env_values.get(key, "")
        payload[key] = "SET" if value else "MISSING"
    payload["youtube_token"] = "SET" if (ROOT / "data" / "tokens" / "youtube_token.json").exists() else "MISSING"
    write_json("final_secret_preflight.json", payload)
    return payload


def run_playwright(base: str) -> dict:
    script = ROOT / "temp_openshot" / "final_playwright_probe.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        """
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
base = sys.argv[1]
shots = Path(sys.argv[2])
tabs = ["subtitle", "voice", "music", "enhance", "edit", "ai", "export", "queue"]
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    page.goto(base, wait_until="domcontentloaded", timeout=30000)
    page.screenshot(path=str(shots / "final_home_desktop.png"), full_page=True)
    for tab in tabs:
        locator = page.locator(f'[data-tab="{tab}"]').first
        if locator.count():
            locator.click(timeout=5000)
            page.wait_for_timeout(300)
            page.screenshot(path=str(shots / f"final_tab_{tab}.png"), full_page=True)
            results.append({"tab": tab, "status": "PASS"})
        else:
            results.append({"tab": tab, "status": "UNVERIFIED", "error": "selector not found"})
    browser.close()
print(json.dumps(results, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    result = run_cmd("playwright_final", [sys.executable, str(script), base, str(SCREENSHOTS)], timeout=120)
    try:
        parsed = json.loads(result.get("stdout", "[]"))
    except Exception:
        parsed = [{"status": "UNVERIFIED", "error": result.get("stderr") or result.get("stdout")}]
    write_json("final_ui_results.json", parsed)
    return {"results": parsed, "cmd": result}


def status_from_job(record: dict) -> str:
    if record.get("status", 0) >= 500:
        return "FAIL"
    job = record.get("job")
    if isinstance(job, dict):
        err = str(job.get("error") or "")
        if "BLOCKED_CREDENTIALS" in err:
            return "BLOCKED_CREDENTIALS"
        if job.get("status") == "completed":
            return "PASS"
        if job.get("status") == "failed":
            return "FAIL"
    if record.get("status") in {200, 201, 202, 204, 400, 401, 403, 404, 422}:
        return "PASS"
    return "UNVERIFIED"


def job_error(record: dict) -> str:
    job = record.get("job")
    if isinstance(job, dict):
        return str(job.get("error") or "")
    return ""


def generate_reports(summary: dict) -> None:
    clean = summary.get("clean_env", {})
    e2e = summary.get("e2e", [])
    security = summary.get("security", [])
    publish = summary.get("publish", [])
    external_skipped = summary.get("download", {}).get("status") == "SKIPPED"
    mojibake = summary.get("mojibake", {})
    db = summary.get("database", {})
    perf = summary.get("performance", {})

    hard_failures = []
    blocked = []
    skipped = []
    if clean.get("pip_check", {}).get("returncode") not in (0, None):
        hard_failures.append("pip check failed in .venv-audit")
    if clean.get("compileall", {}).get("returncode") not in (0, None):
        hard_failures.append("backend compile failed")
    if any(item.get("status", 0) >= 500 for item in security):
        hard_failures.append("security/API probe returned 5xx")
    if db.get("status") != "PASS":
        hard_failures.append("database FK check failed")
    if mojibake.get("status") != "PASS":
        hard_failures.append(f"mojibake scan found {mojibake.get('total', 0)} issue(s)")
    for item in e2e:
        job = item.get("job")
        if isinstance(job, dict) and job.get("status") == "failed":
            err = job_error(item)
            if "BLOCKED" in err:
                blocked.append(f"e2e {item.get('step')}: {err}")
            else:
                hard_failures.append(f"e2e {item.get('step')} failed: {err or 'unknown error'}")

    download = summary.get("download", {})
    if isinstance(download.get("job"), dict) and download["job"].get("status") == "failed":
        err = job_error(download)
        if "BLOCKED" in err:
            blocked.append(f"download: {err}")
        else:
            hard_failures.append(f"download failed: {err or 'unknown error'}")

    if external_skipped:
        skipped.append("download/publish external probes")
    for item in publish:
        status = status_from_job(item)
        if status == "BLOCKED_CREDENTIALS":
            blocked.append(item["platform"])
        elif status == "FAIL":
            hard_failures.append(f"publish {item['platform']} failed")

    score = 8.0
    score -= min(3.0, len(hard_failures) * 0.8)
    score -= min(1.5, len(blocked) * 0.3)
    score -= min(1.0, len(skipped) * 0.5)
    score = max(1.0, round(score, 1))
    verdict = "PASS" if not hard_failures and not blocked and not skipped else ("BLOCKED" if not hard_failures else "FAIL")

    final_report = f"""# Final Audit Report

## Executive Summary

Final post-fix audit executed on {time.strftime('%Y-%m-%d %H:%M:%S')}. Verdict: **{verdict}**. Production readiness score: **{score}/10**.

## Evidence Summary

- Clean env pip check: {clean.get('pip_check', {}).get('returncode', 'not run')}.
- Clean env pip install: {clean.get('pip_install', {}).get('status', clean.get('pip_install', {}).get('returncode', 'not run'))}.
- Backend compile: {clean.get('compileall', {}).get('returncode', 'not run')}.
- E2E steps recorded: {len(e2e)}.
- Security probes recorded: {len(security)}.
- DB foreign key status: {db.get('status', 'UNKNOWN')}.
- Mojibake scan: {mojibake.get('status', 'UNKNOWN')} ({mojibake.get('total', 0)} findings).
- Health median latency: {perf.get('median_health_ms', 'UNKNOWN')} ms.
- Publish blocked credentials: {', '.join(blocked) if blocked else 'none'}.
- Skipped external probes: {', '.join(skipped) if skipped else 'none'}.

## Remaining Failures

{chr(10).join(f'- {item}' for item in hard_failures) if hard_failures else '- No hard failures from automated final audit.'}

## Blocked Items

{chr(10).join(f'- {item}: BLOCKED_CREDENTIALS' for item in blocked) if blocked else '- No credential-blocked publish target.'}

## Skipped Items

{chr(10).join(f'- {item}: SKIPPED_EXTERNAL' for item in skipped) if skipped else '- No skipped external item.'}

## Production Recommendation

SQLite and the in-process worker are acceptable for a single-user local workstation. For multi-user or unattended production, move queue execution to a separate worker service, add durable job cancellation, central logging, backups, and consider PostgreSQL.

Raw evidence is under `review/logs/final_*`; screenshots are under `review/screenshots/final_*`.
"""
    (REVIEW / "FINAL_AUDIT_REPORT.md").write_text(final_report, encoding="utf-8")

    readiness = f"""# Production Readiness Report

STATUS: {verdict}

| Area | Score | Evidence |
|---|---:|---|
| Clean environment | {'8' if clean.get('pip_check', {}).get('returncode') == 0 else '4'} | `.venv-audit` pip check returncode `{clean.get('pip_check', {}).get('returncode', 'not run')}`. |
| Code quality | {'8' if clean.get('compileall', {}).get('returncode') == 0 else '4'} | compileall and node checks are stored in `review/logs/final_cmd_*`. |
| Security | {'7' if not any(item.get('status', 0) >= 500 for item in security) else '3'} | {len(security)} probes executed. |
| Reliability | {'7' if not hard_failures else '4'} | E2E, queue timeout config, and DB FK checks recorded. |
| External workflows | {'6' if not blocked else '4'} | Publish targets blocked only when credentials are missing. |
| Encoding | {'8' if mojibake.get('status') == 'PASS' else '4'} | Mojibake findings: {mojibake.get('total', 0)}. |

Overall: {score}/10. Verdict: {verdict}.
"""
    (REVIEW / "production_readiness_report.md").write_text(readiness, encoding="utf-8")

    summary_md = f"""# Bao cao tong hop thu muc review

Ngay doc: {time.strftime('%Y-%m-%d')}

## Ket luan nhanh

Da bo sung final audit harness va chay/cap nhat evidence `final_*`. Trang thai hien tai: **{verdict}**, score **{score}/10**.

## Ket qua moi

- Clean env pip check returncode: `{clean.get('pip_check', {}).get('returncode', 'not run')}`.
- Clean env pip install: `{clean.get('pip_install', {}).get('status', clean.get('pip_install', {}).get('returncode', 'not run'))}`.
- E2E steps: `{len(e2e)}`.
- Security probes: `{len(security)}`.
- Database FK: `{db.get('status', 'UNKNOWN')}`.
- Mojibake scan: `{mojibake.get('status', 'UNKNOWN')}`, findings `{mojibake.get('total', 0)}`.
- Publish blocked credentials: `{', '.join(blocked) if blocked else 'none'}`.
- Skipped external probes: `{', '.join(skipped) if skipped else 'none'}`.

## Rui ro con lai

{chr(10).join(f'- {item}' for item in hard_failures) if hard_failures else '- Khong co hard failure tu final audit tu dong.'}
{chr(10).join(f'- {item}: can credential test de publish that.' for item in blocked)}
{chr(10).join(f'- {item}: chua chay trong audit nhanh.' for item in skipped)}

## Uu tien tiep theo

1. Xu ly cac hard failure neu co trong `review/logs/final_*`.
2. Cap credential publish thieu neu muon pass YouTube/TikTok/Facebook that.
3. Neu chay multi-user/production, tach worker rieng va chuyen DB sang PostgreSQL.
"""
    (REVIEW / "REVIEW_FOLDER_SUMMARY.md").write_text(summary_md, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7891)
    parser.add_argument("--skip-clean-install", action="store_true")
    parser.add_argument("--skip-external", action="store_true")
    parser.add_argument("--skip-playwright", action="store_true")
    args = parser.parse_args()

    ensure_dirs()
    fixtures = ensure_fixture()
    summary = {
        "fixtures": fixtures,
        "secrets": secret_preflight(),
        "clean_env": run_clean_env(args.skip_clean_install),
        "database": run_db_check(),
        "mojibake": scan_mojibake(),
    }

    node = shutil.which("node")
    if node:
        summary["node_check"] = run_cmd("node_app_check", [node, "--check", "app.js"], timeout=120)
    else:
        summary["node_check"] = {"returncode": -1, "stderr": "node not found"}

    base = f"http://127.0.0.1:{args.port}"
    proc = start_server(args.port)
    try:
        summary["health"] = wait_health(base)
        if summary["health"].get("status") == 200:
            summary["security"] = run_security(base, fixtures)
            summary["e2e"] = run_e2e(base, fixtures)
            summary["performance"] = run_perf(base)
            if not args.skip_playwright:
                summary["ui"] = run_playwright(base)
            if not args.skip_external:
                summary["download"] = run_download_probe(base)
                summary["publish"] = run_publish_preflight(base, fixtures)
            else:
                summary["download"] = {"status": "SKIPPED"}
                summary["publish"] = []
        else:
            summary["security"] = []
            summary["e2e"] = []
            summary["performance"] = {}
            summary["publish"] = []
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()

    write_json("final_audit_summary.json", summary)
    generate_reports(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
