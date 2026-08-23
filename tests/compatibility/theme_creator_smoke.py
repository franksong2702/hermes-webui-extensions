#!/usr/bin/env python3
"""Real-browser Configure compatibility smoke for the Theme Creator extension.

This is an entry-owned smoke against one independently pinned Core checkout. It
does not send a chat or contact a provider. The browser guard is deny-by-default
for off-origin HTTP/WebSocket traffic and all evidence is written to the
requested compatibility evidence directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

try:
    from browser_smoke import (
        CompatibilityFailure,
        EXPECTED_CORE_BASELINE_REQUESTS,
        SetupFailure,
        _assert_browser_health,
        _install_network_guards,
        _start_server,
        _terminate,
        _write_json,
    )
except ModuleNotFoundError:  # pragma: no cover - supports module execution.
    from tests.compatibility.browser_smoke import (
        CompatibilityFailure,
        EXPECTED_CORE_BASELINE_REQUESTS,
        SetupFailure,
        _assert_browser_health,
        _install_network_guards,
        _start_server,
        _terminate,
        _write_json,
    )


REPO_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_VIEWPORT = {"width": 1440, "height": 1000}
MOBILE_VIEWPORT = {"width": 390, "height": 844}
EXTENSION_RESOURCES = (
    "/extensions/theme-creator/assets/theme-creator.js",
    "/extensions/theme-creator/assets/theme-creator.css",
)
CONFIGURE_SELECTOR = '#extensionsInstalled [data-extension-configure-id="theme-creator"]'
DIAGNOSTICS_SELECTOR = '#extensionsDiagnostics [data-extension-configure-id="theme-creator"]'
RAIL_SELECTOR = "#hwxThemeCreatorRailBtn"
PANEL_SELECTOR = "#hwxThemeCreatorPanel"
ACTIVE_CORE_THEME_STYLESHEET = (
    "https://cdn.jsdelivr.net/npm/prismjs@1.29.0/themes/prism-tomorrow.min.css"
)
FLOW_REQUEST_LIMITS = {
    url: (3 if url == ACTIVE_CORE_THEME_STYLESHEET else 1)
    for url in EXPECTED_CORE_BASELINE_REQUESTS
}
SAVED_THEME = {
    "key": "custom-saved",
    "name": "Saved Theme",
    "base": {
        "bg": "#0d0d1a",
        "surface": "#16161f",
        "text": "#f5f5f5",
        "muted": "#9aa0b5",
        "accent": "#f5c542",
        "border": "#2a2a3a",
        "userBubble": "#26314a",
        "bgImage": None,
        "glassOpacity": 0.08,
        "blur": 20,
    },
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--core-dir",
        default=os.environ.get("HERMES_CORE_DIR", ""),
        help="independent Hermes WebUI Core checkout (or HERMES_CORE_DIR)",
    )
    parser.add_argument(
        "--extension-root",
        default=os.environ.get("HERMES_EXTENSION_ROOT", str(REPO_ROOT / "extensions")),
        help="extension source root (default: this checkout's extensions/)",
    )
    parser.add_argument(
        "--evidence-dir",
        default=os.environ.get(
            "COMPATIBILITY_EVIDENCE_DIR",
            str(REPO_ROOT / ".compatibility-evidence"),
        ),
        help="directory for screenshots, server log, and result JSON",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("HERMES_COMPATIBILITY_PORT", "0") or "0"),
        help="optional fixed non-production port; 0 chooses a free ephemeral port",
    )
    return parser.parse_args()


def _new_page(browser: Any, viewport: dict[str, int], init_script: str) -> tuple[Any, Any, dict[str, list[dict[str, Any]]], list[dict[str, str]], list[str]]:
    context = browser.new_context(
        viewport=viewport,
        is_mobile=viewport == MOBILE_VIEWPORT,
        service_workers="block",
    )
    context.add_init_script(init_script)
    network_events = _install_network_guards(context)
    page = context.new_page()
    console_errors: list[dict[str, str]] = []
    page_errors: list[str] = []

    def on_console(message: Any) -> None:
        if message.type != "error":
            return
        location = getattr(message, "location", {}) or {}
        location_url = location.get("url", "") if isinstance(location, dict) else getattr(location, "url", "")
        console_errors.append({"text": str(message.text), "url": str(location_url)})

    page.on("console", on_console)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    return context, page, network_events, console_errors, page_errors


def _boot_page(page: Any, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=30_000)
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass
    for resource in EXTENSION_RESOURCES:
        page.wait_for_function(
            "resource => performance.getEntriesByType('resource').some(entry => entry.name.includes(resource))",
            arg=resource,
            timeout=15_000,
        )
    page.locator(".messages-shell").wait_for(state="visible", timeout=15_000)
    if page.locator(RAIL_SELECTOR).count() != 0:
        raise CompatibilityFailure("Theme Creator created a legacy rail button")
    page.wait_for_function(
        "() => window.HermesThemeCreatorExtension && window.HermesThemeCreatorExtension.version === '0.3.6'",
        timeout=15_000,
    )


def _open_extensions_installed(page: Any) -> Any:
    button = page.locator(CONFIGURE_SELECTOR)
    if button.count() and button.is_visible():
        return button
    settings_button = page.locator('button[data-panel="settings"]').first
    if not settings_button.is_visible():
        menu = page.locator("#btnHamburger")
        menu.wait_for(state="visible", timeout=5_000)
        menu.click()
        page.locator(".sidebar.mobile-open").wait_for(state="visible", timeout=5_000)
        settings_button = page.locator('.sidebar.mobile-open button[data-panel="settings"]').first
    settings_button.click()
    page.locator("#panelSettings").wait_for(state="visible", timeout=10_000)
    page.locator('#settingsMenu button[data-settings-section="extensions"]').click()
    page.locator("#settingsPaneExtensions").wait_for(state="visible", timeout=10_000)
    page.locator('button[data-extensions-tab="installed"]').click()
    page.locator("#extensionsInstalled .extension-installed-list").wait_for(
        state="visible", timeout=10_000
    )
    button.wait_for(state="visible", timeout=15_000)
    return button


def _assert_settled(page: Any, label: str) -> None:
    page.locator(PANEL_SELECTOR).wait_for(state="detached", timeout=5_000)
    try:
        page.wait_for_function(
            """selector => {
              const button = document.querySelector(selector);
              const state = window.HermesExtensionSettings
                && window.HermesExtensionSettings._configureStateForExtension('theme-creator');
              return button && !button.disabled && button.getAttribute('aria-busy') === 'false'
                && state && state.pending === false;
            }""",
            arg=CONFIGURE_SELECTOR,
            timeout=10_000,
        )
    except Exception as exc:
        raise CompatibilityFailure(f"Configure {label} close did not settle Core state") from exc


def _consume_core_theme_refresh(
    network_events: dict[str, list[dict[str, Any]]],
    start_index: int,
    checkpoint: str,
    request_limits: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Consume one bounded exact pinned-Core request set for a known skin change."""

    events = network_events.setdefault("unexpected_http", [])
    if start_index < 0 or start_index > len(events):
        raise CompatibilityFailure(
            f"{checkpoint}: invalid Theme Creator network checkpoint: {start_index}"
        )
    candidates = events[start_index:]
    allowed_counts = request_limits or {
        url: 1 for url in EXPECTED_CORE_BASELINE_REQUESTS
    }
    seen_counts: dict[str, int] = {}
    accepted: list[dict[str, Any]] = []
    for event in candidates:
        url = str(event.get("url", ""))
        expected = EXPECTED_CORE_BASELINE_REQUESTS.get(url)
        is_exact_core_refresh = bool(
            expected
            and event.get("method") == "GET"
            and event.get("resource_type") == expected[0]
            and event.get("occurrence") == expected[1] + 1
        )
        seen_counts[url] = seen_counts.get(url, 0) + 1
        if (
            not is_exact_core_refresh
            or seen_counts[url] > allowed_counts.get(url, 0)
        ):
            raise CompatibilityFailure(
                f"{checkpoint}: unexpected browser egress during Core theme refresh: {event!r}"
            )
        accepted.append({**event, "checkpoint": checkpoint})
    del events[start_index:]
    network_events.setdefault("expected_core_theme_refresh_http", []).extend(accepted)
    return accepted


