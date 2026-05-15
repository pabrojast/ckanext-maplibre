# encoding: utf-8
"""Resource URL resolution, signed proxy tokens and sanitization helpers."""
import hashlib
import hmac
import logging
import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

try:
    from ckan.plugins import toolkit
except ImportError:  # pragma: no cover - ckan not installed (tests only)
    toolkit = None  # type: ignore

log = logging.getLogger(__name__)

RESOURCE_PROXY_TOKEN_TTL = 3600
PROXY_PATH_PREFIX = '/api/maplibre/resource/'

_TOKEN_QUERY_RE = re.compile(r'([?&])token=[^&]*(&|$)')


class ResourceUtils:
    """URL resolution + HMAC tokens for private resources."""

    def __init__(self, config_manager):
        self.config_manager = config_manager

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------
    def _get_token_secret(self) -> bytes:
        if toolkit is not None:
            for key in ('beaker.session.secret', 'SECRET_KEY',
                        'flask.secret_key'):
                try:
                    value = toolkit.config.get(key) if toolkit.config else None
                except Exception:
                    value = None
                if value:
                    return str(value).encode('utf-8')
        return b'ckanext-maplibre-proxy-fallback'

    def generate_resource_token(self, resource_id: str,
                                ttl_seconds: int = RESOURCE_PROXY_TOKEN_TTL) -> str:
        if not resource_id:
            raise ValueError('resource_id is required to generate a token')
        expiry = int(time.time()) + max(60, int(ttl_seconds))
        payload = f'{resource_id}|{expiry}'.encode('utf-8')
        signature = hmac.new(
            self._get_token_secret(), payload, hashlib.sha256
        ).hexdigest()
        return f'{expiry}.{signature}'

    def verify_resource_token(self, resource_id: str, token: str) -> bool:
        if not resource_id or not token or '.' not in token:
            return False
        expiry_str, _, signature = token.partition('.')
        if not signature:
            return False
        try:
            expiry = int(expiry_str)
        except (TypeError, ValueError):
            return False
        if expiry < int(time.time()):
            return False
        payload = f'{resource_id}|{expiry}'.encode('utf-8')
        expected = hmac.new(
            self._get_token_secret(), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------
    def _site_url(self) -> str:
        configured = self.config_manager.site_url or ''
        if not configured and toolkit is not None:
            try:
                configured = toolkit.config.get('ckan.site_url', '') or ''
            except Exception:
                configured = ''
        return (configured or '').rstrip('/')

    def _to_absolute_url(self, url: str) -> str:
        if not url:
            return url
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme and parsed.netloc:
            return url
        site_url = self._site_url()
        if not site_url:
            return url
        if url.startswith('//'):
            scheme = urllib.parse.urlparse(site_url).scheme or 'https'
            return f'{scheme}:{url}'
        return urllib.parse.urljoin(site_url + '/', url.lstrip('/'))

    def build_proxy_resource_url(self,
                                 resource_id: str,
                                 ttl_seconds: int = RESOURCE_PROXY_TOKEN_TTL,
                                 filename: Optional[str] = None) -> str:
        site_url = self._site_url()
        token = self.generate_resource_token(resource_id, ttl_seconds=ttl_seconds)
        path = f'{PROXY_PATH_PREFIX}{resource_id}/content'
        if filename:
            safe = urllib.parse.quote(filename, safe='.-_')
            if safe:
                path = f'{path}/{safe}'
        return f'{site_url}{path}?token={token}'

    @staticmethod
    def strip_tokens(url: str) -> str:
        """Remove any ``token=...`` query parameter from a URL."""
        if not url:
            return url
        stripped = _TOKEN_QUERY_RE.sub(
            lambda m: m.group(1) if m.group(2) == '&' else '',
            url,
        )
        return stripped.rstrip('?&')

    # ------------------------------------------------------------------
    # SLD helpers (parity with terria_view)
    # ------------------------------------------------------------------
    def get_sld_files_from_dataset(self, package_id: str) -> List[Dict]:
        if not package_id or toolkit is None:
            return []
        try:
            package = toolkit.get_action('package_show')(
                {'ignore_auth': True}, {'id': package_id}
            )
        except Exception as exc:
            log.warning('package_show failed for %s: %s', package_id, exc)
            return []
        out: List[Dict] = []
        for res in package.get('resources') or []:
            fmt = (res.get('format') or '').strip().lower()
            if fmt == 'sld':
                out.append({
                    'id': res.get('id'),
                    'name': res.get('name') or 'SLD style',
                    'url': res.get('url'),
                    'description': res.get('description', ''),
                })
        return out

    # ------------------------------------------------------------------
    # Build viewable layer descriptors for the front-end bootstrap
    # ------------------------------------------------------------------
    def build_viewable_resources(self, resource: Dict,
                                 derived: Optional[List[Dict]] = None,
                                 package_private: Optional[bool] = None,
                                 csv_config: Optional[Dict] = None) -> List[Dict]:
        """Return a list of MapLibre-source-ready descriptors.

        Each item: {id, label, kind, format, url, source_resource_id,
        derived_from, is_primary}. ``csv_config`` (when given) carries the
        user-picked spatial column names for CSV resources::

            {'latitude_field': 'lat', 'longitude_field': 'lon',
             'wkt_field': '', 'delimiter': ','}
        """
        derived = derived or []
        is_private = (
            self._resource_is_private(resource)
            if package_private is None else bool(package_private)
        )
        candidates: List[Dict] = []
        if derived:
            for res in derived:
                desc = self._descriptor_for(
                    res, is_private=is_private,
                    derived_from=resource.get('id'),
                    csv_config=csv_config,
                )
                if desc:
                    candidates.append(desc)
        # Always include the primary resource last so users can still see the
        # original if a derived one is broken.
        primary = self._descriptor_for(
            resource, is_private=is_private, csv_config=csv_config,
        )
        if primary:
            primary['is_primary'] = True
            candidates.append(primary)
        return candidates

    def _descriptor_for(self, resource: Dict, is_private: bool,
                        derived_from: Optional[str] = None,
                        csv_config: Optional[Dict] = None) -> Optional[Dict]:
        if not resource:
            return None
        from .config_manager import ConfigManager
        fmt = ConfigManager.resource_format(resource)
        if not fmt:
            return None

        url = self._resolve_resource_url(resource, is_private=is_private)
        if not url:
            return None

        kind, maplibre_url, source_spec = self._url_and_spec(fmt, url)
        if not kind:
            return None
        descriptor = {
            'id': resource.get('id'),
            'label': resource.get('name') or resource.get('description')
                     or resource.get('id'),
            'kind': kind,
            'format': fmt,
            'url': maplibre_url,
            'sourceSpec': source_spec,
            'sourceResourceId': resource.get('id'),
            'derivedFrom': derived_from,
            'is_primary': False,
        }
        # CSV: the viewer needs to know which columns hold coordinates.
        if (csv_config and ConfigManager.is_tabular(resource)
                and source_spec.get('_maplibre_loader') == 'csv'):
            descriptor['csvFields'] = {
                'latitudeField': (csv_config.get('latitude_field') or '').strip(),
                'longitudeField': (csv_config.get('longitude_field') or '').strip(),
                'wktField': (csv_config.get('wkt_field') or '').strip(),
                'delimiter': csv_config.get('delimiter') or '',
            }
        return descriptor

    def _resolve_resource_url(self, resource: Dict, is_private: bool) -> str:
        """Pick the best URL for a resource (proxy if private, raw otherwise)."""
        if not resource:
            return ''
        if is_private:
            filename = self._extract_filename(resource)
            return self.build_proxy_resource_url(
                resource['id'], filename=filename,
            )
        url = resource.get('url') or ''
        return self._to_absolute_url(url)

    def _resource_is_private(self, resource: Dict) -> bool:
        # CKAN sets resource.package.private indirectly via package_show. We
        # don't always have that here, so consult the package if needed.
        if not resource or toolkit is None:
            return False
        pkg_id = resource.get('package_id')
        if not pkg_id:
            return False
        try:
            pkg = toolkit.get_action('package_show')({}, {'id': pkg_id})
            return bool(pkg.get('private'))
        except Exception:
            return False

    @staticmethod
    def _extract_filename(resource: Dict) -> str:
        for key in ('url', 'name'):
            v = resource.get(key)
            if not v:
                continue
            path = urllib.parse.urlparse(v).path or v
            name = os.path.basename(path.rstrip('/'))
            if name:
                return urllib.parse.unquote(name)
        return ''

    @staticmethod
    def _url_and_spec(fmt: str, url: str) -> Tuple[str, str, Dict]:
        """Return (kind, maplibre_url, source_spec_dict) for a given format.

        The source spec is a JSON object you can directly add via
        ``map.addSource(id, spec)`` on the client. The kind is ``vector`` or
        ``raster`` (for layer creation purposes).
        """
        fmt = (fmt or '').lower()
        if fmt == 'pmtiles':
            return 'vector', f'pmtiles://{url}', {
                'type': 'vector',
                'url': f'pmtiles://{url}',
            }
        if fmt in ('fgb', 'flatgeobuf'):
            return 'vector', url, {
                'type': 'geojson',
                'data': {'type': 'FeatureCollection', 'features': []},
                '_maplibre_loader': 'flatgeobuf',
                '_source_url': url,
            }
        if fmt == 'geojson' or fmt == 'json':
            return 'vector', url, {
                'type': 'geojson',
                'data': url,
            }
        if fmt in ('cog', 'tif', 'tiff', 'geotiff'):
            return 'raster', f'cog://{url}', {
                'type': 'raster',
                'url': f'cog://{url}',
                'tileSize': 256,
            }
        if fmt == 'wms':
            return 'raster', url, {
                'type': 'raster',
                'tiles': [url],
                'tileSize': 256,
            }
        if fmt == 'wmts':
            return 'raster', url, {
                'type': 'raster',
                'tiles': [url],
                'tileSize': 256,
            }
        if fmt in ('csv', 'tsv', 'csv-geo-au', 'csv-geo-nz', 'csv-geo-us'):
            # CSV is rendered client-side: viewer.js fetches the file, picks
            # the lat/lon (or wkt) columns the user configured on the view,
            # and feeds a synthesized GeoJSON FeatureCollection into a
            # geojson source. The actual column names are appended to the
            # spec by the plugin layer (csvFields).
            return 'vector', url, {
                'type': 'geojson',
                'data': {'type': 'FeatureCollection', 'features': []},
                '_maplibre_loader': 'csv',
                '_source_url': url,
            }
        # SHP / ZIP / KML / GPKG handled by the pipeline; if it arrives raw
        # here the viewer just shows a "processing" banner.
        return '', '', {}

    # ------------------------------------------------------------------
    # Stream a private resource (for the proxy endpoint)
    # ------------------------------------------------------------------
    def resolve_private_resource_source(self, resource_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (upstream_url, content_type, filename) for proxying.

        ``upstream_url`` may be an http(s) URL to fetch via ``requests`` or a
        local filesystem path (when CKAN's ``ckan.storage_path`` is used).
        """
        try:
            from ckan.lib import uploader
        except ImportError:
            uploader = None  # type: ignore

        if toolkit is None:
            return None, None, None
        try:
            resource = toolkit.get_action('resource_show')(
                {'ignore_auth': True}, {'id': resource_id}
            )
        except Exception as exc:
            log.warning('resource_show failed for %s: %s', resource_id, exc)
            return None, None, None

        filename = self._extract_filename(resource)
        content_type = self._guess_content_type(filename)

        # Try the CKAN uploader (filesystem or cloudstorage).
        if uploader is not None:
            try:
                upload = uploader.get_resource_uploader(resource)
                # Filesystem uploader
                if hasattr(upload, 'get_path'):
                    path = upload.get_path(resource['id'])
                    if path and os.path.exists(path):
                        return path, content_type, filename
                # cloudstorage / S3
                if hasattr(upload, 'get_url_from_filename'):
                    upstream = upload.get_url_from_filename(
                        resource['id'], filename or 'data'
                    )
                    if upstream:
                        return upstream, content_type, filename
            except Exception as exc:
                log.debug('Uploader resolution failed: %s', exc)

        # Last resort: trust the resource's recorded URL.
        return self._to_absolute_url(resource.get('url', '')), content_type, filename

    @staticmethod
    def _guess_content_type(filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower() if filename else ''
        return {
            '.pmtiles': 'application/octet-stream',
            '.fgb': 'application/octet-stream',
            '.cog': 'image/tiff',
            '.tif': 'image/tiff',
            '.tiff': 'image/tiff',
            '.geojson': 'application/geo+json',
            '.json': 'application/json',
            '.zip': 'application/zip',
            '.shp': 'application/octet-stream',
            '.sld': 'application/xml',
        }.get(ext, 'application/octet-stream')
