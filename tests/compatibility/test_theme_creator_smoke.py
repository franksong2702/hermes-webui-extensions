import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

from browser_smoke import EXPECTED_CORE_BASELINE_REQUESTS
from theme_creator_smoke import (
    CONFIGURE_SELECTOR,
    DIAGNOSTICS_SELECTOR,
    EXTENSION_RESOURCES,
    MOBILE_VIEWPORT,
    FLOW_REQUEST_LIMITS,
    RAIL_SELECTOR,
    SAVED_THEME,
    CompatibilityFailure,
    _close_context_once,
    _consume_core_theme_refresh,
    _png_dimensions,
)


class ThemeCreatorConfigureSurfaceTests(unittest.TestCase):
    def test_configure_selector_targets_installed_theme_creator_only(self) -> None:
        self.assertEqual(
            CONFIGURE_SELECTOR,
            '#extensionsInstalled [data-extension-configure-id="theme-creator"]',
        )
        self.assertEqual(
            DIAGNOSTICS_SELECTOR,
            '#extensionsDiagnostics [data-extension-configure-id="theme-creator"]',
        )
        self.assertEqual(RAIL_SELECTOR, "#hwxThemeCreatorRailBtn")
        self.assertEqual(
            EXTENSION_RESOURCES,
            (
                "/extensions/theme-creator/assets/theme-creator.js",
                "/extensions/theme-creator/assets/theme-creator.css",
            ),
        )

    def test_mobile_viewport_is_the_required_dimensions(self) -> None:
        self.assertEqual(MOBILE_VIEWPORT, {"width": 390, "height": 844})

    def test_saved_theme_fixture_has_the_complete_storage_shape(self) -> None:
        self.assertEqual(SAVED_THEME["key"], "custom-saved")
        self.assertIsNone(SAVED_THEME["base"]["bgImage"])
        self.assertEqual(SAVED_THEME["base"]["glassOpacity"], 0.08)
        self.assertEqual(SAVED_THEME["base"]["blur"], 20)


class ThemeCreatorNetworkFilterTests(unittest.TestCase):
    def test_context_close_delivers_late_exact_request_before_consumption(self) -> None:
        url, (resource_type, max_occurrences) = next(iter(EXPECTED_CORE_BASELINE_REQUESTS.items()))
        exact_refresh = {
            "url": url,
            "method": "GET",
            "resource_type": resource_type,
            "occurrence": max_occurrences + 1,
        }
        events = {"unexpected_http": []}

        class FakeContext:
            def __init__(self) -> None:
                self.close_calls = 0

            def close(self) -> None:
                self.close_calls += 1
                events["unexpected_http"].append(exact_refresh)

        context = FakeContext()
        state: dict[str, bool] = {}
        _close_context_once(context, state)
        _close_context_once(context, state)
        self.assertEqual(context.close_calls, 1)
        self.assertEqual(
            _consume_core_theme_refresh(events, 0, "theme-creator-flow"),
            [{**exact_refresh, "checkpoint": "theme-creator-flow"}],
        )

    def test_context_close_does_not_hide_late_unknown_or_overage(self) -> None:
        url, (resource_type, max_occurrences) = next(iter(EXPECTED_CORE_BASELINE_REQUESTS.items()))
        exact_refresh = {
            "url": url,
            "method": "GET",
            "resource_type": resource_type,
            "occurrence": max_occurrences + 1,
        }
        late_events = (
            [{**exact_refresh, "url": url + "?cache-bust=1"}],
            [exact_refresh, dict(exact_refresh)],
        )
        for candidate_events in late_events:
            with self.subTest(candidate_events=candidate_events):
                events = {"unexpected_http": []}

                class FakeContext:
                    def close(self) -> None:
                        events["unexpected_http"].extend(candidate_events)

                _close_context_once(FakeContext(), {})
                with self.assertRaises(CompatibilityFailure):
                    _consume_core_theme_refresh(events, 0, "theme-creator-flow")

    def test_one_exact_request_per_url_is_consumed_at_each_skin_checkpoint(self) -> None:
        url, (resource_type, max_occurrences) = next(iter(EXPECTED_CORE_BASELINE_REQUESTS.items()))
        exact_refresh = {
            "url": url,
            "method": "GET",
            "resource_type": resource_type,
            "occurrence": max_occurrences + 1,
        }
        events = {"unexpected_http": [{"url": "earlier"}, exact_refresh]}
        self.assertEqual(
            _consume_core_theme_refresh(events, 1, "preview"),
            [{**exact_refresh, "checkpoint": "preview"}],
        )
        self.assertEqual(events["unexpected_http"], [{"url": "earlier"}])
        self.assertEqual(
            events["expected_core_theme_refresh_http"],
            [{**exact_refresh, "checkpoint": "preview"}],
        )

    def test_skin_checkpoint_rejects_duplicate_or_inexact_requests(self) -> None:
        url, (resource_type, max_occurrences) = next(iter(EXPECTED_CORE_BASELINE_REQUESTS.items()))
        exact_refresh = {
            "url": url,
            "method": "GET",
            "resource_type": resource_type,
            "occurrence": max_occurrences + 1,
        }
        variants = (
            [exact_refresh, dict(exact_refresh)],
            [{**exact_refresh, "method": "POST"}],
            [{**exact_refresh, "resource_type": "script" if resource_type != "script" else "stylesheet"}],
            [{**exact_refresh, "url": url + "?cache-bust=1"}],
        )
        for candidate_events in variants:
            with self.subTest(candidate_events=candidate_events):
                events = {"unexpected_http": candidate_events}
                with self.assertRaises(CompatibilityFailure):
                    _consume_core_theme_refresh(events, 0, "rollback")
                self.assertEqual(events["unexpected_http"], candidate_events)

    def test_complete_flow_budget_is_explicit_and_rejects_an_extra_request(self) -> None:
        url, (resource_type, max_occurrences) = next(iter(EXPECTED_CORE_BASELINE_REQUESTS.items()))
        exact_refresh = {
            "url": url,
            "method": "GET",
            "resource_type": resource_type,
            "occurrence": max_occurrences + 1,
        }
        allowed = FLOW_REQUEST_LIMITS[url]
        events = {"unexpected_http": [dict(exact_refresh) for _ in range(allowed)]}
        self.assertEqual(
            len(
                _consume_core_theme_refresh(
                    events,
                    0,
                    "theme-creator-flow",
                    request_limits=FLOW_REQUEST_LIMITS,
                )
            ),
            allowed,
        )
        excessive = {"unexpected_http": [dict(exact_refresh) for _ in range(allowed + 1)]}
        with self.assertRaises(CompatibilityFailure):
            _consume_core_theme_refresh(
                excessive,
                0,
                "theme-creator-flow",
                request_limits=FLOW_REQUEST_LIMITS,
            )


class ThemeCreatorEvidenceTests(unittest.TestCase):
    def test_png_dimensions_reads_a_valid_png_header(self) -> None:
        # PNG signature + IHDR length/type + width/height (4 bytes each).
        payload = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (390).to_bytes(4, "big") + (844).to_bytes(4, "big")
        path = NamedTemporaryFile(delete=False)
        try:
            path.write(payload)
            path.close()
            self.assertEqual(_png_dimensions(Path(path.name)), {"width": 390, "height": 844})
        finally:
            Path(path.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
