from dataclasses import dataclass, field

@dataclass
class NotificationConfig:
    """Notification configuration fields extracted from Config dataclass."""
    wechat_webhook_url: str | None = None
    feishu_webhook_url: str | None = None
    feishu_webhook_secret: str | None = None  # 自定义机器人签名密钥（可选）
    feishu_webhook_keyword: str | None = None  # 自定义机器人关键词（可选）
    telegram_bot_token: str | None = None  # Bot Token（@BotFather 获取）
    telegram_chat_id: str | None = None  # Chat ID
    telegram_message_thread_id: str | None = None  # Topic ID (Message Thread ID) for groups
    email_sender: str | None = None  # 发件人邮箱
    email_sender_name: str = "MindLynx-Aistock分析助手"  # 发件人显示名称
    email_password: str | None = None  # 邮箱密码/授权码
    email_receivers: list[str] = field(default_factory=list)  # 收件人列表（留空则发给自己）
    stock_email_groups: list[tuple[list[str], list[str]]] = field(default_factory=list)
    pushover_user_key: str | None = None  # 用户 Key（https://pushover.net 获取）
    pushover_api_token: str | None = None  # 应用 API Token
    ntfy_url: str | None = None
    ntfy_token: str | None = None
    gotify_url: str | None = None
    gotify_token: str | None = None
    custom_webhook_urls: list[str] = field(default_factory=list)
    custom_webhook_bearer_token: str | None = None  # Bearer Token（用于需要认证的 Webhook）
    custom_webhook_body_template: str | None = None  # 自定义 Webhook JSON body 模板
    webhook_verify_ssl: bool = True  # Webhook HTTPS 证书校验，false 可支持自签名（有 MITM 风险）
    discord_bot_token: str | None = None  # Discord Bot Token
    discord_main_channel_id: str | None = None  # Discord 主频道 ID
    discord_webhook_url: str | None = None  # Discord Webhook URL
    discord_interactions_public_key: str | None = None  # Discord Interaction 入站验签公钥
    slack_webhook_url: str | None = None  # Slack Incoming Webhook URL
    slack_bot_token: str | None = None  # Slack Bot Token (xoxb-...)
    slack_channel_id: str | None = None  # Slack 频道 ID (Bot 模式必填)
    astrbot_token: str | None = None
    astrbot_url: str | None = None
    notification_report_channels: list[str] = field(default_factory=list)
    notification_alert_channels: list[str] = field(default_factory=list)
    notification_system_error_channels: list[str] = field(default_factory=list)
    notification_dedup_ttl_seconds: int = 0
    notification_cooldown_seconds: int = 0
    notification_quiet_hours: str = ""
    notification_timezone: str = ""
    notification_min_severity: str = ""
    notification_daily_digest_enabled: bool = False
    single_stock_notify: bool = False
    pushplus_token: str | None = None  # PushPlus Token
    pushplus_topic: str | None = None  # PushPlus 群组编码（一对多推送）
    serverchan3_sendkey: str | None = None  # Server酱3 SendKey
    merge_email_notification: bool = False
    feishu_max_bytes: int = 20000  # 飞书限制约 20KB，默认 20000 字节
    wechat_max_bytes: int = 4000  # 企业微信限制 4096 字节，默认 4000 字节
    discord_max_words: int = 2000  # Discord 限制 2000 字，默认 2000 字
    wechat_msg_type: str = "markdown"  # 企业微信消息类型，默认 markdown 类型
    markdown_to_image_channels: list[str] = field(default_factory=list)  # 逗号分隔：telegram,wechat,custom,email
    markdown_to_image_max_chars: int = 15000  # 超过此长度不转换，避免超大图片
    md2img_engine: str = "wkhtmltoimage"  # wkhtmltoimage | markdown-to-file (Issue #455, better emoji support)
