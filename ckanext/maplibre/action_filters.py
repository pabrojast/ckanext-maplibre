# encoding: utf-8
"""Chained CKAN action filters.

We wrap ``resource_view_create`` / ``resource_view_update`` to validate the
``view_state`` payload before CKAN persists it. This catches oversized blobs
and any token=... slip-through that the front-end forgot to strip.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict

import ckan.plugins.toolkit as toolkit

from .view_state import (
    parse_view_state,
    sanitize_view_state_for_storage,
    serialize_view_state,
)

log = logging.getLogger(__name__)


def chained_resource_view_create(config_manager) -> Callable:
    """Return a chained action that validates view_state on create."""
    @toolkit.chained_action
    def action(original_action, context, data_dict):
        _sanitize_in_place(data_dict, config_manager)
        return original_action(context, data_dict)
    return action


def chained_resource_view_update(config_manager) -> Callable:
    @toolkit.chained_action
    def action(original_action, context, data_dict):
        _sanitize_in_place(data_dict, config_manager)
        return original_action(context, data_dict)
    return action


def _sanitize_in_place(data_dict: Dict, config_manager) -> None:
    """Sanitize fields the user (or a prior bug) may have corrupted."""
    if data_dict.get('view_type') != 'maplibre':
        return

    # Auto-heal title/description if a previous version of the form
    # rendered duplicate inputs and WebOb persisted them as a JSON-ish
    # list literal (``["Maplibre", ""]``). Take the first non-empty
    # entry; if everything is empty fall back to an empty string so
    # CKAN's required-validator surfaces the issue cleanly.
    for field in ('title', 'description'):
        if field in data_dict:
            data_dict[field] = _unwrap_dup_list(data_dict[field])

    raw = data_dict.get('view_state')
    if raw:
        try:
            parsed = parse_view_state(raw)
        except Exception as exc:
            log.warning('view_state parse failed: %s', exc)
            raise toolkit.ValidationError(
                {'view_state': [f'Invalid JSON: {exc}']}
            )
        try:
            cleaned = sanitize_view_state_for_storage(
                parsed,
                max_bytes=config_manager.max_view_state_bytes,
            )
        except ValueError as exc:
            raise toolkit.ValidationError(
                {'view_state': [str(exc)]}
            )
        data_dict['view_state'] = serialize_view_state(cleaned)


def _unwrap_dup_list(value: Any) -> Any:
    """If ``value`` is a JSON-array-looking string or a list, return the
    first non-empty entry. Otherwise return ``value`` unchanged.

    Recovers from a prior bug where the form rendered duplicate
    ``name="title"`` inputs and the resulting list got serialized into
    the database as the string ``["Maplibre", ""]``.
    """
    if isinstance(value, list):
        for entry in value:
            if entry not in ('', None):
                return entry
        return ''
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            try:
                parsed = json.loads(stripped)
            except (TypeError, ValueError):
                return value
            if isinstance(parsed, list):
                for entry in parsed:
                    if isinstance(entry, str) and entry:
                        return entry
                return ''
    return value
