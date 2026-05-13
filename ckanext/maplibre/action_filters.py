# encoding: utf-8
"""Chained CKAN action filters.

We wrap ``resource_view_create`` / ``resource_view_update`` to validate the
``view_state`` payload before CKAN persists it. This catches oversized blobs
and any token=... slip-through that the front-end forgot to strip.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict

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
    """Validate, sanitize and re-serialize ``view_state`` if present."""
    if data_dict.get('view_type') != 'maplibre':
        return
    raw = data_dict.get('view_state')
    if not raw:
        return
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
