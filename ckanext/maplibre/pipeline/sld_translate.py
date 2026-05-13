# encoding: utf-8
"""SLD (OGC Symbology Encoding 1.1) → MapLibre style layers.

This is a deliberately small subset focused on what `ckanext-terria_view`
users have in practice: Point/Line/Polygon/Text symbolizers with simple
PropertyIsEqualTo/IsBetween/And/Or filters. Anything outside that subset is
ignored (we log a warning and continue).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from lxml import etree
    _LXML = True
except ImportError:
    import xml.etree.ElementTree as etree  # type: ignore
    _LXML = False


log = logging.getLogger(__name__)

NS = {
    'sld': 'http://www.opengis.net/sld',
    'se': 'http://www.opengis.net/se',
    'ogc': 'http://www.opengis.net/ogc',
    'fes': 'http://www.opengis.net/fes/2.0',
}


def translate_sld(sld_source: bytes | str, *,
                  source_id: str = 'src') -> Dict[str, List[Dict]]:
    """Parse an SLD/SE document and return MapLibre layer definitions.

    Returns ``{'layers': [...], 'warnings': [...]}``.
    """
    root = _parse_root(sld_source)
    if root is None:
        return {'layers': [], 'warnings': ['could not parse SLD']}

    layers: List[Dict] = []
    warnings: List[str] = []

    for rule in _findall_anyns(root, 'Rule'):
        filter_expr = _translate_filter(_find_anyns(rule, 'Filter'))
        for symbolizer in _iter_symbolizers(rule):
            tag = _localname(symbolizer.tag)
            translator = _SYMBOLIZER_TRANSLATORS.get(tag)
            if translator is None:
                warnings.append(f'unsupported symbolizer: {tag}')
                continue
            generated = translator(symbolizer, source_id=source_id,
                                   filter_expr=filter_expr)
            if isinstance(generated, list):
                layers.extend(generated)
            elif generated:
                layers.append(generated)

    return {'layers': layers, 'warnings': warnings}


# ---------------------------------------------------------------------------
# Symbolizer translators
# ---------------------------------------------------------------------------
def _translate_point(symbolizer, *, source_id: str,
                     filter_expr: Optional[List]) -> Dict:
    graphic = _find_anyns(symbolizer, 'Graphic')
    radius = _text_to_number(
        _xpath_first(graphic, ['Size']) if graphic is not None else None,
        default=5.0,
    )
    fill_color, fill_opacity = _read_fill(
        _xpath_first(graphic, ['Mark', 'Fill']) if graphic is not None else None
    )
    stroke_color, stroke_width = _read_stroke(
        _xpath_first(graphic, ['Mark', 'Stroke']) if graphic is not None else None
    )
    paint = {
        'circle-radius': radius,
        'circle-color': fill_color or '#3388ff',
        'circle-opacity': fill_opacity if fill_opacity is not None else 0.85,
    }
    if stroke_color:
        paint['circle-stroke-color'] = stroke_color
    if stroke_width is not None:
        paint['circle-stroke-width'] = stroke_width
    layer = {
        'id': f'{source_id}-circle-{_layer_counter()}',
        'type': 'circle',
        'source': source_id,
        'paint': paint,
    }
    _apply_filter(layer, filter_expr)
    return layer


def _translate_line(symbolizer, *, source_id: str,
                    filter_expr: Optional[List]) -> Dict:
    color, width = _read_stroke(_find_anyns(symbolizer, 'Stroke'))
    paint = {
        'line-color': color or '#3388ff',
        'line-width': width if width is not None else 1.5,
    }
    dash = _read_dasharray(_find_anyns(symbolizer, 'Stroke'))
    if dash:
        paint['line-dasharray'] = dash
    layer = {
        'id': f'{source_id}-line-{_layer_counter()}',
        'type': 'line',
        'source': source_id,
        'paint': paint,
    }
    _apply_filter(layer, filter_expr)
    return layer


def _translate_polygon(symbolizer, *, source_id: str,
                       filter_expr: Optional[List]) -> List[Dict]:
    fill_color, fill_opacity = _read_fill(_find_anyns(symbolizer, 'Fill'))
    stroke_color, stroke_width = _read_stroke(_find_anyns(symbolizer, 'Stroke'))
    layers: List[Dict] = []
    fill_layer = {
        'id': f'{source_id}-fill-{_layer_counter()}',
        'type': 'fill',
        'source': source_id,
        'paint': {
            'fill-color': fill_color or '#3388ff',
            'fill-opacity': fill_opacity if fill_opacity is not None else 0.4,
        },
    }
    _apply_filter(fill_layer, filter_expr)
    layers.append(fill_layer)
    if stroke_color or stroke_width is not None:
        outline = {
            'id': f'{source_id}-outline-{_layer_counter()}',
            'type': 'line',
            'source': source_id,
            'paint': {
                'line-color': stroke_color or '#3388ff',
                'line-width': stroke_width if stroke_width is not None else 1.0,
            },
        }
        _apply_filter(outline, filter_expr)
        layers.append(outline)
    return layers


def _translate_text(symbolizer, *, source_id: str,
                    filter_expr: Optional[List]) -> Optional[Dict]:
    label = _find_anyns(symbolizer, 'Label')
    field = None
    if label is not None:
        prop = _find_anyns(label, 'PropertyName')
        if prop is not None and prop.text:
            field = prop.text.strip()
    if not field:
        return None
    fill_color, _ = _read_fill(_find_anyns(symbolizer, 'Fill'))
    layer = {
        'id': f'{source_id}-symbol-{_layer_counter()}',
        'type': 'symbol',
        'source': source_id,
        'layout': {
            'text-field': ['get', field],
            'text-size': 12,
        },
        'paint': {
            'text-color': fill_color or '#000000',
        },
    }
    _apply_filter(layer, filter_expr)
    return layer


_SYMBOLIZER_TRANSLATORS = {
    'PointSymbolizer': _translate_point,
    'LineSymbolizer': _translate_line,
    'PolygonSymbolizer': _translate_polygon,
    'TextSymbolizer': _translate_text,
}


# ---------------------------------------------------------------------------
# Filter translator
# ---------------------------------------------------------------------------
def _translate_filter(filter_node) -> Optional[List]:
    if filter_node is None:
        return None
    children = list(filter_node)
    if not children:
        return None
    return _translate_filter_expr(children[0])


def _translate_filter_expr(node) -> Optional[List]:
    tag = _localname(node.tag)
    if tag == 'And':
        parts = [p for p in (_translate_filter_expr(c) for c in node) if p]
        return ['all'] + parts if parts else None
    if tag == 'Or':
        parts = [p for p in (_translate_filter_expr(c) for c in node) if p]
        return ['any'] + parts if parts else None
    if tag == 'Not':
        children = list(node)
        if not children:
            return None
        inner = _translate_filter_expr(children[0])
        return ['!', inner] if inner else None
    if tag == 'PropertyIsEqualTo':
        prop, value = _read_property_value(node)
        if prop is None:
            return None
        return ['==', ['get', prop], value]
    if tag == 'PropertyIsNotEqualTo':
        prop, value = _read_property_value(node)
        if prop is None:
            return None
        return ['!=', ['get', prop], value]
    if tag == 'PropertyIsGreaterThan':
        prop, value = _read_property_value(node)
        if prop is None:
            return None
        return ['>', ['get', prop], value]
    if tag == 'PropertyIsLessThan':
        prop, value = _read_property_value(node)
        if prop is None:
            return None
        return ['<', ['get', prop], value]
    if tag == 'PropertyIsBetween':
        prop_node = _find_anyns(node, 'PropertyName')
        lower = _find_anyns(node, 'LowerBoundary')
        upper = _find_anyns(node, 'UpperBoundary')
        if prop_node is None or lower is None or upper is None:
            return None
        prop = (prop_node.text or '').strip()
        low_val = _text_to_number(_find_anyns(lower, 'Literal'))
        up_val = _text_to_number(_find_anyns(upper, 'Literal'))
        return ['all',
                ['>=', ['get', prop], low_val],
                ['<=', ['get', prop], up_val]]
    log.debug('SLD: ignoring unsupported filter operator: %s', tag)
    return None


def _read_property_value(node):
    prop_node = _find_anyns(node, 'PropertyName')
    literal = _find_anyns(node, 'Literal')
    if prop_node is None or literal is None:
        return None, None
    prop = (prop_node.text or '').strip()
    raw = (literal.text or '').strip()
    try:
        return prop, float(raw) if '.' in raw else int(raw)
    except ValueError:
        return prop, raw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_LAYER_COUNT = {'n': 0}


def _layer_counter() -> int:
    _LAYER_COUNT['n'] += 1
    return _LAYER_COUNT['n']


def _parse_root(source):
    if isinstance(source, str):
        source = source.encode('utf-8')
    try:
        if _LXML:
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            return etree.fromstring(source, parser=parser)
        return etree.fromstring(source)
    except Exception as exc:
        log.warning('SLD parse error: %s', exc)
        return None


def _localname(tag: str) -> str:
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


def _find_anyns(parent, localname: str):
    if parent is None:
        return None
    for child in parent.iter():
        if _localname(child.tag) == localname:
            return child
    return None


def _findall_anyns(parent, localname: str):
    if parent is None:
        return []
    return [c for c in parent.iter() if _localname(c.tag) == localname]


def _xpath_first(node, path: List[str]):
    current = node
    for name in path:
        current = _find_anyns(current, name) if current is not None else None
    return current


def _iter_symbolizers(rule):
    for child in rule:
        tag = _localname(child.tag)
        if tag.endswith('Symbolizer'):
            yield child


def _read_fill(node):
    if node is None:
        return None, None
    color = _css_param(node, 'fill') or _css_param(node, 'fill-color')
    opacity = _css_param(node, 'fill-opacity')
    opacity_value = _maybe_number(opacity)
    return color, opacity_value


def _read_stroke(node):
    if node is None:
        return None, None
    color = _css_param(node, 'stroke') or _css_param(node, 'stroke-color')
    width = _css_param(node, 'stroke-width')
    return color, _maybe_number(width)


def _read_dasharray(node):
    if node is None:
        return None
    dash = _css_param(node, 'stroke-dasharray')
    if not dash:
        return None
    try:
        return [float(x) for x in dash.split()]
    except ValueError:
        return None


def _css_param(node, key: str) -> Optional[str]:
    for child in node:
        if _localname(child.tag) in ('CssParameter', 'SvgParameter'):
            attrib_name = child.get('name')
            if attrib_name == key and child.text:
                return child.text.strip()
    return None


def _text_to_number(node, default: float = 0.0) -> float:
    if node is None or node.text is None:
        return default
    try:
        return float(node.text.strip())
    except (ValueError, AttributeError):
        return default


def _maybe_number(value: Optional[str]):
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _apply_filter(layer: Dict, filter_expr) -> None:
    if filter_expr:
        layer['filter'] = filter_expr
