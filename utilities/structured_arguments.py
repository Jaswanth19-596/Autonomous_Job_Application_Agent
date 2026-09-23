"""Normalize JSON-encoded containers only where the schema expects containers."""

import json


def normalize_tool_arguments(value, schema, root=None):
    root = schema if root is None else root
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return normalize_tool_arguments(value, target, root)
    alternatives = schema.get("oneOf", schema.get("anyOf", []))
    if alternatives:
        # Preserve strings when a string is an allowed alternative.
        if isinstance(value, str) and any(s.get("type") == "string" for s in alternatives):
            return value
        discriminator = schema.get("discriminator", {})
        candidate_value = value
        if isinstance(value, str) and discriminator:
            try:
                candidate_value = json.loads(value)
            except ValueError:
                pass
        if isinstance(candidate_value, dict) and discriminator:
            tag = candidate_value.get(discriminator.get("propertyName"))
            reference = discriminator.get("mapping", {}).get(tag) if isinstance(tag, str) else None
            if reference:
                return normalize_tool_arguments(candidate_value, {"$ref": reference}, root)
        for candidate in alternatives:
            normalized = normalize_tool_arguments(value, candidate, root)
            if normalized != value:
                return normalized
        return value
    kind = schema.get("type")
    if isinstance(value, str) and kind in {"object", "array"}:
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return value
        if (kind == "object" and isinstance(decoded, dict)) or (
            kind == "array" and isinstance(decoded, list)
        ):
            value = decoded
    if isinstance(value, dict) and kind == "object":
        properties = schema.get("properties", {})
        return {
            key: normalize_tool_arguments(item, properties.get(key, {}), root)
            for key, item in value.items()
        }
    if isinstance(value, list) and kind == "array":
        return [normalize_tool_arguments(item, schema.get("items", {}), root) for item in value]
    return value
