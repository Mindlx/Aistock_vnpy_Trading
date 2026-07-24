"""System configuration endpoints."""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_system_config_service
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.system_config import (
    DiscoverLLMChannelModelsRequest,
    DiscoverLLMChannelModelsResponse,
    ExportSystemConfigResponse,
    ImportSystemConfigRequest,
    SetupStatusResponse,
    SystemConfigConflictResponse,
    SystemConfigResponse,
    SystemConfigSchemaResponse,
    SystemConfigValidationErrorResponse,
    TestLLMChannelRequest,
    TestLLMChannelResponse,
    TestNotificationChannelRequest,
    TestNotificationChannelResponse,
    UpdateSystemConfigRequest,
    UpdateSystemConfigResponse,
    ValidateSystemConfigRequest,
    ValidateSystemConfigResponse,
)
from src.auth import COOKIE_NAME, is_auth_enabled, refresh_auth_state, verify_session
from src.services.system_config_service import (
    ConfigConflictError,
    ConfigImportError,
    ConfigValidationError,
    SystemConfigService,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class EnvBackupAccessDenied(Exception):
    """Raised when raw `.env` backup access is not allowed for this request."""

    def __init__(self, *, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _allow_env_backup_access(request: Request) -> None:
    """Gate raw .env backup/restore to explicit secure modes.

    - Desktop runtime keeps existing local behavior via DSA_DESKTOP_MODE.
    - Non-desktop runtime must have admin auth enabled and a valid session.
    """
    if os.getenv("DSA_DESKTOP_MODE") == "true":
        return

    refresh_auth_state()
    if not is_auth_enabled():
        raise EnvBackupAccessDenied(
            status_code=403,
            message="System config backup is disabled; enable admin authentication first",
        )

    cookie_val = request.cookies.get(COOKIE_NAME)
    if cookie_val and verify_session(cookie_val):
        return

    raise EnvBackupAccessDenied(
        status_code=401,
        message="System config backup requires a valid admin session",
    )


def _raise_env_backup_access_error(exc: EnvBackupAccessDenied) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={
            "error": "env_backup_access_denied",
            "message": exc.message,
        },
    )


@router.get(
    "/config",
    response_model=SystemConfigResponse,
    responses={
        200: {"description": "Configuration loaded"},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get system configuration",
    description="Read current configuration from .env and return raw values.",
)
def get_system_config(
    include_schema: bool = Query(True, description="Whether to include schema metadata"),
    service: SystemConfigService = Depends(get_system_config_service),
) -> SystemConfigResponse:
    """Load and return current system configuration."""
    try:
        payload = service.get_config(include_schema=include_schema)
        return SystemConfigResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to load system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to load system configuration",
            },
        )


@router.get(
    "/config/setup/status",
    response_model=SetupStatusResponse,
    responses={
        200: {"description": "Setup status loaded"},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get first-run setup status",
    description="Read a side-effect-free setup readiness summary from saved and runtime configuration.",
)
def get_setup_status(
    service: SystemConfigService = Depends(get_system_config_service),
) -> SetupStatusResponse:
    """Return first-run setup status without writing config or reloading runtime state."""
    try:
        payload = service.get_setup_status()
        return SetupStatusResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to load setup status: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to load setup status",
            },
        )


@router.put(
    "/config",
    response_model=UpdateSystemConfigResponse,
    responses={
        200: {"description": "Configuration updated"},
        400: {"description": "Validation failed", "model": SystemConfigValidationErrorResponse},
        409: {"description": "Version conflict", "model": SystemConfigConflictResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Update system configuration",
    description="Update key-value pairs in .env. Mask token preserves existing secret values.",
)
def update_system_config(
    request: UpdateSystemConfigRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> UpdateSystemConfigResponse:
    """Validate and persist system configuration updates."""
    try:
        payload = service.update(
            config_version=request.config_version,
            items=[item.model_dump() for item in request.items],
            mask_token=request.mask_token,
            reload_now=request.reload_now,
        )
        return UpdateSystemConfigResponse.model_validate(payload)
    except ConfigValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_failed",
                "message": "System configuration validation failed",
                "issues": exc.issues,
            },
        )
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "config_version_conflict",
                "message": "Configuration has changed, please reload and retry",
                "current_config_version": exc.current_version,
            },
        )
    except Exception as exc:
        logger.error("Failed to update system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to update system configuration",
            },
        )


@router.get(
    "/config/export",
    response_model=ExportSystemConfigResponse,
    responses={
        200: {"description": "Env exported"},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Env backup disabled", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Export env backup",
    description="Return the raw saved .env content for configuration backup.",
)
def export_system_config(
    request: Request,
    service: SystemConfigService = Depends(get_system_config_service),
) -> ExportSystemConfigResponse:
    """Export the active `.env` file for config backup."""
    try:
        _allow_env_backup_access(request)
    except EnvBackupAccessDenied as exc:
        logger.warning("System config export blocked: %s", exc)
        _raise_env_backup_access_error(exc)

    try:
        payload = service.export_env()
        return ExportSystemConfigResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to export system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to export system configuration",
            },
        )