def _close_context_once(context: Any, state: dict[str, bool]) -> None:
    """Close a browser context at most once, including from an error path."""

    if state.get("closed"):
        return
    state["closed"] = True
    context.close()


def _close_panel(page: Any, method: str) -> None:
    if method == "x":
        page.locator(f"{PANEL_SELECTOR} .hwx-tc-x").click()
    elif method == "escape":
        page.keyboard.press("Escape")
    elif method == "backdrop":
        page.locator(PANEL_SELECTOR).evaluate(
            "panel => panel.dispatchEvent(new MouseEvent('click', {bubbles: true}))"
        )
    else:  # pragma: no cover - guarded by the caller's fixed list.
        raise AssertionError(f"unknown close method: {method}")


def _assert_keyboard_modal(page: Any) -> None:
    state = page.evaluate(
        """selector => {
          const panel = document.querySelector(selector);
          const dialog = panel && panel.querySelector('[role="dialog"]');
          const controls = panel ? Array.from(panel.querySelectorAll(
            'button:not([disabled]), select:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
          )).filter(control => !control.hidden && control.getClientRects().length) : [];
          return {
            modal: dialog && dialog.getAttribute('aria-modal'),
            initialFocusInName: !!(document.activeElement && document.activeElement.matches('.hwx-tc-name')),
            controlCount: controls.length,
          };
        }""",
        PANEL_SELECTOR,
    )
    if state.get("modal") != "true" or state.get("initialFocusInName") is not True:
        raise CompatibilityFailure(f"Theme Creator modal/focus contract failed: {state!r}")
    if state["controlCount"] < 2:
        raise CompatibilityFailure(f"Theme Creator focus trap has too few controls: {state!r}")
    page.evaluate(
        """selector => {
          const panel = document.querySelector(selector);
          const controls = Array.from(panel.querySelectorAll(
            'button:not([disabled]), select:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
          )).filter(control => !control.hidden && control.getClientRects().length);
          controls[controls.length - 1].focus();
        }""",
        PANEL_SELECTOR,
    )
    page.keyboard.press("Tab")
    forward_wrapped = page.evaluate(
        """selector => {
          const panel = document.querySelector(selector);
          const controls = Array.from(panel.querySelectorAll(
            'button:not([disabled]), select:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
          )).filter(control => !control.hidden && control.getClientRects().length);
          return document.activeElement === controls[0];
        }""",
        PANEL_SELECTOR,
    )
    if not forward_wrapped:
        raise CompatibilityFailure("Theme Creator forward Tab escaped the modal")
    page.keyboard.press("Shift+Tab")
    reverse_wrapped = page.evaluate(
        """selector => {
          const panel = document.querySelector(selector);
          const controls = Array.from(panel.querySelectorAll(
            'button:not([disabled]), select:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
          )).filter(control => !control.hidden && control.getClientRects().length);
          return document.activeElement === controls[controls.length - 1];
        }""",
        PANEL_SELECTOR,
    )
    if not reverse_wrapped:
        raise CompatibilityFailure("Theme Creator reverse Tab escaped the modal")


