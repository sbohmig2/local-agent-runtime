"""Stable public failures without provider content or credentials."""

from __future__ import annotations


class RuntimeFailure(RuntimeError):
    """A bounded failure safe to expose to an authenticated consumer."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def invalid_configuration(message: str) -> RuntimeFailure:
    return RuntimeFailure("invalid_configuration", message)


def provider_unavailable(message: str = "The selected provider is unavailable") -> RuntimeFailure:
    return RuntimeFailure("provider_unavailable", message, status_code=503)


def invalid_request(message: str) -> RuntimeFailure:
    return RuntimeFailure("invalid_request", message)


def not_found(message: str) -> RuntimeFailure:
    return RuntimeFailure("not_found", message, status_code=404)


def conflict(message: str) -> RuntimeFailure:
    return RuntimeFailure("conflict", message, status_code=409)