@router.post(
    "/config/import",
    response_model=UpdateSystemConfigResponse,
    responses={
        200: {"description": "Env imported"},
        400: {
            "description": "Import failed",
            "content": {
                "application/json": {
                    "schema": {
                        "anyOf": [
                            {"$ref": "#/components/schemas/ErrorResponse"},
                            {"$ref": "#/components/schemas/SystemConfigValidationErrorResponse"},
                        ]
                    }
                }
            },
        },
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Env backup disabled", "model": ErrorResponse},
        409: {"description": "Version conflict", "model": SystemConfigConflictResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Import env backup",
    description="Merge raw .env text into the saved configuration with config version conflict protection.",
)
def import_system_config(
    request: ImportSystemConfigRequest,
    request_obj: Request,
    service: SystemConfigService = Depends(get_system_config_service),
) -> UpdateSystemConfigResponse:
    """Import a `.env` backup into the active config."""
    try:
        _allow_env_backup_access(request_obj)
    except EnvBackupAccessDenied as exc:
        logger.warning("System config import blocked: %s", exc)
        _raise_env_backup_access_error(exc)

    try:
        payload = service.import_env(
            config_version=request.config_version,
            content=request.content,
            reload_now=request.reload_now,
        )
        return UpdateSystemConfigResponse.model_validate(payload)
    except ConfigImportError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_import_file",
                "message": exc.message,
            },
        )
    except ConfigValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_failed",
                "message": "System configuration validation failed",
                "issues": exc.issues,
            },
        )
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "config_version_conflict",
                "message": "Configuration has changed, please reload and retry",
                "current_config_version": exc.current_version,
            },
        )
    except Exception as exc:
        logger.error("Failed to import system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to import system configuration",
            },
        )


@router.post(
    "/config/validate",
    response_model=ValidateSystemConfigResponse,
    responses={
        200: {"description": "Validation completed"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Validate system configuration",
    description="Validate submitted configuration values without writing to .env.",
)
def validate_system_config(
    request: ValidateSystemConfigRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> ValidateSystemConfigResponse:
    """Run pre-save validation only."""
    try:
        payload = service.validate(items=[item.model_dump() for item in request.items])
        return ValidateSystemConfigResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to validate system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to validate system configuration",
            },
        )


@router.post(
    "/config/llm/test-channel",
    response_model=TestLLMChannelResponse,
    responses={
        200: {"description": "Channel test completed"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Test one LLM channel",
    description="Run a minimal LLM request against one unsaved or saved channel definition.",
)
def test_llm_channel(
    request: TestLLMChannelRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> TestLLMChannelResponse:
    """Validate and test one channel definition without writing `.env`."""
    try:
        payload = service.test_llm_channel(
            name=request.name,
            protocol=request.protocol,
            base_url=request.base_url,
            api_key=request.api_key,
            models=request.models,
            enabled=request.enabled,
            timeout_seconds=request.timeout_seconds,
            capability_checks=request.capability_checks,
        )
        return TestLLMChannelResponse.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": str(exc),
            },
        )
    except Exception as exc:
        logger.error("Failed to test LLM channel: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to test LLM channel",
            },
        )


@router.post(
    "/config/notification/test-channel",
    response_model=TestNotificationChannelResponse,
    responses={
        200: {"description": "Notification channel test completed"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Test one notification channel",
    description="Send a short test notification using unsaved or saved notification configuration.",
)
def test_notification_channel(
    request: TestNotificationChannelRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> TestNotificationChannelResponse:
    """Validate and test one notification channel without writing `.env`."""
    try:
        payload = service.test_notification_channel(
            channel=request.channel,
            items=[item.model_dump() for item in request.items],
            mask_token=request.mask_token,
            title=request.title,
            content=request.content,
            timeout_seconds=request.timeout_seconds,
        )
        return TestNotificationChannelResponse.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": str(exc),
            },
        )
    except Exception as exc:
        logger.error("Failed to test notification channel: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to test notification channel",
            },
        )


@router.post(
    "/config/llm/discover-models",
    response_model=DiscoverLLMChannelModelsResponse,
    responses={
        200: {"description": "Model discovery completed"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Discover models for one LLM channel",
    description="Call one unsaved or saved channel's `/models` endpoint and return discovered model IDs.",
)
def discover_llm_channel_models(
    request: DiscoverLLMChannelModelsRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> DiscoverLLMChannelModelsResponse:
    """Discover models for one channel definition without writing `.env`."""
    try:
        payload = service.discover_llm_channel_models(
            name=request.name,
            protocol=request.protocol,
            base_url=request.base_url,
            api_key=request.api_key,
            models=request.models,
            timeout_seconds=request.timeout_seconds,
        )
        return DiscoverLLMChannelModelsResponse.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": str(exc),
            },
        )
    except Exception as exc:
        logger.error("Failed to discover LLM channel models: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to discover LLM channel models",
            },
        )


@router.get(
    "/config/schema",
    response_model=SystemConfigSchemaResponse,
    responses={
        200: {"description": "Schema loaded"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get system configuration schema",
    description="Return categorized field metadata used for dynamic settings form rendering.",
)
def get_system_config_schema(
    service: SystemConfigService = Depends(get_system_config_service),
) -> SystemConfigSchemaResponse:
    """Return schema metadata for system configuration fields."""
    try:
        payload = service.get_schema()
        return SystemConfigSchemaResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to load system configuration schema: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to load system configuration schema",
            },
        )


# ── 桩端点：generation-backends / agent-backends / scheduler ──

@router.get("/config/generation-backends/status")
def get_generation_backends_status():
    import urllib.request
    backends = []
    channels_str = os.getenv("LLM_CHANNELS", "")
    for ch_name in [c.strip() for c in channels_str.split(",") if c.strip()]:
        base_url = os.getenv(f"LLM_{ch_name.upper()}_BASE_URL", "")
        model_str = os.getenv(f"LLM_{ch_name.upper()}_MODELS", "")
        enabled = os.getenv(f"LLM_{ch_name.upper()}_ENABLED", "false").lower() == "true"
        available = False
        models = []
        if enabled and base_url:
            try:
                url = base_url.rstrip("/") + "/v1/models"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=2) as resp:
                    data = json.loads(resp.read())
                    models = [m["id"] for m in data.get("data", []) if "id" in m]
                available = len(models) > 0
            except Exception:
                available = False
        backends.append({
            "id": ch_name,
            "type": "litellm",
            "available": available,
            "base_url": base_url,
            "models": models,
            "supportsFunctions": True,
            "supportsVision": False,
        })
    if not backends:
        backends.append({"id": "default", "type": "litellm", "available": False, "models": []})
    return {"backends": backends}


