import os
import json
import base64
import sqlite3
import shutil
import tempfile
from pathlib import Path
import ctypes
from ctypes import wintypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char))
    ]

def decrypt_key_with_dpapi(encrypted_key: bytes) -> bytes:
    """Decrypt the master key using Windows DPAPI."""
    CryptUnprotectData = ctypes.windll.crypt32.CryptUnprotectData
    CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB)
    ]
    CryptUnprotectData.restype = wintypes.BOOL

    in_blob = DATA_BLOB(len(encrypted_key), ctypes.create_string_buffer(encrypted_key))
    out_blob = DATA_BLOB()
    
    res = CryptUnprotectData(
        ctypes.byref(in_blob),
        None, None, None, None, 0,
        ctypes.byref(out_blob)
    )
    
    if not res:
        raise RuntimeError("Failed to decrypt Chrome key via DPAPI")
        
    decrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    return decrypted

def get_master_key(local_state_path: Path) -> bytes:
    """Read and decrypt the master key from the browser's Local State file."""
    if not local_state_path.exists():
        raise FileNotFoundError(f"Local State not found at {local_state_path}")
        
    with open(local_state_path, "r", encoding="utf-8") as f:
        local_state = json.load(f)
        
    encrypted_key_b64 = local_state["os_crypt"]["encrypted_key"]
    encrypted_key = base64.b64decode(encrypted_key_b64)
    
    # Strip DPAPI prefix (first 5 bytes should be b'DPAPI')
    if encrypted_key.startswith(b'DPAPI'):
        encrypted_key = encrypted_key[5:]
        
    return decrypt_key_with_dpapi(encrypted_key)

def decrypt_cookie_val(encrypted_val: bytes, master_key: bytes) -> str:
    """Decrypt cookie values using AES-256-GCM."""
    if not encrypted_val:
        return ""
    try:
        if encrypted_val.startswith(b'v10') or encrypted_val.startswith(b'v11'):
            nonce = encrypted_val[3:15]
            ciphertext = encrypted_val[15:]
            aesgcm = AESGCM(master_key)
            decrypted = aesgcm.decrypt(nonce, ciphertext, None)
            return decrypted.decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""

def chrome_to_unix_epoch(expires_utc: int) -> float:
    """Convert Webkit/Chrome timestamp (microseconds since 1601) to Unix epoch."""
    if expires_utc == 0:
        return 0
    # 11644473600 is microseconds/seconds offset between 1601 and 1970
    unix_time = (expires_utc / 1000000) - 11644473600
    if unix_time < 0:
        return 0
    return unix_time

def grab_browser_cookies(browser_name: str, target_domains: list) -> list:
    """
    Grab and decrypt cookies for specified domains from a given browser on Windows.
    Supported browsers: 'chrome', 'edge'.
    """
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if not local_appdata:
        return []
        
    if browser_name == "chrome":
        base_dir = Path(local_appdata) / "Google" / "Chrome" / "User Data"
    elif browser_name == "edge":
        base_dir = Path(local_appdata) / "Microsoft" / "Edge" / "User Data"
    else:
        return []
        
    local_state_path = base_dir / "Local State"
    if not local_state_path.exists():
        return []
        
    try:
        master_key = get_master_key(local_state_path)
    except Exception as e:
        print(f"Error getting master key for {browser_name}: {e}")
        return []
        
    # Search in Default and all Profile folders
    cookie_paths = []
    
    # 1. Default profile
    default_cookies = base_dir / "Default" / "Network" / "Cookies"
    if default_cookies.exists():
        cookie_paths.append(("Default", default_cookies))
        
    # 2. Other profiles (Profile 1, Profile 2, etc.)
    for path in base_dir.iterdir():
        if path.is_dir() and (path.name.startswith("Profile ") or path.name == "System Profile"):
            p_cookies = path / "Network" / "Cookies"
            if p_cookies.exists():
                cookie_paths.append((path.name, p_cookies))
                
    result_cookies = []
    
    for profile_name, path in cookie_paths:
        # Copy to avoid locking issues if the browser is running
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"temp_{browser_name}_cookies_{profile_name}")
        try:
            shutil.copy2(path, temp_path)
        except Exception:
            continue
            
        try:
            conn = sqlite3.connect(temp_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Construct a SQL query to check domains
            domain_placeholders = " OR ".join(["host_key LIKE ?" for _ in target_domains])
            query = f"SELECT host_key, name, value, path, expires_utc, is_secure, is_httponly, samesite, encrypted_value FROM cookies WHERE {domain_placeholders}"
            
            # Form wildcards for domains (e.g. '.chatgpt.com')
            wildcards = [f"%{d}%" for d in target_domains]
            
            cursor.execute(query, wildcards)
            rows = cursor.fetchall()
            
            for row in rows:
                decrypted_val = decrypt_cookie_val(row["encrypted_value"], master_key)
                val = decrypted_val if decrypted_val else row["value"]
                
                # Expiry date mapping
                exp = chrome_to_unix_epoch(row["expires_utc"])
                
                # SameSite mapping
                same_site_val = "Lax"
                ss = row["samesite"]
                if ss == 0:
                    same_site_val = "None"
                elif ss == 1:
                    same_site_val = "Lax"
                elif ss == 2:
                    same_site_val = "Strict"
                    
                cookie_obj = {
                    "name": row["name"],
                    "value": val,
                    "domain": row["host_key"],
                    "path": row["path"],
                    "secure": bool(row["is_secure"]),
                    "httpOnly": bool(row["is_httponly"]),
                    "sameSite": same_site_val
                }
                if exp > 0:
                    cookie_obj["expires"] = int(exp)
                    
                # De-duplicate: keep newer or replace if same name
                result_cookies.append(cookie_obj)
                
            conn.close()
        except Exception as e:
            print(f"Error reading sqlite db for {browser_name} {profile_name}: {e}")
        finally:
            try:
                os.remove(temp_path)
            except Exception:
                pass
                
    # Filter duplicates by domain + name
    seen = set()
    unique_cookies = []
    for c in result_cookies:
        key = (c["domain"], c["name"])
        if key not in seen:
            seen.add(key)
            unique_cookies.append(c)
            
    return unique_cookies
