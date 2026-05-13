# encoding: utf-8
"""Unit tests for ckanext.maplibre.config_manager."""
import pytest

from ckanext.maplibre.config_manager import (
    ConfigManager,
    PIPELINE_CLOUD_NATIVE,
)


@pytest.mark.parametrize('resource,expected', [
    ({'format': 'PMTiles'}, True),
    ({'format': 'fgb'}, True),
    ({'format': 'FlatGeobuf'}, True),
    ({'format': 'geojson'}, True),
    ({'format': 'tif'}, True),
    ({'format': 'cog'}, True),
    ({'format': 'shp'}, True),
    ({'format': 'wms'}, True),
    ({'format': 'wfs'}, False),
    ({'format': ''}, False),
    ({'format': 'xlsx'}, False),
    ({'url': 'http://example.com/data.pmtiles'}, True),
])
def test_can_view_resource(resource, expected):
    cm = ConfigManager()
    assert cm.can_view_resource(resource) is expected


def test_needs_pipeline_skips_already_cloud_native():
    cm = ConfigManager()
    for fmt in PIPELINE_CLOUD_NATIVE:
        assert not ConfigManager.needs_pipeline({'format': fmt}), fmt


def test_needs_pipeline_for_shp_and_tiff():
    assert ConfigManager.needs_pipeline({'format': 'shp'})
    assert ConfigManager.needs_pipeline({'format': 'tif'})
    assert ConfigManager.needs_pipeline({'format': 'geojson'})


def test_resource_format_from_url():
    assert ConfigManager.resource_format(
        {'url': 'https://x.org/file.GeoTIFF?token=abc'}
    ) == 'geotiff'


def test_default_basemaps_have_required_keys():
    cm = ConfigManager()
    for key, bm in cm.basemaps.items():
        assert bm.get('tiles'), key
        assert bm.get('type'), key
        assert bm.get('label'), key
