# encoding: utf-8
"""Unit tests for ckanext.maplibre.view_state."""
import json

import pytest

from ckanext.maplibre.view_state import (
    DEFAULT_VIEW_STATE,
    parse_view_state,
    sanitize_view_state_for_storage,
    serialize_view_state,
)


def test_parse_view_state_handles_string():
    raw = '{"version": 1, "camera": {"center": [1, 2]}}'
    parsed = parse_view_state(raw)
    assert parsed['camera']['center'] == [1, 2]


def test_parse_view_state_falls_back_on_invalid():
    parsed = parse_view_state('not-json')
    assert parsed == DEFAULT_VIEW_STATE


def test_sanitize_strips_token_query_params():
    state = {
        'version': 1,
        'style': {
            'sources': {
                'a': {
                    'type': 'vector',
                    'url': 'pmtiles://https://x.org/data.pmtiles?token=abc',
                    'tiles': [
                        'https://x.org/{z}/{x}/{y}.pbf?token=abc&other=1',
                    ],
                },
                'b': {
                    'type': 'geojson',
                    'data': 'https://x.org/a.geojson?token=xyz',
                    '_maplibre_transient': True,
                },
            },
            'sprite': 'https://x.org/sprite?token=abc',
            'glyphs': 'https://x.org/glyphs?token=abc',
        },
    }
    cleaned = sanitize_view_state_for_storage(state, max_bytes=1_000_000)
    assert 'b' not in cleaned['style']['sources'], 'transient source must be dropped'
    src_a = cleaned['style']['sources']['a']
    assert 'token=' not in src_a['url']
    assert all('token=' not in t for t in src_a['tiles'])
    assert 'token=' not in cleaned['style']['sprite']
    assert 'token=' not in cleaned['style']['glyphs']


def test_sanitize_rejects_oversize_payload():
    big_payload = {'version': 1, 'blob': 'x' * 50}
    with pytest.raises(ValueError):
        sanitize_view_state_for_storage(big_payload, max_bytes=10)


def test_serialize_round_trip():
    state = {'version': 1, 'camera': {'zoom': 5}}
    encoded = serialize_view_state(state)
    assert json.loads(encoded) == state
