# encoding: utf-8
"""Main plugin for ckanext-maplibre."""
import json
import logging
from typing import Any, Dict, List, Optional

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

from .config_manager import ConfigManager, PIPELINE_CLOUD_NATIVE
from .resource_utils import ResourceUtils
from .serialization import serialize_json_for_script_tag
from .view_state import (
    DEFAULT_VIEW_STATE,
    parse_view_state,
)
from .api_endpoints import maplibre_api
from . import action_filters

log = logging.getLogger(__name__)

PLUGIN_NAME = 'maplibre'


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class MapLibreViewPlugin(plugins.SingletonPlugin):
    """MapLibre GL JS resource view plugin."""

    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IConfigurable, inherit=True)
    plugins.implements(plugins.IResourceView, inherit=True)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.ITemplateHelpers)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IResourceController, inherit=True)

    def __init__(self, name: Optional[str] = None):
        super().__init__()
        self.config_manager = ConfigManager()
        self.resource_utils = ResourceUtils(self.config_manager)

    # ---------------------------------------------------------------------
    # IConfigurer
    # ---------------------------------------------------------------------
    def update_config(self, config_: Dict) -> None:
        toolkit.add_template_directory(config_, 'templates')
        toolkit.add_public_directory(config_, 'public')
        toolkit.add_resource('public/maplibre_viewer', 'ckanext_maplibre')

    # ---------------------------------------------------------------------
    # IConfigurable
    # ---------------------------------------------------------------------
    def configure(self, config: Dict) -> None:
        cm = self.config_manager
        cm.site_url = config.get('ckan.site_url', '')
        cm.default_title = config.get(
            f'ckanext.{PLUGIN_NAME}.default_title', 'MapLibre Viewer'
        )
        cm.max_view_state_bytes = _as_int(
            config.get(f'ckanext.{PLUGIN_NAME}.max_view_state_bytes'),
            4 * 1024 * 1024,
        )
        cm.pipeline_enable = _as_bool(
            config.get(f'ckanext.{PLUGIN_NAME}.pipeline.enable'), True
        )
        cm.pipeline_max_input_bytes = _as_int(
            config.get(f'ckanext.{PLUGIN_NAME}.pipeline.max_input_bytes'),
            10 * 1024 * 1024 * 1024,
        )
        cm.cdn_libs = _as_bool(
            config.get(f'ckanext.{PLUGIN_NAME}.cdn_libs'), True
        )
        raw_basemaps = config.get(f'ckanext.{PLUGIN_NAME}.basemaps')
        if raw_basemaps:
            try:
                cm.basemaps = json.loads(raw_basemaps)
            except (TypeError, ValueError):
                log.warning('ckanext.maplibre.basemaps is not valid JSON, '
                            'falling back to defaults.')
        cm.tippecanoe_path = config.get(
            f'ckanext.{PLUGIN_NAME}.pipeline.tippecanoe_path', ''
        )
        cm.ogr2ogr_path = config.get(
            f'ckanext.{PLUGIN_NAME}.pipeline.ogr2ogr_path', ''
        )
        cm.gdal_translate_path = config.get(
            f'ckanext.{PLUGIN_NAME}.pipeline.gdal_translate_path', ''
        )
        cm.pmtiles_path = config.get(
            f'ckanext.{PLUGIN_NAME}.pipeline.pmtiles_path', ''
        )

        if cm.pipeline_enable:
            self._check_pipeline_binaries()

    def _check_pipeline_binaries(self) -> None:
        import shutil
        bins = {
            'ogr2ogr': self.config_manager.ogr2ogr_path or 'ogr2ogr',
            'gdal_translate': self.config_manager.gdal_translate_path or 'gdal_translate',
            'tippecanoe': self.config_manager.tippecanoe_path or 'tippecanoe',
        }
        missing = [name for name, path in bins.items() if not shutil.which(path)]
        if missing:
            log.warning(
                'ckanext-maplibre pipeline binaries missing: %s. '
                'Conversions for those formats will be skipped.',
                ', '.join(missing),
            )

    # ---------------------------------------------------------------------
    # IBlueprint
    # ---------------------------------------------------------------------
    def get_blueprint(self):
        return maplibre_api

    # ---------------------------------------------------------------------
    # ITemplateHelpers
    # ---------------------------------------------------------------------
    def get_helpers(self) -> Dict:
        return {
            'maplibre_pipeline_status': self._helper_pipeline_status,
            'maplibre_get_derived_resources': self._helper_get_derived_resources,
            'maplibre_get_sld_files': self._helper_get_sld_files,
            'maplibre_basemaps_json': self._helper_basemaps_json,
            'maplibre_use_cdn_libs': lambda: self.config_manager.cdn_libs,
        }

    def _helper_pipeline_status(self, resource_id: str) -> Dict:
        from .pipeline.jobs import get_status
        return get_status(resource_id) or {'status': 'idle'}

    def _helper_get_derived_resources(self, package: Dict,
                                      base_resource_id: str) -> List[Dict]:
        if not package:
            return []
        derived: List[Dict] = []
        for res in package.get('resources') or []:
            extras = res.get('extras') or {}
            if extras.get('maplibre_derived_from') == base_resource_id:
                derived.append(res)
        return derived

    def _helper_get_sld_files(self, package_id: str) -> List[Dict]:
        try:
            return self.resource_utils.get_sld_files_from_dataset(package_id)
        except Exception as exc:
            log.warning('maplibre_get_sld_files failed: %s', exc)
            return []

    def _helper_basemaps_json(self) -> str:
        return json.dumps(self.config_manager.basemaps)

    # ---------------------------------------------------------------------
    # IResourceView
    # ---------------------------------------------------------------------
    def info(self) -> Dict:
        return {
            'name': PLUGIN_NAME,
            'title': toolkit._('MapLibre Viewer'),
            'default_title': toolkit._(self.config_manager.default_title),
            'icon': 'globe',
            'always_available': True,
            'filterable': True,
            'iframed': False,
            'schema': self.config_manager.get_schema_info(),
            'preview_enabled': True,
            'requires_datastore': False,
        }

    def can_view(self, data_dict: Dict) -> bool:
        resource = data_dict.get('resource') or {}
        return self.config_manager.can_view_resource(resource)

    def view_template(self, context: Dict, data_dict: Dict) -> str:
        return 'maplibre_view.html'

    def form_template(self, context: Dict, data_dict: Dict) -> str:
        return 'maplibre_form.html'

    def setup_template_variables(self, context: Dict, data_dict: Dict) -> Dict:
        resource = data_dict.get('resource') or {}
        resource_view = data_dict.get('resource_view') or {}

        try:
            package = toolkit.get_action('package_show')(
                {'user': toolkit.g.user if hasattr(toolkit.g, 'user') else None},
                {'id': resource.get('package_id')},
            )
        except Exception:
            package = {}

        derived_resources = self._helper_get_derived_resources(
            package, resource.get('id', ''))

        viewable_resources = self.resource_utils.build_viewable_resources(
            resource=resource,
            derived=derived_resources,
            package_private=package.get('private'),
        )

        try:
            view_state = parse_view_state(
                resource_view.get('view_state', ''),
                default=DEFAULT_VIEW_STATE,
            )
        except ValueError:
            view_state = DEFAULT_VIEW_STATE

        viewer_bootstrap = {
            'viewId': resource_view.get('id', ''),
            'resourceId': resource.get('id', ''),
            'packageId': resource.get('package_id', ''),
            'viewState': view_state,
            'viewableResources': viewable_resources,
            'basemaps': self.config_manager.basemaps,
            'defaultBasemap': resource_view.get('basemap')
                              or 'osm',
            'styleSource': resource_view.get('style_source') or 'auto',
            'enableClustering': _as_bool(
                resource_view.get('enable_clustering'), False,
            ),
            'showAttributesPopup': _as_bool(
                resource_view.get('show_attributes_popup'), True,
            ),
            'apiBase': '/api/maplibre',
            'useCdnLibs': self.config_manager.cdn_libs,
            'canSave': bool(getattr(toolkit.g, 'user', None)),
            'pipelineStatus': self._helper_pipeline_status(resource.get('id', '')),
        }

        return {
            'resource': resource,
            'resource_view': resource_view,
            'viewer_bootstrap_json': serialize_json_for_script_tag(
                viewer_bootstrap),
            'derived_resources': derived_resources,
            'use_cdn_libs': self.config_manager.cdn_libs,
        }

    # ---------------------------------------------------------------------
    # IActions — chained validators
    # ---------------------------------------------------------------------
    def get_actions(self) -> Dict:
        return {
            'resource_view_create': action_filters.chained_resource_view_create(
                self.config_manager),
            'resource_view_update': action_filters.chained_resource_view_update(
                self.config_manager),
        }

    # ---------------------------------------------------------------------
    # IResourceController — enqueue conversion on upload
    # ---------------------------------------------------------------------
    def after_resource_create(self, context: Dict, resource: Dict) -> None:
        self._maybe_enqueue_conversion(resource)

    def after_resource_update(self, context: Dict, resource: Dict) -> None:
        self._maybe_enqueue_conversion(resource)

    # Legacy CKAN 2.9 names (kept for compatibility).
    def after_create(self, context: Dict, resource: Dict) -> None:
        self._maybe_enqueue_conversion(resource)

    def after_update(self, context: Dict, resource: Dict) -> None:
        self._maybe_enqueue_conversion(resource)

    def _maybe_enqueue_conversion(self, resource: Dict) -> None:
        if not self.config_manager.pipeline_enable:
            return
        fmt = ConfigManager.resource_format(resource)
        if fmt in PIPELINE_CLOUD_NATIVE:
            return
        if not ConfigManager.needs_pipeline(resource):
            return
        # Skip resources that are themselves outputs of our pipeline.
        extras = resource.get('extras') or {}
        if extras.get('maplibre_derived_from'):
            return
        try:
            from .pipeline.jobs import enqueue_conversion
            enqueue_conversion(resource['id'])
            log.info('Enqueued maplibre conversion for resource %s (format=%s)',
                     resource.get('id'), fmt)
        except Exception as exc:
            log.warning('Failed to enqueue maplibre conversion for %s: %s',
                        resource.get('id'), exc)
