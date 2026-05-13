# encoding: utf-8
"""Flask Blueprint with the JSON API used by the front-end viewer."""
import json
import logging
import os
from typing import Optional, Tuple
import urllib.parse

import requests
from flask import Blueprint, Response, jsonify, request, stream_with_context

from ckan.plugins import toolkit

from .config_manager import ConfigManager
from .resource_utils import ResourceUtils
from .view_state import (
    DEFAULT_VIEW_STATE,
    parse_view_state,
    sanitize_view_state_for_storage,
    serialize_view_state,
)

log = logging.getLogger(__name__)

maplibre_api = Blueprint('maplibre_api', __name__)


def _config_manager() -> ConfigManager:
    plugin = _get_plugin()
    if plugin is not None:
        return plugin.config_manager
    return ConfigManager(
        site_url=toolkit.config.get('ckan.site_url', ''),
    )


def _resource_utils() -> ResourceUtils:
    plugin = _get_plugin()
    if plugin is not None:
        return plugin.resource_utils
    return ResourceUtils(_config_manager())


def _get_plugin():
    try:
        from ckan import plugins as p
        for plugin in p.PluginImplementations(p.IResourceView):
            info = plugin.info() if hasattr(plugin, 'info') else {}
            if info.get('name') == 'maplibre':
                return plugin
    except Exception as exc:  # pragma: no cover - defensive
        log.debug('Could not resolve maplibre plugin: %s', exc)
    return None


def _json_error(message: str, status: int = 500) -> Response:
    payload = jsonify({'success': False, 'error': message})
    payload.status_code = status
    return payload


def _user_context() -> dict:
    user = None
    try:
        user = toolkit.g.user
    except Exception:
        user = None
    return {
        'user': user or '',
        'ignore_auth': False,
    }


# ---------------------------------------------------------------------------
# Save view_state
# ---------------------------------------------------------------------------
@maplibre_api.route(
    '/api/maplibre/view/<view_id>/save-config',
    methods=['POST'],
)
def save_view_state(view_id: str):
    if request.content_length and request.content_length > 16 * 1024 * 1024:
        return _json_error('Payload too large', 413)
    try:
        payload = request.get_json(force=False, silent=True)
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return _json_error('JSON body required', 400)

    view_state = payload.get('view_state')
    if view_state is None:
        return _json_error('view_state is required', 400)

    cm = _config_manager()
    try:
        cleaned = sanitize_view_state_for_storage(
            view_state,
            max_bytes=cm.max_view_state_bytes,
        )
    except ValueError as exc:
        return _json_error(str(exc), 413)

    context = _user_context()
    try:
        current = toolkit.get_action('resource_view_show')(
            context, {'id': view_id})
    except toolkit.ObjectNotFound:
        return _json_error('View not found', 404)
    except toolkit.NotAuthorized:
        return _json_error('Not authorized', 403)

    update_data = {
        'id': view_id,
        'resource_id': current.get('resource_id'),
        'view_type': current.get('view_type'),
        'title': current.get('title'),
        'description': current.get('description', ''),
        'view_state': serialize_view_state(cleaned),
    }
    # Preserve any other schema fields the view already had.
    for key in ('basemap', 'style_source', 'sld_resource_id',
                'enable_clustering', 'show_attributes_popup',
                'show_fields', 'filterable'):
        if key in current:
            update_data.setdefault(key, current[key])

    try:
        updated = toolkit.get_action('resource_view_update')(
            context, update_data)
    except toolkit.NotAuthorized:
        return _json_error('Not authorized to update this view', 403)
    except Exception as exc:
        log.exception('resource_view_update failed for %s', view_id)
        return _json_error(f'Failed to update view: {exc}', 500)

    return jsonify({
        'success': True,
        'view_id': view_id,
        'updated_at': updated.get('updated', ''),
    })


# ---------------------------------------------------------------------------
# Read view_state
# ---------------------------------------------------------------------------
@maplibre_api.route('/api/maplibre/view/<view_id>/state', methods=['GET'])
def get_view_state(view_id: str):
    context = _user_context()
    try:
        view = toolkit.get_action('resource_view_show')(
            context, {'id': view_id})
    except toolkit.ObjectNotFound:
        return _json_error('View not found', 404)
    except toolkit.NotAuthorized:
        return _json_error('Not authorized', 403)
    state = parse_view_state(view.get('view_state', ''),
                             default=DEFAULT_VIEW_STATE)
    return jsonify({'success': True, 'view_state': state})