def _exercise_configure(page: Any, screenshot: Path | None = None) -> dict[str, Any]:
    button = _open_extensions_installed(page)
    installed_buttons = page.locator(CONFIGURE_SELECTOR).count()
    page.locator('button[data-extensions-tab="diagnostics"]').click()
    page.locator("#extensionsDiagnostics .extension-installed-list").wait_for(
        state="visible", timeout=10_000
    )
    diagnostics_buttons = page.locator(DIAGNOSTICS_SELECTOR).count()
    page.locator('button[data-extensions-tab="installed"]').click()
    button.wait_for(state="visible", timeout=10_000)
    if installed_buttons != 1 or diagnostics_buttons != 0:
        raise CompatibilityFailure(
            f"Theme Creator Configure visibility mismatch: installed={installed_buttons}, diagnostics={diagnostics_buttons}"
        )
    button.evaluate(
        """button => {
          window.__themeCreatorFocusRestoreCount = 0;
          if (!button.__themeCreatorFocusPatched) {
            const nativeFocus = button.focus.bind(button);
            button.focus = (...args) => {
              window.__themeCreatorFocusRestoreCount += 1;
              return nativeFocus(...args);
            };
            button.__themeCreatorFocusPatched = true;
          }
        }"""
    )
    collection_before = page.evaluate("() => localStorage.getItem('hermes-ext-custom-themes')")
    close_settlements: list[str] = []
    for index, method in enumerate(("x", "escape", "backdrop")):
        button = _open_extensions_installed(page)
        button.wait_for(state="visible", timeout=10_000)
        button.click()
        page.locator(PANEL_SELECTOR).wait_for(state="visible", timeout=5_000)
        page.wait_for_function(
            """selector => {
              const button = document.querySelector(selector);
              const state = window.HermesExtensionSettings
                && window.HermesExtensionSettings._configureStateForExtension('theme-creator');
              return button && button.disabled && button.getAttribute('aria-busy') === 'true'
                && state && state.pending === true;
            }""",
            arg=CONFIGURE_SELECTOR,
            timeout=10_000,
        )
        if index == 0:
            _assert_keyboard_modal(page)
            reuse = page.evaluate(
                """selector => {
                  const before = document.querySelector(selector);
                  window.HermesThemeCreatorExtension.open();
                  const state = window.HermesExtensionSettings
                    && window.HermesExtensionSettings._configureStateForExtension('theme-creator');
                  return {
                    samePanel: before === document.querySelector(selector),
                    panelCount: document.querySelectorAll(selector).length,
                    pending: !!(state && state.pending),
                    focusedName: !!(document.activeElement && document.activeElement.matches('.hwx-tc-name')),
                  };
                }""",
                PANEL_SELECTOR,
            )
            if reuse != {"samePanel": True, "panelCount": 1, "pending": True, "focusedName": True}:
                raise CompatibilityFailure(f"Configure-owned programmatic reuse failed: {reuse!r}")
            page.evaluate("selector => document.querySelector(selector)?.click()", CONFIGURE_SELECTOR)
            page.wait_for_timeout(50)
            if page.locator(PANEL_SELECTOR).count() != 1:
                raise CompatibilityFailure("second Configure click created a duplicate Theme Creator editor")
        preview = page.locator(f"{PANEL_SELECTOR} .hwx-tc-preview")
        preview.click()
        page.wait_for_function(
            "() => document.documentElement.dataset.skin === 'custom-preview'",
            timeout=5_000,
        )
        if screenshot and index == 0:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
        _close_panel(page, method)
        _assert_settled(page, method)
        if method == "escape" and not page.locator("#panelSettings").is_visible():
            raise CompatibilityFailure("Escape propagated into Core and hid the Settings panel")
        skin_after_close = page.evaluate("() => document.documentElement.dataset.skin || 'default'")
        collection_after = page.evaluate("() => localStorage.getItem('hermes-ext-custom-themes')")
        if skin_after_close != "custom-saved":
            raise CompatibilityFailure(
                f"{method} close did not roll back to the prior skin: {skin_after_close!r}"
            )
        if collection_after != collection_before:
            raise CompatibilityFailure(f"{method} close changed the stored theme collection")
        close_settlements.append(method)
    focus_restores = page.evaluate("() => window.__themeCreatorFocusRestoreCount || 0")
    if focus_restores != len(close_settlements):
        raise CompatibilityFailure(
            f"Core did not restore Theme Creator Configure opener focus exactly once per close: {focus_restores!r}"
        )
    return {
        "installed_buttons": installed_buttons,
        "diagnostics_buttons": diagnostics_buttons,
        "pending_before_second_click": True,
        "second_click_suppressed": True,
        "keyboard_modal_verified": True,
        "configure_programmatic_reuse_verified": True,
        "escape_preserved_settings": True,
        "close_settlements": close_settlements,
        "focus_restores": focus_restores,
        "stored_theme_collection_unchanged": True,
        "theme_refresh_actions": 2 * len(close_settlements),
    }


