"""Structured error codes and classification for provider failures."""

from enum import Enum

from pydantic import BaseModel


class ErrorCode(str, Enum):
    """Structured error codes for provider failures."""

    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_AUTH = "provider_auth"
    PROVIDER_SERVICE_UNAVAILABLE = "provider_service_unavailable"
    PROVIDER_BAD_GATEWAY = "provider_bad_gateway"
    PROVIDER_GATEWAY_TIMEOUT = "provider_gateway_timeout"
    CONTENT_REFUSED = "content_refused"
    INVALID_RESPONSE = "invalid_response"
    CONTEXT_OVERFLOW = "context_overflow"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


class ErrorSeverity(str, Enum):
    """Error severity levels for alerting and escalation."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class StructuredError(BaseModel):
    """Structured error with actionable metadata."""

    code: ErrorCode
    message: str
    retry_eligible: bool
    user_action_required: bool
    severity: ErrorSeverity
    recommended_delay_seconds: int = 60

    @classmethod
    def from_http_status(cls, status_code: int, message: str) -> "StructuredError":
        """Create structured error from HTTP status code."""
        error_map = {
            401: (ErrorCode.PROVIDER_AUTH, False, True, ErrorSeverity.ERROR, 0),
            403: (ErrorCode.PROVIDER_AUTH, False, True, ErrorSeverity.ERROR, 0),
            429: (ErrorCode.PROVIDER_RATE_LIMIT, True, False, ErrorSeverity.WARNING, 120),
            502: (ErrorCode.PROVIDER_BAD_GATEWAY, True, False, ErrorSeverity.WARNING, 60),
            503: (ErrorCode.PROVIDER_SERVICE_UNAVAILABLE, True, False, ErrorSeverity.WARNING, 120),
            504: (ErrorCode.PROVIDER_GATEWAY_TIMEOUT, True, False, ErrorSeverity.ERROR, 180),
        }

        code, retry, action, severity, delay = error_map.get(
            status_code,
            (ErrorCode.UNKNOWN, False, True, ErrorSeverity.ERROR, 60),
        )

        return cls(
            code=code,
            message=message,
            retry_eligible=retry,
            user_action_required=action,
            severity=severity,
            recommended_delay_seconds=delay,
        )

    @classmethod
    def timeout(cls, duration: int) -> "StructuredError":
        """Create timeout error."""
        return cls(
            code=ErrorCode.PROVIDER_TIMEOUT,
            message=f"Timeout du provider après {duration} secondes.",
            retry_eligible=True,
            user_action_required=False,
            severity=ErrorSeverity.ERROR,
            recommended_delay_seconds=min(180, duration // 2),
        )

    @classmethod
    def content_refusal(cls, reason: str) -> "StructuredError":
        """Create content refusal error."""
        return cls(
            code=ErrorCode.CONTENT_REFUSED,
            message=reason,
            retry_eligible=False,
            user_action_required=True,
            severity=ErrorSeverity.WARNING,
            recommended_delay_seconds=0,
        )

    @classmethod
    def invalid_response(cls, detail: str) -> "StructuredError":
        """Create invalid response error."""
        return cls(
            code=ErrorCode.INVALID_RESPONSE,
            message=detail,
            retry_eligible=True,
            user_action_required=False,
            severity=ErrorSeverity.WARNING,
            recommended_delay_seconds=30,
        )
