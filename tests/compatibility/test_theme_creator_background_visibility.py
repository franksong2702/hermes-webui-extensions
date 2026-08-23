"""Browser regression coverage for Theme Creator background-image visibility."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
THEME_CREATOR_CSS = REPO_ROOT / "extensions/theme-creator/assets/theme-creator.css"
VIEWPORTS = (
    {"name": "desktop", "width": 1454, "height": 1000},
    {"name": "mobile", "width": 390, "height": 844},
)


@unittest.skipUnless(
    importlib.util.find_spec("playwright"),
    "Playwright is required for computed-style visibility assertions",
)
class ThemeCreatorBackgroundVisibilityTests(unittest.TestCase):
    def test_hidden_state_controls_empty_preview_layout(self) -> None:
        from playwright.sync_api import sync_playwright

        css = THEME_CREATOR_CSS.read_text(encoding="utf-8")
        html = f"""
          <style>{css}</style>
          <div class="hwx-tc-panel">
            <div class="hwx-tc-card" role="dialog" aria-label="Theme Creator">
              <div class="hwx-tc-title">Theme Creator</div>
              <div class="hwx-tc-section-title">Background image <span class="hwx-tc-muted">(optional)</span></div>
              <div class="hwx-tc-bg-row">
                <span class="hwx-tc-filelabel-text">Choose image…</span>
                <span class="hwx-tc-bg-none">None</span>
                <div class="hwx-tc-bg-preview-wrap" hidden>
                  <img class="hwx-tc-bg-preview" alt="Background">
                  <button class="hwx-tc-link hwx-tc-remove-img">Remove</button>
                </div>
              </div>
            </div>
          </div>
        """

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for viewport in VIEWPORTS:
                    with self.subTest(viewport=viewport["name"]):
                        page = browser.new_page(viewport=viewport)
                        page.set_content(html)

                        self._assert_state(page, has_background=False)

                        page.evaluate(
                            """() => {
                              document.querySelector('.hwx-tc-bg-none').hidden = true;
                              document.querySelector('.hwx-tc-bg-preview-wrap').hidden = false;
                            }"""
                        )
                        self._assert_state(page, has_background=True)

                        page.evaluate(
                            """() => {
                              document.querySelector('.hwx-tc-bg-none').hidden = false;
                              document.querySelector('.hwx-tc-bg-preview-wrap').hidden = true;
                            }"""
                        )
                        self._assert_state(page, has_background=False)
                        evidence_dir = os.environ.get("COMPATIBILITY_EVIDENCE_DIR")
                        if not evidence_dir and os.environ.get("GITHUB_ACTIONS") == "true":
                            evidence_dir = "compatibility-evidence"
                        if evidence_dir:
                            output = Path(evidence_dir)
                            output.mkdir(parents=True, exist_ok=True)
                            page.screenshot(
                                path=str(output / f"theme-creator-empty-background-{viewport['name']}.png"),
                                full_page=True,
                            )
                        page.close()
            finally:
                browser.close()

    def _assert_state(self, page, *, has_background: bool) -> None:
        state = page.evaluate(
            """() => ({
              noneDisplay: getComputedStyle(document.querySelector('.hwx-tc-bg-none')).display,
              previewDisplay: getComputedStyle(document.querySelector('.hwx-tc-bg-preview-wrap')).display,
              removeVisible: !!document.querySelector('.hwx-tc-remove-img').getClientRects().length,
            })"""
        )
        if has_background:
            self.assertEqual(state["noneDisplay"], "none")
            self.assertNotEqual(state["previewDisplay"], "none")
            self.assertTrue(state["removeVisible"])
        else:
            self.assertNotEqual(state["noneDisplay"], "none")
            self.assertEqual(state["previewDisplay"], "none")
            self.assertFalse(state["removeVisible"])


if __name__ == "__main__":
    unittest.main()