def _assert_native_skin_after_reload(page: Any, base_url: str) -> dict[str, Any]:
    # `_boot_page` performs the fresh navigation. Do not call `page.reload()`
    # first: that would navigate twice and fabricate a second Core request set.
    _boot_page(page, base_url)
    collection = page.evaluate("() => JSON.parse(localStorage.getItem('hermes-ext-custom-themes') || '[]')")
    expected = page.evaluate("() => ({key: 'custom-saved', name: 'Saved Theme'})")
    if not any(item.get("key") == expected["key"] and item.get("name") == expected["name"] for item in collection):
        raise CompatibilityFailure(f"saved theme collection was lost after reload: {collection!r}")
    _open_extensions_installed(page)
    # The settings side menu is collapsed outside the 390px mobile viewport.
    # Dispatch the existing button's native click and still require the target
    # pane to become visible; do not call Core's section-switching function.
    page.locator(
        '#settingsMenu button[data-settings-section="appearance"]'
    ).evaluate("button => button.click()")
    page.locator("#settingsPaneAppearance").wait_for(state="visible", timeout=10_000)
    native_skin = page.locator('#skinPickerGrid [data-skin-val="custom-saved"]')
    native_skin.wait_for(state="visible", timeout=10_000)
    if native_skin.count() != 1:
        raise CompatibilityFailure(f"native skin surface has unexpected saved-theme count: {native_skin.count()}")
    active_skin = page.evaluate("() => document.documentElement.dataset.skin || 'default'")
    if active_skin != "custom-saved":
        raise CompatibilityFailure(f"saved theme was not re-applied after reload: {active_skin!r}")
    return {
        "saved_theme_count": len(collection),
        "native_skin_buttons": native_skin.count(),
        "active_skin": active_skin,
    }


