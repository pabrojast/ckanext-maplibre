# encoding: utf-8
"""Helpers for safely embedding JSON inside HTML script tags."""
import json
from typing import Any


def serialize_json_for_script_tag(value: Any) -> str:
    """Return JSON that is safe to inline inside ``<script>`` text nodes."""
    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return (
        encoded
        .replace('<', '\\u003c')
        .replace('>', '\\u003e')
        .replace('&', '\\u0026')
        .replace('\u2028', '\\u2028')
        .replace('\u2029', '\\u2029')
    )
