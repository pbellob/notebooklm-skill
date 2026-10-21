#!/usr/bin/env python3
"""
Simple NotebookLM Question Interface
Based on MCP server implementation - simplified without sessions

Implements hybrid auth approach:
- Persistent browser profile (user_data_dir) for fingerprint consistency
- Manual cookie injection from state.json for session cookies (Playwright bug workaround)
See: https://github.com/microsoft/playwright/issues/36139
"""

import argparse
import sys
import time
import re
from pathlib import Path

from patchright.sync_api import sync_playwright

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from auth_manager import AuthManager
from notebook_manager import NotebookLibrary
from config import QUERY_INPUT_SELECTORS, RESPONSE_SELECTORS
from browser_utils import BrowserFactory, StealthUtils


# Follow-up reminder (adapted from MCP server for stateless operation)
# Since we don't have persistent sessions, we encourage comprehensive questions
FOLLOW_UP_REMINDER = (
    "\n\nEXTREMELY IMPORTANT: Is that ALL you need to know? "
    "You can always ask another question! Think about it carefully: "
    "before you reply to the user, review their original request and this answer. "
    "If anything is still unclear or missing, ask me another comprehensive question "
    "that includes all necessary context (since each question opens a new browser session)."
)


def _clear_conversation(page) -> bool:
    """Clear NotebookLM conversation history via UI button."""
    try:
        clicked = page.evaluate("""() => {
            const candidates = Array.from(document.querySelectorAll('button, [role="button"]'));
            const keywords = [
                'clear','borrar','delete conversation','reset',
                'limpiar','nueva conversacion','nueva conversación',
                'new chat','new conversation','sweep','broom',
                'eliminar','vaciar','reiniciar'
            ];
            const btn = candidates.find(b => {
                const label = (
                    (b.getAttribute('aria-label') || '') +
                    (b.getAttribute('title') || '') +
                    (b.textContent || '')
                ).toLowerCase();
                return keywords.some(k => label.includes(k));
            });
            if (btn) { btn.click(); return true; }
            return false;
        }""")
        if clicked:
            time.sleep(1)
            # Confirm dialog if any (press Enter or look for confirm button)
            try:
                confirm = page.wait_for_selector('button:has-text("Clear"), button:has-text("Confirm"), button:has-text("Delete"), button:has-text("OK")', timeout=2000)
                if confirm:
                    confirm.click()
                    time.sleep(1)
            except:
                pass
            print("  🧹 Cleared conversation history")
            return True
    except:
        pass
    return False


