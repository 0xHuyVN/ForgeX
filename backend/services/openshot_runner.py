import os
import subprocess
import tempfile
from pathlib import Path

OPENSHOT_CLI_PATH = r"C:\Program Files\OpenShot Video Editor\openshot-qt-cli.exe"

def is_openshot_available() -> bool:
    """Check if the native OpenShot CLI with C++ bindings is installed and accessible."""
    return os.path.exists(OPENSHOT_CLI_PATH)

def run_script_in_openshot(script_path: str, extra_env: dict = None) -> subprocess.CompletedProcess:
    """Run a Python script file inside the native OpenShot Python 3.8 environment."""
    if not is_openshot_available():
        raise FileNotFoundError(f"OpenShot CLI not found at: {OPENSHOT_CLI_PATH}")
    
    script_abs_path = os.path.abspath(script_path)
    if not os.path.exists(script_abs_path):
        raise FileNotFoundError(f"Script file not found: {script_abs_path}")
        
    env = os.environ.copy()
    env["OPENSHOT_RENDER_SCRIPT"] = script_abs_path
    if extra_env:
        env.update(extra_env)
        
    # Use CREATE_NO_WINDOW on Windows to prevent pop-up consoles
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NO_WINDOW
        
    result = subprocess.run(
        [OPENSHOT_CLI_PATH],
        env=env,
        capture_output=True,
        text=True,
        creationflags=creation_flags
    )
    return result

def run_code_in_openshot(code: str, extra_env: dict = None) -> subprocess.CompletedProcess:
    """Run inline Python code inside the native OpenShot Python 3.8 environment."""
    temp_dir = Path(tempfile.gettempdir())
    temp_file = temp_dir / "openshot_runner_temp.py"
    
    # Write the code to a temp file
    temp_file.write_text(code, encoding="utf-8")
    
    try:
        result = run_script_in_openshot(str(temp_file), extra_env)
        return result
    finally:
        # Clean up temp file
        if temp_file.exists():
            try:
                temp_file.unlink()
            except:
                pass
