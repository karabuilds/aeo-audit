"""Rebuild the published sample report from the fictional fixtures (mock mode, no keys).

Runs the full pipeline in a temporary project, then copies the outputs to
samples/ledgerly/ and takes the README screenshot at docs/sample-report.png.

Run from the repository root:  python scripts/build_sample.py
Requires Playwright with Chromium (pip install -e ".[pdf]" && playwright install chromium).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "report.pdf",
    "report.html",
    "metrics.json",
    "insights.json",
    "classifier_log.jsonl",
    "run.json",
)


async def screenshot(html: Path, png: Path) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=os.environ.get("AEO_AUDIT_CHROMIUM") or None
        )
        page = await browser.new_page(
            viewport={"width": 816, "height": 1056}, device_scale_factor=2
        )
        await page.goto(html.resolve().as_uri())
        # Screen styles add a margin around the page, which reads better in the README.
        await page.screenshot(path=str(png), full_page=True)
        await browser.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        shutil.copytree(ROOT / "config", project / "config",
                        ignore=shutil.ignore_patterns("*.local.yaml"))  # fmt: skip
        (project / "companies").mkdir()
        for name in ("ledgerly.yaml", "ledgerly.queries.yaml"):
            shutil.copy(ROOT / "companies" / name, project / "companies" / name)
        shutil.copytree(ROOT / "tests" / "fixtures" / "mock" / "ledgerly",
                        project / "tests" / "fixtures" / "mock" / "ledgerly")  # fmt: skip
        cmd = [
            sys.executable,
            "-m",
            "aeo_audit",
            "all",
            "ledgerly",
            "--mock",
            "--yes",
            "--root",
            str(project),
        ]
        subprocess.run(cmd, check=True)
        run_dir = next((project / "output" / "ledgerly").iterdir())
        dest = ROOT / "samples" / "ledgerly"
        dest.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            shutil.copy(run_dir / name, dest / name)
        shutil.rmtree(dest / "raw", ignore_errors=True)
        shutil.copytree(run_dir / "raw", dest / "raw")
        (ROOT / "docs").mkdir(exist_ok=True)
        asyncio.run(screenshot(dest / "report.html", ROOT / "docs" / "sample-report.png"))
    print(f"Sample written to {dest} and docs/sample-report.png")


if __name__ == "__main__":
    main()