def ask_notebooklm(question: str, notebook_url: str, headless: bool = True, read_last: bool = False) -> str:
    """
    Ask a question to NotebookLM

    Args:
        question: Question to ask
        notebook_url: NotebookLM notebook URL
        headless: Run browser in headless mode

    Returns:
        Answer text from NotebookLM
    """
    auth = AuthManager()

    if not auth.is_authenticated():
        print("⚠️ Not authenticated. Run: python auth_manager.py setup")
        return None

    print(f"💬 Asking: {question}")
    print(f"📚 Notebook: {notebook_url}")

    playwright = None
    context = None

    try:
        # Start playwright
        playwright = sync_playwright().start()

        # Launch persistent browser context using factory
        context = BrowserFactory.launch_persistent_context(
            playwright,
            headless=headless
        )

        # Navigate to notebook
        page = context.new_page()
        print("  🌐 Opening notebook...")
        # Visit notebook.google.com first to establish session cookies
        # (UNAL accounts use notebook.google.com via SSO, not notebooklm.google.com)
        page.goto("https://notebook.google.com", wait_until="domcontentloaded")
        if "accounts.google.com" in page.url or "smartkey.xertica.com" in page.url:
            raise Exception("Not authenticated — run auth_manager.py setup")
        # Now navigate to the actual notebook
        page.goto(notebook_url, wait_until="domcontentloaded")

        # Verify we're on NotebookLM (handle both notebook.google.com and notebooklm.google.com)
        current_url = page.url
        print(f"  🔍 Current URL: {current_url}")
        if not re.match(r"^https://notebook(?:lm)?\.google\.com/", current_url):
            raise Exception(f"Unexpected URL after navigation: {current_url}")

        # Give the page extra time to fully render dynamic content
        time.sleep(5)

        # Wait for query input — try each selector with generous timeout
        print("  ⏳ Waiting for query input...")
        query_element = None

        for selector in QUERY_INPUT_SELECTORS:
            try:
                query_element = page.wait_for_selector(
                    selector,
                    timeout=20000,
                    state="visible"
                )
                if query_element:
                    # Verify it's actually interactable (not hidden inside a shadow DOM)
                    try:
                        query_element.scroll_into_view_if_needed(timeout=3000)
                    except:
                        pass
                    print(f"  ✓ Found input: {selector}")
                    break
            except:
                continue

        # If still not found, dump visible textareas for debugging
        if not query_element:
            all_textareas = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('textarea')).map(t => ({
                    class: t.className,
                    placeholder: t.placeholder,
                    ariaLabel: t.getAttribute('aria-label'),
                    visible: t.offsetParent !== null
                }));
            }""")
            print(f"  🔎 Textareas on page: {all_textareas}")
            print("  ❌ Could not find query input")
            return None

        # Navigate to the notebook URL fresh to guarantee a clean conversation
        page.goto(notebook_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        # Re-find query input after reload
        input_el = None
        for sel in QUERY_INPUT_SELECTORS:
            try:
                el = page.wait_for_selector(sel, timeout=10000)
                if el and el.is_visible():
                    input_el = el
                    break
            except Exception:
                continue
        if not input_el:
            print("  ❌ Could not find query input after reload")
            return None

        # Wait for existing turns to fully load (stabilize) before counting
        stable_existing = 0
        last_count = -1
        for _ in range(15):  # up to 30s
            count = len(page.query_selector_all(".to-user-container"))
            if count == last_count:
                stable_existing += 1
                if stable_existing >= 3:
                    break
            else:
                stable_existing = 0
                last_count = count
            time.sleep(2)
        existing_turns = last_count
        print(f"  📊 Existing turns (stable): {existing_turns}")

        # Record content of every existing turn before submitting,
        # so we can detect which one changes after the new question is sent.
        # In read_last mode nothing is sent, so leave the set empty and the
        # wait loop below picks the last turn already on the page.
        pre_submit_texts = set()
        if not read_last:
            for el in page.query_selector_all(".to-user-container"):
                pre_submit_texts.add(el.inner_text().strip())

            # Type question (human-like, fast)
            print("  ⏳ Typing question...")

            # Use primary selector for typing
            input_selector = QUERY_INPUT_SELECTORS[0]
            StealthUtils.human_type(page, input_selector, question)

            # Submit. Enter alone is not reliable (in some UI states it just
            # inserts a newline), so verify the box actually cleared and fall
            # back to clicking the send button.
            typed = page.eval_on_selector(
                input_selector, "el => (el.value || '').length"
            )
            print(f"  ⌨️  Characters in the box: {typed}")

            print("  📤 Submitting...")
            page.keyboard.press("Enter")
            time.sleep(3)

            remaining = page.eval_on_selector(
                input_selector, "el => (el.value || '').trim().length"
            )
            if remaining > 0:
                print("  ⚠️ Enter did not submit; looking for the send button...")
                SUBMIT_SELECTORS = [
                    'button[aria-label="Enviar"]',
                    'button[aria-label="Submit"]',
                    'button[aria-label*="nviar"]',
                    'button[aria-label*="ubmit"]',
                    'button.submit-button',
                    'button:has(mat-icon[data-mat-icon-name="send"])',
                    'button:has-text("send")',
                ]
                clicked = False
                for sel in SUBMIT_SELECTORS:
                    try:
                        btn = page.query_selector(sel)
                        if btn and btn.is_visible() and btn.is_enabled():
                            btn.click()
                            print(f"  ✓ Clicked: {sel}")
                            clicked = True
                            break
                    except Exception:
                        continue
                if not clicked:
                    buttons = page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('button'))
                            .filter(b => b.offsetParent !== null)
                            .map(b => ({
                                label: b.getAttribute('aria-label'),
                                text: (b.textContent || '').trim().slice(0, 30)
                            }))
                            .slice(0, 40);
                    }""")
                    print(f"  🔎 Visible buttons: {buttons}")
                    print("  ❌ Could not submit the question")
                    return None
                time.sleep(3)

            StealthUtils.random_delay(500, 1500)
        else:
            print("  📖 Read-last mode: reading the last answer already in the chat")

        # Wait for response: wait for "keep_pin" or "Guardar" button to appear in a new turn
        # (these buttons only appear when NotebookLM finishes generating a response)
        print("  ⏳ Waiting for answer...")

        answer = None
        stable_count = 0
        last_text = None
        deadline = time.time() + 420  # 7 minutes timeout

        while time.time() < deadline:
            try:
                current_turns = page.query_selector_all(".to-user-container")

                # Pick the LAST turn whose text did not exist before submission
                # (old history turns may shift position, so never compare by index)
                latest = None
                for turn in reversed(current_turns):
                    text = turn.inner_text().strip()
                    if text and text not in pre_submit_texts:
                        latest = turn
                        break

                if latest is None:
                    time.sleep(2)
                    continue

                full_text = latest.inner_text().strip()

                # "keep_pin"/"Guardar" are the UI buttons that appear once
                # generation finishes. They are the fast path, but the labels
                # change with the UI language, so fall back to requiring a
                # longer period with no change in the text.
                gated = ("keep_pin" in full_text) or ("Guardar" in full_text)
                required_stable = 15 if gated else 30  # ~30s gated, ~60s otherwise

                text = full_text

                # Strip "Thoughts\nexpand_more\n" preamble
                if "Thoughts" in text and "expand_more" in text:
                    idx = text.find("expand_more")
                    text = text[idx + len("expand_more"):].strip()

                if len(text) < 30:
                    time.sleep(2)
                    continue

                if text == last_text:
                    stable_count += 1
                    if stable_count >= required_stable:
                        # Expand all collapsed sections before reading final text
                        try:
                            page.evaluate("""() => {
                                const btns = Array.from(document.querySelectorAll('.to-user-container:last-child button, .to-user-container:last-child [role="button"]'));
                                btns.forEach(b => {
                                    const t = (b.textContent || '').trim();
                                    if (t === 'expand_more' || t === 'more_horiz') b.click();
                                });
                            }""")
                            time.sleep(1.5)
                            # Re-read after expanding
                            expanded_text = latest.inner_text().strip()
                            if "Thoughts" in expanded_text and "expand_more" in expanded_text:
                                idx = expanded_text.find("expand_more")
                                expanded_text = expanded_text[idx + len("expand_more"):].strip()
                            answer = expanded_text if len(expanded_text) > len(text) else text
                        except Exception:
                            answer = text
                        break
                else:
                    stable_count = 0
                    last_text = text
            except Exception:
                pass

            time.sleep(2)

        if not answer:
            print("  ❌ Timeout waiting for answer")
            return None

        print("  ✅ Got answer!")
        # Add follow-up reminder to encourage Claude to ask more questions
        return answer + FOLLOW_UP_REMINDER

    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None

    finally:
        # Always clean up
        if context:
            try:
                context.close()
            except:
                pass

        if playwright:
            try:
                playwright.stop()
            except:
                pass


