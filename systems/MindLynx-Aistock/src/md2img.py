"""
===================================
Markdown 转图片工具模块
===================================

将 Markdown 转为 PNG 图片（用于不支持 Markdown 的通知渠道）。
支持 wkhtmltoimage (imgkit) 与 markdown-to-file (m2f)，后者对 emoji 支持更好 (Issue #455)。

Security note: imgkit passes HTML to wkhtmltoimage via stdin, not argv, so
command injection from content is not applicable. Output is rasterized to PNG
(no script execution). Input is from system-generated reports, not raw user
input. Risk is considered low for the current use case.
"""

import logging
import os
import shutil
import subprocess
import tempfile

from src.formatters import markdown_to_html_document

logger = logging.getLogger(__name__)


def _markdown_to_image_m2f(markdown_text: str) -> bytes | None:
    """Convert Markdown to PNG via markdown-to-file (m2f) CLI. Better emoji support (Issue #455)."""
    # 搜索 m2f（包括 npm global 和 ~/.local/bin）
    m2f_path = shutil.which("m2f")
    if m2f_path is None:
        for alt in [
            os.path.expanduser("~/.npm-global/bin/m2f"),
            os.path.expanduser("~/.local/bin/m2f"),
            "/usr/local/bin/m2f",
        ]:
            if os.path.isfile(alt) and os.access(alt, os.X_OK):
                m2f_path = alt
                break
    if m2f_path is None:
        logger.warning(
            "m2f (markdown-to-file) not found in PATH. Install with: npm i -g markdown-to-file. Fallback to text."
        )
        return None

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        md_path = os.path.join(temp_dir, "report.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown_text)

        result = subprocess.run(
            [m2f_path, md_path, "png", f"outputDirectory={temp_dir}"],
            capture_output=True,
            timeout=60,
            check=False,
        )
        png_path = os.path.join(temp_dir, "report.png")
        if result.returncode != 0 or not os.path.isfile(png_path):
            logger.warning(
                "m2f conversion failed: returncode=%s, stderr=%s",
                result.returncode,
                (result.stderr or b"").decode("utf-8", errors="replace")[:200],
            )
            return None

        with open(png_path, "rb") as f:
            return f.read()
    except subprocess.TimeoutExpired:
        logger.warning("m2f conversion timed out (60s)")
        return None
    except Exception as e:
        logger.warning("markdown_to_image (m2f) failed: %s", e)
        return None
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except OSError as e:
                logger.debug("Failed to remove temp dir %s: %s", temp_dir, e)


def _markdown_to_image_wkhtml(markdown_text: str) -> bytes | None:
    """Convert Markdown to PNG via imgkit/wkhtmltoimage."""
    try:
        import imgkit
    except ImportError:
        logger.debug("imgkit not installed, markdown_to_image unavailable")
        return None

    html = markdown_to_html_document(markdown_text)
    try:
        # 确保 wkhtmltoimage 在 PATH 中（systemd 环境可能缺少 ~/.local/bin）
        for _p in [
            os.path.expanduser("~/.local/bin"),
            os.path.expanduser("~/.npm-global/bin"),
            "/usr/local/bin",
        ]:
            if _p not in os.environ.get("PATH", ""):
                os.environ["PATH"] = f"{_p}:{os.environ.get('PATH', '')}"

        import imgkit

        options = {
            "format": "jpg",
            "quality": "60",
            "encoding": "UTF-8",
            "quiet": "",
            # 393px viewport matches iPhone 16 Pro (393dp) and Huawei P70 Pro.
            # JPG quality=60 provides readable text; higher values don't help at this scale.
            # Long images are inherent to full-page rendering — users scroll.
            "width": "393",
            "disable-smart-width": "",
        }
        out = imgkit.from_string(html, False, options=options)
        if out and isinstance(out, bytes) and len(out) > 0:
            return out
        logger.warning("imgkit.from_string returned empty or invalid result")
        return None
    except OSError as e:
        if "wkhtmltoimage" in str(e).lower() or "wkhtmltopdf" in str(e).lower():
            logger.debug("wkhtmltopdf/wkhtmltoimage not found: %s", e)
        else:
            logger.warning("imgkit/wkhtmltoimage error: %s", e)
        return None
    except Exception as e:
        logger.warning("markdown_to_image conversion failed: %s", e)
        return None


def markdown_to_pdf(markdown_text: str, font_size: str = "18pt") -> bytes | None:
    """
    Convert Markdown to PDF bytes via WeasyPrint.

    PDF provides vector text (crisp at any zoom), multi-page layout, and
    smaller file size compared to images — ideal for full-page reports.

    Args:
        markdown_text: Raw Markdown content.
        font_size: Base font size for body text. Default "18pt" (手机端适配).

    Returns:
        PDF bytes, or None if conversion fails.
    """
    try:
        import re as _re

        from src.formatters import markdown_to_html_document
        from weasyprint import HTML, CSS

        html = markdown_to_html_document(markdown_text)
        # Strip embedded CSS from markdown_to_html_document so our PDF CSS takes effect
        html = _re.sub(r"<style>.*?</style>", "", html, flags=_re.DOTALL)

        mobile_css = CSS(string=f"""\
            @page {{ size: A4; margin: 1.2cm 1.5cm; }}
            body {{
                font-family: -apple-system, "PingFang SC", "Noto Sans SC", sans-serif;
                font-size: {font_size};
                line-height: 1.6;
                color: #1a1a1a;
                max-width: 100%;
            }}
            h1 {{ font-size: 28pt; margin: 0.6em 0 0.3em; color: #1a1a1a; }}
            h2 {{ font-size: 18pt; margin: 0.4em 0 0.2em; color: #333; }}
            h3 {{ font-size: 20pt; font-weight: bold; margin: 0.6em 0 0.2em; color: #333; page-break-after: avoid; }}
            p {{ margin: 0.5em 0; text-indent: 2em; }}
            h3 + p, h2 + p {{ text-indent: 0; }}
            blockquote p {{ margin: 0.4em 0; text-indent: 0; }}
            blockquote {{ margin: 0.3em 0; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 12pt; margin: 0.6em 0; }}
            th {{ background: #f3f4f6; }}
            td, th {{ padding: 4px 8px; border: 1px solid #ccc; text-align: center; }}
            blockquote {{
                margin: 0.5em 0; padding: 0.3em 1em;
                border-left: 4px solid #999; color: #444;
            }}
            img {{ max-width: 100%; height: auto; display: block; margin: 0.5em auto; }}
            code {{ font-size: 85%; background: #f5f5f5; padding: 1px 4px; }}
            pre {{ font-size: 85%; background: #f5f5f5; padding: 8px; overflow-x: auto; }}
        """)

        pdf_bytes = HTML(string=html).write_pdf(stylesheets=[mobile_css])
        if pdf_bytes and len(pdf_bytes) > 0:
            logger.info("Markdown 已转换为 PDF: %d bytes (font=%s)", len(pdf_bytes), font_size)
            return pdf_bytes
        logger.warning("PDF conversion returned empty result")
        return None
    except ImportError as e:
        logger.warning("PDF 转换依赖缺失 (weasyprint): %s。pip install weasyprint", e)
        return None
    except Exception as e:
        logger.warning("PDF 转换失败: %s", e)
        return None


def markdown_to_image(markdown_text: str, max_chars: int = 15000) -> bytes | None:
    """
    Convert Markdown to PNG image bytes.

    Engine is read from config.md2img_engine: wkhtmltoimage (default) or
    markdown-to-file (better emoji support, Issue #455).

    When conversion fails or dependencies unavailable, returns None so caller
    can fall back to text sending.

    Args:
        markdown_text: Raw Markdown content.
        max_chars: Skip conversion and return None if content exceeds this length
            (avoids huge images). Default 15000.

    Returns:
        PNG bytes, or None if conversion fails or dependencies unavailable.
    """
    if len(markdown_text) > max_chars:
        logger.warning(
            "Markdown content (%d chars) exceeds max_chars (%d), skipping image conversion",
            len(markdown_text),
            max_chars,
        )
        return None

    try:
        from src.config import get_config

        engine = getattr(get_config(), "md2img_engine", "wkhtmltoimage")
    except Exception:
        engine = "wkhtmltoimage"

    if engine == "markdown-to-file":
        return _markdown_to_image_m2f(markdown_text)
    return _markdown_to_image_wkhtml(markdown_text)
