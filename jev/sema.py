"""Küçük JSON Schema doğrulayıcı ve metinden JSON ayıklama.

Yalnızca şemalarımızda kullanılan alt küme desteklenir:
type (tek ya da liste), enum, properties, required, additionalProperties (bool),
items, anyOf. Hata iletileri Türkçedir ve JSON yolunu içerir.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).parent / "schemas"

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}

_TYPE_TR = {"object": "nesne", "array": "dizi", "string": "metin", "integer": "tam sayı",
            "number": "sayı", "boolean": "true/false", "null": "null"}


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    errors: list[str] = []
    _validate(value, schema, path, errors)
    return errors


def _validate(v: Any, s: dict, path: str, errors: list[str]) -> None:
    if len(errors) > 50:
        return
    if "anyOf" in s:
        for sub in s["anyOf"]:
            if not validate(v, sub, path):
                return
        errors.append(f"{path}: izin verilen biçimlerden hiçbirine uymuyor")
        return
    t = s.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPE_CHECKS[x](v) for x in types):
            want = " ya da ".join(_TYPE_TR.get(x, x) for x in types)
            errors.append(f"{path}: {want} olmalı, {type(v).__name__} geldi")
            return
    if "enum" in s and v not in s["enum"]:
        errors.append(f"{path}: şunlardan biri olmalı: {', '.join(map(str, s['enum']))} (gelen: {v!r})")
        return
    if isinstance(v, dict) and ("properties" in s or "required" in s):
        props = s.get("properties", {})
        for key in s.get("required", []):
            if key not in v:
                errors.append(f"{path}: '{key}' alanı eksik")
        if s.get("additionalProperties") is False:
            for key in v:
                if key not in props:
                    errors.append(f"{path}: beklenmeyen alan '{key}'")
        for key, sub in props.items():
            if key in v:
                _validate(v[key], sub, f"{path}.{key}", errors)
    if isinstance(v, list) and "items" in s:
        for i, item in enumerate(v):
            _validate(item, s["items"], f"{path}[{i}]", errors)


_FENCE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n\s*```", re.S)


def extract_json(text: str | None) -> Any:
    """Model metninden JSON değeri çıkarır. Bulamazsa None döner."""
    if not text:
        return None
    t = text.strip().lstrip("﻿")
    try:
        return json.loads(t)
    except ValueError:
        pass
    for m in _FENCE.finditer(t):
        try:
            return json.loads(m.group(1))
        except ValueError:
            continue
    dec = json.JSONDecoder()
    best = None
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            obj, end = dec.raw_decode(t, i)
        except ValueError:
            continue
        if isinstance(obj, dict) and (best is None or end - i > best[0]):
            best = (end - i, obj)
    return best[1] if best else None


def schema_text(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