@router.post("/config/generation-backends/status/preview")
def preview_generation_backend_status():
    import urllib.request
    base_url = os.getenv("LLM_QWEN36_BASE_URL", "")
    available = False
    if base_url:
        try:
            url = base_url.rstrip("/") + "/v1/models"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2):
                available = True
        except Exception:
            available = False
    return {"type": "litellm", "available": available}


@router.post("/config/generation-backends/smoke-test")
def smoke_test_generation_backend():
    import urllib.request, json
    base_url = os.getenv("LLM_QWEN36_BASE_URL", "")
    model = os.getenv("LITELLM_MODEL", "").split("/")[-1] or "deepseek-v4-flash"
    if not base_url:
        return {"success": False, "message": "no LLM base URL configured"}
    try:
        payload = json.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}).encode()
        req = urllib.request.Request(
            base_url.rstrip("/") + "/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            success = "choices" in result
            return {"success": success, "message": "smoke test passed" if success else "unexpected response"}
    except Exception as e:
        return {"success": False, "message": f"smoke test failed: {e}"}


@router.get("/config/agent-backends/status")
def get_agent_backends_status():
    agent_model = os.getenv("AGENT_LITELLM_MODEL", "")
    agent_mode = os.getenv("AGENT_MODE", "false").lower() == "true"
    available = agent_mode and bool(agent_model)
    error_code = None if available else ("agent_mode_disabled" if not agent_mode else "model_not_configured")
    return {
        "backend": "litellm",
        "available": available,
        "experimental": False,
        "version": None,
        "errorCode": error_code,
        "message": None,
    }


@router.post("/config/agent-backends/status/preview")
def preview_agent_backend_status():
    return get_agent_backends_status()


@router.get("/scheduler/status")
def get_scheduler_status():
    import subprocess, sqlite3
    # 检查 systemd scheduler 服务状态
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "Aistock_vnpy_Trading-scheduler.service"],
            capture_output=True, text=True, timeout=5,
        )
        svc_status = r.stdout.strip()
    except Exception:
        svc_status = "unknown"

    # 读取最近一次分析时间
    try:
        db_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "stock_analysis.db")
        conn = sqlite3.connect(db_path)
        cur = conn.execute("SELECT MAX(created_at) FROM analysis_history")
        last_run = cur.fetchone()[0]
        conn.close()
    except Exception:
        last_run = None

    return {
        "status": svc_status,
        "enabled": svc_status == "active",
        "last_run": last_run,
        "next_run": None,
    }


@router.post("/scheduler/run-now")
def run_scheduler_now():
    import subprocess
    try:
        subprocess.run(
            ["systemctl", "--user", "start", "Aistock_vnpy_Trading-scheduler.service"],
            capture_output=True, text=True, timeout=15,
        )
        return {"success": True, "message": "scheduler service triggered"}
    except Exception as e:
        return {"success": False, "message": f"trigger failed: {e}"}
