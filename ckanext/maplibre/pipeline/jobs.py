# encoding: utf-8
"""RQ job entrypoints — keep these importable without CKAN context.

Status is persisted in Redis under ``maplibre:pipeline:<resource_id>``. If
Redis isn't available we fall back to an in-process dict (useful for tests
and dev installs without a worker).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.parse
from typing import Any, Dict, Optional

import requests

from ckan.plugins import toolkit

from .detect import classify_resource

log = logging.getLogger(__name__)

REDIS_KEY_PREFIX = 'maplibre:pipeline:'
_LOCAL_STATUS: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Status persistence
# ---------------------------------------------------------------------------
def _redis():
    try:
        from ckan.lib.redis import connect_to_redis
        return connect_to_redis()
    except Exception as exc:
        log.debug('Redis unavailable, using in-process status store: %s', exc)
        return None


def set_status(resource_id: str, **fields) -> Dict[str, Any]:
    record = get_status(resource_id) or {}
    record.update(fields)
    record['resource_id'] = resource_id
    record['updated_at'] = int(time.time())
    payload = json.dumps(record)
    r = _redis()
    if r is not None:
        try:
            r.set(REDIS_KEY_PREFIX + resource_id, payload, ex=7 * 24 * 3600)
            return record
        except Exception as exc:
            log.warning('redis set failed: %s', exc)
    _LOCAL_STATUS[resource_id] = record
    return record


def get_status(resource_id: str) -> Optional[Dict[str, Any]]:
    r = _redis()
    if r is not None:
        try:
            raw = r.get(REDIS_KEY_PREFIX + resource_id)
            if raw:
                return json.loads(raw)
        except Exception as exc:
            log.warning('redis get failed: %s', exc)
    return _LOCAL_STATUS.get(resource_id)


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------
def enqueue_conversion(resource_id: str) -> None:
    """Enqueue a conversion job for ``resource_id`` on the ``maplibre`` queue."""
    set_status(resource_id, status='queued', error='')
    try:
        toolkit.enqueue_job(
            convert_resource, [resource_id],
            queue='maplibre',
            title=f'maplibre.convert_resource:{resource_id}',
        )
    except Exception as exc:
        # CKAN < 2.7 or no RQ — run inline (best-effort, may block).
        log.warning('enqueue_job failed (%s); running inline', exc)
        convert_resource(resource_id)


# ---------------------------------------------------------------------------
# Worker entrypoint
# ---------------------------------------------------------------------------
def convert_resource(resource_id: str) -> Dict[str, Any]:
    """Worker entrypoint. Returns the final status record."""
    set_status(resource_id, status='running', error='', target_resource_id='')

    try:
        plugin_cfg = _get_plugin_config()
        resource = toolkit.get_action('resource_show')(
            {'ignore_auth': True}, {'id': resource_id})
    except Exception as exc:
        return set_status(resource_id, status='failed', error=str(exc))

    if (resource.get('extras') or {}).get('maplibre_derived_from'):
        return set_status(resource_id, status='skipped',
                          error='resource is itself a derived artifact')

    try:
        with tempfile.TemporaryDirectory(prefix='maplibre-') as work_dir:
            input_path = _download_resource(resource, work_dir, plugin_cfg)
            classification = classify_resource(
                resource, local_path=input_path)
            if classification.kind == 'skip' or not classification.target_format:
                return set_status(resource_id, status='skipped',
                                  reason=classification.reason,
                                  classification=classification.__dict__)

            if classification.kind == 'vector':
                from . import vector
                result = vector.process(
                    input_path, work_dir, classification,
                    ogr2ogr_path=plugin_cfg.get('ogr2ogr_path', ''),
                    tippecanoe_path=plugin_cfg.get('tippecanoe_path', ''),
                    pmtiles_path=plugin_cfg.get('pmtiles_path', ''),
                )
            elif classification.kind == 'raster':
                from . import raster
                result = raster.process(
                    input_path, work_dir,
                    gdal_translate_path=plugin_cfg.get('gdal_translate_path', ''),
                )
            else:
                return set_status(resource_id, status='failed',
                                  error=f'unknown kind {classification.kind}')

            derived_id = _publish_derived_resource(
                resource=resource,
                output_path=result.output_path,
                target_format=result.target_format,
            )
            return set_status(
                resource_id, status='done',
                target_resource_id=derived_id,
                target_format=result.target_format,
                feature_count=getattr(result, 'feature_count', None),
            )
    except Exception as exc:
        log.exception('maplibre conversion failed for %s', resource_id)
        return set_status(resource_id, status='failed', error=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_plugin_config() -> Dict[str, str]:
    cfg = toolkit.config or {}
    return {
        'ogr2ogr_path': cfg.get(
            'ckanext.maplibre.pipeline.ogr2ogr_path', ''),
        'tippecanoe_path': cfg.get(
            'ckanext.maplibre.pipeline.tippecanoe_path', ''),
        'gdal_translate_path': cfg.get(
            'ckanext.maplibre.pipeline.gdal_translate_path', ''),
        'pmtiles_path': cfg.get(
            'ckanext.maplibre.pipeline.pmtiles_path', ''),
        'max_input_bytes': int(cfg.get(
            'ckanext.maplibre.pipeline.max_input_bytes',
            10 * 1024 * 1024 * 1024)),
    }


def _download_resource(resource: Dict, work_dir: str,
                       plugin_cfg: Dict[str, str]) -> str:
    """Download the resource file to ``work_dir`` and return the local path."""
    # 1. Filesystem uploader: the file is already on disk.
    try:
        from ckan.lib import uploader
        upload = uploader.get_resource_uploader(resource)
        if hasattr(upload, 'get_path'):
            local_path = upload.get_path(resource['id'])
            if local_path and os.path.exists(local_path):
                _check_size(local_path, plugin_cfg)
                return local_path
        if hasattr(upload, 'get_url_from_filename'):
            url = upload.get_url_from_filename(
                resource['id'],
                _filename_from_resource(resource) or 'data',
            )
            if url:
                return _http_download(url, work_dir, plugin_cfg)
    except Exception as exc:
        log.debug('uploader resolution failed: %s', exc)

    # 2. Last resort: download from resource.url.
    url = resource.get('url')
    if not url:
        raise RuntimeError(f'resource {resource["id"]} has no URL to download')
    return _http_download(url, work_dir, plugin_cfg)


def _http_download(url: str, work_dir: str,
                   plugin_cfg: Dict[str, str]) -> str:
    filename = os.path.basename(urllib.parse.urlparse(url).path) or 'input'
    out_path = os.path.join(work_dir, filename)
    max_bytes = plugin_cfg.get('max_input_bytes', 10 * 1024 * 1024 * 1024)
    bytes_written = 0
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out_path, 'wb') as fh:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    raise RuntimeError(
                        f'Input exceeds max_input_bytes ({max_bytes})'
                    )
                fh.write(chunk)
    return out_path


def _check_size(path: str, plugin_cfg: Dict[str, str]) -> None:
    max_bytes = plugin_cfg.get('max_input_bytes', 10 * 1024 * 1024 * 1024)
    size = os.path.getsize(path)
    if size > max_bytes:
        raise RuntimeError(
            f'Input file is {size} bytes, exceeds max_input_bytes ({max_bytes})'
        )


def _filename_from_resource(resource: Dict) -> str:
    name = resource.get('name') or ''
    url = resource.get('url') or ''
    if '/' in url:
        return os.path.basename(urllib.parse.urlparse(url).path)
    return name


def _publish_derived_resource(*, resource: Dict, output_path: str,
                              target_format: str) -> str:
    """Upload ``output_path`` as a new sibling resource of the dataset."""
    from werkzeug.datastructures import FileStorage
    base_name = resource.get('name') or os.path.basename(
        urllib.parse.urlparse(resource.get('url') or '').path
    ) or 'dataset'
    base_root = os.path.splitext(base_name)[0]
    derived_name = f'{base_root}.{target_format}'
    derived_format = target_format.upper()

    with open(output_path, 'rb') as fh:
        upload = FileStorage(
            stream=fh,
            filename=os.path.basename(output_path),
            content_type=_content_type_for(target_format),
        )
        action_payload = {
            'package_id': resource['package_id'],
            'name': derived_name,
            'format': derived_format,
            'description': (
                f'Auto-generated by ckanext-maplibre from '
                f'resource {resource["id"]}.'
            ),
            'upload': upload,
            'maplibre_derived_from': resource['id'],
            'maplibre_pipeline_version': '1',
        }
        context = {'ignore_auth': True, 'user': 'ckan.system'}
        created = toolkit.get_action('resource_create')(
            context, action_payload)
    return created.get('id') or ''


def _content_type_for(target_format: str) -> str:
    return {
        'pmtiles': 'application/octet-stream',
        'fgb': 'application/octet-stream',
        'cog': 'image/tiff',
        'geojson': 'application/geo+json',
    }.get(target_format, 'application/octet-stream')
