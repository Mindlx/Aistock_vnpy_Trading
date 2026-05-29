## Purpose

The Notification subsystem delivers analysis results, alerts, and system errors to users across 13+ channels (WeChat Work, Feishu, Telegram, Email, Pushover, ntfy, Gotify, PushPlus, Server酱, Custom Webhook, Discord, Slack, AstrBot). It provides noise control (dedup, cooldown, quiet hours), channel routing by message type, and markdown-to-image conversion for platforms that don't support rich text.

## Requirements

### Requirement: The system SHALL support 13 simultaneous notification channels
Each channel SHALL have an independent sender implementation in `src/notification_sender/`. Each sender SHALL implement a `send_to_<channel>()` method (e.g., `send_to_wechat()`, `send_to_feishu()`) that returns a boolean success status. A single channel failure SHALL NOT block other channels. Channels SHALL be auto-detected from configuration — if a channel's required credentials are not configured, it SHALL be silently omitted from the active channel list.

#### Scenario: One channel fails
- **WHEN** WeChat Work webhook returns a 4xx error but Telegram sends successfully
- **THEN** the system SHALL log the WeChat failure and report Telegram as successful — Telegram delivery SHALL NOT be affected

#### Scenario: Channel auto-detection
- **WHEN** only `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are configured
- **THEN** only the Telegram channel SHALL be active; other channels SHALL be silently skipped

### Requirement: Messages SHALL be routed by type
The system SHALL support three route types: `report` (daily analysis), `alert` (event-driven alerts), and `system_error` (error notifications). Each route type SHALL have a configurable channel whitelist via env vars (`NOTIFICATION_REPORT_CHANNELS`, `NOTIFICATION_ALERT_CHANNELS`, `NOTIFICATION_SYSTEM_ERROR_CHANNELS`). The whitelist SHALL be an intersection — only channels listed AND configured SHALL receive that message type.

#### Scenario: Route whitelist
- **WHEN** `NOTIFICATION_REPORT_CHANNELS=feishu,telegram` and both channels are configured
- **THEN** daily analysis reports SHALL only be sent to Feishu and Telegram

### Requirement: Noise control SHALL prevent duplicate and excessive notifications
The system SHALL implement four noise control mechanisms in `src/notification_noise.py`: deduplication by content hash within a configurable TTL (`NOTIFICATION_DEDUP_TTL_SECONDS`), rate limiting by cooldown key (`NOTIFICATION_COOLDOWN_SECONDS`), quiet hours (`NOTIFICATION_QUIET_HOURS` with IANA timezone support), and minimum severity filtering. All controls SHALL be opt-in (disabled by default when env vars are not set).

#### Scenario: Dedup prevents duplicate
- **WHEN** two identical analysis results are generated for the same stock within the dedup TTL window
- **THEN** the second notification SHALL be dropped with reason code `dedup`

#### Scenario: Quiet hours silence
- **WHEN** a notification is triggered during configured quiet hours
- **THEN** it SHALL be dropped with reason code `quiet_hours`

### Requirement: Markdown reports SHALL be convertible to images
For channels configured in `MARKDOWN_TO_IMAGE_CHANNELS`, the system SHALL convert the analysis report from Markdown to PNG using `wkhtmltoimage` or the `markdown-to-file` CLI engine. The image SHALL be 420px wide. Conversion SHALL be skipped if content exceeds `markdown_to_image_max_chars` (default 15,000). On conversion failure, the system SHALL fall back to text delivery.

#### Scenario: Image generation for Telegram
- **WHEN** Telegram is in `MARKDOWN_TO_IMAGE_CHANNELS`
- **THEN** the system SHALL render the report as a PNG and send it as a photo via Telegram Bot API

### Requirement: The system SHALL support multi-user routing
The system SHALL support per-group stock lists and per-group notification channels via `STOCK_GROUP_N` and `NOTIFY_N` configuration pattern. Different user groups SHALL receive analysis for their specific stock lists through their designated channels. Implementation is in `src/core/multi_user.py` and integrated into `pipeline_notification.py`.

#### Scenario: Multi-user group delivery
- **WHEN** `STOCK_GROUP_1=600519,300750` and `NOTIFY_1=wechat:https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx` are configured alongside `STOCK_GROUP_2=AAPL,MSFT` and `NOTIFY_2=telegram:bot_token:chat_id`
- **THEN** Group 1 SHALL receive A-share analysis via WeChat while Group 2 SHALL receive US stock analysis via Telegram. Ungrouped stocks SHALL fall through to the default notification channels.

### Requirement: Notification diagnostics SHALL be available
The system SHALL support a read-only `--check-notify` CLI mode that inspects channel configuration, validates credentials, and reports which channels would be active for each route type — without actually sending any messages.

#### Scenario: Check-notify diagnostics
- **WHEN** the user runs `python main.py --check-notify`
- **THEN** the system SHALL output an analysis of all configured channels, their route assignments, and their configuration status without sending any notifications
