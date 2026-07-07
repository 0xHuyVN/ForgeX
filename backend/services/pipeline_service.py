"""
Pipeline Service — central dispatcher for all processing pipelines.
Types: download, transcribe, translate, tts, render, export_audio, split, process_music, pipeline (full chain)
Each step logs progress and registers outputs in the asset library.
"""
import json
import time
import os
import hashlib
import shutil
from pathlib import Path
from ..config import SUBTITLES_DIR, VOICES_DIR, EXPORTS_DIR, CACHE_DIR
from ..database import db_cursor
from .path_guard import safe_filename, safe_media_input, safe_output_dir, safe_output_path


def _as_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def run_pipeline(queue_item: dict) -> bool:
    """Main dispatcher — routes to correct pipeline based on queue item type."""
    from .job_logger import set_current_job_id, job_log
    item_id = queue_item["id"]
    set_current_job_id(item_id)
    try:
        ptype = queue_item["type"]
        project_id = queue_item["project_id"]
        input_path = queue_item["input_path"] or ""
        params = json.loads(queue_item["params"]) if isinstance(queue_item["params"], str) else (queue_item["params"] or {})
        try:
            from .queue_manager import pop_sensitive_params
            params.update(pop_sensitive_params(item_id))
        except Exception:
            pass

        _update(item_id, "running", 0)
        job_log("info", f"[{ptype}] Pipeline started for project {project_id}")

        try:
            if ptype == "download":
                return _download(item_id, project_id, params)
            elif ptype == "transcribe":
                return _transcribe(item_id, project_id, input_path, params)
            elif ptype == "translate":
                return _translate(item_id, project_id, params)
            elif ptype == "tts":
                return _tts(item_id, project_id, params)
            elif ptype == "render":
                return _render(item_id, project_id, input_path, params)
            elif ptype == "ocr_hardsub":
                return _ocr_hardsub(item_id, project_id, input_path, params)
            elif ptype == "remove_hardsub":
                return _remove_hardsub(item_id, project_id, input_path, params)
            elif ptype == "export_audio":
                return _export_audio(item_id, project_id, input_path, params)
            elif ptype == "split":
                return _split(item_id, project_id, input_path, params)
            elif ptype == "process_music":
                return _process_music(item_id, project_id, input_path, params)
            elif ptype == "duck_music":
                return _duck_music(item_id, project_id, input_path, params)
            elif ptype == "ffmpeg_command":
                return _ffmpeg_command(item_id, project_id, input_path, params)
            elif ptype == "merge_videos":
                return _merge_videos(item_id, project_id, params)
            elif ptype == "auto_reframe":
                return _auto_reframe(item_id, project_id, input_path, params)
            elif ptype == "dynamic_template":
                return _dynamic_template(item_id, project_id, input_path, params)
            elif ptype == "tts_text":
                return _tts_text(item_id, project_id, params)
            elif ptype == "train_voice":
                return _train_voice(item_id, project_id, params)
            elif ptype == "clone_pipeline":
                return _clone_pipeline(item_id, project_id, input_path, params)
            elif ptype == "publish":
                return _publish(item_id, project_id, input_path, params)
            elif ptype == "ai_recap":
                return _ai_recap(item_id, project_id, input_path, params)
            elif ptype == "ai_task":
                return _ai_task(item_id, project_id, input_path, params)
            elif ptype == "scene_detect":
                return _scene_detect(item_id, project_id, input_path, params)
            elif ptype == "extract_subtitle_stream":
                return _extract_subtitle_stream(item_id, project_id, input_path, params)
            elif ptype == "pipeline":
                return _full(item_id, project_id, input_path, params)
            else:
                job_log("error", f"Unknown type: {ptype}")
                _update(item_id, "failed", error=f"Unknown type: {ptype}")
                return False
        except Exception as e:
            job_log("error", f"Pipeline failed: {e}")
            _update(item_id, "failed", error=str(e))
            return False
    finally:
        set_current_job_id(None)


# ─── Download Pipeline ───

def _download(item_id: int, project_id: int, params: dict) -> bool:
    url = params.get("url", "")
    if not url:
        raise ValueError("url required for download pipeline")

    _log(item_id, "info", f"Downloading: {url}")
    from .downloader import download_video

    dl_id = params.get("download_id")
    if not dl_id:
        with db_cursor() as cur:
            cur.execute("INSERT INTO downloads (url, platform, status) VALUES (?,?,?)", (url, params.get("platform", "auto"), "waiting"))
            dl_id = cur.lastrowid

    download_video(dl_id, url, params.get("quality", "best"), params.get("cookie_file"), params.get("proxy"), params.get("output_dir"))

    with db_cursor() as cur:
        row = cur.execute("SELECT * FROM downloads WHERE id=?", (dl_id,)).fetchone()
        if row and row["status"] == "completed" and row["output_path"]:
            out_path = row["output_path"]
            _register_asset("videos", out_path, project_id)
            _log(item_id, "info", f"Downloaded to: {out_path}")
            _set_output_path(item_id, out_path)
            _update(item_id, "completed", 100)
            return True

    _update(item_id, "failed", error="Download failed")
    return False


# ─── Transcribe (STT) Pipeline ───

def _transcribe(item_id: int, project_id: int, video_path: str, params: dict) -> bool:
    if not video_path or not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    video_path = str(safe_media_input(video_path, field="transcribe input"))

    lang = params.get("language", "vi")
    vocal_sep = params.get("vocal_separation", None)
    use_whisperx = params.get("whisperx", False)

    _log(item_id, "info", "Transcribing with Whisper (vocal_separation={}, whisperx={})...".format(vocal_sep, use_whisperx))
    cache_path = _cache_path("transcript", video_path, {"language": lang, "vocal_separation": vocal_sep, "whisperx": use_whisperx}, ".srt")
    if cache_path.exists():
        srt_content = cache_path.read_text(encoding="utf-8")
        srt_path = SUBTITLES_DIR / f"project_{project_id}_stt.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO subtitles (project_id, source, language, content) VALUES (?,?,?,?)",
                (project_id, f"whisper_cache_{lang}", lang, srt_content),
            )
        _register_asset("subtitle", str(srt_path), project_id)
        _set_output_path(item_id, str(srt_path))
        _log(item_id, "info", f"Transcript cache hit: {srt_path}")
        _update(item_id, "completed", 100)
        return True

    from .whisper_stt import transcribe_video
    result = transcribe_video(video_path, lang, project_id, vocal_separation=vocal_sep, use_whisperx=use_whisperx)

    srt_path = result.get("srt_path", "")
    if srt_path and os.path.exists(srt_path):
        _copy_to_cache(srt_path, cache_path)
        _register_asset("subtitle", srt_path, project_id)
        _log(item_id, "info", f"SRT saved: {srt_path}")
        _set_output_path(item_id, srt_path)
        _update(item_id, "completed", 100)
        return True

    _update(item_id, "failed", error="Transcription failed")
    return False


