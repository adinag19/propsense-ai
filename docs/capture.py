"""
PropSense.AI — Screenshot + GIF capture script.
Run with both uvicorn (8000) and Next.js (3000) running.
"""
import asyncio
import shutil
from pathlib import Path
from playwright.async_api import async_playwright

FRONTEND = "http://localhost:3000"
OUT = Path(__file__).parent / "screenshots"
OUT.mkdir(exist_ok=True)


async def ss(page, name, full=False):
    path = str(OUT / name)
    await page.screenshot(path=path, full_page=full)
    print(f"  {name}")


async def run(pw):
    browser = await pw.chromium.launch(headless=False, slow_mo=60)
    ctx = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(OUT / "video"),
        record_video_size={"width": 1440, "height": 900},
    )
    page = await ctx.new_page()

    # ── 1. Landing ────────────────────────────────────────────────────────────
    print("Opening PropSense...")
    await page.goto(FRONTEND, wait_until="domcontentloaded")
    await asyncio.sleep(2)
    await ss(page, "01_landing.png")

    # ── 2. Type AI query ──────────────────────────────────────────────────────
    await page.fill("textarea",
        "PR, 36 years old, $320k savings, earning $13k a month. "
        "Looking to buy a 5-room HDB near MRT in the North East, "
        "budget $800k. First property, planning to live there for 18 years.")
    await asyncio.sleep(0.5)
    await page.click("text=Fill Form with AI")
    print("  Waiting for AI to fill form...")
    await asyncio.sleep(10)
    await ss(page, "02_ai_filled.png")

    # ── 3. Score (keep planning area blank for auto-suggestions) ──────────────
    await page.click("text=Get Buy Readiness Score")
    print("  Scoring... (~35s)")
    # Wait for score — poll for /100 text which appears when score renders
    await page.wait_for_function(
        "document.body.innerText.includes('/100')",
        timeout=90000, polling=1000
    )
    await asyncio.sleep(2)
    await ss(page, "03_score_with_suggestions.png")

    # ── 4. Click the first area suggestion card ───────────────────────────────
    # Look for area suggestion cards (they have planning area + CAGR)
    print("  Clicking area suggestion...")
    # Scroll down to see suggestions if needed
    await page.evaluate("window.scrollTo(0, 300)")
    await asyncio.sleep(0.5)

    # Find and click area card
    area_clicked = False
    for selector in ["[class*='areaCard']", "button:has-text('CAGR')", "button:has-text('%')"]:
        cards = await page.query_selector_all(selector)
        if cards:
            await cards[0].scroll_into_view_if_needed()
            await cards[0].click()
            area_clicked = True
            break

    if area_clicked:
        print("  Waiting for area score...")
        await page.wait_for_function(
            "document.body.innerText.includes('/100')",
            timeout=90000, polling=1000
        )
        await asyncio.sleep(2)
        await page.evaluate("window.scrollTo(0, 0)")
        await ss(page, "04_area_score.png")

        # ── 5. Score breakdown ────────────────────────────────────────────────
        await page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(0.8)
        await ss(page, "05_score_breakdown.png")

        # ── 6. Lifestyle + transactions ───────────────────────────────────────
        await page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(0.8)
        await ss(page, "06_lifestyle_transactions.png")

    # ── 7. Set a specific planning area so map auto-zooms ─────────────────────
    # Then switch to map — autoZoomArea will trigger the flyTo
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(0.3)
    # Set Tampines which is guaranteed to be in /areas-geo (D18)
    pa_input = page.get_by_placeholder("e.g. Tampines")
    await pa_input.fill("Tampines")
    await asyncio.sleep(0.5)
    await page.click("text=Hot Areas Map")
    print("  Waiting for map to render and zoom...")
    await asyncio.sleep(4)  # initial render

    # Wait for zoom to complete — "Overview" back button appears when zoomed in
    try:
        await page.wait_for_selector("text=Overview", timeout=15000)
        await asyncio.sleep(3)  # let flyTo + project fetch settle
        print("  Map zoomed in")
    except Exception:
        print("  Map did not auto-zoom (no area in form)")
        await asyncio.sleep(3)

    await ss(page, "07_map_zoomed.png")

    # ── 8. Wait for project markers then screenshot ────────────────────────────
    await asyncio.sleep(3)
    await ss(page, "08_map_projects.png")

    # ── Done ──────────────────────────────────────────────────────────────────
    video = await page.video.path() if page.video else None
    await ctx.close()
    await browser.close()

    gif = OUT.parent / "demo.gif"
    _to_gif(video, str(gif))
    print(f"\nDone. GIF: {gif}")
    print(f"Screenshots: {OUT}")


def _to_gif(video, gif_path):
    if video and shutil.which("ffmpeg"):
        import subprocess
        subprocess.run([
            "ffmpeg", "-y", "-i", video,
            "-vf", "fps=6,scale=900:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
            "-loop", "0", gif_path,
        ], capture_output=True, check=True)
        print("  GIF via ffmpeg")
    else:
        from PIL import Image
        imgs = sorted(OUT.glob("0*.png"))
        if not imgs:
            return
        frames = [Image.open(str(p)).convert("RGB").resize(
            (900, int(Image.open(str(p)).height * 900 / Image.open(str(p)).width)),
            Image.LANCZOS) for p in imgs]
        frames[0].save(gif_path, save_all=True, append_images=frames[1:],
                       duration=2000, loop=0, optimize=True)
        print("  GIF via Pillow stitch")


async def main():
    async with async_playwright() as pw:
        await run(pw)

if __name__ == "__main__":
    asyncio.run(main())
