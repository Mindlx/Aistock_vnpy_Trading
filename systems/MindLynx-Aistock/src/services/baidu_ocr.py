"""
百度 OCR 通用文字识别（标准版）客户端。

用于从图片中提取文字，作为 HTML 解析的降级/备用方案。
调用百度 AI 开放平台通用文字识别（高精度版 / 标准版）API。

用法:
    ocr = BaiduOcrClient()
    text = ocr.recognize_url("https://example.com/image.jpg")
    # 或
    text = ocr.recognize_bytes(image_bytes)
"""

from __future__ import annotations

import logging
import os
import time
from typing import ClassVar

import requests

logger = logging.getLogger(__name__)


class BaiduOcrClient:
    """百度 OCR 客户端（access_token 自动管理）。"""

    # ── 常量 ───────────────────────────────────────────────
    # 通用文字识别（标准版）API
    OCR_API_URL: ClassVar[str] = (
        "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
    )
    # OAuth 2.0 token 获取
    TOKEN_URL: ClassVar[str] = (
        "https://aip.baidubce.com/oauth/2.0/token"
    )

    REQUEST_TIMEOUT: ClassVar[int] = 15

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("BAIDU_OCR_API_KEY", "")
        self._secret_key = secret_key or os.getenv("BAIDU_OCR_SECRET_KEY", "")
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0  # 时间戳

    # ── 公共接口 ────────────────────────────────────────────

    def recognize_url(self, image_url: str, **kwargs) -> str:
        """从图片 URL 识别文字。

        内部先下载图片再调用识别；
        若下载失败或图片不是有效格式，返回空字符串。
        """
        try:
            resp = requests.get(
                image_url,
                timeout=self.REQUEST_TIMEOUT,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                },
            )
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "image" not in content_type:
                logger.debug("BaiduOCR: URL 返回非图片内容 [%s]", image_url)
                return ""
            return self.recognize_bytes(resp.content, **kwargs)
        except requests.RequestException as e:
            logger.debug("BaiduOCR: 图片下载失败 [%s]: %s", image_url, e)
            return ""

    def recognize_bytes(self, image_data: bytes, **kwargs) -> str:
        """从图片二进制数据识别文字。

        参数:
            image_data: 图片的二进制内容（JPEG / PNG）
            **kwargs: 透传给 API 的参数，如 language_type="CHN_ENG"

        返回:
            识别出的文本（多行合并，用换行分隔），失败时返回空字符串
        """
        if not image_data:
            return ""

        token = self._ensure_token()
        if not token:
            return ""

        try:
            params = {
                "access_token": token,
                # 标准版自动检测中英文，无需指定 language_type
            }
            if kwargs:
                params.update(kwargs)

            resp = requests.post(
                self.OCR_API_URL,
                params=params,
                data={"image": image_data},  # base64 编码交给 requests
                timeout=self.REQUEST_TIMEOUT,
            )
            result = resp.json()
        except requests.RequestException as e:
            logger.debug("BaiduOCR: API 请求失败: %s", e)
            return ""
        except ValueError as e:
            logger.debug("BaiduOCR: JSON 解析失败: %s", e)
            return ""

        if "error_code" in result:
            logger.debug(
                "BaiduOCR: API 错误 [%s] %s",
                result.get("error_code"),
                result.get("error_msg", ""),
            )
            # token 过期 → 强制刷新后重试一次
            if result.get("error_code") in (110, 111):
                self._access_token = None
                return self.recognize_bytes(image_data, **kwargs)
            return ""

        # 组装结果
        words_result = result.get("words_result", [])
        lines = [item["words"] for item in words_result if "words" in item]
        return "\n".join(lines)

    # ── Token 管理 ──────────────────────────────────────────

    def _ensure_token(self) -> str | None:
        """获取有效的 access_token。"""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        if not self._api_key or not self._secret_key:
            logger.debug("BaiduOCR: API Key / Secret Key 未配置")
            return None

        try:
            resp = requests.post(
                self.TOKEN_URL,
                params={
                    "grant_type": "client_credentials",
                    "client_id": self._api_key,
                    "client_secret": self._secret_key,
                },
                timeout=self.REQUEST_TIMEOUT,
            )
            result = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.debug("BaiduOCR: Token 获取失败: %s", e)
            return None

        token = result.get("access_token")
        expires_in = result.get("expires_in", 2592000)  # 默认 30 天
        if token:
            self._access_token = token
            self._token_expires_at = time.time() + expires_in
            return token

        logger.debug("BaiduOCR: Token 获取失败 [%s]", result.get("error_description", ""))
        return None

    # ── 可用性检查 ──────────────────────────────────────────

    @classmethod
    def is_configured(cls) -> bool:
        """检查环境变量是否已配置。"""
        return bool(os.getenv("BAIDU_OCR_API_KEY")) and bool(os.getenv("BAIDU_OCR_SECRET_KEY"))
