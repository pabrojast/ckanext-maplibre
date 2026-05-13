# encoding: utf-8
"""Format detection and view schema for ckanext-maplibre."""
import os
import re
from typing import Dict, Optional


VECTOR_FORMATS = {'geojson', 'json', 'shp', 'zip', 'fgb', 'flatgeobuf',
                  'pmtiles', 'kml', 'gpkg'}
RASTER_FORMATS = {'tif', 'tiff', 'geotiff', 'cog'}
# Keep this aligned with ResourceUtils._url_and_spec().
SERVICE_FORMATS = {'wms', 'wmts'}

SUPPORTED_FORMATS = sorted(VECTOR_FORMATS | RASTER_FORMATS | SERVICE_FORMATS)
SUPPORTED_FORMATS_REGEX = '^(' + '|'.join(re.escape(s) for s in SUPPORTED_FORMATS) + ')$'

PIPELINE_RAW_VECTOR = {'shp', 'zip', 'kml', 'gpkg'}
PIPELINE_RAW_RASTER = {'tif', 'tiff', 'geotiff'}
PIPELINE_CLOUD_NATIVE = {'pmtiles', 'fgb', 'flatgeobuf', 'cog'}


class ConfigManager:
    """Holds plugin-wide settings and resource format helpers."""

    def __init__(self,
                 site_url: str = '',
                 default_title: str = 'MapLibre Viewer',
                 max_view_state_bytes: int = 4 * 1024 * 1024,
                 pipeline_enable: bool = True,
                 pipeline_max_input_bytes: int = 10 * 1024 * 1024 * 1024,
                 cdn_libs: bool = True,
                 basemaps: Optional[Dict] = None,
                 tippecanoe_path: str = '',
                 ogr2ogr_path: str = '',
                 gdal_translate_path: str = '',
                 pmtiles_path: str = ''):
        self.site_url = site_url
        self.default_title = default_title
        self.max_view_state_bytes = max_view_state_bytes
        self.pipeline_enable = pipeline_enable
        self.pipeline_max_input_bytes = pipeline_max_input_bytes
        self.cdn_libs = cdn_libs
        self.basemaps = basemaps or self._default_basemaps()
        self.tippecanoe_path = tippecanoe_path
        self.ogr2ogr_path = ogr2ogr_path
        self.gdal_translate_path = gdal_translate_path
        self.pmtiles_path = pmtiles_path

    @staticmethod
    def _default_basemaps() -> Dict:
        return {
            'osm': {
                'label': 'OpenStreetMap',
                'type': 'raster',
                'tiles': [
                    'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
                ],
                'attribution': '© OpenStreetMap contributors',
                'tileSize': 256,
                'maxzoom': 19,
            },
            'carto-positron': {
                'label': 'Carto Positron',
                'type': 'raster',
                'tiles': [
                    'https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
                ],
                'attribution': '© OpenStreetMap, © Carto',
                'tileSize': 256,
                'maxzoom': 19,
            },
            'esri-imagery': {
                'label': 'Esri World Imagery',
                'type': 'raster',
                'tiles': [
                    'https://server.arcgisonline.com/ArcGIS/rest/services/'
                    'World_Imagery/MapServer/tile/{z}/{y}/{x}',
                ],
                'attribution': 'Tiles © Esri',
                'tileSize': 256,
                'maxzoom': 19,
            },
        }

    @staticmethod
    def resource_format(resource: Dict) -> str:
        """Return a lowercased file extension for a resource."""
        if not resource:
            return ''
        fmt = (resource.get('format') or '').strip().lower()
        if fmt:
            return fmt
        url = resource.get('url') or ''
        if not url:
            return ''
        ext = os.path.splitext(url.split('?', 1)[0])[1]
        return ext.lstrip('.').lower()

    def can_view_resource(self, resource: Dict) -> bool:
        fmt = self.resource_format(resource)
        if not fmt:
            return False
        return re.match(SUPPORTED_FORMATS_REGEX, fmt) is not None

    @staticmethod
    def is_vector(resource: Dict) -> bool:
        return ConfigManager.resource_format(resource) in VECTOR_FORMATS

    @staticmethod
    def is_raster(resource: Dict) -> bool:
        return ConfigManager.resource_format(resource) in RASTER_FORMATS

    @staticmethod
    def is_cloud_native(resource: Dict) -> bool:
        return ConfigManager.resource_format(resource) in PIPELINE_CLOUD_NATIVE

    @staticmethod
    def needs_pipeline(resource: Dict) -> bool:
        fmt = ConfigManager.resource_format(resource)
        if fmt in PIPELINE_CLOUD_NATIVE:
            return False
        return fmt in PIPELINE_RAW_VECTOR or fmt in PIPELINE_RAW_RASTER or fmt == 'geojson'

    def get_schema_info(self) -> Dict:
        """Return a CKAN view schema mapping (field -> list of validators)."""
        from ckan.plugins import toolkit
        ignore_missing = toolkit.get_validator('ignore_missing')
        boolean_validator = toolkit.get_validator('boolean_validator')
        default = toolkit.get_validator('default')
        return {
            'view_state': [ignore_missing],
            'style_source': [ignore_missing],
            'sld_resource_id': [ignore_missing],
            'enable_clustering': [default(False), boolean_validator],
            'show_attributes_popup': [default(True), boolean_validator],
            'basemap': [ignore_missing],
            'show_fields': [ignore_missing],
            'filterable': [default(True), boolean_validator],
            '__extras': [ignore_missing],
        }
