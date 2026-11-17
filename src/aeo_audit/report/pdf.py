"""Render the HTML report to a one-page US Letter PDF with Playwright (Chromium)."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path


class PDFUnavailable(Exception):
    pass


INSTALL_HELP = (
    "PDF output needs Playwright and Chromium:\n"
    '  pip install -e ".[pdf]"\n'
    "  playwright install chromium\n"
    "The HTML report was still written. You can also open it in a browser and print to PDF."
)


async def _render(html_path: Path, pdf_path: Path) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise PDFUnavailable(INSTALL_HELP) from exc
    async with async_playwright() as p:
        executable = os.environ.get("AEO_AUDIT_CHROMIUM")
        try:
            browser = await p.chromium.launch(executable_path=executable or None)
        except Exception as exc:
            raise PDFUnavailable(
                f"Could not start Chromium ({type(exc).__name__}).\n{INSTALL_HELP}"
            ) from exc
        try:
            page = await browser.new_page()
            await page.goto(html_path.resolve().as_uri(), wait_until="load")
            await page.emulate_media(media="print")
            await page.pdf(path=str(pdf_path), format="Letter", print_background=True,
                           prefer_css_page_size=True)  # fmt: skip
        finally:
            await browser.close()


def render_pdf(html_path: Path, pdf_path: Path) -> int:
    """Write the PDF and return its page count."""
    asyncio.run(_render(html_path, pdf_path))
    return pdf_page_count(pdf_path)


def pdf_page_count(pdf_path: Path) -> int:
    data = pdf_path.read_bytes()
    return len(re.findall(rb"/Type\s*/Page(?!s)", data))