# ---------------------------------------------------------------------------
# Signed proxy for private resources
# ---------------------------------------------------------------------------
@maplibre_api.route(
    '/api/maplibre/resource/<resource_id>/content',
    methods=['GET', 'HEAD', 'OPTIONS'],
    defaults={'filename': None},
)
@maplibre_api.route(
    '/api/maplibre/resource/<resource_id>/content/<path:filename>',
    methods=['GET', 'HEAD', 'OPTIONS'],
)
def proxy_resource_content(resource_id: str, filename: Optional[str]):
    if request.method == 'OPTIONS':
        return _cors_preflight()

    token = request.args.get('token', '')
    ru = _resource_utils()
    if not ru.verify_resource_token(resource_id, token):
        return _cors_response(_json_error('Invalid or expired token', 401))

    try:
        upstream_url, content_type, fname = ru.resolve_private_resource_source(
            resource_id)
    except Exception as exc:
        log.exception('resolve_private_resource_source failed for %s',
                      resource_id)
        return _cors_response(_json_error(f'Resolver failure: {exc}', 500))

    if not upstream_url:
        return _cors_response(_json_error('Resource not resolvable', 404))

    # Local filesystem path: stream directly.
    if upstream_url.startswith('/') and os.path.exists(upstream_url):
        return _stream_file(upstream_url, content_type, fname)

    # HTTP upstream — forward Range headers (PMTiles, COG and FlatGeobuf all
    # depend on HTTP Range Requests to avoid downloading whole files).
    headers = {}
    if 'Range' in request.headers:
        headers['Range'] = request.headers['Range']
    if 'If-None-Match' in request.headers:
        headers['If-None-Match'] = request.headers['If-None-Match']
    method = 'HEAD' if request.method == 'HEAD' else 'GET'
    try:
        upstream = requests.request(
            method, upstream_url,
            headers=headers, stream=True, timeout=30,
        )
    except requests.RequestException as exc:
        log.warning('Upstream fetch failed for %s: %s', resource_id, exc)
        return _cors_response(_json_error(f'Upstream error: {exc}', 502))

    resp_headers = {
        'Content-Type': upstream.headers.get('Content-Type') or content_type
                        or 'application/octet-stream',
        'Accept-Ranges': upstream.headers.get('Accept-Ranges', 'bytes'),
    }
    for h in ('Content-Length', 'Content-Range', 'ETag', 'Last-Modified'):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]
    if fname:
        resp_headers['Content-Disposition'] = (
            f'inline; filename="{urllib.parse.quote(fname)}"'
        )

    def generate():
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk

    response = Response(
        stream_with_context(generate()) if method == 'GET' else b'',
        status=upstream.status_code,
        headers=resp_headers,
    )
    return _cors_response(response)


def _stream_file(path: str, content_type: Optional[str],
                 filename: Optional[str]) -> Response:
    size = os.path.getsize(path)
    range_header = request.headers.get('Range', '')
    start, end = 0, size - 1
    status = 200
    if range_header.startswith('bytes='):
        try:
            piece = range_header[6:].split(',', 1)[0]
            start_s, end_s = piece.split('-', 1)
            if start_s:
                start = int(start_s)
            if end_s:
                end = int(end_s)
            end = min(end, size - 1)
            if start < 0 or start > end:
                return _cors_response(_json_error('Invalid range', 416))
            status = 206
        except ValueError:
            return _cors_response(_json_error('Invalid range', 416))
    length = end - start + 1

    def generate():
        with open(path, 'rb') as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        'Content-Type': content_type or 'application/octet-stream',
        'Content-Length': str(length),
        'Accept-Ranges': 'bytes',
    }
    if status == 206:
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    if filename:
        headers['Content-Disposition'] = (
            f'inline; filename="{urllib.parse.quote(filename)}"'
        )
    response = Response(
        stream_with_context(generate()) if request.method == 'GET' else b'',
        status=status, headers=headers,
    )
    return _cors_response(response)


def _cors_preflight() -> Response:
    response = Response(status=204)
    return _cors_response(response)


def _cors_response(response: Response) -> Response:
    origin = request.headers.get('Origin')
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
    else:
        response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = (
        'Range, Content-Type, Authorization, If-None-Match'
    )
    response.headers['Access-Control-Expose-Headers'] = (
        'Content-Length, Content-Range, ETag, Accept-Ranges'
    )
    return response


# ---------------------------------------------------------------------------
# Pipeline status
# ---------------------------------------------------------------------------
@maplibre_api.route(
    '/api/maplibre/pipeline/<resource_id>/status',
    methods=['GET'],
)
def pipeline_status(resource_id: str):
    from .pipeline.jobs import get_status
    status = get_status(resource_id) or {'status': 'idle'}
    return jsonify({'success': True, 'resource_id': resource_id,
                    'pipeline': status})
