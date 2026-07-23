"""Alert API schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TargetScopeValue = Literal["single_symbol"]
SeverityValue = Literal["info", "warning", "critical"]
DryRunStatusValue = Literal["triggered", "not_triggered", "evaluation_error"]


class AlertRuleCreateRequest(BaseModel):
    name: str | None = Field(None, max_length=64)
    target_scope: TargetScopeValue = "single_symbol"
    target: str = Field(..., min_length=1, max_length=64)
    alert_type: str = Field(..., min_length=1, max_length=32)
    parameters: dict[str, Any] = Field(default_factory=dict)
    severity: SeverityValue = "warning"
    enabled: bool = True
    cooldown_policy: dict[str, Any] | None = None
    notification_policy: dict[str, Any] | None = None


class AlertRuleUpdateRequest(BaseModel):
    name: str | None = Field(None, max_length=64)
    target_scope: TargetScopeValue | None = None
    target: str | None = Field(None, min_length=1, max_length=64)
    alert_type: str | None = Field(None, min_length=1, max_length=32)
    parameters: dict[str, Any] | None = None
    severity: SeverityValue | None = None
    enabled: bool | None = None
    cooldown_policy: dict[str, Any] | None = None
    notification_policy: dict[str, Any] | None = None


class AlertRuleItem(BaseModel):
    id: int
    name: str
    target_scope: str
    target: str
    alert_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    severity: str
    enabled: bool
    source: str
    cooldown_policy: dict[str, Any] | None = None
    notification_policy: dict[str, Any] | None = None
    last_triggered_at: str | None = None
    cooldown_until: str | None = None
    cooldown_active: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AlertRuleListResponse(BaseModel):
    items: list[AlertRuleItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class AlertDeleteResponse(BaseModel):
    deleted: int


class AlertRuleTestResponse(BaseModel):
    rule_id: int
    status: DryRunStatusValue
    triggered: bool
    observed_value: Any | None = None
    message: str


class AlertTriggerItem(BaseModel):
    id: int
    rule_id: int | None = None
    target: str
    observed_value: float | None = None
    threshold: float | None = None
    reason: str | None = None
    data_source: str | None = None
    data_timestamp: str | None = None
    triggered_at: str | None = None
    status: str
    diagnostics: str | None = None


class AlertTriggerListResponse(BaseModel):
    items: list[AlertTriggerItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class AlertNotificationItem(BaseModel):
    id: int
    trigger_id: int | None = None
    channel: str
    attempt: int
    success: bool
    error_code: str | None = None
    retryable: bool
    latency_ms: int | None = None
    diagnostics: str | None = None
    created_at: str | None = None


class AlertNotificationListResponse(BaseModel):
    items: list[AlertNotificationItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
