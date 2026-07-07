import requests
import json
import traceback
from playwright.sync_api import sync_playwright

def check_chatgpt_cookies_requests(cookies_list: list) -> dict:
    """Check ChatGPT cookies validity using a quick requests call."""
    session = requests.Session()
    # Convert Playwright format to requests format
    for c in cookies_list:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://chatgpt.com/"
    }
    
    try:
        # Check next-auth session endpoint
        resp = session.get("https://chatgpt.com/api/auth/session", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if "accessToken" in data or "user" in data:
                email = data.get("user", {}).get("email", "Unknown")
                return {"status": "live", "message": f"ChatGPT Live (Email: {email})"}
        
        # Fallback: check index page redirects
        resp_index = session.get("https://chatgpt.com/", headers=headers, timeout=10, allow_redirects=True)
        if "auth/login" in resp_index.url:
            return {"status": "die", "message": "ChatGPT Cookies Expired (Redirected to login)"}
            
        # If we didn't get 200 on session but no redirect, check page contents
        if "login-button" in resp_index.text or "Sign in" in resp_index.text:
            return {"status": "die", "message": "ChatGPT Cookies Expired (Login button detected)"}
            
        return {"status": "unknown", "message": "Unable to verify conclusively via API, try Playwright verification."}
    except Exception as e:
        return {"status": "error", "message": f"ChatGPT check error: {str(e)}"}

def check_gemini_cookies_requests(cookies_list: list) -> dict:
    """Check Gemini cookies validity using a quick requests call."""
    session = requests.Session()
    for c in cookies_list:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }
    
    try:
        # Request gemini app home page
        resp = session.get("https://gemini.google.com/app", headers=headers, timeout=10, allow_redirects=True)
        
        if "accounts.google.com" in resp.url or "ServiceLogin" in resp.url or "signin" in resp.url:
            return {"status": "die", "message": "Gemini Cookies Expired (Redirected to Google login)"}
            
        if resp.status_code == 200:
            # Check for common keywords indicating login
            text = resp.text.lower()
            if "sign in" in text or "đăng nhập" in text or "accounts.google" in text:
                return {"status": "die", "message": "Gemini Cookies Expired (Login prompt found)"}
            return {"status": "live", "message": "Gemini Live"}
            
        return {"status": "die", "message": f"Gemini returned status {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "message": f"Gemini check error: {str(e)}"}

def check_cookies_playwright(provider: str, cookies_list: list) -> dict:
    """Check cookies using headless Playwright for ultimate accuracy."""
    try:
        with sync_playwright() as p:
            # Use headless browser
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            
            # Load cookies
            context.add_cookies(cookies_list)
            page = context.new_page()
            
            if provider == "chatgpt":
                page.goto("https://chatgpt.com", timeout=30000)
                page.wait_for_load_state("networkidle")
                
                # Check for textarea or contenteditable chat input
                chat_input = page.locator("textarea, div[contenteditable='true']").first
                login_btn = page.locator("button:has-text('Log in'), a[href*='login']").first
                
                if chat_input.is_visible() and not login_btn.is_visible():
                    browser.close()
                    return {"status": "live", "message": "ChatGPT Live (Playwright verified)"}
                else:
                    browser.close()
                    return {"status": "die", "message": "ChatGPT Cookies Expired (No input/Login visible)"}
                    
            elif provider == "gemini":
                page.goto("https://gemini.google.com/app", timeout=30000)
                page.wait_for_load_state("networkidle")
                
                # Check if we are redirected to accounts.google.com
                url = page.url
                if "accounts.google.com" in url or "ServiceLogin" in url:
                    browser.close()
                    return {"status": "die", "message": "Gemini Cookies Expired (Redirected to Google login)"}
                    
                # Look for chat elements
                chat_input = page.locator("div[contenteditable='true'], textarea").first
                login_btn = page.locator("a:has-text('Sign in'), a[href*='ServiceLogin']").first
                
                if chat_input.is_visible() and not login_btn.is_visible():
                    browser.close()
                    return {"status": "live", "message": "Gemini Live (Playwright verified)"}
                else:
                    browser.close()
                    return {"status": "die", "message": "Gemini Cookies Expired (No chat input or Sign in visible)"}
                    
            browser.close()
            return {"status": "unknown", "message": "Unsupported provider"}
    except Exception as e:
        return {"status": "error", "message": f"Playwright verification failed: {str(e)}"}

def verify_cookie_status(provider: str, cookies_list: list, use_playwright: bool = False) -> dict:
    """Verify cookies status using requests first (fast), then optionally Playwright."""
    if not cookies_list:
        return {"status": "empty", "message": "No cookies configured."}
        
    if use_playwright:
        return check_cookies_playwright(provider, cookies_list)
        
    if provider == "chatgpt":
        res = check_chatgpt_cookies_requests(cookies_list)
        if res["status"] in ("unknown", "error") and not use_playwright:
            # Retry with playwright if requests was inconclusive
            return check_cookies_playwright(provider, cookies_list)
        return res
    elif provider == "gemini":
        res = check_gemini_cookies_requests(cookies_list)
        if res["status"] in ("unknown", "error") and not use_playwright:
            return check_cookies_playwright(provider, cookies_list)
        return res
    else:
        return {"status": "error", "message": f"Unsupported provider {provider}"}