# ─── Translation Pipeline ───

def _translate(item_id: int, project_id: int, params: dict, finalize: bool = True) -> bool:
    src = params.get("source_lang", "zh")
    tgt = params.get("target_lang", "vi")
    engine = params.get("translate_engine", "nllb")
    model = params.get("translate_model")
    semantic_segmentation = bool(params.get("semantic_segmentation", False))
    srt_content = (params.get("srt_content") or "").strip()

    if not srt_content:
        with db_cursor() as cur:
            row = cur.execute(
                "SELECT content FROM subtitles WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()

        if not row:
            raise ValueError("No subtitle found for translation")

        srt_content = row["content"]
    _log(item_id, "info", f"Translating {src}→{tgt} via {engine} ({len(srt_content)} chars)")

    cache_path = _cache_path("translation", srt_content, {"src": src, "tgt": tgt, "engine": engine, "model": model}, ".srt")
    if cache_path.exists():
        translated = cache_path.read_text(encoding="utf-8")
        _log(item_id, "info", "Translation cache hit")
    else:
        from .translator import translate_srt
        translated = translate_srt(
            srt_content,
            src,
            tgt,
            engine,
            model=model,
            semantic_segmentation=semantic_segmentation,
            project_id=project_id,
        )
        cache_path.write_text(translated, encoding="utf-8")

    try:
        from .translator import postprocess_translation_text
        translated = postprocess_translation_text(translated, src, tgt)
    except Exception:
        pass

    trans_path = SUBTITLES_DIR / f"project_{project_id}_translated.srt"
    trans_path.write_text(translated, encoding="utf-8")

    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
            (project_id, f"translated_{engine}_{src}_to_{tgt}", translated),
        )

    _register_asset("subtitle", str(trans_path), project_id)
    _log(item_id, "info", f"Translated SRT: {trans_path}")
    _set_output_path(item_id, str(trans_path))
    if finalize:
        _update(item_id, "completed", 100)
    return True


# ─── TTS Pipeline ───

def _tts(
    item_id: int,
    project_id: int,
    params: dict,
    finalize: bool = True,
    progress_start: int = 20,
    progress_end: int = 95,
) -> bool:
    provider = params.get("tts_provider", "edge")
    voice = params.get("tts_voice", "vi-VN-NamMinhNeural")
    speed = params.get("speed", 1.0)

    with db_cursor() as cur:
        row = cur.execute(
            """SELECT source, content FROM subtitles
               WHERE project_id=?
                 AND source != 'tts_aligned'
                 AND source NOT LIKE 'tts_source%'
                 AND source NOT LIKE 'tts_optimized%'
               ORDER BY created_at DESC, id DESC
               LIMIT 1""",
            (project_id,),
        ).fetchone()

    if not row:
        raise ValueError("No subtitle found for TTS")

    subtitle_content = row["content"]
    subtitle_source = row["source"] or ""
    tts_timeline_strategy = (params.get("tts_timeline_strategy") or "subtitle_fit").strip().lower()
    tts_trim_overflow = _as_bool(params.get("tts_trim_overflow"), False)
    try:
        tts_max_tempo = max(1.0, float(params.get("tts_max_tempo", 2.0) or 2.0))
    except (TypeError, ValueError):
        tts_max_tempo = 2.0
    should_optimize_tts_subtitle = (
        _as_bool(params.get("rewrite_enabled"), False)
        or (
            _as_bool(params.get("tts_allow_shorten"), False)
            and _as_bool(params.get("tts_optimize_subtitles"), False)
        )
    )
    if (
        should_optimize_tts_subtitle
        and not subtitle_source.startswith("tts_optimized")
        and subtitle_source != "tts_aligned"
    ):
        try:
            from .ai_service import optimize_srt_for_tts
            target_lang = params.get("target_lang") or params.get("language") or "vi"
            target_cps = float(params.get("tts_target_cps", 13.0) or 13.0)
            optimized = optimize_srt_for_tts(
                subtitle_content,
                language=target_lang,
                target_cps=target_cps,
                engine=params.get("tts_optimize_engine", "auto"),
                naturalize=_as_bool(params.get("tts_naturalize"), True),
            )
            if optimized and optimized.strip() and optimized != subtitle_content:
                subtitle_content = optimized
                opt_path = SUBTITLES_DIR / f"project_{project_id}_tts_optimized.srt"
                opt_path.write_text(optimized, encoding="utf-8")
                with db_cursor() as cur:
                    cur.execute(
                        "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
                        (project_id, f"tts_optimized_{target_cps:g}cps", optimized),
                    )
                _register_asset("subtitle", str(opt_path), project_id)
                _log(item_id, "info", f"Optimized subtitles for TTS: {opt_path}")
        except Exception as e:
            _log(item_id, "warning", f"Subtitle TTS optimization skipped: {e}")

    align = _as_bool(params.get("tts_align"), True)
    api_key = params.get("fpt_api_key", None)
    tts_output = str(VOICES_DIR / f"project_{project_id}_tts.wav")
    tts_source_path = SUBTITLES_DIR / f"project_{project_id}_tts_source.srt"
    if align:
        tts_source_path.write_text(subtitle_content, encoding="utf-8")
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
                (project_id, "tts_source", subtitle_content),
            )
        _register_asset("subtitle", str(tts_source_path), project_id)
        _log(item_id, "info", f"TTS source subtitles saved: {tts_source_path}")
    cache_path = _cache_path(
        "tts",
        subtitle_content,
        {
            "provider": provider,
            "voice": voice,
            "speed": speed,
            "align": align,
            "timeline_strategy": tts_timeline_strategy,
            "trim_overflow": tts_trim_overflow,
            "max_tempo": tts_max_tempo,
            "timeline_version": 14,
        },
        ".wav",
    )
    aligned_cache_path = cache_path.with_suffix(".srt")

    if cache_path.exists() and (not align or aligned_cache_path.exists()):
        shutil.copy2(cache_path, tts_output)
        _log(item_id, "info", f"TTS cache hit: {tts_output}")
        if align and aligned_cache_path.exists():
            aligned_subtitle = aligned_cache_path.read_text(encoding="utf-8", errors="replace")
            aligned_path = SUBTITLES_DIR / f"project_{project_id}_tts_aligned.srt"
            aligned_path.write_text(aligned_subtitle, encoding="utf-8")
            with db_cursor() as cur:
                cur.execute(
                    "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
                    (project_id, "tts_aligned", aligned_subtitle),
                )
            _register_asset("subtitle", str(aligned_path), project_id)
            try:
                from .timeline_service import sync_timeline_subtitle
                sync_timeline_subtitle(project_id, aligned_subtitle)
            except Exception as e:
                _log(item_id, "warning", f"Failed to sync cached TTS-aligned subtitles to timeline: {e}")
    elif align:
        if cache_path.exists() and not aligned_cache_path.exists():
            _log(item_id, "warning", "Ignoring stale TTS cache because aligned subtitle timing cache is missing.")
        _log(item_id, "info", f"Generating Timeline-aligned TTS via {provider}")
        from .tts_engine import synthesize_timeline
        def _tts_progress(done, total):
            if total:
                span = max(1, progress_end - progress_start)
                pct = progress_start + min(span, int((done / total) * span))
                _update(item_id, "running", pct)
        aligned_subtitle = synthesize_timeline(
            subtitle_content,
            provider,
            voice,
            speed,
            tts_output,
            api_key=api_key,
            progress_cb=_tts_progress,
            strategy=tts_timeline_strategy,
            trim_overflow=tts_trim_overflow,
            max_tempo=tts_max_tempo,
        )
        if aligned_subtitle and aligned_subtitle.strip():
            aligned_path = SUBTITLES_DIR / f"project_{project_id}_tts_aligned.srt"
            aligned_path.write_text(aligned_subtitle, encoding="utf-8")
            with db_cursor() as cur:
                cur.execute(
                    "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
                    (project_id, "tts_aligned", aligned_subtitle),
                )
            _register_asset("subtitle", str(aligned_path), project_id)
            try:
                aligned_cache_path.write_text(aligned_subtitle, encoding="utf-8")
            except Exception as e:
                _log(item_id, "warning", f"Failed to cache TTS-aligned subtitle: {e}")
            try:
                from .timeline_service import sync_timeline_subtitle
                sync_timeline_subtitle(project_id, aligned_subtitle)
            except Exception as e:
                _log(item_id, "warning", f"Failed to sync TTS-aligned subtitles to timeline: {e}")
            _log(item_id, "info", f"TTS-aligned subtitles saved: {aligned_path}")
    else:
        text = _extract_text_from_srt(subtitle_content)
        _log(item_id, "info", f"Generating flat TTS via {provider} ({len(text)} chars)")
        from .tts_engine import synthesize
        synthesize(text, provider, voice, speed, tts_output, api_key=api_key)

    if os.path.exists(tts_output):
        _copy_to_cache(tts_output, cache_path)
        _register_asset("voice", tts_output, project_id)
        try:
            from .timeline_service import sync_timeline_voice
            sync_timeline_voice(project_id, tts_output)
        except Exception as e:
            _log(item_id, "warning", f"Failed to sync voice to timeline: {e}")
        _log(item_id, "info", f"TTS saved: {tts_output}")
        _set_output_path(item_id, tts_output)
        if finalize:
            _update(item_id, "completed", 100)
        return True

    _update(item_id, "failed", error="TTS generation failed")
    return False


