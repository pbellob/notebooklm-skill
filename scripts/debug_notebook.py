#!/usr/bin/env python3
"""Screenshot the notebook to inspect its current state."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from patchright.sync_api import sync_playwright
from browser_utils import BrowserFactory

with sync_playwright() as p:
    context = BrowserFactory.launch_persistent_context(p, headless=False)
    page = context.new_page()
    page.goto("https://notebook.google.com/notebook/f3b1dff0-51b6-4cc9-9af3-b6aca4e0cd65", wait_until="domcontentloaded")
    time.sleep(5)
    page.screenshot(path="/tmp/notebook_state.png", full_page=True)
    print("Screenshot: /tmp/notebook_state.png")

    # Check for AI notes / system prompts
    ai_notes = page.query_selector_all("[class*='note'], [class*='instruction'], [class*='system']")
    print(f"AI note elements found: {len(ai_notes)}")

    # Get all visible text panels
    panels = page.evaluate("""
    () => {
        const panels = document.querySelectorAll('.sources-panel, .chat-panel, [class*=note-item], [class*=ai-note]');
        return Array.from(panels).map(p => p.innerText.slice(0, 200));
    }
    """)
    for i, t in enumerate(panels):
        print(f"Panel {i}: {t!r}")

    context.close()
