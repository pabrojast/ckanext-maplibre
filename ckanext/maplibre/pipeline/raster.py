# encoding: utf-8
"""GeoTIFF → Cloud Optimized GeoTIFF."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .gdal_wrapper import run_command, ConversionFailed

log = logging.getLogger(__name__)


@dataclass
class RasterResult:
    output_path: str
    target_format: str = 'cog'


def process(input_path: str, work_dir: str,
            *, gdal_translate_path: str = '',
            compression: str = 'DEFLATE',
            blocksize: int = 512,
            overview_resampling: str = 'AVERAGE') -> RasterResult:
    """Convert a GeoTIFF to a COG with overviews.

    GDAL 3.1+ COG driver generates overviews automatically; we still pass
    ``OVERVIEW_RESAMPLING`` explicitly so the choice is deterministic.
    """
    if not os.path.exists(input_path):
        raise ConversionFailed('gdal_translate', 1,
                               f'Input not found: {input_path}')
    output_path = os.path.join(work_dir, 'output.cog.tif')
    args = [
        '-of', 'COG',
        '-co', f'COMPRESS={compression}',
        '-co', f'BLOCKSIZE={blocksize}',
        '-co', f'OVERVIEW_RESAMPLING={overview_resampling}',
        '-co', 'BIGTIFF=IF_SAFER',
        input_path,
        output_path,
    ]
    run_command('gdal_translate', args,
                configured_path=gdal_translate_path)
    if not os.path.exists(output_path):
        raise ConversionFailed('gdal_translate', 1,
                               'COG output file was not produced')
    return RasterResult(output_path=output_path, target_format='cog')