# ─── Render Pipeline ───

def _ocr_hardsub(item_id: int, project_id: int, video_path: str, params: dict) -> bool:
    if not video_path or not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    video_path = str(safe_media_input(video_path, field="ocr input"))

    _log(item_id, "info", "Running RapidOCR hard-sub extraction")
    _update(item_id, "running", 10)
    from .ocr_service import extract_hard_subtitles

    result = extract_hard_subtitles(video_path, project_id, params.get("region") or params.get("subtitle_region"))
    if result.get("error"):
        _update(item_id, "failed", error=result["error"])
        return False

    out = result.get("srt_path", "")
    if out:
        _set_output_path(item_id, out)
        _register_asset("subtitle", out, project_id)
    _update(item_id, "completed", 100)
    return True


def _remove_hardsub(item_id: int, project_id: int, video_path: str, params: dict) -> bool:
    if not video_path or not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    video_path = str(safe_media_input(video_path, field="hardsub input"))

    output_name = Path(params.get("output_name") or f"project_{project_id}_hardsub_blur.mp4").stem
    output_dir = safe_output_dir(params.get("output_dir") or (EXPORTS_DIR / f"project_{project_id}"), field="hardsub output_dir")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(safe_output_path(output_dir / f"{output_name}.mp4", field="hardsub output", extensions={".mp4"}))
    region = params.get("region") or params.get("subtitle_region") or {"x": 0.0, "y": 0.75, "width": 1.0, "height": 0.25}

    _log(item_id, "info", "Removing hard subtitle by blur mask")
    _update(item_id, "running", 15)
    from .ffmpeg_utils import blur_subtitle_region

    ok = blur_subtitle_region(video_path, out_path, region)
    if not ok or not os.path.exists(out_path):
        _update(item_id, "failed", error="Hard-sub blur failed")
        return False

    _set_output_path(item_id, out_path)
    _register_asset("videos", out_path, project_id)
    _update(item_id, "completed", 100)
    return True


