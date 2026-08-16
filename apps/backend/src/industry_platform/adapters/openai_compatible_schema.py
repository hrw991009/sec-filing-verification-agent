"""Bounded JSON and structured-output contract for the OpenAI-compatible adapter."""

import json
from collections.abc import Mapping
from typing import Final, cast

MAX_RESPONSE_SCHEMA_DEPTH: Final = 20
MAX_RESPONSE_SCHEMA_BYTES: Final = 64_000

_SUPPORTED_JSON_SCHEMA_FIELDS: Final = frozenset(
    {
        "additionalProperties",
        "const",
        "description",
        "enum",
        "items",
        "properties",
        "required",
        "type",
    }
)
_SUPPORTED_JSON_TYPES: Final = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)


class InvalidProviderResponse(ValueError):
    """Signal malformed Provider data without retaining its sensitive contents."""


class InvalidProviderRequest(ValueError):
    """Signal a request outside the Adapter's explicit structured-output subset."""


def _reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise InvalidProviderResponse
        document[key] = value
    return document


def _reject_non_finite_number(_: str) -> object:
    raise InvalidProviderResponse


def decode_json_value(value: bytes | str) -> object:
    """Decode strict JSON, rejecting duplicates, non-finite values, and unsafe depth."""

    try:
        return json.loads(
            value,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_non_finite_number,
        )
    except (RecursionError, TypeError, UnicodeDecodeError, ValueError):
        raise InvalidProviderResponse from None


def decode_json_object(value: bytes | str) -> dict[str, object]:
    """Decode one strict JSON object."""

    decoded = decode_json_value(value)
    if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
        raise InvalidProviderResponse
    return cast(dict[str, object], decoded)


def thaw_json_value(value: object) -> object:
    """Convert recursively frozen Runtime JSON back to outbound JSON containers."""

    if isinstance(value, Mapping):
        return {key: thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [thaw_json_value(item) for item in value]
    return value


def _schema_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise InvalidProviderRequest
    return cast(Mapping[str, object], value)


def _schema_sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise InvalidProviderRequest
    return tuple(value)


def validate_supported_schema(schema: Mapping[str, object], *, depth: int = 0) -> None:
    """Validate the deliberately small, locally enforceable JSON Schema subset."""

    if depth == 0:
        try:
            encoded_schema = json.dumps(
                thaw_json_value(schema),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (RecursionError, TypeError, ValueError):
            raise InvalidProviderRequest from None
        if len(encoded_schema) > MAX_RESPONSE_SCHEMA_BYTES:
            raise InvalidProviderRequest
    if depth > MAX_RESPONSE_SCHEMA_DEPTH or set(schema) - _SUPPORTED_JSON_SCHEMA_FIELDS:
        raise InvalidProviderRequest
    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in _SUPPORTED_JSON_TYPES:
        raise InvalidProviderRequest
    if depth == 0 and schema_type != "object":
        raise InvalidProviderRequest

    if "enum" in schema:
        enum_values = _schema_sequence(schema["enum"])
        if not enum_values:
            raise InvalidProviderRequest
    if "description" in schema and not isinstance(schema["description"], str):
        raise InvalidProviderRequest

    properties_value = schema.get("properties")
    required_value = schema.get("required")
    additional_value = schema.get("additionalProperties")
    items_value = schema.get("items")
    if schema_type == "object":
        properties = {} if properties_value is None else _schema_mapping(properties_value)
        for child_schema in properties.values():
            validate_supported_schema(_schema_mapping(child_schema), depth=depth + 1)
        if required_value is None:
            raise InvalidProviderRequest
        required = _schema_sequence(required_value)
        if (
            not all(isinstance(item, str) for item in required)
            or len(required) != len(set(required))
            or set(cast(tuple[str, ...], required)) != set(properties)
        ):
            raise InvalidProviderRequest
        if additional_value is not False:
            raise InvalidProviderRequest
        if items_value is not None:
            raise InvalidProviderRequest
    elif any(value is not None for value in (properties_value, required_value, additional_value)):
        raise InvalidProviderRequest

    if schema_type == "array":
        if items_value is None:
            raise InvalidProviderRequest
        validate_supported_schema(_schema_mapping(items_value), depth=depth + 1)
    elif items_value is not None:
        raise InvalidProviderRequest


def _json_type_matches(value: object, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return value is None


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            thaw_json_value(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (RecursionError, TypeError, ValueError):
        raise InvalidProviderResponse from None


def _validate_json_instance(
    value: object,
    schema: Mapping[str, object],
    *,
    depth: int = 0,
) -> None:
    if depth > MAX_RESPONSE_SCHEMA_DEPTH:
        raise InvalidProviderResponse
    expected_type = cast(str, schema["type"])
    if not _json_type_matches(value, expected_type):
        raise InvalidProviderResponse
    if "const" in schema and _canonical_json(value) != _canonical_json(schema["const"]):
        raise InvalidProviderResponse
    if "enum" in schema and _canonical_json(value) not in {
        _canonical_json(item) for item in _schema_sequence(schema["enum"])
    }:
        raise InvalidProviderResponse

    if expected_type == "object":
        document = cast(dict[str, object], value)
        properties_value = schema.get("properties")
        properties = {} if properties_value is None else _schema_mapping(properties_value)
        required_value = schema.get("required")
        required = () if required_value is None else _schema_sequence(required_value)
        if not set(cast(tuple[str, ...], required)).issubset(document):
            raise InvalidProviderResponse
        for key, item in document.items():
            child_schema = properties.get(key)
            if child_schema is None:
                if schema.get("additionalProperties") is False:
                    raise InvalidProviderResponse
                continue
            _validate_json_instance(
                item,
                _schema_mapping(child_schema),
                depth=depth + 1,
            )
    elif expected_type == "array":
        items_schema = _schema_mapping(schema["items"])
        for item in cast(list[object], value):
            _validate_json_instance(item, items_schema, depth=depth + 1)


def validate_structured_output(output_text: str, schema: Mapping[str, object]) -> None:
    """Reject output that is not strict JSON or violates the declared local schema."""

    validate_supported_schema(schema)
    instance = decode_json_value(output_text)
    _validate_json_instance(instance, schema)
