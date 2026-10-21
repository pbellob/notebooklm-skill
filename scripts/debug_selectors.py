#!/usr/bin/env python3
"""Ask a question and dump all possible response element selectors."""
import sys, time, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from patchright.sync_api import sync_playwright
from browser_utils import BrowserFactory
from browser_utils import StealthUtils

NOTEBOOK_URL = "https://notebook.google.com/notebook/f3b1dff0-51b6-4cc9-9af3-b6aca4e0cd65"
QUESTION = "¿Qué es un convertidor Buck-Boost y para qué sirve? Responde en 2 líneas."

with sync_playwright() as p:
    context = BrowserFactory.launch_persistent_context(p, headless=False)
    page = context.new_page()
    page.goto(NOTEBOOK_URL, wait_until="domcontentloaded")
    page.wait_for_url(re.compile(r"^https://notebook(?:lm)?\.google\.com/"), timeout=10000)
    time.sleep(4)

    # Type and submit question
    inp = page.wait_for_selector("textarea.query-box-input", timeout=10000, state="visible")
    StealthUtils.human_type(page, "textarea.query-box-input", QUESTION)
    page.keyboard.press("Enter")
    print("Question submitted, waiting 15s for response...")
    time.sleep(15)

    page.screenshot(path="/tmp/after_question.png", full_page=True)
    print("Screenshot: /tmp/after_question.png")

    # Dump ALL text-containing elements that could be the response
    results = page.evaluate("""
    () => {
        const candidates = [];
        const selectors = [
            '.response-container', '.message-content', '.chat-message',
            '.to-user-container', '[class*="message"]', '[class*="response"]',
            '[class*="answer"]', '[class*="chat"]', '[class*="turn"]',
            'message-turn', 'div[data-message]', '.model-response',
            '[class*="model"]', 'nb-chat-turn', 'chat-turn',
            '.markdown-content', '[class*="markdown"]'
        ];
        for (const sel of selectors) {
            const els = document.querySelectorAll(sel);
            for (const el of els) {
                const txt = el.innerText.trim();
                if (txt.length > 50 && txt.length < 3000) {
                    candidates.push({
                        sel,
                        cls: el.className.slice(0, 80),
                        tag: el.tagName,
                        txt: txt.slice(0, 200)
                    });
                }
            }
        }
        return candidates;
    }
    """)
    print(f"\nFound {len(results)} candidate elements:")
    for r in results:
        print(f"\n  sel={r['sel']} tag={r['tag']} cls={r['cls']!r}")
        print(f"  text={r['txt']!r}")

    context.close()
