# encoding: utf-8
"""Classify a CKAN resource into the right target format."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


# Heuristic thresholds — see plan section "Regla de decisión".
GEOJSON_KEEP_BYTES = 5 * 1024 * 1024              # 5 MB
FGB_MAX_BYTES = 500 * 1024 * 1024                 # 500 MB
FGB_MAX_FEATURES = 2_000_000
GEOJSON_KEEP_FEATURES = 100_000

VECTOR_RAW_EXTS = {'shp', 'zip', 'kml', 'gpkg', 'geojson', 'json'}
RASTER_RAW_EXTS = {'tif', 'tiff', 'geotiff'}


@dataclass
class Classification:
    kind: str            # 'vector' | 'raster' | 'skip'
    source_format: str   # e.g. 'shp_zip', 'geojson', 'geotiff'
    target_format: str   # 'pmtiles' | 'fgb' | 'cog' | 'geojson' | ''
    estimated_features: Optional[int] = None
    estimated_bytes: Optional[int] = None
    reason: str = ''


def classify_resource(resource: dict, *,
                      local_path: Optional[str] = None) -> Classification:
    """Decide which target format to produce.

    ``local_path`` (when provided) is used to read the on-disk file size; we
    don't try to feature-count without GDAL because that itself is a big job
    deferred to the worker.
    """
    fmt = (resource.get('format') or '').strip().lower()
    if not fmt:
        url = (resource.get('url') or '').split('?', 1)[0]
        fmt = os.path.splitext(url)[1].lstrip('.').lower()

    size_bytes = None
    if local_path and os.path.exists(local_path):
        try:
            size_bytes = os.path.getsize(local_path)
        except OSError:
            size_bytes = None

    if fmt in RASTER_RAW_EXTS:
        return Classification(
            kind='raster',
            source_format=fmt,
            target_format='cog',
            estimated_bytes=size_bytes,
            reason='raster → COG with overviews',
        )

    if fmt == 'zip':
        # Treat as Shapefile zip — pipeline will validate via ogr2ogr.
        return _vector_decision('shp_zip', size_bytes)

    if fmt in VECTOR_RAW_EXTS:
        return _vector_decision(fmt, size_bytes)

    return Classification(
        kind='skip', source_format=fmt or 'unknown',
        target_format='', reason='format not handled by pipeline',
    )


def _vector_decision(source_format: str,
                     size_bytes: Optional[int]) -> Classification:
    if source_format == 'geojson' and (size_bytes is None or
                                       size_bytes < GEOJSON_KEEP_BYTES):
        return Classification(
            kind='vector', source_format=source_format,
            target_format='', estimated_bytes=size_bytes,
            reason='small GeoJSON kept as-is',
        )
    if size_bytes is not None and size_bytes >= FGB_MAX_BYTES:
        return Classification(
            kind='vector', source_format=source_format,
            target_format='pmtiles', estimated_bytes=size_bytes,
            reason=f'≥{FGB_MAX_BYTES} bytes → PMTiles',
        )
    if size_bytes is None:
        # Default to FGB; the worker will switch to PMTiles if the dataset
        # turns out to be huge (post-ogr2ogr feature count check).
        return Classification(
            kind='vector', source_format=source_format,
            target_format='fgb', estimated_bytes=size_bytes,
            reason='size unknown, default FlatGeobuf',
        )
    return Classification(
        kind='vector', source_format=source_format,
        target_format='fgb', estimated_bytes=size_bytes,
        reason=f'{size_bytes} bytes → FlatGeobuf',
    )