def _png_dimensions(path: Path) -> dict[str, int]:
    try:
        data = path.read_bytes()
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return {"width": width, "height": height}
    except OSError:
        pass
    return {}


def _run_flow(base_url: str, evidence_dir: Path, browser: Any, viewport: dict[str, int], name: str) -> dict[str, Any]:
    seed = json.dumps([SAVED_THEME], separators=(",", ":"))
    init_script = f"""(() => {{
      try {{
        if (window.top !== window) return;
        localStorage.setItem('hermes-ext-custom-themes', {json.dumps(seed)});
        localStorage.setItem('hermes-skin', 'custom-saved');
      }} catch (_) {{}}
    }})();"""
    context, page, network_events, console_errors, page_errors = _new_page(browser, viewport, init_script)
    screenshot = evidence_dir / f"theme-creator-{name}.png"
    context_state = {"closed": False}
    try:
        _boot_page(page, base_url)
        configure = _exercise_configure(page, screenshot=screenshot)
        persisted = _assert_native_skin_after_reload(page, base_url)
        # Playwright can deliver a request caused by an earlier skin action only
        # while the context is shutting down. Close first, then validate the
        # complete flow against one finite exact-URL budget. Per-action slicing
        # would misattribute late delivery between adjacent checkpoints.
        _close_context_once(context, context_state)
        _consume_core_theme_refresh(
            network_events,
            0,
            "theme-creator-flow",
            request_limits=FLOW_REQUEST_LIMITS,
        )
        _assert_browser_health(
            case_name=f"theme-creator-{name}",
            console_errors=console_errors,
            page_errors=page_errors,
            extension_fragments=EXTENSION_RESOURCES,
            network_events=network_events,
        )
        return {
            "status": "passed",
            "viewport": viewport,
            "configure": configure,
            "persistence": persisted,
            "legacy_rail_count": 0,
            "unexpected_http": network_events.get("unexpected_http", []),
            "expected_core_theme_refresh_http": network_events.get(
                "expected_core_theme_refresh_http", []
            ),
            "unexpected_websockets": network_events.get("unexpected_websockets", []),
            "screenshot": {"path": screenshot.name, **_png_dimensions(screenshot)},
        }
    except Exception:
        try:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
        except Exception:
            pass
        raise
    finally:
        _close_context_once(context, context_state)
        _write_json(evidence_dir / f"theme-creator-{name}-network.json", network_events)