def _render(
    item_id: int,
    project_id: int,
    video_path: str,
    params: dict,
    finalize: bool = True,
    progress_start: int = 10,
    progress_end: int = 95,
) -> bool:
    render_started_at = time.perf_counter()
    if not video_path or not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    video_path = str(safe_media_input(video_path, field="render input"))

    output_name = params.get("output_name", f"output_{project_id}")
    output_format = str(params.get("format", "mp4")).lower().lstrip(".") or "mp4"
    output_dir = params.get("output_dir")
    if output_dir and len(output_dir.strip(" .:\\/")) > 0 and output_dir != "........":
        export_dir = safe_output_dir(output_dir, field="render output_dir")
    else:
        export_dir = EXPORTS_DIR / f"project_{project_id}"
    export_dir.mkdir(parents=True, exist_ok=True)
    output_stem = Path(output_name).stem
    final_output = str(safe_output_path(
        export_dir / f"{output_stem}.{output_format}",
        field="render output",
        extensions={output_format},
    ))

    _log(item_id, "info", f"Rendering video to {final_output} (Single Pass Optimization)")

    # Step 1: Prepare subtitle for burn and/or video stretch timing.
    burn = _as_bool(params.get("burn_subtitle"), True)
    needs_stretch_timing = _as_bool(params.get("extend_video_to_tts"), False)
    srt_path = None
    source_srt_path = None
    if burn or needs_stretch_timing:
        with db_cursor() as cur:
            row = cur.execute(
                """SELECT source, content FROM subtitles
                   WHERE project_id=?
                   ORDER BY
                     CASE
                       WHEN ? AND source='tts_aligned' THEN 0
                       WHEN NOT ? AND source='tts_source' THEN 0
                       WHEN source LIKE 'tts_optimized%' THEN 1
                       WHEN source LIKE 'translated_%' THEN 2
                       ELSE 3
                     END,
                     created_at DESC,
                     id DESC
                   LIMIT 1""",
                (project_id, 1 if needs_stretch_timing else 0, 1 if needs_stretch_timing else 0),
            ).fetchone()
            source_row = cur.execute(
                """SELECT source, content FROM subtitles
                   WHERE project_id=?
                     AND source != 'tts_aligned'
                   ORDER BY
                     CASE
                       WHEN source='tts_source' THEN 0
                       WHEN source LIKE 'tts_optimized%' THEN 1
                       WHEN source LIKE 'translated_%' THEN 2
                       ELSE 3
                     END,
                     created_at DESC,
                     id DESC
                   LIMIT 1""",
                (project_id,),
            ).fetchone()
        if row:
            srt_path = str(SUBTITLES_DIR / f"project_{project_id}_burn.srt")
            Path(srt_path).write_text(row["content"], encoding="utf-8")
            _log(item_id, "info", f"Prepared subtitles for render timing/burn from {row['source'] or 'unknown'}")
        if source_row:
            source_srt_path = str(SUBTITLES_DIR / f"project_{project_id}_source_for_stretch.srt")
            Path(source_srt_path).write_text(source_row["content"], encoding="utf-8")
            _log(item_id, "info", f"Prepared source subtitles for stretch from {source_row['source'] or 'unknown'}")

    render_params = dict(params)
    render_params["project_id"] = project_id
    if srt_path:
        render_params["subtitle_path"] = srt_path
    if source_srt_path:
        render_params["source_subtitle_path"] = source_srt_path

    _update(item_id, "running", progress_start)

    # Step 2: Render in single pass
    from .ffmpeg_utils import single_pass_render
    final_path = Path(final_output)
    pending_dir = CACHE_DIR / "render_pending" / f"project_{project_id}"
    pending_dir.mkdir(parents=True, exist_ok=True)
    encoded_tmp = str(pending_dir / f"{final_path.stem}_{item_id}_encoded{final_path.suffix}")
    subtitle_cache_material = ""
    if srt_path and os.path.exists(srt_path):
        subtitle_cache_material = Path(srt_path).read_text(encoding="utf-8", errors="ignore")
    cacheable_render = str(render_params.get("quality", "")).lower() == "draft" or _as_bool(render_params.get("cache_render"), False)
    render_cache_path = _cache_path(
        "render",
        video_path,
        {"params": _stable_params(render_params), "subtitle": subtitle_cache_material, "render_version": 5},
        f".{output_format}",
    )
    if cacheable_render and render_cache_path.exists():
        shutil.copy2(render_cache_path, encoded_tmp)
        _log(item_id, "info", f"Render cache hit: {encoded_tmp}")
        _update(item_id, "running", progress_end)
        _run_render_quality_gate(item_id, project_id, encoded_tmp, params, srt_path, source_srt_path)
        os.replace(encoded_tmp, final_output)
        try:
            from .telemetry_service import record_event
            record_event("render_completed", {
                "project_id": project_id,
                "queue_item_id": item_id,
                "cached": True,
                "duration_ms": int((time.perf_counter() - render_started_at) * 1000),
                "output_path": final_output,
            })
        except Exception:
            pass
        return _finalize_render(item_id, project_id, video_path, final_output, output_format, finalize)

    def _render_progress(pct):
        mapped = progress_start + int((max(0, min(100, pct)) / 100) * max(1, progress_end - progress_start))
        _update(item_id, "running", mapped)
    
    if not single_pass_render(video_path, encoded_tmp, render_params, progress_cb=_render_progress):
        raise RuntimeError(f"FFmpeg single_pass_render failed for {video_path}")
        
    if not os.path.exists(encoded_tmp):
        raise RuntimeError(f"single_pass_render output not created: {encoded_tmp}")

    _run_render_quality_gate(item_id, project_id, encoded_tmp, params, srt_path, source_srt_path)
    os.replace(encoded_tmp, final_output)
    if cacheable_render:
        _copy_to_cache(final_output, render_cache_path)
    _update(item_id, "running", progress_end)

    # Clean up subtitle file if created
    if srt_path and os.path.exists(srt_path):
        try:
            os.remove(srt_path)
        except Exception:
            pass
    if source_srt_path and os.path.exists(source_srt_path):
        try:
            os.remove(source_srt_path)
        except Exception:
            pass

    try:
        from .telemetry_service import record_event
        record_event("render_completed", {
            "project_id": project_id,
            "queue_item_id": item_id,
            "cached": False,
            "duration_ms": int((time.perf_counter() - render_started_at) * 1000),
            "output_path": final_output,
            "format": output_format,
        })
    except Exception:
        pass

    return _finalize_render(item_id, project_id, video_path, final_output, output_format, finalize)


# ─── Full Pipeline (download → transcribe → translate → tts → render) ───

# Render quality gate helpers
def _run_render_quality_gate(
    item_id: int,
    project_id: int,
    rendered_path: str,
    params: dict,
    subtitle_path: str | None = None,
    source_subtitle_path: str | None = None,
) -> dict:
    if not _as_bool(params.get("quality_gate"), True):
        _log(item_id, "info", "Render quality gate skipped by params")
        return {"ok": True, "status": "SKIPPED", "summary": {}}

    from .quality_checker import run_quality_check

    target = str(params.get("quality_target") or params.get("target") or "short_9_16")
    strict_pass_only = _as_bool(params.get("quality_gate_strict"), False)
    explicit_subtitle_path = params.get("quality_subtitle_path") or params.get("subtitle_path")
    check_subtitle_path = explicit_subtitle_path or subtitle_path
    aligned_subtitle_path = params.get("aligned_subtitle_path") or subtitle_path

    _log(item_id, "info", f"Checking render quality gate target={target}")
    result = run_quality_check(
        rendered_path,
        subtitle_path=check_subtitle_path,
        font_path=params.get("font_path"),
        target=target,
        source_subtitle_path=params.get("source_subtitle_path") or source_subtitle_path,
        aligned_subtitle_path=aligned_subtitle_path,
    )
    status = result.get("status", "UNKNOWN")
    summary = result.get("summary") or {}
    _log(
        item_id,
        "info",
        "Render quality gate result: "
        f"{status} ({summary.get('errors', 0)} errors, "
        f"{summary.get('warnings', 0)} warnings, {summary.get('infos', 0)} infos)",
    )

    issues = result.get("issues") or []
    for issue in issues[:8]:
        severity = str(issue.get("severity") or "info").lower()
        level = "error" if severity == "error" else "warning" if severity == "warning" else "info"
        _log(item_id, level, f"Quality {issue.get('code', 'ISSUE')}: {issue.get('message', '')}")
    if len(issues) > 8:
        _log(item_id, "info", f"Quality gate omitted {len(issues) - 8} additional issue(s)")

    blocked = not result.get("ok", False) or (strict_pass_only and status != "PASS")
    if blocked:
        if not _as_bool(params.get("keep_failed_quality_output"), False):
            try:
                os.remove(rendered_path)
            except OSError:
                pass
        top_errors = [x.get("message", "") for x in issues if x.get("severity") == "error"]
        reason = "; ".join([x for x in top_errors if x][:3]) or f"status={status}"
        raise RuntimeError(f"Render quality gate failed: {reason}")

    return result


