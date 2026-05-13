# encoding: utf-8
"""SHP / GeoJSON / KML → FlatGeobuf or PMTiles."""
from __future__ import annotations

import logging
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Optional

from .detect import (
    Classification,
    FGB_MAX_FEATURES,
    GEOJSON_KEEP_FEATURES,
)
from .gdal_wrapper import (
    BinaryNotFound,
    ConversionFailed,
    run_command,
    tippecanoe_version,
)

log = logging.getLogger(__name__)


@dataclass
class VectorResult:
    output_path: str
    target_format: str   # 'fgb' | 'pmtiles' | 'geojson'
    feature_count: Optional[int] = None
    bbox: Optional[tuple] = None


def process(input_path: str, work_dir: str, classification: Classification,
            *, ogr2ogr_path: str = '', tippecanoe_path: str = '',
            pmtiles_path: str = '') -> VectorResult:
    """Convert ``input_path`` to the target format under ``work_dir``."""
    if classification.kind != 'vector':
        raise ValueError('process() requires a vector classification')

    # 1. Materialize a single GDAL-readable input (unzip SHP if needed).
    readable_path = _ensure_gdal_readable(input_path, work_dir)

    # 2. Reproject to EPSG:4326 GeoJSON intermediate. This is the canonical
    #    handoff to tippecanoe and also lets us count features cheaply.
    geojson_path = os.path.join(work_dir, '_intermediate.geojson')
    _run_ogr2ogr(
        readable_path, geojson_path,
        driver='GeoJSON', ogr2ogr_path=ogr2ogr_path,
    )

    feature_count = _count_geojson_features(geojson_path)

    # Re-route based on actual feature count if we didn't know the size.
    target = classification.target_format
    if not target:
        if feature_count <= GEOJSON_KEEP_FEATURES:
            target = ''  # keep as-is, just re-projected
        elif feature_count <= FGB_MAX_FEATURES:
            target = 'fgb'
        else:
            target = 'pmtiles'
    elif target == 'fgb' and feature_count > FGB_MAX_FEATURES:
        # Promote to PMTiles when the worker discovers the dataset is huge.
        target = 'pmtiles'

    if target == 'fgb':
        out = os.path.join(work_dir, 'output.fgb')
        _run_ogr2ogr(geojson_path, out,
                     driver='FlatGeobuf',
                     extra_args=['-lco', 'SPATIAL_INDEX=YES'],
                     ogr2ogr_path=ogr2ogr_path)
        return VectorResult(output_path=out, target_format='fgb',
                            feature_count=feature_count)

    if target == 'pmtiles':
        out = os.path.join(work_dir, 'output.pmtiles')
        _run_tippecanoe(geojson_path, out, tippecanoe_path=tippecanoe_path,
                        pmtiles_path=pmtiles_path)
        return VectorResult(output_path=out, target_format='pmtiles',
                            feature_count=feature_count)

    # Default: keep the re-projected GeoJSON.
    return VectorResult(output_path=geojson_path, target_format='geojson',
                        feature_count=feature_count)


def _ensure_gdal_readable(input_path: str, work_dir: str) -> str:
    """If the input is a SHP zip, unzip to a temp dir and return the .shp."""
    ext = os.path.splitext(input_path)[1].lower()
    if ext != '.zip':
        return input_path
    extract_dir = os.path.join(work_dir, 'unzipped')
    os.makedirs(extract_dir, exist_ok=True)
    safe_unzip(input_path, extract_dir)
    # Find first .shp
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f.lower().endswith('.shp'):
                return os.path.join(root, f)
    raise ConversionFailed('unzip', 1,
                           f'No .shp file inside {input_path}')


def safe_unzip(zip_path: str, dest_dir: str) -> None:
    """Extract with path traversal protection."""
    with zipfile.ZipFile(zip_path, 'r') as zf:
        dest_real = os.path.realpath(dest_dir)
        for member in zf.namelist():
            target = os.path.realpath(os.path.join(dest_dir, member))
            if not (target == dest_real or target.startswith(dest_real + os.sep)):
                raise ConversionFailed(
                    'unzip', 1,
                    f'Refused to extract path-traversal entry: {member}',
                )
            if member.endswith('/'):
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(member) as src, open(target, 'wb') as dst:
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)


def _run_ogr2ogr(input_path: str, output_path: str, *, driver: str,
                 extra_args=None, ogr2ogr_path: str = '') -> None:
    args = [
        '-overwrite',
        '-f', driver,
        '-t_srs', 'EPSG:4326',
    ]
    if extra_args:
        args.extend(extra_args)
    args.extend([output_path, input_path])
    try:
        run_command('ogr2ogr', args, configured_path=ogr2ogr_path)
    except BinaryNotFound:
        raise


def _run_tippecanoe(input_path: str, output_path: str, *,
                    tippecanoe_path: str = '',
                    pmtiles_path: str = '') -> None:
    version = tippecanoe_version(configured_path=tippecanoe_path)
    if version and _supports_pmtiles_output(version):
        args = [
            '-o', output_path,
            '--force',
            '--drop-densest-as-needed',
            '--extend-zooms-if-still-dropping',
            input_path,
        ]
        run_command('tippecanoe', args, configured_path=tippecanoe_path)
        return
    # Fallback: emit MBTiles then convert with go-pmtiles.
    mbtiles_path = output_path + '.mbtiles'
    args = [
        '-o', mbtiles_path,
        '--force',
        '--drop-densest-as-needed',
        '--extend-zooms-if-still-dropping',
        input_path,
    ]
    run_command('tippecanoe', args, configured_path=tippecanoe_path)
    run_command('pmtiles', ['convert', mbtiles_path, output_path],
                configured_path=pmtiles_path)


def _supports_pmtiles_output(version: str) -> bool:
    match = re.search(r'(\d+)\.(\d+)', version or '')
    if not match:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    if major > 2:
        return True
    return major == 2 and minor >= 17


def _count_geojson_features(path: str) -> int:
    """Count features without loading the whole file into memory."""
    try:
        import ijson
    except ImportError:
        ijson = None
    if ijson is not None:
        count = 0
        with open(path, 'rb') as fh:
            for _ in ijson.items(fh, 'features.item'):
                count += 1
        return count
    # Fallback: line-based heuristic that works for the line-delimited form
    # ogr2ogr emits ("{ "type": "Feature", ... },"). Loads full file as a
    # last resort.
    import json
    with open(path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    if isinstance(data, dict) and isinstance(data.get('features'), list):
        return len(data['features'])
    return 0