def main() -> int:
    args = _parse_args()
    evidence_dir = Path(args.evidence_dir).expanduser().resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"status": "running", "track": "theme-creator-configure", "cases": {}}
    results_path = evidence_dir / "theme-creator-results.json"
    proc = None
    log_file = None
    try:
        core_dir = Path(args.core_dir).expanduser().resolve()
        extension_root = Path(args.extension_root).expanduser().resolve()
        if not core_dir.is_dir():
            raise SetupFailure("HERMES_CORE_DIR/--core-dir must point to an independent Hermes WebUI checkout")
        source_dir = extension_root / "theme-creator"
        if not source_dir.is_dir():
            raise SetupFailure(f"Theme Creator extension directory not found: {source_dir}")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise SetupFailure("Playwright is required; install tests/compatibility/requirements.txt") from exc

        with tempfile.TemporaryDirectory(prefix="hermes-theme-creator-compat-") as temp:
            temp_root = Path(temp)
            bundle_root = temp_root / "theme-creator-bundle"
            shutil.copytree(source_dir, bundle_root / "theme-creator")
            state_root = temp_root / "state"
            proc, log_file, base_url, port = _start_server(
                core_dir=core_dir,
                extension_root=bundle_root,
                manifest_relative="theme-creator/manifest.json",
                state_root=state_root,
                log_path=temp_root / "theme-creator-server.log",
                requested_port=args.port,
            )
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-dev-shm-usage"],
                    )
                    try:
                        results["cases"]["desktop"] = _run_flow(
                            base_url, evidence_dir, browser, DESKTOP_VIEWPORT, "desktop"
                        )
                        results["cases"]["mobile"] = _run_flow(
                            base_url, evidence_dir, browser, MOBILE_VIEWPORT, "mobile"
                        )
                    finally:
                        browser.close()
            finally:
                _terminate(proc, log_file)
                proc = None
                log_file = None
                if (temp_root / "theme-creator-server.log").is_file():
                    shutil.copyfile(temp_root / "theme-creator-server.log", evidence_dir / "theme-creator-server.log")
            results["port"] = port
        results["status"] = "passed"
        _write_json(results_path, results)
        print("THEME CREATOR CONFIGURE COMPATIBILITY PASSED")
        print(f"evidence={evidence_dir}")
        return 0
    except SetupFailure as exc:
        results["status"] = "setup_failure"
        results["error"] = str(exc)
        _write_json(results_path, results)
        print(f"SETUP FAILURE: {exc}", file=sys.stderr)
        print(f"evidence={evidence_dir}", file=sys.stderr)
        return 2
    except CompatibilityFailure as exc:
        results["status"] = "failed"
        results["error"] = str(exc)
        results["traceback"] = traceback.format_exc()
        _write_json(results_path, results)
        print(f"THEME CREATOR CONFIGURE COMPATIBILITY FAILED: {exc}", file=sys.stderr)
        print(f"evidence={evidence_dir}", file=sys.stderr)
        return 1
    except Exception as exc:
        results["status"] = "harness_error"
        results["error"] = f"{type(exc).__name__}: {exc}"
        results["traceback"] = traceback.format_exc()
        _write_json(results_path, results)
        print(f"HARNESS ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"evidence={evidence_dir}", file=sys.stderr)
        return 2
    finally:
        _terminate(proc, log_file)


if __name__ == "__main__":
    sys.exit(main())
