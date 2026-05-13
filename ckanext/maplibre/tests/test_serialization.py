# encoding: utf-8
"""Unit tests for safe JSON serialization helpers."""
import json

from ckanext.maplibre.serialization import serialize_json_for_script_tag


def test_serialize_json_for_script_tag_escapes_html_sensitive_chars():
    payload = {'value': '</script><img src=x onerror=1>&\u2028\u2029'}
    encoded = serialize_json_for_script_tag(payload)
    assert '</script>' not in encoded
    assert '&' not in encoded
    assert '\u2028' not in encoded
    assert '\u2029' not in encoded
    assert json.loads(encoded) == payload
