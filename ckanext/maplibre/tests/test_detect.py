# encoding: utf-8
"""Unit tests for the conversion pipeline classifier."""
import os
import tempfile

import pytest

from ckanext.maplibre.pipeline.detect import (
    FGB_MAX_BYTES,
    GEOJSON_KEEP_BYTES,
    classify_resource,
)


def test_geotiff_routes_to_cog():
    c = classify_resource({'format': 'GeoTIFF'})
    assert c.kind == 'raster'
    assert c.target_format == 'cog'


def test_shp_zip_routes_to_fgb():
    c = classify_resource({'format': 'ZIP'}, local_path=None)
    assert c.kind == 'vector'
    assert c.target_format == 'fgb'


def test_small_geojson_kept_as_is(tmp_path):
    path = tmp_path / 'small.geojson'
    path.write_bytes(b'{"type":"FeatureCollection","features":[]}')
    c = classify_resource({'format': 'geojson'}, local_path=str(path))
    assert c.kind == 'vector'
    assert c.target_format == ''


def test_large_vector_routes_to_pmtiles(tmp_path):
    path = tmp_path / 'huge.shp'
    path.write_bytes(b'0' * (FGB_MAX_BYTES + 1))
    c = classify_resource({'format': 'shp'}, local_path=str(path))
    assert c.kind == 'vector'
    assert c.target_format == 'pmtiles'


def test_unsupported_returns_skip():
    c = classify_resource({'format': 'xlsx'})
    assert c.kind == 'skip'
    assert c.target_format == ''
