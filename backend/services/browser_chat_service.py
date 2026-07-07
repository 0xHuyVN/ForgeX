import os
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

def run_chatgpt_automation(prompt: str, cookies_list: list, output_folder: str = None) -> str:
    """
    Runs ChatGPT in a headless Playwright browser, loads cookies,
    sends a prompt, waits for response, and returns the response.
    If output_folder is provided, writes the result to a file.
    """
    if not cookies_list:
        raise ValueError("No ChatGPT cookies provided")
        
    with sync_playwright() as p:
        # Launch browser. We can add stealth options if needed, but chromium with standard headers is usually fine with cookies.
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        
        # Load cookies
        context.add_cookies(cookies_list)
        page = context.new_page()
        
        try:
            # Navigate to chatgpt
            page.goto("https://chatgpt.com", timeout=45000)
            page.wait_for_load_state("networkidle")
            
            # Verify we are logged in
            login_btn = page.locator("button:has-text('Log in'), a[href*='login']").first
            if login_btn.is_visible():
                raise RuntimeError("ChatGPT session is expired or invalid. Please update cookies.")
                
            # Wait for the input box
            # ChatGPT uses a textarea with id 'prompt-textarea'
            input_selector = "#prompt-textarea"
            page.wait_for_selector(input_selector, timeout=15000)
            
            # Fill the prompt
            page.fill(input_selector, prompt)
            time.sleep(0.5)
            
            # Press enter to send
            page.press(input_selector, "Enter")
            
            # Wait for generation to start and finish
            # ChatGPT has a stop button while generating: 'button[aria-label="Stop generating"]' or 'button[data-testid="stop-button"]'
            time.sleep(2.0)  # wait for UI to update
            
            stop_btn_selector = 'button[aria-label="Stop generating"], button[data-testid="stop-button"]'
            try:
                # Wait up to 10 seconds for stop button to appear (generation started)
                page.wait_for_selector(stop_btn_selector, timeout=10000, state="visible")
            except Exception:
                # If it doesn't appear, maybe it was super fast or didn't trigger. We will proceed.
                pass
                
            # Now wait for the stop button to disappear (generation finished)
            try:
                page.wait_for_selector(stop_btn_selector, timeout=120000, state="detached")
            except Exception:
                # Fallback: wait for the send button to become visible and enabled again
                send_btn_selector = 'button[data-testid="send-button"]'
                page.wait_for_selector(send_btn_selector, timeout=30000, state="visible")
                
            # Extra wait for safety
            time.sleep(1.5)
            
            # Get the last assistant response
            # ChatGPT responses are inside elements with class 'markdown'
            responses = page.locator(".markdown").all_text_contents()
            if not responses:
                # Try locating by assistant role
                responses = page.locator('div[data-message-author-role="assistant"] .markdown').all_text_contents()
                
            if not responses:
                # Fallback: look for generic assistant message blocks
                responses = page.locator('div[data-message-author-role="assistant"]').all_text_contents()
                
            if not responses:
                raise RuntimeError("Failed to extract assistant response from DOM")
                
            last_response = responses[-1].strip()
            
            # Save if output folder is specified
            if output_folder:
                os.makedirs(output_folder, exist_ok=True)
                out_path = Path(output_folder) / f"chatgpt_result_{int(time.time())}.txt"
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(last_response)
                    
            return last_response
            
        finally:
            browser.close()

def run_gemini_automation(prompt: str, cookies_list: list, output_folder: str = None) -> str:
    """
    Runs Gemini in a headless Playwright browser, loads cookies,
    sends a prompt, waits for response, and returns the response.
    If output_folder is provided, writes the result to a file.
    """
    if not cookies_list:
        raise ValueError("No Gemini cookies provided")
        
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        
        context.add_cookies(cookies_list)
        page = context.new_page()
        
        try:
            # Navigate to gemini app
            page.goto("https://gemini.google.com/app", timeout=45000)
            page.wait_for_load_state("networkidle")
            
            # Verify we are logged in (no redirection to accounts.google.com)
            if "accounts.google.com" in page.url or "ServiceLogin" in page.url:
                raise RuntimeError("Gemini session is expired or invalid. Please update cookies.")
                
            # Input textarea on Gemini is a div with role="textbox" or class contenteditable
            input_selector = "div[contenteditable='true'], textarea"
            page.wait_for_selector(input_selector, timeout=15000)
            
            # Fill the prompt
            page.fill(input_selector, prompt)
            time.sleep(0.5)
            
            # Press enter to send (or we can click send button)
            # Gemini send button is typically inside the input container
            send_btn = page.locator("button[aria-label='Send message'], button.send-button").first
            if send_btn.is_visible():
                send_btn.click()
            else:
                page.press(input_selector, "Enter")
                
            # Wait for Gemini to finish generating
            # Gemini usually shows a loading indicator or progressive text loading.
            # Once it finishes, the stop button disappears or action buttons (like thumbs up) appear.
            # Let's wait for the "Stop" button to become detached or action buttons to become visible.
            time.sleep(3.0)  # Wait for request to register
            
            # Wait for text to finish rendering
            # A robust way is to poll the page and wait for the last message length to stop changing
            last_len = 0
            stable_count = 0
            for _ in range(60):  # max 60 seconds
                time.sleep(1.0)
                # Gemini responses are inside <message-content> or .message-content or .markdown
                responses = page.locator("message-content, .message-content, .markdown").all_text_contents()
                if responses:
                    curr_len = len(responses[-1].strip())
                    if curr_len > 0 and curr_len == last_len:
                        stable_count += 1
                        if stable_count >= 3:  # unchanged for 3 seconds
                            break
                    else:
                        stable_count = 0
                    last_len = curr_len
                else:
                    stable_count = 0
                    
            responses = page.locator("message-content, .message-content, .markdown").all_text_contents()
            if not responses:
                raise RuntimeError("Failed to extract Gemini response from DOM")
                
            last_response = responses[-1].strip()
            
            # Save if output folder is specified
            if output_folder:
                os.makedirs(output_folder, exist_ok=True)
                out_path = Path(output_folder) / f"gemini_result_{int(time.time())}.txt"
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(last_response)
                    
            return last_response
            
        finally:
            browser.close()
