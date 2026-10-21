#!/usr/bin/env python3
"""Script to list all notebooks from NotebookLM main page."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config as cfg
from patchright.sync_api import sync_playwright

def list_notebooks():
    state_file = cfg.STATE_FILE

    with sync_playwright() as p:
        chrome_path = os.environ.get("CHROME_PATH")
        launch_args = {"headless": False, "args": cfg.BROWSER_ARGS}
        if chrome_path:
            launch_args["executable_path"] = chrome_path
        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(storage_state=str(state_file) if state_file.exists() else None)
        page = context.new_page()

        print("🌐 Navigando a NotebookLM...")
        page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        if "accounts.google.com" in page.url or "login" in page.url:
            print("❌ Sesión expirada. Ejecuta auth_manager.py reauth")
            browser.close()
            return

        print(f"✅ En: {page.url}")
        time.sleep(2)

        # Get notebook cards with their links
        cards = page.query_selector_all("mat-card")
        print(f"\n📚 Notebooks encontrados ({len(cards) - 1}):")
        for card in cards:
            link = card.query_selector("a[href*='notebook']")
            if not link:
                continue
            href = link.get_attribute("href")
            url = f"https://notebooklm.google.com{href}" if href and href.startswith("/") else href
            # Get title text - look for the notebook name element
            title_el = card.query_selector(".notebook-title, h3, .title")
            title = title_el.inner_text().strip() if title_el else card.inner_text().strip().split("\n")[1]
            print(f"  - {title}")
            print(f"    URL: {url}")

        # Save screenshot for debugging
        page.screenshot(path="/tmp/notebooklm_main.png")
        print("\n📸 Screenshot guardado en /tmp/notebooklm_main.png")

        browser.close()

if __name__ == "__main__":
    list_notebooks()
