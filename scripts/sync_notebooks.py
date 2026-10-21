#!/usr/bin/env python3
"""Sync all notebooks from NotebookLM into the local library."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from patchright.sync_api import sync_playwright
from browser_utils import BrowserFactory
from notebook_manager import NotebookLibrary


def extract_notebooks(page) -> list:
    """Extract all notebook titles and URLs from the rendered page via JS."""
    return page.evaluate("""
    () => {
        const links = Array.from(document.querySelectorAll("a[href*='/notebook/']"));
        return links.map(link => {
            const href = link.getAttribute('href');
            // aria-labelledby points to "project-{uuid}-title" element
            const labelledBy = link.getAttribute('aria-labelledby') || '';
            const titleId = labelledBy.split(' ').find(id => id.endsWith('-title'));
            let title = '';
            if (titleId) {
                const titleEl = document.getElementById(titleId);
                if (titleEl) title = titleEl.innerText.trim();
            }
            // Fallback: walk up card and find title-ish element
            if (!title) {
                let el = link.parentElement;
                for (let i = 0; i < 8; i++) {
                    if (!el) break;
                    const t = el.querySelector('[class*="title"], [id*="title"], h3, h2');
                    if (t && t.innerText.trim()) { title = t.innerText.trim(); break; }
                    el = el.parentElement;
                }
            }
            return { href, title: title || '(sin título)' };
        });
    }
    """)


def sync_notebooks(headless: bool = True):
    library = NotebookLibrary()

    print("🌐 Abriendo NotebookLM...")
    with sync_playwright() as p:
        context = BrowserFactory.launch_persistent_context(p, headless=headless)
        page = context.new_page()

        page.goto("https://notebook.google.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        if "accounts.google.com" in page.url or "login" in page.url.lower():
            print("❌ Sesión expirada. Ejecuta: python scripts/run.py auth_manager.py setup")
            context.close()
            return

        print(f"✅ Cargando notebooks desde: {page.url}")
        time.sleep(2)

        # Click "Ver más" / "See more" button until it disappears
        for _ in range(30):
            ver_mas = page.query_selector("button:has-text('Ver más'), button:has-text('See more'), button:has-text('ver más')")
            if not ver_mas:
                break
            try:
                ver_mas.click()
                time.sleep(1.5)
            except Exception:
                break

        # Also scroll to load any lazy items
        for _ in range(5):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)

        notebooks = extract_notebooks(page)
        context.close()

    # Deduplicate by href
    seen = {}
    for nb in notebooks:
        if nb["href"] not in seen:
            seen[nb["href"]] = nb
    notebooks = list(seen.values())

    if not notebooks:
        print("⚠️  No se encontraron notebooks.")
        return

    print(f"\n📚 Encontrados {len(notebooks)} notebooks. Sincronizando...\n")

    added = 0
    skipped = 0
    for nb in notebooks:
        title = nb["title"]
        href = nb["href"]
        url = f"https://notebook.google.com{href}" if href.startswith("/") else href
        nb_id = title.lower().replace(' ', '-').replace('_', '-')

        existing = library.get_notebook(nb_id)
        if existing:
            print(f"  ⏭️  Ya existe: {title}")
            skipped += 1
            continue

        try:
            library.add_notebook(
                url=url,
                name=title,
                description=f"Notebook de NotebookLM: {title}",
                topics=["notebooklm"],
            )
            print(f"  ✅ Agregado: {title}")
            added += 1
        except ValueError as e:
            print(f"  ⏭️  {e}")
            skipped += 1

    print(f"\n✅ Sincronización completa: {added} nuevos, {skipped} ya existían.")
    print(f"📚 Total en biblioteca: {len(library.list_notebooks())}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-browser", action="store_true")
    args = parser.parse_args()
    sync_notebooks(headless=not args.show_browser)
