"""
Wechat 发送提醒服务

职责：
1. 通过企业微信 Webhook 发送文本消息
2. 通过企业微信 Webhook 发送图片消息
3. 通过企业微信 Webhook 发送文件（PDF 等）
"""

import base64
import hashlib
import logging
import os
import re
import time
from collections import deque

import requests

from src.config import Config
from src.formatters import chunk_content_by_max_bytes

logger = logging.getLogger(__name__)


# WeChat Work image msgtype limit ~2MB (base64 payload)
WECHAT_IMAGE_MAX_BYTES = 2 * 1024 * 1024

# 企业微信 API 速率限制：每个 webhook 最多 20次/分钟，留10%余量
WECOM_RATE_LIMIT = 18
WECOM_RATE_WINDOW = 60


class WechatSender:
    def __init__(self, config: Config):
        """
        初始化企业微信配置

        Args:
            config: 配置对象
        """
        self._wechat_url = config.wechat_webhook_url
        self._wechat_max_bytes = getattr(config, "wechat_max_bytes", 4000)
        self._wechat_msg_type = getattr(config, "wechat_msg_type", "markdown")
        self._webhook_verify_ssl = getattr(config, "webhook_verify_ssl", True)
        self._send_times: deque[float] = deque()

    def _acquire_rate_limit(self):
        """滑动窗口速率限制：最多 WECOM_RATE_LIMIT 次/60s"""
        now = time.monotonic()
        window_start = now - WECOM_RATE_WINDOW
        while self._send_times and self._send_times[0] < window_start:
            self._send_times.popleft()
        if len(self._send_times) >= WECOM_RATE_LIMIT:
            sleep_time = self._send_times[0] + WECOM_RATE_WINDOW - now
            if sleep_time > 0:
                logger.warning(
                    "企业微信达到速率限制(%d/%ds)，等待%.1fs",
                    WECOM_RATE_LIMIT, WECOM_RATE_WINDOW, sleep_time,
                )
                time.sleep(sleep_time)
        self._send_times.append(time.monotonic())

    def send_to_wechat(self, content: str, *, timeout_seconds: float | None = None) -> bool:
        """
        推送消息到企业微信机器人

        企业微信 Webhook 消息格式：
        支持 markdown 类型以及 text 类型, markdown 类型在微信中无法展示，可以使用 text 类型,
        markdown 类型会解析 markdown 格式,text 类型会直接发送纯文本。

        markdown 类型示例：
        {
            "msgtype": "markdown",
            "markdown": {
                "content": "## 标题\n\n内容"
            }
        }

        text 类型示例：
        {
            "msgtype": "text",
            "text": {
                "content": "内容"
            }
        }

        注意：企业微信 Markdown 限制 4096 字节（非字符）, Text 类型限制 2048 字节，超长内容会自动分批发送
        可通过环境变量 WECHAT_MAX_BYTES 调整限制值

        Args:
            content: Markdown 格式的消息内容

        Returns:
            是否发送成功
        """
        if not self._wechat_url:
            logger.warning("企业微信 Webhook 未配置，跳过推送")
            return False

        self._acquire_rate_limit()

        # 根据消息类型动态限制上限，避免 text 类型超过企业微信 2048 字节限制
        if self._wechat_msg_type == "text":
            max_bytes = min(self._wechat_max_bytes, 2000)  # 预留一定字节给系统/分页标记
        else:
            max_bytes = self._wechat_max_bytes  # markdown 默认 4000 字节

        # 检查字节长度，超长则分批发送
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > max_bytes:
            logger.info(f"消息内容超长({content_bytes}字节/{len(content)}字符)，将分批发送")
            try:
                return self._send_wechat_chunked(content, max_bytes)
            except Exception as e:
                logger.error(f"企业微信分批发送失败: {e}")
                return False

        try:
            return self._send_wechat_message(content, timeout_seconds=timeout_seconds)
        except Exception as e:
            logger.error(f"发送企业微信消息失败: {e}")
            return False

    def _send_wechat_image(self, image_bytes: bytes) -> bool:
        """Send image via WeChat Work webhook msgtype image (Issue #289)."""
        if not self._wechat_url:
            return False
        if len(image_bytes) > WECHAT_IMAGE_MAX_BYTES:
            logger.warning(
                "企业微信图片超限 (%d > %d bytes)，拒绝发送，调用方应 fallback 为文本",
                len(image_bytes),
                WECHAT_IMAGE_MAX_BYTES,
            )
            return False
        try:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            md5_hash = hashlib.md5(image_bytes).hexdigest()
            payload = {
                "msgtype": "image",
                "image": {"base64": b64, "md5": md5_hash},
            }
            response = requests.post(self._wechat_url, json=payload, timeout=30, verify=self._webhook_verify_ssl)
            if response.status_code == 200:
                result = response.json()
                if result.get("errcode") == 0:
                    logger.info("企业微信图片发送成功")
                    return True
                logger.error("企业微信图片发送失败: %s", result.get("errmsg", ""))
            else:
                logger.error("企业微信请求失败: HTTP %s", response.status_code)
            return False
        except Exception as e:
            logger.error("企业微信图片发送异常: %s", e)
            return False

    def _send_wechat_message(self, content: str, *, timeout_seconds: float | None = None) -> bool:
        """发送企业微信消息（3次指数退避重试）"""
        payload = self._gen_wechat_payload(content)

        for attempt in range(3):
            try:
                response = requests.post(
                    self._wechat_url, json=payload, timeout=timeout_seconds or 10, verify=self._webhook_verify_ssl
                )
                if response.status_code == 200:
                    result = response.json()
                    if result.get("errcode") == 0:
                        logger.info("企业微信消息发送成功")
                        return True
                    else:
                        logger.error(f"企业微信返回错误: {result}")
                        return False
                else:
                    logger.error(f"企业微信请求失败: HTTP {response.status_code}")
                    return False
            except requests.exceptions.Timeout:
                if attempt < 2:
                    delay = (2 ** attempt) * 1.5
                    logger.warning(f"企业微信推送超时，{delay:.0f}s后重试({attempt+1}/3)")
                    time.sleep(delay)
                else:
                    logger.error("企业微信推送超时（10s），3次重试均失败")
                    return False
            except Exception as e:
                if attempt < 2:
                    delay = (2 ** attempt) * 1.5
                    logger.warning(f"企业微信推送异常: {e}，{delay:.0f}s后重试({attempt+1}/3)")
                    time.sleep(delay)
                else:
                    logger.error(f"企业微信推送异常: {e}，3次重试均失败")
                    return False

    def _send_wechat_chunked(self, content: str, max_bytes: int) -> bool:
        """
        分批发送长消息到企业微信

        按股票分析块（以 --- 或 ### 分隔）智能分割，确保每批不超过限制

        Args:
            content: 完整消息内容
            max_bytes: 单条消息最大字节数

        Returns:
            是否全部发送成功
        """
        chunks = chunk_content_by_max_bytes(content, max_bytes, add_page_marker=True)
        total_chunks = len(chunks)
        success_count = 0
        for i, chunk in enumerate(chunks):
            if self._send_wechat_message(chunk):
                success_count += 1
            else:
                logger.error(f"企业微信第 {i + 1}/{total_chunks} 批发送失败")
            if i < total_chunks - 1:
                time.sleep(1)
        return success_count == len(chunks)

    # ── 文件发送（PDF 等）──

    def _extract_webhook_key(self) -> str | None:
        """Extract the webhook key from the webhook URL."""
        if not self._wechat_url:
            return None
        m = re.search(r"[?&]key=([^&]+)", self._wechat_url)
        if m:
            return m.group(1)
        return None

    def _upload_wechat_media(self, file_bytes: bytes, filename: str) -> str | None:
        """Upload a file to WeChat Work media API and return media_id."""
        key = self._extract_webhook_key()
        if not key:
            logger.error("无法从 Webhook URL 提取 key")
            return None

        upload_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key={key}&type=file"
        try:
            with requests.Session() as session:
                resp = session.post(
                    upload_url,
                    files={"media": (filename, file_bytes, "application/pdf")},
                    timeout=120,
                    verify=self._webhook_verify_ssl,
                )
                if resp.status_code != 200:
                    logger.error("企业微信文件上传失败: HTTP %s", resp.status_code)
                    return None
                result = resp.json()
                if result.get("errcode") == 0:
                    media_id = result.get("media_id")
                    logger.info("企业微信文件上传成功: media_id=%s", media_id)
                    return media_id
                logger.error("企业微信文件上传失败: %s", result.get("errmsg", ""))
                return None
        except Exception as e:
            logger.error("企业微信文件上传异常: %s", e)
            return None

    def send_to_wechat_file(self, file_bytes: bytes, filename: str = "report.pdf") -> bool:
        """Upload and send a file (e.g. PDF) to WeChat Work via webhook."""
        if not self._wechat_url:
            return False

        media_id = self._upload_wechat_media(file_bytes, filename)
        if not media_id:
            return False

        payload = {"msgtype": "file", "file": {"media_id": media_id}}
        try:
            resp = requests.post(
                self._wechat_url, json=payload, timeout=30, verify=self._webhook_verify_ssl
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("errcode") == 0:
                    logger.info("企业微信文件发送成功: %s", filename)
                    return True
                logger.error("企业微信文件发送失败: %s", result.get("errmsg", ""))
            else:
                logger.error("企业微信文件请求失败: HTTP %s", resp.status_code)
            return False
        except Exception as e:
            logger.error("企业微信文件发送异常: %s", e)
            return False

    def _gen_wechat_payload(self, content: str) -> dict:
        """生成企业微信消息 payload"""
        if self._wechat_msg_type == "text":
            return {"msgtype": "text", "text": {"content": content}}
        else:
            return {"msgtype": "markdown", "markdown": {"content": content}}
