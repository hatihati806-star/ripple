"""Interactive checks for the Ripple app: ranking selection, per-body chart, point sampling.

Screenshots alone prove the map paints; they do not prove the interactions work. This drives
real clicks through the built app and asserts on the resulting DOM, which is what the
"click a lake for its forecast" claim actually depends on.

Usage:
    python ripple/tools/interaction_check.py http://localhost:4173 --out ripple/screens/verify
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

CHROMIUM_ARGS = [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--enable-webgl",
    "--ignore-gpu-blocklist",
]


def collect_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url}"))
    return errors


def wait_for_map(page: Page) -> None:
    page.wait_for_selector(".maplibregl-canvas", timeout=30_000)
    page.wait_for_timeout(4000)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--out", default="ripple/screens/verify")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"checks": [], "failures": []}

    def check(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail[:400]})
        if not ok:
            report["failures"].append(f"{name}: {detail[:200]}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        errors = collect_errors(page)
        page.goto(args.url, wait_until="load")
        wait_for_map(page)

        # 0. The assistant. Only the *refusal* path is exercised here: it is decided before
        #    the model is called, so this check costs nothing and is deterministic. Asserting
        #    on a model answer would make this harness depend on a paid API and on sampling.
        assistant_button = page.locator('button:has-text("Ask")')
        check("assistant opener present", assistant_button.count() > 0)
        if assistant_button.count() > 0:
            assistant_button.first.click()
            page.wait_for_timeout(400)
            panel = page.locator('section[aria-label="AI assistant"]')
            check("assistant panel opened", panel.count() == 1)
            check(
                "assistant states its grounding",
                "only from ripple" in panel.inner_text().lower(),
                panel.inner_text()[:160],
            )
            suggestions = panel.locator("button").filter(has_text="Worst lake now")
            check("assistant offers starter questions", suggestions.count() > 0)
            field = panel.locator('input[aria-label="Question for the assistant"]')
            check("assistant has a question field", field.count() == 1)
            send = panel.locator('button[aria-label="Send question"]')
            check("assistant has a send control", send.count() == 1)
            if field.count() == 1:
                field.fill("Who won the world cup in 2018?")
                send.click()
                page.wait_for_timeout(2000)
                text = panel.inner_text().lower()
                check(
                    "assistant refuses an out-of-scope question without the model",
                    "only answer questions about the environment" in text
                    and "refused without calling the model" in text,
                    text[-200:],
                )
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            check("Escape closes the assistant", panel.count() == 0)

        # 1. Ranking selection: click the top row, expect a body panel with a real chart.
        rows = page.locator('section[aria-label="Water body ranking"] ol button')
        check("ranking rows rendered", rows.count() > 0, f"{rows.count()} rows")
        if rows.count() > 0:
            name = rows.first.inner_text().strip().split("\n")[0]
            rows.first.click()
            page.wait_for_timeout(2600)
            panel = page.locator('aside[aria-label="Water body details"]')
            check("body panel opened", panel.count() == 1)
            if panel.count() == 1:
                text = panel.inner_text()
                check("panel names the body", "Lake" in text or "Bay" in text or "Reservoir" in text, text[:80])
                chart = panel.locator("svg[role='img']")
                check("chart rendered", chart.count() == 1)
                observed_paths = panel.locator("svg path").count()
                check("chart has line geometry", observed_paths >= 2, f"{observed_paths} paths")
                check("forecast legend present", "forecast" in text.lower())
                check("method notes present", "not a regulatory measurement" in text)
            page.screenshot(path=str(out_dir / "interaction-body.png"))

        # 2. Point inspection on open water: click the middle of the map.
        box = page.locator(".maplibregl-canvas").first.bounding_box()
        if box:
            page.mouse.click(box["x"] + box["width"] * 0.42, box["y"] + box["height"] * 0.42)
            page.wait_for_timeout(6000)
            panel = page.locator('aside[aria-label="Water body details"]')
            check("point panel opened", panel.count() == 1)
            if panel.count() == 1:
                # inner_text applies text-transform, so labels render uppercase.
                text = panel.inner_text().lower()
                check("point panel reports sampling", "inspected point" in text or "°" in text, text[:100])
                check(
                    "point panel is either sampled or explicit about no water",
                    "water here" in text or "no series" in text,
                )
                check(
                    "probe rasters actually loaded",
                    "could not load the probe" not in text,
                    text[:160],
                )
            page.screenshot(path=str(out_dir / "interaction-point.png"))

        # 3. Keyboard timeline control.
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(800)
        label_after = page.locator('[role="tab"][aria-selected="true"]').inner_text()
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(800)
        label_back = page.locator('[role="tab"][aria-selected="true"]').inner_text()
        check("arrow keys move the playhead", label_after != label_back, f"{label_after} -> {label_back}")

        # 4. Speed control and legend collapse are reachable and toggle state.
        speed = page.locator('button[aria-label="Playback speed 2x"]')
        speed.click()
        page.wait_for_timeout(200)
        check("speed control toggles", speed.get_attribute("aria-pressed") == "true")

        # 5. Map 3D mode: the toggle must flip state and return to flat.
        toggle = page.locator('button[aria-label="Toggle 3D terrain view"]')
        check("map 3D toggle present", toggle.count() == 1)
        if toggle.count() == 1:
            toggle.click()
            page.wait_for_timeout(2500)
            check("map 3D turns on", toggle.get_attribute("aria-pressed") == "true")
            toggle.click()
            page.wait_for_timeout(1200)
            check("map 3D turns off", toggle.get_attribute("aria-pressed") == "false")

        # 6. Per-lake 3D view: opens a live three.js scene, reads the relief, follows frames,
        #    scales with the exaggeration control, and disposes on Escape.
        #    Re-select a body first: the point inspection in step 2 replaced the selection.
        rows_again = page.locator('section[aria-label="Water body ranking"] ol button')
        if rows_again.count() > 0:
            rows_again.first.click()
            page.wait_for_timeout(2600)
        opener = page.get_by_role("button", name="View this lake in 3D")
        check("3D opener offered for the selected body", opener.count() == 1)
        if opener.count() == 1:
            opener.click()
            page.wait_for_selector('[data-lake3d][data-ready="true"]', timeout=25_000)
            check(
                "three.js scene exposed while open",
                page.evaluate("() => Boolean(window.__ROCKY_THREE_SCENE__?.renderer)") is True,
            )

            box3 = page.locator("[data-lake3d] canvas").bounding_box()
            read = ""
            if box3:
                for dx, dy in ((0.5, 0.5), (0.45, 0.55), (0.55, 0.45), (0.5, 0.62), (0.58, 0.52)):
                    page.mouse.click(
                        box3["x"] + box3["width"] * dx, box3["y"] + box3["height"] * dy
                    )
                    page.wait_for_timeout(350)
                    read = page.locator('[data-lake3d] [role="status"]').inner_text()
                    if "Risk" in read:
                        break
            check("clicking the relief reads a risk value", "Risk" in read, read[:120])

            # Compare the geometry *object*, not its shape. A frame step must re-derive
            # the relief, and `scene.update()` builds a fresh BufferGeometry every time,
            # so a changed uuid is the faithful signal. Comparing vertex heights instead
            # tests the data rather than the app: a body that reads a saturated 1.00 in
            # both frames -- a small, permanently turbid lake, or the top-ranked body on
            # any given run -- legitimately produces two identical surfaces, and the
            # previous version of this check failed on exactly that.
            surface_id = (
                "() => window.__ROCKY_THREE_SCENE__.scene"
                ".getObjectByName('lake-surface')?.geometry.uuid ?? ''"
            )
            before = page.evaluate(surface_id)
            check("relief has a surface to step", bool(before), before)
            page.keyboard.press("ArrowRight")
            rebuilt = False
            for _ in range(24):
                page.wait_for_timeout(500)
                after = page.evaluate(surface_id)
                if after and after != before:
                    rebuilt = True
                    break
            check("frame step rebuilds the 3D relief", rebuilt, f"{before[:8]} -> "
                  f"{page.evaluate(surface_id)[:8]}")

            exaggerated = page.locator('button[aria-label="Vertical exaggeration 2x"]')
            exaggerated.click()
            page.wait_for_timeout(300)
            scale_y = page.evaluate(
                "() => window.__ROCKY_THREE_SCENE__.scene.getObjectByName('lake-surface')?.scale.y"
            )
            check(
                "exaggeration control scales the surface",
                exaggerated.get_attribute("aria-pressed") == "true" and scale_y == 2,
                f"scale.y={scale_y}",
            )

            page.keyboard.press("Escape")
            page.wait_for_timeout(700)
            check("Escape closes the 3D view", page.locator("[data-lake3d]").count() == 0)
            check(
                "3D scene disposed on close",
                page.evaluate("() => window.__ROCKY_THREE_SCENE__ === undefined") is True,
            )

        # Third-party basemap tiles are outside this app's control; a single missing tile
        # from OpenFreeMap is reported but must not be conflated with an app defect, since
        # the product layer is served locally.
        third_party = [error for error in errors if "openfreemap.org" in error or "cartocdn" in error]
        report["console_errors"] = [
            error for error in errors
            if "favicon" not in error and error not in third_party
        ]
        report["third_party_warnings"] = third_party
        check("no app console errors during interaction", len(report["console_errors"]) == 0,
              "; ".join(report["console_errors"][:3]))
        browser.close()

    (out_dir / "interaction-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for entry in report["checks"]:
        print(("PASS " if entry["ok"] else "FAIL ") + entry["name"] + (f" — {entry['detail']}" if entry["detail"] and not entry["ok"] else ""))
    if report["failures"]:
        print(f"\n{len(report['failures'])} failures")
        return 1
    print(f"\nall {len(report['checks'])} interaction checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
