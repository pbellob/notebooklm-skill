#!/usr/bin/env python3
"""Debug script to inspect NotebookLM page structure."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from patchright.sync_api import sync_playwright
from browser_utils import BrowserFactory


def debug_page():
    with sync_playwright() as p:
        context = BrowserFactory.launch_persistent_context(p, headless=False)
        page = context.new_page()

        page.goto("https://notebook.google.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        # Find all scrollable containers
        info = page.evaluate("""
        () => {
            // Check for virtual scroll containers
            const vsContainers = Array.from(document.querySelectorAll('cdk-virtual-scroll-viewport'));
            // Check for overflow:auto/scroll containers
            const allEls = Array.from(document.querySelectorAll('*'));
            const scrollable = allEls.filter(el => {
                const s = window.getComputedStyle(el);
                return (s.overflow === 'auto' || s.overflow === 'scroll' ||
                        s.overflowY === 'auto' || s.overflowY === 'scroll') &&
                        el.scrollHeight > el.clientHeight;
            }).map(el => ({
                tag: el.tagName,
                cls: el.className.slice(0, 80),
                scrollHeight: el.scrollHeight,
                clientHeight: el.clientHeight,
                id: el.id
            }));

            // Also check for nav links / tabs
            const navLinks = Array.from(document.querySelectorAll('a, button, [role=tab]'))
                .filter(el => el.innerText.trim().length > 1 && el.innerText.trim().length < 40)
                .map(el => ({ tag: el.tagName, text: el.innerText.trim(), href: el.getAttribute('href') }))
                .slice(0, 30);

            return {
                vsContainers: vsContainers.length,
                scrollableContainers: scrollable.slice(0, 10),
                navLinks
            };
        }
        """)

        print(f"Virtual scroll containers: {info['vsContainers']}")
        print(f"\nScrollable containers ({len(info['scrollableContainers'])}):")
        for c in info['scrollableContainers']:
            print(f"  <{c['tag']} id={c['id']!r} class={c['cls']!r}> scrollH={c['scrollHeight']} clientH={c['clientHeight']}")

        print(f"\nNav links/buttons:")
        for l in info['navLinks']:
            print(f"  {l['tag']}: {l['text']!r}  href={l['href']!r}")

        context.close()


if __name__ == "__main__":
    debug_page()
