"""Typed JSON values accepted by PostgreSQL JSONB columns."""

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

__all__ = ["JsonScalar", "JsonValue"]