# Full Pipeline (download -> transcribe -> translate -> tts -> render)
def _full(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    _log(item_id, "info", "Full pipeline started")

    # Step 1: Download if URL provided
    video_path = input_path
    if video_path and not params.get("url"):
        video_path = str(safe_media_input(video_path, field="pipeline input"))
    if params.get("url"):
        _log(item_id, "info", "Step 1/5: Downloading video...")
        _update(item_id, "running", 5)
        from .downloader import download_video
        with db_cursor() as cur:
            cur.execute("INSERT INTO downloads (url, platform, status) VALUES (?,?,?)",
                        (params["url"], params.get("platform", "auto"), "waiting"))
            dl_id = cur.lastrowid
        download_video(dl_id, params["url"], params.get("quality", "best"), params.get("cookie_file"), params.get("proxy"), params.get("output_dir"))
        with db_cursor() as cur:
            row = cur.execute("SELECT * FROM downloads WHERE id=?", (dl_id,)).fetchone()
            if row and row["output_path"]:
                video_path = row["output_path"]
                _register_asset("videos", video_path, project_id)
    _update(item_id, "running", 20)

    # Step 2: Transcribe (skip if SRT already exists for this project)
    with db_cursor() as cur:
        has_srt = cur.execute(
            "SELECT 1 FROM subtitles WHERE project_id=? LIMIT 1", (project_id,)
        ).fetchone() is not None
    if has_srt:
        _log(item_id, "info", "Step 2/5: Skipping transcription (SRT already loaded)")
    else:
        _log(item_id, "info", "Step 2/5: Transcribing audio...")
        if video_path and os.path.exists(video_path):
            from .ffmpeg_utils import extract_audio
            from .whisper_stt import transcribe
            audio_path = extract_audio(video_path)
            lang = params.get("language", "vi")
            cache_path = _cache_path("transcript", audio_path, {"language": lang, "whisperx": params.get("whisperx", False)}, ".srt")
            if cache_path.exists():
                srt_content = cache_path.read_text(encoding="utf-8")
                srt_path = str(SUBTITLES_DIR / f"project_{project_id}_stt.srt")
                Path(srt_path).write_text(srt_content, encoding="utf-8")
                with db_cursor() as cur:
                    cur.execute(
                        "INSERT INTO subtitles (project_id, source, language, content) VALUES (?,?,?,?)",
                        (project_id, f"whisper_cache_{lang}", lang, srt_content),
                    )
                _log(item_id, "info", f"Transcript cache hit: {srt_path}")
            else:
                result = transcribe(audio_path, lang, project_id)
                srt_path = result.get("srt_path", "")
                _copy_to_cache(srt_path, cache_path)
            _register_asset("subtitle", srt_path, project_id)
    _update(item_id, "running", 40)

    # Step 3: Translate
    src = params.get("source_lang", "vi")
    tgt = params.get("target_lang", "vi")
    if src != tgt and _as_bool(params.get("translate_enabled"), True):
        _log(item_id, "info", f"Step 3/5: Translating {src}→{tgt}...")
        _translate(item_id, project_id, params, finalize=False)
    elif src != tgt:
        _log(item_id, "info", "Step 3/5: Skipping translation (disabled)")
    else:
        _log(item_id, "info", "Step 3/5: Skipping translation (same language)")
    _update(item_id, "running", 60)

    # Step 4: TTS
    if params.get("tts_enabled", True):
        _log(item_id, "info", "Step 4/5: Generating voice...")
        try:
            _tts(item_id, project_id, params, finalize=False, progress_start=60, progress_end=75)
        except Exception as e:
            _log(item_id, "warning", f"TTS step failed (continuing): {e}")
    _update(item_id, "running", 75)

    # Step 5: Render
    _log(item_id, "info", "Step 5/5: Rendering final video...")
    try:
        _render(item_id, project_id, video_path, params, finalize=False, progress_start=75, progress_end=98)
    except Exception as e:
        _log(item_id, "error", f"Render step failed: {e}")
        _update(item_id, "failed", error=str(e))
        return False

    _log(item_id, "info", "Full pipeline complete!")
    _update(item_id, "completed", 100)
    return True


# ─── Export Audio ───

def _export_audio(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    if not input_path or not os.path.exists(input_path):
        raise FileNotFoundError(f"Input not found: {input_path}")
    fmt = params.get("format", "mp3")
    out = str(safe_output_path(
        params.get("output_path") or (EXPORTS_DIR / f"audio_{project_id}_{int(time.time())}.{fmt}"),
        field="audio output",
        extensions={fmt},
    ))
    _log(item_id, "info", f"Exporting audio to {out}")
    from .ffmpeg_utils import export_audio
    if not export_audio(input_path, out, fmt):
        raise RuntimeError("Audio export failed")
    if not os.path.exists(out):
        raise RuntimeError(f"Audio export output not created: {out}")
    _register_asset("audio", out, project_id)
    _set_output_path(item_id, out)
    _update(item_id, "completed", 100)
    return True


# ─── Split Video ───

def _split(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    if not input_path or not os.path.exists(input_path):
        raise FileNotFoundError(f"Input not found: {input_path}")
    start = params.get("start", 0)
    end = params.get("end", 10)
    out = str(safe_output_path(
        params.get("output_path") or input_path.replace(".mp4", f"_part_{int(start)}-{int(end)}.mp4"),
        field="split output",
        extensions={Path(input_path).suffix or ".mp4"},
    ))
    _log(item_id, "info", f"Splitting {start}-{end} → {out}")
    from .ffmpeg_utils import split_video
    split_video(input_path, out, start, end)
    _register_asset("videos", out, project_id)
    _set_output_path(item_id, out)
    _update(item_id, "completed", 100)
    return True


# ─── Process Music ───

def _process_music(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    if not input_path or not os.path.exists(input_path):
        raise FileNotFoundError(f"Input not found: {input_path}")
    input_path = str(safe_media_input(input_path, field="music input", extensions={".mp3", ".wav", ".m4a", ".flac", ".ogg"}))
    _log(item_id, "info", "Processing music track...")
    from .audio_processor import process_music
    out = process_music(input_path, params.get("volume", 1.0), params.get("fade_in", 0), params.get("fade_out", 0), params.get("normalize", False))
    _register_asset("audio", out, project_id)
    try:
        from .timeline_service import sync_timeline_music
        sync_timeline_music(project_id, out)
    except Exception as e:
        _log(item_id, "warning", f"Failed to sync music to timeline: {e}")
    _set_output_path(item_id, out)
    _update(item_id, "completed", 100)
    return True


def _duck_music(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    music_path = input_path or params.get("music_path", "")
    voice_path = params.get("voice_path", "")
    if not voice_path and project_id:
        candidate = VOICES_DIR / f"project_{project_id}_tts.wav"
        if candidate.exists():
            voice_path = str(candidate)
    if not music_path or not os.path.exists(music_path):
        raise FileNotFoundError(f"Music not found: {music_path}")
    if not voice_path or not os.path.exists(voice_path):
        raise FileNotFoundError(f"Voice not found: {voice_path}")
    music_path = str(safe_media_input(music_path, field="music path", extensions={".mp3", ".wav", ".m4a", ".flac", ".ogg"}))
    voice_path = str(safe_media_input(voice_path, field="voice path", extensions={".mp3", ".wav", ".m4a", ".flac", ".ogg"}))
    _log(item_id, "info", "Auto ducking music under voice")
    _update(item_id, "running", 10)
    from .audio_processor import auto_duck
    out = auto_duck(music_path, voice_path)
    _register_asset("audio", out, project_id)
    try:
        from .timeline_service import sync_timeline_music
        sync_timeline_music(project_id, out)
    except Exception as e:
        _log(item_id, "warning", f"Failed to sync music to timeline: {e}")
    _set_output_path(item_id, out)
    _update(item_id, "completed", 100)
    return True


def _ffmpeg_command(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    cmd = params.get("cmd") or []
    output_path = params.get("output_path", "")
    category = params.get("category", "videos")
    if not cmd:
        raise ValueError("cmd required for ffmpeg_command")
    if not output_path:
        raise ValueError("output_path required for ffmpeg_command")
    if input_path:
        safe_media_input(input_path, field="ffmpeg input")
    if output_path:
        output_path = str(safe_output_path(output_path, field="ffmpeg output"))
        params["output_path"] = output_path
        cmd = [output_path if str(arg) == str(params.get("output_path")) else arg for arg in cmd]
    if str(cmd[-1]) != output_path:
        raise ValueError("ffmpeg_command output_path must be the final command argument")
    temp_files = {str(Path(p).expanduser().resolve()) for p in (params.get("temp_files") or [])}
    for idx, arg in enumerate(cmd[:-1]):
        if str(arg) == "-i" and idx + 1 < len(cmd):
            candidate = str(cmd[idx + 1])
            resolved_candidate = str(Path(candidate).expanduser().resolve())
            if resolved_candidate in temp_files:
                continue
            safe_media_input(
                candidate,
                field="ffmpeg input",
                extensions={
                    ".3gp", ".aac", ".aif", ".aiff", ".avi", ".flac", ".m4a",
                    ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".ogg", ".opus",
                    ".png", ".jpg", ".jpeg", ".webp", ".wav", ".webm", ".wmv",
                },
            )
    _log(item_id, "info", f"Running FFmpeg command: {output_path or input_path}")
    _update(item_id, "running", 10)
    from .ffmpeg_utils import run_ffmpeg, get_video_info
    duration = 0
    if input_path and os.path.exists(input_path):
        duration = float(get_video_info(input_path).get("duration", 0) or 0)

    def _progress(pct):
        _update(item_id, "running", 10 + int((max(0, min(100, pct)) / 100) * 85))

    try:
        if not run_ffmpeg(cmd, progress_cb=_progress if duration > 0 else None, duration=duration):
            raise RuntimeError("FFmpeg command failed")
        if output_path and os.path.exists(output_path):
            _register_asset(category, output_path, project_id)
            _set_output_path(item_id, output_path)
        _update(item_id, "completed", 100)
        return True
    finally:
        for temp_file in params.get("temp_files") or []:
            try:
                Path(temp_file).unlink(missing_ok=True)
            except Exception:
                pass


def _merge_videos(item_id: int, project_id: int, params: dict) -> bool:
    file_paths = params.get("file_paths") or []
    output_path = params.get("output_path", "")
    if not file_paths or not output_path:
        raise ValueError("file_paths and output_path required")
    file_paths = [str(safe_media_input(p, field="merge input")) for p in file_paths]
    output_path = str(safe_output_path(output_path, field="merge output", extensions={".mp4", ".mkv", ".mov"}))
    _log(item_id, "info", f"Merging {len(file_paths)} video files")
    _update(item_id, "running", 10)
    from .ffmpeg_utils import merge_videos
    if not merge_videos(file_paths, output_path):
        raise RuntimeError("Merge failed")
    _register_asset("videos", output_path, project_id)
    _set_output_path(item_id, output_path)
    _update(item_id, "completed", 100)
    return True


def _auto_reframe(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    video_path = str(safe_media_input(input_path, field="auto-reframe input"))
    output_path = str(safe_output_path(
        params.get("output_path") or (EXPORTS_DIR / f"project_{project_id}" / f"{Path(video_path).stem}_reframe_9x16.mp4"),
        field="auto-reframe output",
        extensions={".mp4"},
    ))
    _log(item_id, "info", f"Auto-reframing video to {output_path}")
    _update(item_id, "running", 10)
    from .auto_reframe_service import render_auto_reframe
    result = render_auto_reframe(
        video_path,
        output_path,
        int(params.get("width", 1080)),
        int(params.get("height", 1920)),
        int(params.get("fps", 30)),
    )
    _register_asset("videos", result["output"], project_id)
    _set_output_path(item_id, result["output"])
    _update(item_id, "completed", 100)
    return True


def _dynamic_template(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    template_name = params.get("template_name")
    if not template_name:
        raise ValueError("template_name required")
    output_path = params.get("output_path") or ""
    if output_path:
        output_path = str(safe_output_path(output_path, field="template output", extensions={".mp4", ".mov", ".mkv"}))
    _log(item_id, "info", f"Rendering dynamic template: {template_name}")
    _update(item_id, "running", 10)
    from .dynamic_template_service import render_dynamic_template
    result = render_dynamic_template(
        project_id,
        input_path,
        template_name,
        output_path,
        params.get("overrides") or {},
    )
    _register_asset("videos", result["output"], project_id)
    _set_output_path(item_id, result["output"])
    _update(item_id, "completed", 100)
    return True


def _tts_text(item_id: int, project_id: int, params: dict) -> bool:
    text = params.get("text", "")
    if not text:
        raise ValueError("text required for tts_text")
    provider = params.get("provider", "edge")
    voice = params.get("voice", "vi-VN-NamMinhNeural")
    speed = params.get("speed", 1.0)
    out = str(safe_output_path(
        params.get("output_path") or (VOICES_DIR / f"tts_{_hash_text(text)[:16]}.wav"),
        field="tts output",
        extensions={".wav", ".mp3", ".m4a"},
    ))
    cache_path = _cache_path("tts_text", text, {"provider": provider, "voice": voice, "speed": speed}, Path(out).suffix or ".wav")
    _update(item_id, "running", 10)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        shutil.copy2(cache_path, out)
        _log(item_id, "info", f"TTS text cache hit: {out}")
    elif src != tgt:
        _log(item_id, "info", "Step 3/5: Skipping translation (disabled)")
    else:
        if cache_path.exists():
            cache_path.unlink(missing_ok=True)
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists() and out_path.stat().st_size == 0:
            out_path.unlink(missing_ok=True)
        from .tts_engine import synthesize
        synthesize(text, provider, voice, speed, out, api_key=params.get("api_key"))
        if os.path.exists(out) and os.path.getsize(out) > 0:
            _copy_to_cache(out, cache_path)
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise RuntimeError("TTS output not created")
    _register_asset("voice", out, project_id)
    _set_output_path(item_id, out)
    _update(item_id, "completed", 100)
    return True


def _train_voice(item_id: int, project_id: int, params: dict) -> bool:
    sample_path = params.get("sample_path", "")
    name = safe_filename(params.get("name", ""), field="clone name")
    if not sample_path or not name:
        raise ValueError("sample_path and name required")
    sample_path = str(safe_media_input(sample_path, field="sample path", extensions={".wav", ".mp3", ".m4a", ".flac", ".ogg"}))
    _log(item_id, "info", f"Training voice clone: {name}")
    _update(item_id, "running", 10)
    from .voice_clone import train_clone
    train_clone(sample_path, name)
    _set_output_path(item_id, str(VOICES_DIR / "clones" / name))
    _update(item_id, "completed", 100)
    return True


def _clone_pipeline(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    sample_path = params.get("sample_path", "")
    name = safe_filename(params.get("clone_name") or params.get("name") or "oneclick", field="clone name")
    engine = (params.get("clone_engine") or "f5").lower()
    if not sample_path:
        raise ValueError("sample_path required")
    sample_path = str(safe_media_input(sample_path, field="sample path", extensions={".wav", ".mp3", ".m4a", ".flac", ".ogg"}))
    _log(item_id, "info", f"Preparing clone voice profile: {engine}:{name}")
    _update(item_id, "running", 5)
    if engine == "f5":
        from .voice_clone import train_f5_clone
        train_f5_clone(sample_path, name, ref_text=params.get("ref_text", ""))
    else:
        from .voice_clone import train_clone
        train_clone(sample_path, name)
    pipeline_params = dict(params)
    pipeline_params["tts_provider"] = "clone"
    pipeline_params["tts_voice"] = name
    return _full(item_id, project_id, input_path, pipeline_params)


def _publish(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    video_path = input_path or params.get("video_path", "")
    platform = params.get("platform", "")
    if not video_path or not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    video_path = str(safe_media_input(video_path, field="publish input"))
    _log(item_id, "info", f"Publishing/exporting for {platform}")
    _update(item_id, "running", 10)
    from .publish_service import (
        preflight_publish_credentials,
        publish_facebook,
        publish_tiktok,
        publish_youtube,
    )
    readiness = preflight_publish_credentials(platform)
    _log(item_id, "info", f"Publish credential preflight: {platform} ready={readiness.get('ready')}")
    if not readiness.get("ready"):
        required = ", ".join(readiness.get("required", []))
        raise RuntimeError(f"BLOCKED_CREDENTIALS: {platform} requires {required}")
    if platform == "youtube":
        result = publish_youtube(video_path, params.get("title", "My Video"), params.get("description", ""), params.get("privacy", "private"), project_id=project_id)
    elif platform == "tiktok":
        result = publish_tiktok(video_path, params.get("title", "My Video"), params.get("description", ""), project_id=project_id)
    elif platform == "facebook":
        result = publish_facebook(video_path, params.get("title", "My Video"), params.get("description", ""), project_id=project_id)
    else:
        raise ValueError(f"Unknown publish platform: {platform}")
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "Publish failed")
    output = result.get("output") or result.get("url") or result.get("id") or video_path
    _set_output_path(item_id, str(output))
    _update(item_id, "completed", 100)
    return True


def _ai_recap(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    transcript = params.get("text", "")
    style = params.get("style", "review")
    language = params.get("language", "vi")

    if not transcript:
        with db_cursor() as cur:
            row = cur.execute(
                "SELECT content FROM subtitles WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        if row:
            transcript = row["content"]

    if not transcript and input_path:
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Video not found: {input_path}")
        input_path = str(safe_media_input(input_path, field="recap input"))
        _log(item_id, "info", "No subtitle text found, transcribing input for recap")
        _update(item_id, "running", 20)
        from .whisper_stt import transcribe_file
        transcript = transcribe_file(input_path)

    if not transcript:
        raise ValueError("No transcript text or video_path available for recap")

    cache_path = _cache_path("ai_recap", transcript, {"style": style, "language": language}, ".txt")
    if cache_path.exists():
        recap = cache_path.read_text(encoding="utf-8")
        _log(item_id, "info", "AI recap cache hit")
    else:
        _update(item_id, "running", 60)
        from .ai_service import generate_recap_from_transcript
        recap = generate_recap_from_transcript(transcript, style, language)
        cache_path.write_text(recap, encoding="utf-8")

    out_dir = CACHE_DIR / "ai"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"project_{project_id}_recap.txt"
    out_path.write_text(recap, encoding="utf-8")
    _set_output_path(item_id, str(out_path))
    _log(item_id, "info", f"Recap saved: {out_path}")
    _update(item_id, "completed", 100)
    return True


def _ai_task(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    task = params.get("task", "")
    _log(item_id, "info", f"Running AI task: {task}")
    _update(item_id, "running", 10)
    if input_path:
        input_path = str(safe_media_input(input_path, field="ai input"))

    from .ai_service import (
        detect_characters,
        detect_speakers,
        generate_hashtags,
        generate_summary,
        generate_title,
    )

    if task == "summary":
        result = {
            "summary": generate_summary(
                params.get("text", ""),
                int(params.get("max_length", 200)),
                params.get("engine", "gpt"),
            ) or "Summary generation unavailable"
        }
    elif task == "characters":
        if not input_path or not os.path.exists(input_path):
            raise FileNotFoundError(f"Video not found: {input_path}")
        raw = detect_characters(input_path)
        chars = []
        for i, c in enumerate(raw[:20]):
            chars.append({
                "name": f"Character_{i + 1}",
                "confidence": round(float(c.get("confidence", 0)), 3),
                "frame": c.get("frame", 0),
                "bbox": {"x": c.get("x", 0), "y": c.get("y", 0), "w": c.get("w", 0), "h": c.get("h", 0)},
            })
        if not chars:
            chars.append({"name": "No faces detected", "confidence": 0, "frame": 0, "bbox": {"x": 0, "y": 0, "w": 0, "h": 0}})
        result = {"characters": chars}
    elif task == "speakers":
        if not input_path or not os.path.exists(input_path):
            raise FileNotFoundError(f"Video not found: {input_path}")
        speaker_map = {}
        for s in detect_speakers(input_path)[:20]:
            spk = s.get("speaker", "unknown")
            speaker_map.setdefault(spk, []).append({"start": s.get("start", 0), "end": s.get("end", 0)})
        if not speaker_map:
            speaker_map["No speakers detected"] = []
        result = {"speakers": speaker_map}
    elif task == "title":
        title = generate_title(input_path or "", params.get("style", "review"))
        result = {"titles": [title, f"{title} - Phan tich chi tiet", f"{title} - Danh gia chan thuc"]}
    elif task == "hashtags":
        result = {"hashtags": generate_hashtags(params.get("text", ""), int(params.get("count", 5))) or ["review", "movie", "phim"]}
    else:
        raise ValueError(f"Unknown AI task: {task}")

    out_dir = CACHE_DIR / "ai"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"project_{project_id}_{task}_{item_id}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _set_output_path(item_id, str(out_path))
    _log(item_id, "info", f"AI task saved: {out_path}")
    _update(item_id, "completed", 100)
    return True


def _scene_detect(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    if not input_path or not os.path.exists(input_path):
        raise FileNotFoundError(f"Input not found: {input_path}")
    input_path = str(safe_media_input(input_path, field="scene input"))
    _log(item_id, "info", "Detecting scenes...")
    _update(item_id, "running", 10)
    from .scene_detect import detect_scenes
    detect_scenes(input_path, params.get("threshold", 27.0), project_id)
    _set_output_path(item_id, f"scenes://project/{project_id}")
    _update(item_id, "completed", 100)
    return True


def _extract_subtitle_stream(item_id: int, project_id: int, input_path: str, params: dict) -> bool:
    if not input_path or not os.path.exists(input_path):
        raise FileNotFoundError(f"Video not found: {input_path}")
    input_path = str(safe_media_input(input_path, field="subtitle stream input"))
    stream_index = params.get("stream_index", 0)
    output_path = str(safe_output_path(
        params.get("output_path") or (SUBTITLES_DIR / f"sub_{project_id}_{Path(input_path).stem}_extracted_{stream_index}.srt"),
        field="subtitle stream output",
        extensions={".srt", ".ass", ".vtt"},
    ))
    _log(item_id, "info", f"Extracting subtitle stream {stream_index}")
    _update(item_id, "running", 10)

    from .ffmpeg_utils import run_ffmpeg
    cmd = ["-i", input_path, "-map", f"0:{stream_index}", output_path]
    ok = run_ffmpeg(cmd, timeout=120)
    if not ok or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        cmd = ["-i", input_path, "-map", f"0:{stream_index}", "-f", "srt", output_path]
        ok = run_ffmpeg(cmd, timeout=120)
    if not ok or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Subtitle stream extraction failed")

    content = Path(output_path).read_text(encoding="utf-8", errors="replace")
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO subtitles (project_id, source, content) VALUES (?,?,?)",
            (project_id, f"extracted_{stream_index}", content),
        )
    _register_asset("subtitle", output_path, project_id)
    _set_output_path(item_id, output_path)
    _update(item_id, "completed", 100)
    return True


# ─── Helpers ───

def _update(item_id: int, status: str, progress: float = None, error: str = None):
    from .queue_manager import update_item_status
    update_item_status(item_id, status, progress, error)


def _set_output_path(item_id: int, output_path: str):
    with db_cursor() as cur:
        cur.execute("UPDATE queue_items SET output_path=? WHERE id=?", (output_path, item_id))


def _log(item_id: int, level: str, message: str):
    from .job_logger import job_log
    job_log(level, message)


def _extract_text_from_srt(srt_content: str) -> str:
    lines = []
    for line in srt_content.strip().split("\n"):
        line = line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        lines.append(line)
    return " ".join(lines)


def _register_asset(category: str, file_path: str, project_id: int = 0):
    """Register a pipeline output in the assets table."""
    if not file_path or not os.path.exists(file_path):
        return
    try:
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO assets (type, name, path, size) VALUES (?,?,?,?)",
                (category, Path(file_path).name, file_path, os.path.getsize(file_path)),
            )
    except Exception:
        pass


def _finalize_render(item_id: int, project_id: int, video_path: str, final_output: str, output_format: str, finalize: bool) -> bool:
    file_size = os.path.getsize(final_output)
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO exports (project_id, input_path, output_path, format, file_size, status) VALUES (?,?,?,?,?,?)",
            (project_id, video_path, final_output, output_format, file_size, "completed"),
        )
    _register_asset("videos", final_output, project_id)
    _log(item_id, "info", f"Render complete: {final_output}")
    _set_output_path(item_id, final_output)
    if finalize:
        _update(item_id, "completed", 100)
    return True


def _cache_path(namespace: str, material, params: dict = None, suffix: str = ".cache") -> Path:
    cache_dir = CACHE_DIR / "pipeline_cache" / namespace
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    material_is_existing_path = False
    if isinstance(material, os.PathLike):
        material_is_existing_path = os.path.exists(material)
    elif isinstance(material, str) and len(material) < 260 and "\n" not in material and "\r" not in material:
        try:
            material_is_existing_path = os.path.exists(material)
        except OSError:
            material_is_existing_path = False
    if material_is_existing_path:
        path = Path(material)
        stat = path.stat()
        digest.update(str(path.resolve()).encode("utf-8", errors="ignore"))
        digest.update(str(stat.st_size).encode())
        digest.update(str(stat.st_mtime_ns).encode())
    else:
        digest.update(str(material).encode("utf-8", errors="ignore"))
    digest.update(json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
    safe_suffix = suffix if str(suffix).startswith(".") else f".{suffix}"
    return cache_dir / f"{digest.hexdigest()}{safe_suffix}"


def _copy_to_cache(src: str, dst: Path):
    if src and os.path.exists(src):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _stable_params(params: dict) -> dict:
    ignored = {"output_name", "output_dir", "project_id"}
    return {k: v for k, v in (params or {}).items() if k not in ignored and not str(k).endswith("_path")}


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
