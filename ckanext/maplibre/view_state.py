# encoding: utf-8
"""Build, parse and sanitize the persisted view_state JSON.

The view_state is the unit of "save view" — a JSON object containing the
MapLibre style (``map.getStyle()``), camera, and UI hints. It is stored on
``resource_view.view_state`` (a string field). Tokens and per-user proxy
URLs are stripped before persistence so the saved view is shareable.
"""
import json
import re
from typing import Any, Dict, Optional


VIEW_STATE_VERSION = 1

DEFAULT_VIEW_STATE: Dict[str, Any] = {
    'version': VIEW_STATE_VERSION,
    'camera': {
        'center': [0.0, 0.0],
        'zoom': 2,
        'bearing': 0,
        'pitch': 0,
    },
    'style': None,
    'ui': {
        'basemap': 'osm',
        'active_layer_ids': [],
        'opacities': {},
    },
}


_TOKEN_QUERY_RE = re.compile(r'([?&])token=[^&]*(&|$)')


def parse_view_state(value: Any, default: Optional[Dict] = None) -> Dict:
    """Parse the stored ``view_state`` (string or dict) into a dict."""
    if not value:
        return _clone(default) if default is not None else _clone(DEFAULT_VIEW_STATE)
    if isinstance(value, dict):
        return _clone(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return _clone(default) if default is not None else _clone(DEFAULT_VIEW_STATE)
        if isinstance(parsed, dict):
            return parsed
    return _clone(default) if default is not None else _clone(DEFAULT_VIEW_STATE)


def serialize_view_state(state: Dict) -> str:
    return json.dumps(state, separators=(',', ':'), ensure_ascii=False)


def sanitize_view_state_for_storage(state: Dict,
                                    max_bytes: int = 4 * 1024 * 1024) -> Dict:
    """Strip per-user tokens, transient sources, and enforce size limit.

    Raises ``ValueError`` if the resulting JSON exceeds ``max_bytes``.
    """
    if not isinstance(state, dict):
        raise ValueError('view_state must be a JSON object')

    out = _clone(state)
    out.setdefault('version', VIEW_STATE_VERSION)

    style = out.get('style')
    if isinstance(style, dict):
        # Strip transient FlatGeobuf streaming sources and per-user tokens.
        sources = style.get('sources')
        if isinstance(sources, dict):
            for src_id, src in list(sources.items()):
                if not isinstance(src, dict):
                    sources.pop(src_id, None)
                    continue
                if src.get('_maplibre_transient'):
                    sources.pop(src_id, None)
                    continue
                _strip_token_from_source(src)
        # Rewrite sprite/glyphs absolute URLs only if they look like our own
        # site (kept as-is otherwise; the viewer will re-resolve them).
        for key in ('sprite', 'glyphs'):
            val = style.get(key)
            if isinstance(val, str) and val:
                style[key] = _strip_token_from_url(val)

    encoded = serialize_view_state(out)
    if len(encoded.encode('utf-8')) > max_bytes:
        raise ValueError(
            f'view_state too large ({len(encoded)} bytes, max {max_bytes})'
        )
    return out


def _strip_token_from_source(src: Dict) -> None:
    for url_key in ('url', 'data'):
        val = src.get(url_key)
        if isinstance(val, str):
            src[url_key] = _strip_token_from_url(val)
    tiles = src.get('tiles')
    if isinstance(tiles, list):
        src['tiles'] = [_strip_token_from_url(t) if isinstance(t, str) else t
                        for t in tiles]


def _strip_token_from_url(url: str) -> str:
    if not url:
        return url
    cleaned = _TOKEN_QUERY_RE.sub(
        lambda m: m.group(1) if m.group(2) == '&' else '',
        url,
    )
    return cleaned.rstrip('?&')


def _clone(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return json.loads(json.dumps(value))
