"""Bounded JSON and local-only schema validation at the application boundary."""

import json
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from local_agent_runtime.errors import RuntimeFailure, invalid_request


def json_text(value: object, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        raise invalid_request("The value must be bounded JSON") from None
    if len(text) > limit:
        raise RuntimeFailure("input_limit_exceeded", "The input exceeds its limit")
    return text


def checked_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, Mapping):
        raise invalid_request("The schema must be an object")
    raw = json_text(schema, 32_000)
    clean: dict[str, Any] = json.loads(raw)

    def check(value: object, depth: int = 0) -> None:
        if depth > 24:
            raise invalid_request("Schema nesting exceeds its limit")
        if isinstance(value, dict):
            # No resolver, arbitrary regular expressions, or dynamic references.
            if {"$ref", "$dynamicRef", "$recursiveRef", "pattern", "patternProperties"} & set(
                value
            ):
                raise invalid_request("Schema references and regular expressions are unsupported")
            for child in value.values():
                check(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check(child, depth + 1)

    check(clean)
    try:
        Draft202012Validator.check_schema(clean)
    except (SchemaError, TypeError, RecursionError):
        raise invalid_request("The schema is invalid") from None
    return clean


def validate_output(value: object, schema: Mapping[str, Any]) -> None:
    try:
        Draft202012Validator(schema).validate(value)
    except (ValidationError, TypeError, RecursionError):
        raise RuntimeFailure(
            "schema_validation_failed", "Provider output does not match the schema", status_code=502
        ) from None
