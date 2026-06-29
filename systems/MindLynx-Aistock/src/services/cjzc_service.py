"""
财经早餐 HTML 正文提取服务

东方财富 财经早餐（ak.stock_info_cjzc_em()）返回的数据包含：
  - 标题 / 摘要 / 发布时间 / 链接
  每篇链接指向的 article 页面同时在 HTML 中嵌有结构化正文
  （<div id="ContentBody"> → <h3 class="emh3"> + <p>），
  比摘要更详尽。本服务负责提取该正文。

用法:
  text = CjzcExtractor.extract("http://finance.eastmoney.com/a/...")
"""

from __future__ import annotations

import logging
import re
from typing import ClassVar

import requests
from bs4 import BeautifulSoup

from src.services.baidu_ocr import BaiduOcrClient

logger = logging.getLogger(__name__)


class CjzcExtractor:
    """财经早餐 article 正文提取器。"""

    # ── 类级缓存 ────────────────────────────────────────
    _cache: ClassVar[dict[str, str]] = {}  # url → extracted_text
    _cache_hits: ClassVar[int] = 0

    # ── 网络配置 ────────────────────────────────────────
    REQUEST_TIMEOUT: ClassVar[int] = 15       # 秒
    USER_AGENT: ClassVar[str] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )

    # 要跳过的页面标签（不包含正文的标签）
    SKIP_TAGS: ClassVar[set[str]] = {"script", "style", "noscript"}

    @classmethod
    def extract(cls, url: str, max_chars: int = 3000) -> str:
        """获取财经早餐文章的完整正文。

        参数:
            url: 东方财富财经早餐 article 链接
            max_chars: 最大截断长度（0 = 不限）

        返回:
            提取出的纯文本，提取失败时返回空字符串
        """
        if not url:
            return ""

        # 缓存命中
        if url in cls._cache:
            cls._cache_hits += 1
            return cls._cache[url]

        text = cls._do_extract(url)
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "……"

        cls._cache[url] = text
        return text

    # ── 内部实现 ────────────────────────────────────────

    @classmethod
    def _do_extract(cls, url: str) -> str:
        """核心提取逻辑。"""
        try:
            resp = requests.get(
                url,
                timeout=cls.REQUEST_TIMEOUT,
                headers={"User-Agent": cls.USER_AGENT},
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            logger.debug("财经早餐页面获取失败 [%s]: %s", url, e)
            return ""

        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            logger.debug("财经早餐页面解析失败 [%s]: %s", url, e)
            return ""

        # 定位正文区域
        content_body = soup.find("div", id="ContentBody")
        if content_body is None:
            # 降级：尝试 .txtinfos
            content_body = soup.find("div", class_="txtinfos")
        if content_body is not None:
            # ── HTML 解析分支 ────────────────────────────
            return cls._extract_text_from_body(content_body)

        # ── HTML 提取失败 → Baidu OCR 降级 ──────────────
        logger.debug("财经早餐页面未找到 ContentBody，尝试 Baidu OCR [%s]", url)
        ocr_text = cls._try_ocr_fallback(url, soup)
        if ocr_text:
            logger.info("财经早餐 OCR 降级成功 [%s]", url)
            return ocr_text

        logger.debug("财经早餐页面提取完全失败 [%s]", url)
        return ""

    # ── 正文提取子方法 ────────────────────────────────────

    @classmethod
    def _extract_text_from_body(cls, content_body) -> str:
        # 移除封面图
        for center in content_body.find_all("center"):
            center.decompose()

        # 移除无意义标签
        for tag_name in cls.SKIP_TAGS:
            for tag in content_body.find_all(tag_name):
                tag.decompose()

        # 移除所有 a / span 标签但保留其文本
        for tag in content_body.find_all(["a", "span"]):
            tag.unwrap()

        # 提取纯文本，用换行分隔
        raw = content_body.get_text(separator="\n", strip=True)

        # 后处理：压缩多余空行、清理空白
        text = re.sub(r"\n{3,}", "\n\n", raw)
        text = text.strip()

        return text

    @classmethod
    def _try_ocr_fallback(cls, url: str, soup) -> str:
        """Baidu OCR 降级：找到文章中最大图片并 OCR。
        
        查找策略：
        1. #ContentBody 下的 <img>（即使 div 本身未找到，内容可能在其他结构中）
        2. .txtinfos 下的 <img>
        3. 页面中面积最大的尺寸 img（排除图标/装饰图）
        """
        if not BaiduOcrClient.is_configured():
            return ""

        candidates: list[tuple[str, int, int]] = []

        for selector in ("div#ContentBody img", "div.txtinfos img", "div.newsContent img",
                         "div.mainleft img", "div.article-content img"):
            for img in soup.select(selector):
                src = img.get("src", "")
                if not src or "logo" in src.lower() or "icon" in src.lower():
                    continue
                w = cls._parse_dim(img.get("width", 0))
                h = cls._parse_dim(img.get("height", 0))
                if w > 100 or h > 100 or src.endswith((".jpg", ".jpeg", ".png")):
                    candidates.append((src, w, h))

        if not candidates:
            return ""

        # 按面积降序取最大图
        candidates.sort(key=lambda x: x[1] * x[2], reverse=True)
        best_src = candidates[0][0]

        # 补全相对 URL
        if best_src.startswith("//"):
            best_src = "https:" + best_src
        elif best_src.startswith("/"):
            best_src = "https://finance.eastmoney.com" + best_src

        ocr = BaiduOcrClient()
        text = ocr.recognize_url(best_src)
        return text

    @staticmethod
    def _parse_dim(value) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            match = re.sub(r"[^\d]", "", value)
            return int(match) if match else 0
        return 0

    @classmethod
    def cache_info(cls) -> dict:
        """缓存的统计信息。"""
        return {
            "size": len(cls._cache),
            "hits": cls._cache_hits,
        }

    @classmethod
    def clear_cache(cls) -> None:
        """清空缓存。"""
        cls._cache.clear()
        cls._cache_hits = 0