def main():
    parser = argparse.ArgumentParser(description='Ask NotebookLM a question')

    parser.add_argument('--question', help='Question to ask')
    parser.add_argument('--read-last', action='store_true',
                        help='Do not ask anything; read the last answer already in the chat')
    parser.add_argument('--notebook-url', help='NotebookLM notebook URL')
    parser.add_argument('--notebook-id', help='Notebook ID from library')
    parser.add_argument('--show-browser', action='store_true', help='Show browser')

    args = parser.parse_args()

    if not args.question and not args.read_last:
        parser.error('--question is required unless --read-last is used')

    # Resolve notebook URL
    notebook_url = args.notebook_url

    if not notebook_url and args.notebook_id:
        library = NotebookLibrary()
        notebook = library.get_notebook(args.notebook_id)
        if notebook:
            notebook_url = notebook['url']
        else:
            print(f"❌ Notebook '{args.notebook_id}' not found")
            return 1

    if not notebook_url:
        # Check for active notebook first
        library = NotebookLibrary()
        active = library.get_active_notebook()
        if active:
            notebook_url = active['url']
            print(f"📚 Using active notebook: {active['name']}")
        else:
            # Show available notebooks
            notebooks = library.list_notebooks()
            if notebooks:
                print("\n📚 Available notebooks:")
                for nb in notebooks:
                    mark = " [ACTIVE]" if nb.get('id') == library.active_notebook_id else ""
                    print(f"  {nb['id']}: {nb['name']}{mark}")
                print("\nSpecify with --notebook-id or set active:")
                print("python scripts/run.py notebook_manager.py activate --id ID")
            else:
                print("❌ No notebooks in library. Add one first:")
                print("python scripts/run.py notebook_manager.py add --url URL --name NAME --description DESC --topics TOPICS")
            return 1

    # Ask the question
    answer = ask_notebooklm(
        question=args.question or '',
        notebook_url=notebook_url,
        headless=not args.show_browser,
        read_last=args.read_last
    )

    if answer:
        print("\n" + "=" * 60)
        print(f"Question: {args.question or '(last answer in chat)'}")
        print("=" * 60)
        print()
        print(answer)
        print()
        print("=" * 60)
        return 0
    else:
        print("\n❌ Failed to get answer")
        return 1


if __name__ == "__main__":
    sys.exit(main())
