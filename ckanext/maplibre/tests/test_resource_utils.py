# encoding: utf-8
"""Unit tests for ResourceUtils (token sign/verify + URL helpers)."""
import time
import types

import pytest

from ckanext.maplibre.config_manager import ConfigManager
from ckanext.maplibre.resource_utils import ResourceUtils


@pytest.fixture
def utils(monkeypatch):
    """Provide a ResourceUtils with a stub toolkit.config."""
    cm = ConfigManager(site_url='https://ckan.example.org')
    ru = ResourceUtils(cm)
    monkeypatch.setattr(ru, '_get_token_secret', lambda: b'test-secret')
    return ru


def test_token_round_trip(utils):
    token = utils.generate_resource_token('abc-123')
    assert utils.verify_resource_token('abc-123', token)


def test_token_rejects_wrong_resource(utils):
    token = utils.generate_resource_token('abc-123')
    assert not utils.verify_resource_token('other-id', token)


def test_token_rejects_tampered_signature(utils):
    token = utils.generate_resource_token('abc-123')
    expiry, _, sig = token.partition('.')
    tampered = f'{expiry}.{"0" * len(sig)}'
    assert not utils.verify_resource_token('abc-123', tampered)


def test_token_rejects_expired(utils):
    token = utils.generate_resource_token('abc-123', ttl_seconds=60)
    # Forge an expired token with the same secret
    expiry = int(time.time()) - 10
    import hashlib
    import hmac
    payload = f'abc-123|{expiry}'.encode('utf-8')
    sig = hmac.new(b'test-secret', payload, hashlib.sha256).hexdigest()
    expired = f'{expiry}.{sig}'
    assert not utils.verify_resource_token('abc-123', expired)


def test_strip_tokens_removes_token_param():
    cleaned = ResourceUtils.strip_tokens(
        'https://x.org/a.pmtiles?token=abc&z=1'
    )
    assert 'token=' not in cleaned
    assert 'z=1' in cleaned


def test_strip_tokens_handles_single_param():
    cleaned = ResourceUtils.strip_tokens('https://x.org/a.pmtiles?token=abc')
    assert cleaned == 'https://x.org/a.pmtiles'


def test_url_spec_for_pmtiles():
    kind, url, spec = ResourceUtils._url_and_spec(
        'pmtiles', 'https://x.org/a.pmtiles')
    assert kind == 'vector'
    assert url.startswith('pmtiles://')
    assert spec['type'] == 'vector'


def test_url_spec_for_cog():
    kind, url, spec = ResourceUtils._url_and_spec(
        'cog', 'https://x.org/a.tif')
    assert kind == 'raster'
    assert url.startswith('cog://')
    assert spec['type'] == 'raster'


def test_build_viewable_resources_uses_proxy_for_private_resources(utils):
    resources = utils.build_viewable_resources(
        {'id': 'res-1', 'url': '/dataset/private.geojson', 'format': 'geojson'},
        package_private=True,
    )
    assert resources
    assert resources[0]['url'].startswith(
        'https://ckan.example.org/api/maplibre/resource/res-1/content'
    )


def test_build_viewable_resources_keeps_direct_url_for_public_resources(utils):
    resources = utils.build_viewable_resources(
        {'id': 'res-1', 'url': '/dataset/public.geojson', 'format': 'geojson'},
        package_private=False,
    )
    assert resources
    assert resources[0]['url'] == 'https://ckan.example.org/dataset/public.geojson'
