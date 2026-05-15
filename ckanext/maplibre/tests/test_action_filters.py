# encoding: utf-8
"""Unit tests for action_filters._unwrap_dup_list."""
import pytest

# action_filters imports ckan.plugins.toolkit at module level, so import the
# helper through a thin shim that doesn't trigger that import path.
import importlib
import sys
import types


def _load_unwrap():
    """Load _unwrap_dup_list without importing ckan."""
    # Stub the ckan.plugins.toolkit import.
    if 'ckan' not in sys.modules:
        ckan_mod = types.ModuleType('ckan')
        plugins_mod = types.ModuleType('ckan.plugins')
        toolkit_mod = types.ModuleType('ckan.plugins.toolkit')
        toolkit_mod.chained_action = lambda f: f
        toolkit_mod.ValidationError = type('ValidationError', (Exception,), {})
        plugins_mod.toolkit = toolkit_mod
        ckan_mod.plugins = plugins_mod
        sys.modules['ckan'] = ckan_mod
        sys.modules['ckan.plugins'] = plugins_mod
        sys.modules['ckan.plugins.toolkit'] = toolkit_mod
    mod = importlib.import_module('ckanext.maplibre.action_filters')
    return mod._unwrap_dup_list


unwrap = _load_unwrap()


def test_unwrap_passes_plain_string():
    assert unwrap('Maplibre') == 'Maplibre'


def test_unwrap_passes_empty_string():
    assert unwrap('') == ''


def test_unwrap_passes_none():
    assert unwrap(None) is None


def test_unwrap_json_array_string_takes_first_nonempty():
    assert unwrap('["Maplibre", ""]') == 'Maplibre'


def test_unwrap_json_array_string_with_leading_empty():
    assert unwrap('["", "real"]') == 'real'


def test_unwrap_json_array_string_all_empty_returns_empty():
    assert unwrap('["", ""]') == ''


def test_unwrap_actual_list_value():
    assert unwrap(['Maplibre', '']) == 'Maplibre'


def test_unwrap_keeps_non_array_brackets():
    # Not a JSON array — leave it alone.
    assert unwrap('[broken') == '[broken'
