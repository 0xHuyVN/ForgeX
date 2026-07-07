from ..database import db_cursor


def detect_scenes(video_path: str, threshold: float = 27.0, project_id: int = 0):
    """Detect scene changes using PySceneDetect or FFmpeg fallback."""
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
        
        video = open_video(video_path)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=threshold))
        scene_manager.detect_scenes(video)
        scene_list = scene_manager.get_scene_list()

        with db_cursor() as cur:
            cur.execute("DELETE FROM scenes WHERE project_id=?", (project_id,))
            for i, (start, end) in enumerate(scene_list):
                cur.execute(
                    "INSERT INTO scenes (project_id, scene_index, start_time, end_time) VALUES (?,?,?,?)",
                    (project_id, i + 1, start.get_seconds(), end.get_seconds()),
                )
    except ImportError:
        _ffmpeg_scene_detect(video_path, threshold, project_id)


def _ffmpeg_scene_detect(video_path, threshold, project_id):
    """Fallback: use FFmpeg scene detection."""
    import subprocess

    cmd = [
        "ffmpeg", "-i", video_path,
        "-filter:v", f"select='gt(scene,{threshold/100})',showinfo",
        "-f", "null", "-",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
        times = []
        for line in result.stderr.split("\n"):
            if "pts_time:" in line:
                import re
                m = re.search(r"pts_time:([\d.]+)", line)
                if m:
                    times.append(float(m.group(1)))

        with db_cursor() as cur:
            cur.execute("DELETE FROM scenes WHERE project_id=?", (project_id,))
            prev = 0.0
            for i, t in enumerate(times):
                cur.execute(
                    "INSERT INTO scenes (project_id, scene_index, start_time, end_time) VALUES (?,?,?,?)",
                    (project_id, i + 1, prev, t),
                )
                prev = t
            if times:
                cur.execute(
                    "INSERT INTO scenes (project_id, scene_index, start_time, end_time) VALUES (?,?,?,?)",
                    (project_id, len(times) + 1, prev, prev + 30),
                )
    except Exception:
        pass
