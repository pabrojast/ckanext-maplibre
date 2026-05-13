# encoding: utf-8
"""Unit tests for the SLD → MapLibre translator."""
import pytest

from ckanext.maplibre.pipeline.sld_translate import translate_sld


POINT_SLD = b"""<?xml version="1.0" encoding="UTF-8"?>
<sld:StyledLayerDescriptor xmlns:sld="http://www.opengis.net/sld"
                           xmlns:se="http://www.opengis.net/se"
                           xmlns:ogc="http://www.opengis.net/ogc"
                           version="1.1.0">
  <sld:NamedLayer>
    <se:Name>cities</se:Name>
    <sld:UserStyle>
      <se:FeatureTypeStyle>
        <se:Rule>
          <se:PointSymbolizer>
            <se:Graphic>
              <se:Mark>
                <se:WellKnownName>circle</se:WellKnownName>
                <se:Fill>
                  <se:SvgParameter name="fill">#ff0000</se:SvgParameter>
                </se:Fill>
                <se:Stroke>
                  <se:SvgParameter name="stroke">#000000</se:SvgParameter>
                  <se:SvgParameter name="stroke-width">2</se:SvgParameter>
                </se:Stroke>
              </se:Mark>
              <se:Size>8</se:Size>
            </se:Graphic>
          </se:PointSymbolizer>
        </se:Rule>
      </se:FeatureTypeStyle>
    </sld:UserStyle>
  </sld:NamedLayer>
</sld:StyledLayerDescriptor>
"""


POLYGON_WITH_FILTER = b"""<?xml version="1.0" encoding="UTF-8"?>
<sld:StyledLayerDescriptor xmlns:sld="http://www.opengis.net/sld"
                           xmlns:se="http://www.opengis.net/se"
                           xmlns:ogc="http://www.opengis.net/ogc"
                           version="1.1.0">
  <sld:NamedLayer><sld:UserStyle><se:FeatureTypeStyle>
    <se:Rule>
      <ogc:Filter>
        <ogc:PropertyIsEqualTo>
          <ogc:PropertyName>region</ogc:PropertyName>
          <ogc:Literal>north</ogc:Literal>
        </ogc:PropertyIsEqualTo>
      </ogc:Filter>
      <se:PolygonSymbolizer>
        <se:Fill>
          <se:SvgParameter name="fill">#aabbcc</se:SvgParameter>
          <se:SvgParameter name="fill-opacity">0.5</se:SvgParameter>
        </se:Fill>
        <se:Stroke>
          <se:SvgParameter name="stroke">#101010</se:SvgParameter>
          <se:SvgParameter name="stroke-width">1.5</se:SvgParameter>
        </se:Stroke>
      </se:PolygonSymbolizer>
    </se:Rule>
  </se:FeatureTypeStyle></sld:UserStyle></sld:NamedLayer>
</sld:StyledLayerDescriptor>
"""


def test_point_symbolizer_produces_circle_layer():
    result = translate_sld(POINT_SLD, source_id='cities')
    layers = result['layers']
    assert layers, 'expected at least one layer'
    circle = next((l for l in layers if l['type'] == 'circle'), None)
    assert circle is not None
    assert circle['paint']['circle-color'] == '#ff0000'
    assert circle['paint']['circle-radius'] == 8.0
    assert circle['paint']['circle-stroke-color'] == '#000000'
    assert circle['paint']['circle-stroke-width'] == 2.0


def test_polygon_symbolizer_with_filter():
    result = translate_sld(POLYGON_WITH_FILTER, source_id='regions')
    layers = result['layers']
    types = [l['type'] for l in layers]
    assert 'fill' in types and 'line' in types
    fill = next(l for l in layers if l['type'] == 'fill')
    assert fill['filter'] == ['==', ['get', 'region'], 'north']
    assert fill['paint']['fill-color'] == '#aabbcc'
    assert fill['paint']['fill-opacity'] == 0.5


def test_parse_failure_returns_empty():
    result = translate_sld(b'not xml')
    assert result['layers'] == []
    assert result['warnings']
