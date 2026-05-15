/*
 * ckanext-maplibre viewer
 * ------------------------------------------------------------------
 * Boots MapLibre GL JS, registers PMTiles + COG protocols, loads the
 * configured resource(s), and provides UI for layer toggling, basemap
 * selection, attribute popup, style editing, save-view, and an
 * incremental conversion-status banner.
 *
 * Plain vanilla JS — no module bundler. The required libraries are
 * loaded as globals via the view template (see maplibre_view.html).
 */
(function () {
  'use strict';

  var bootstrap = readBootstrap();
  if (!bootstrap) return;

  whenLibsReady(bootstrap, function () {
    new Viewer(bootstrap).start();
  });

  // -----------------------------------------------------------------
  // Setup helpers
  // -----------------------------------------------------------------
  function readBootstrap() {
    var node = document.getElementById('ckanext-maplibre-bootstrap');
    if (!node) {
      console.error('ckanext-maplibre: bootstrap node not found');
      return null;
    }
    try {
      return JSON.parse(node.textContent || '{}');
    } catch (err) {
      console.error('ckanext-maplibre: invalid bootstrap JSON', err);
      return null;
    }
  }

  function whenLibsReady(boot, callback) {
    var started = Date.now();
    // Always defer at least one tick so the rest of this IIFE (which
    // defines Viewer.prototype.* below) finishes before ``callback`` runs.
    // Otherwise, when libsAvailable() returns true immediately (e.g. CSV
    // resources need no extra libs), the callback fires while the
    // prototype is still bare and ``new Viewer().start()`` blows up with
    // "(intermediate value).start is not a function".
    (function poll() {
      if (libsAvailable(boot)) {
        setTimeout(callback, 0);
        return;
      }
      if (Date.now() - started > 15000) {
        console.error('ckanext-maplibre: required libraries did not load');
        return;
      }
      setTimeout(poll, 100);
    })();
  }

  // -----------------------------------------------------------------
  // Viewer
  // -----------------------------------------------------------------
  function Viewer(boot) {
    this.boot = boot;
    this.map = null;
    this.userLayers = [];
    this.userSources = {};
    this.fgbLoaders = {};
    this.savedLayerStates = buildSavedLayerStateMap(
      boot.viewState && boot.viewState.style
    );
    this.activeLayerIds = hasSavedActiveLayerIds(boot.viewState)
      ? boot.viewState.ui.active_layer_ids.slice()
      : null;
    this.useSavedStyle = boot.styleSource === 'custom_style_json';
    this.enableClustering = !!boot.enableClustering;
    this.showAttributesPopup = boot.showAttributesPopup !== false;
    this.basemapKey = (boot.viewState && boot.viewState.ui &&
                       boot.viewState.ui.basemap) ||
                      boot.defaultBasemap || 'osm';
  }

  Viewer.prototype.start = function () {
    registerProtocols();
    this.map = new maplibregl.Map({
      container: 'ckanext-maplibre-map',
      style: this.basemapStyle(this.basemapKey),
      center: cameraOrDefault(this.boot, 'center', [0, 0]),
      zoom: cameraOrDefault(this.boot, 'zoom', 2),
      bearing: cameraOrDefault(this.boot, 'bearing', 0),
      pitch: cameraOrDefault(this.boot, 'pitch', 0),
    });
    this.map.addControl(new maplibregl.NavigationControl());
    this.map.addControl(new maplibregl.ScaleControl({ maxWidth: 120 }));

    var self = this;
    this.map.on('load', function () {
      self.applySavedStyle();
      self.addUserResources();
      self.buildControls();
      self.attachPopup();
      self.maybePollPipelineStatus();
    });
  };

  // -----------------------------------------------------------------
  // Style + layer loading
  // -----------------------------------------------------------------
  Viewer.prototype.basemapStyle = function (key) {
    var bm = this.boot.basemaps && this.boot.basemaps[key];
    if (!bm) bm = this.boot.basemaps && this.boot.basemaps.osm;
    if (!bm) {
      return {
        version: 8,
        sources: {},
        layers: [
          { id: 'background', type: 'background',
            paint: { 'background-color': '#f8f9fa' } },
        ],
      };
    }
    var source = {
      type: bm.type || 'raster',
      tiles: bm.tiles,
      tileSize: bm.tileSize || 256,
      attribution: bm.attribution || '',
      maxzoom: bm.maxzoom || 22,
    };
    return {
      version: 8,
      sources: { 'basemap': source },
      layers: [
        { id: 'basemap', type: 'raster', source: 'basemap',
          minzoom: 0, maxzoom: 24 },
      ],
    };
  };

  Viewer.prototype.applySavedStyle = function () {
    if (!this.useSavedStyle) return;
    // Saved layer paint/layout/filter is re-applied as each managed layer is
    // added so the basemap source can stay under plugin control.
  };

  Viewer.prototype.addManagedLayer = function (layerSpec) {
    var saved = this.useSavedStyle && this.savedLayerStates[layerSpec.id];
    if (saved) {
      if (saved.filter) layerSpec.filter = clone(saved.filter);
      if (saved.paint) layerSpec.paint = mergeObjects(layerSpec.paint, saved.paint);
      if (saved.layout) layerSpec.layout = mergeObjects(layerSpec.layout, saved.layout);
      if (saved.minzoom !== undefined) layerSpec.minzoom = saved.minzoom;
      if (saved.maxzoom !== undefined) layerSpec.maxzoom = saved.maxzoom;
    }
    this.map.addLayer(layerSpec);
    this.applySavedVisibility(layerSpec.id);
  };

  Viewer.prototype.applySavedVisibility = function (layerId) {
    if (this.activeLayerIds === null || !this.map.getLayer(layerId)) return;
    this.map.setLayoutProperty(
      layerId,
      'visibility',
      this.activeLayerIds.indexOf(layerId) >= 0 ? 'visible' : 'none'
    );
  };

  Viewer.prototype.controlLayerIds = function (layer) {
    var ids = [layer.id];
    if (Array.isArray(layer.linkedLayerIds)) {
      ids = ids.concat(layer.linkedLayerIds);
    }
    return ids;
  };

  Viewer.prototype.layerVisibility = function (layer) {
    if (!this.map.getLayer(layer.id)) return 'visible';
    return this.map.getLayoutProperty(layer.id, 'visibility') || 'visible';
  };

  Viewer.prototype.setLayerVisibility = function (layer, visibility) {
    var self = this;
    this.controlLayerIds(layer).forEach(function (layerId) {
      if (!self.map.getLayer(layerId)) return;
      self.map.setLayoutProperty(layerId, 'visibility', visibility);
    });
  };

  Viewer.prototype.syncLinkedLayerVisibility = function (layerId, linkedLayerIds) {
    var visibility = 'visible';
    if (this.map.getLayer(layerId)) {
      visibility = this.map.getLayoutProperty(layerId, 'visibility') || 'visible';
    }
    var self = this;
    linkedLayerIds.forEach(function (linkedLayerId) {
      if (!self.map.getLayer(linkedLayerId)) return;
      self.map.setLayoutProperty(linkedLayerId, 'visibility', visibility);
    });
  };

  Viewer.prototype.addUserResources = function () {
    var resources = this.boot.viewableResources || [];
    var bounds = null;
    var self = this;

    resources.forEach(function (res, idx) {
      if (!res || !res.url) return;
      var sourceId = 'maplibre-src-' + idx;
      var layerIdBase = 'maplibre-layer-' + idx;
      var spec = res.sourceSpec;

      if (spec && spec._maplibre_loader === 'flatgeobuf') {
        self.addFlatGeobufSource(sourceId, layerIdBase, res, spec);
      } else if (spec && spec._maplibre_loader === 'csv') {
        self.addCsvSource(sourceId, layerIdBase, res, spec);
      } else if (res.kind === 'raster') {
        self.addRasterLayer(sourceId, layerIdBase, res, spec);
      } else if (res.format === 'pmtiles') {
        self.addPMTilesLayer(sourceId, layerIdBase, res, spec);
      } else {
        self.addGeoJsonLayer(sourceId, layerIdBase, res, spec);
      }
    });

    // Fit to extent if we can derive one from a PMTiles header or FGB header
    if (this.firstPMTiles) {
      this.firstPMTiles.getHeader().then(function (header) {
        if (header && typeof header.minLon === 'number') {
          self.map.fitBounds([
            [header.minLon, header.minLat],
            [header.maxLon, header.maxLat],
          ], { padding: 30, duration: 0 });
        }
      }).catch(function () {});
    }
  };

  Viewer.prototype.addRasterLayer = function (sourceId, layerIdBase, res, spec) {
    var sourceSpec = clone(spec) || { type: 'raster', tiles: [res.url],
                                      tileSize: 256 };
    delete sourceSpec._maplibre_loader;
    delete sourceSpec._source_url;
    this.map.addSource(sourceId, sourceSpec);
    var layerId = layerIdBase + '-raster';
    this.addManagedLayer({
      id: layerId,
      type: 'raster',
      source: sourceId,
      paint: { 'raster-opacity': 0.9 },
    });
    this.userSources[sourceId] = res;
    this.userLayers.push({ id: layerId, sourceId: sourceId, kind: 'raster',
                           label: res.label });
  };

  Viewer.prototype.addPMTilesLayer = function (sourceId, layerIdBase, res) {
    var p = new pmtiles.PMTiles(res.url.replace(/^pmtiles:\/\//, ''));
    if (!this.firstPMTiles) this.firstPMTiles = p;
    var self = this;
    p.getHeader().then(function (header) {
      var sourceType = header.tileType === pmtiles.TileType.Mvt
        ? 'vector' : 'raster';
      var sourceSpec = { type: sourceType, url: res.url };
      self.map.addSource(sourceId, sourceSpec);
      self.userSources[sourceId] = res;
      if (sourceType === 'vector') {
        var sourceLayer = guessVectorSourceLayer(header) || 'default';
        self.addDefaultVectorLayers(sourceId, layerIdBase, sourceLayer, res);
      } else {
        var layerId = layerIdBase + '-raster';
        self.addManagedLayer({
          id: layerId, type: 'raster', source: sourceId,
          paint: { 'raster-opacity': 0.95 },
        });
        self.userLayers.push({ id: layerId, sourceId: sourceId,
                               kind: 'raster', label: res.label });
      }
      self.refreshControls();
    }).catch(function (err) {
      console.error('PMTiles header read failed', err);
    });
  };

  Viewer.prototype.addDefaultVectorLayers = function (sourceId, layerIdBase,
                                                     sourceLayer, res) {
    var fillId = layerIdBase + '-fill';
    var lineId = layerIdBase + '-line';
    var pointId = layerIdBase + '-point';
    this.addManagedLayer({
      id: fillId, type: 'fill', source: sourceId,
      'source-layer': sourceLayer,
      filter: ['==', '$type', 'Polygon'],
      paint: { 'fill-color': '#3388ff', 'fill-opacity': 0.35 },
    });
    this.addManagedLayer({
      id: lineId, type: 'line', source: sourceId,
      'source-layer': sourceLayer,
      filter: ['any', ['==', '$type', 'LineString'],
               ['==', '$type', 'Polygon']],
      paint: { 'line-color': '#1f6fbf', 'line-width': 1 },
    });
    this.addManagedLayer({
      id: pointId, type: 'circle', source: sourceId,
      'source-layer': sourceLayer,
      filter: ['==', '$type', 'Point'],
      paint: { 'circle-radius': 4, 'circle-color': '#d6336c',
               'circle-stroke-color': '#fff', 'circle-stroke-width': 1 },
    });
    this.userLayers.push({ id: fillId, sourceId: sourceId, kind: 'vector',
                           label: res.label + ' (polygons)' });
    this.userLayers.push({ id: lineId, sourceId: sourceId, kind: 'vector',
                           label: res.label + ' (lines)' });
    this.userLayers.push({ id: pointId, sourceId: sourceId, kind: 'vector',
                           label: res.label + ' (points)' });
  };

  Viewer.prototype.addGeoJsonLayer = function (sourceId, layerIdBase, res, spec) {
    var sourceSpec = buildGeoJsonSourceSpec(
      spec,
      res.url,
      this.enableClustering
    );
    this.map.addSource(sourceId, sourceSpec);
    this.userSources[sourceId] = res;
    this.addGeoJsonLayersForSource(sourceId, layerIdBase, res,
                                   !!sourceSpec.cluster);
  };

  Viewer.prototype.addGeoJsonLayersForSource = function (sourceId, layerIdBase,
                                                         res, clustered) {
    var fillId = layerIdBase + '-fill';
    var lineId = layerIdBase + '-line';
    var pointId = layerIdBase + '-point';
    var pointFilter = clustered
      ? ['all', ['==', ['geometry-type'], 'Point'], ['!', ['has', 'point_count']]]
      : ['==', ['geometry-type'], 'Point'];
    this.addManagedLayer({
      id: fillId, type: 'fill', source: sourceId,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': '#3388ff', 'fill-opacity': 0.35 },
    });
    this.addManagedLayer({
      id: lineId, type: 'line', source: sourceId,
      filter: ['any',
               ['==', ['geometry-type'], 'LineString'],
               ['==', ['geometry-type'], 'Polygon']],
      paint: { 'line-color': '#1f6fbf', 'line-width': 1.2 },
    });
    this.addManagedLayer({
      id: pointId, type: 'circle', source: sourceId,
      filter: pointFilter,
      paint: { 'circle-radius': 4, 'circle-color': '#d6336c',
               'circle-stroke-color': '#fff', 'circle-stroke-width': 1 },
    });
    this.userLayers.push({ id: fillId, sourceId: sourceId, kind: 'vector',
                           label: res.label + ' (polygons)' });
    this.userLayers.push({ id: lineId, sourceId: sourceId, kind: 'vector',
                           label: res.label + ' (lines)' });
    if (clustered) {
      var clusterId = layerIdBase + '-cluster';
      var clusterCountId = layerIdBase + '-cluster-count';
      this.addManagedLayer({
        id: clusterId, type: 'circle', source: sourceId,
        filter: ['has', 'point_count'],
        paint: {
          'circle-color': ['step', ['get', 'point_count'],
                           '#228be6', 100, '#1c7ed6', 1000, '#1864ab'],
          'circle-radius': ['step', ['get', 'point_count'],
                            13, 100, 18, 1000, 23],
          'circle-opacity': 0.85,
        },
      });
      this.addManagedLayer({
        id: clusterCountId, type: 'symbol', source: sourceId,
        filter: ['has', 'point_count'],
        layout: {
          'text-field': ['get', 'point_count_abbreviated'],
          'text-size': 12,
        },
        paint: {
          'text-color': '#ffffff',
        },
      });
      this.syncLinkedLayerVisibility(pointId, [clusterId, clusterCountId]);
      this.userLayers.push({
        id: pointId,
        sourceId: sourceId,
        kind: 'vector',
        label: res.label + ' (points)',
        linkedLayerIds: [clusterId, clusterCountId],
      });
      return;
    }
    this.userLayers.push({ id: pointId, sourceId: sourceId, kind: 'vector',
                           label: res.label + ' (points)' });
  };

  Viewer.prototype.addFlatGeobufSource = function (sourceId, layerIdBase,
                                                   res, spec) {
    if (typeof flatgeobuf === 'undefined') {
      console.warn('FlatGeobuf library not loaded; falling back to GeoJSON');
      this.addGeoJsonLayer(sourceId, layerIdBase, res, {
        type: 'geojson', data: spec._source_url || res.url,
      });
      return;
    }
    var emptyFc = { type: 'FeatureCollection', features: [] };
    this.map.addSource(sourceId, buildGeoJsonSourceSpec(
      { type: 'geojson', data: emptyFc },
      emptyFc,
      this.enableClustering
    ));
    this.userSources[sourceId] = res;
    this.addGeoJsonLayersForFGB(sourceId, layerIdBase, res,
                                this.enableClustering);

    var self = this;
    var url = spec._source_url || res.url;
    function refresh() {
      var b = self.map.getBounds();
      var bbox = {
        minX: b.getWest(), minY: b.getSouth(),
        maxX: b.getEast(), maxY: b.getNorth(),
      };
      var features = [];
      var iter = flatgeobuf.deserialize(url, bbox);
      (async function () {
        try {
          for await (var feat of iter) {
            features.push(feat);
            if (features.length >= 50000) break;
          }
          self.map.getSource(sourceId).setData({
            type: 'FeatureCollection', features: features,
          });
        } catch (err) {
          console.warn('FlatGeobuf stream failed', err);
        }
      })();
    }
    this.fgbLoaders[sourceId] = refresh;
    refresh();
    this.map.on('moveend', refresh);
  };

  Viewer.prototype.addGeoJsonLayersForFGB = function (sourceId, layerIdBase,
                                                      res, clustered) {
    this.addGeoJsonLayersForSource(sourceId, layerIdBase, res, clustered);
  };

  // ----- CSV -----
  // Renders a CSV file as point features. The user picks the lat/lon (or WKT)
  // columns when creating the view; without those we show a help banner
  // instead of trying to guess.
  Viewer.prototype.addCsvSource = function (sourceId, layerIdBase, res, spec) {
    var fields = res.csvFields || {};
    var hasLatLon = !!(fields.latitudeField && fields.longitudeField);
    var hasWkt = !!fields.wktField;
    if (!hasLatLon && !hasWkt) {
      flashBanner(
        'CSV view needs the latitude/longitude columns (or a WKT column). ' +
        'Edit the view and configure them under "CSV spatial columns".',
        'error');
      return;
    }
    var emptyFc = { type: 'FeatureCollection', features: [] };
    this.map.addSource(sourceId, buildGeoJsonSourceSpec(
      { type: 'geojson', data: emptyFc },
      emptyFc,
      this.enableClustering));
    this.userSources[sourceId] = res;
    this.addGeoJsonLayersForSource(sourceId, layerIdBase, res,
                                   this.enableClustering);

    var url = spec._source_url || res.url;
    var self = this;
    loadCsvAsGeoJson(url, fields).then(function (fc) {
      var src = self.map.getSource(sourceId);
      if (!src) return;
      src.setData(fc);
      if (fc.features.length === 0) {
        flashBanner('CSV loaded but no rows had valid coordinates. ' +
                    'Check the column names match the CSV header (case-sensitive).',
                    'error');
        return;
      }
      // Fit to the data extent.
      var bbox = csvBbox(fc);
      if (bbox) {
        self.map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]],
                           { padding: 40, duration: 0 });
      }
    }).catch(function (err) {
      console.error('CSV load failed', err);
      flashBanner('Failed to load CSV: ' + err.message, 'error');
    });
  };

  function loadCsvAsGeoJson(url, fields) {
    if (typeof Papa === 'undefined') {
      return Promise.reject(new Error(
        'PapaParse library not loaded. Set ckanext.maplibre.cdn_libs=true ' +
        'or bundle papaparse locally.'
      ));
    }
    return fetch(url, { credentials: 'omit' }).then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.text();
    }).then(function (text) {
      var parseOpts = {
        header: true,
        skipEmptyLines: true,
        dynamicTyping: false,
        delimiter: fields.delimiter || '',  // '' = autodetect
      };
      var result = Papa.parse(text, parseOpts);
      if (result.errors && result.errors.length) {
        console.warn('CSV parse warnings', result.errors.slice(0, 3));
      }
      var rows = result.data || [];
      var features = [];
      var latKey = fields.latitudeField;
      var lonKey = fields.longitudeField;
      var wktKey = fields.wktField;
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        var geom = null;
        if (wktKey && row[wktKey]) {
          geom = parseWkt(String(row[wktKey]));
        } else if (latKey && lonKey) {
          var lat = parseFloat(row[latKey]);
          var lon = parseFloat(row[lonKey]);
          if (isFinite(lat) && isFinite(lon)) {
            geom = { type: 'Point', coordinates: [lon, lat] };
          }
        }
        if (!geom) continue;
        features.push({
          type: 'Feature',
          geometry: geom,
          properties: row,
        });
      }
      return { type: 'FeatureCollection', features: features };
    });
  }

  function parseWkt(wkt) {
    // Minimal WKT parser: handles POINT, LINESTRING, POLYGON, MULTIPOINT,
    // MULTILINESTRING, MULTIPOLYGON. Returns null if it can't parse.
    if (!wkt) return null;
    var s = wkt.trim();
    var m = /^(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON)\s*(Z|M|ZM)?\s*\((.*)\)\s*$/i.exec(s);
    if (!m) return null;
    var kind = m[1].toUpperCase();
    var body = m[3];
    try {
      if (kind === 'POINT') {
        return { type: 'Point', coordinates: parseWktCoord(body) };
      }
      if (kind === 'LINESTRING') {
        return { type: 'LineString',
                 coordinates: parseWktCoordList(body) };
      }
      if (kind === 'MULTIPOINT') {
        var clean = body.replace(/\(|\)/g, '');
        return { type: 'MultiPoint',
                 coordinates: parseWktCoordList(clean) };
      }
      if (kind === 'POLYGON') {
        return { type: 'Polygon',
                 coordinates: parseWktRings(body) };
      }
      if (kind === 'MULTILINESTRING') {
        return { type: 'MultiLineString',
                 coordinates: parseWktRings(body) };
      }
      if (kind === 'MULTIPOLYGON') {
        return { type: 'MultiPolygon',
                 coordinates: parseWktPolys(body) };
      }
    } catch (e) {
      return null;
    }
    return null;
  }

  function parseWktCoord(text) {
    var nums = text.trim().split(/\s+/).map(Number);
    if (!isFinite(nums[0]) || !isFinite(nums[1])) return null;
    return [nums[0], nums[1]];
  }

  function parseWktCoordList(text) {
    return text.split(',').map(function (pair) {
      return parseWktCoord(pair);
    }).filter(Boolean);
  }

  function parseWktRings(text) {
    // POLYGON((x y, x y),(x y, x y))
    var rings = [];
    var depth = 0, start = 0;
    for (var i = 0; i < text.length; i++) {
      if (text[i] === '(') { if (depth === 0) start = i + 1; depth++; }
      else if (text[i] === ')') { depth--; if (depth === 0) {
          rings.push(parseWktCoordList(text.substring(start, i))); } }
    }
    return rings;
  }

  function parseWktPolys(text) {
    // MULTIPOLYGON(((x y, x y)),((x y, x y)))
    var polys = [];
    var depth = 0, start = 0;
    for (var i = 0; i < text.length; i++) {
      if (text[i] === '(') {
        if (depth === 0) start = i + 1;
        depth++;
      } else if (text[i] === ')') {
        depth--;
        if (depth === 0) {
          polys.push(parseWktRings(text.substring(start, i)));
        }
      }
    }
    return polys;
  }

  function csvBbox(fc) {
    var bbox = null;
    fc.features.forEach(function (f) {
      if (!f.geometry || !f.geometry.coordinates) return;
      eachCoord(f.geometry.coordinates, function (lon, lat) {
        if (!bbox) bbox = [lon, lat, lon, lat];
        if (lon < bbox[0]) bbox[0] = lon;
        if (lat < bbox[1]) bbox[1] = lat;
        if (lon > bbox[2]) bbox[2] = lon;
        if (lat > bbox[3]) bbox[3] = lat;
      });
    });
    return bbox;
  }

  // -----------------------------------------------------------------
  // Controls (right-side panel)
  // -----------------------------------------------------------------
  Viewer.prototype.buildControls = function () {
    this.refreshControls();
  };

  Viewer.prototype.refreshControls = function () {
    var host = document.getElementById('ckanext-maplibre-controls');
    if (!host) return;
    host.innerHTML = '';

    host.appendChild(this.basemapPicker());
    host.appendChild(this.layerListSection());
    host.appendChild(this.stylePanel());
    host.appendChild(this.actionsRow());
  };

  Viewer.prototype.basemapPicker = function () {
    var self = this;
    var wrap = el('div');
    wrap.appendChild(el('h4', { text: 'Basemap' }));
    var select = el('select');
    Object.keys(this.boot.basemaps || {}).forEach(function (key) {
      var opt = el('option', { value: key, text: self.boot.basemaps[key].label || key });
      if (key === self.basemapKey) opt.selected = true;
      select.appendChild(opt);
    });
    select.addEventListener('change', function () {
      self.basemapKey = select.value;
      // Replace the basemap source/layer in place.
      var bm = self.boot.basemaps[self.basemapKey];
      if (!bm) return;
      if (self.map.getLayer('basemap')) self.map.removeLayer('basemap');
      if (self.map.getSource('basemap')) self.map.removeSource('basemap');
      self.map.addSource('basemap', {
        type: bm.type || 'raster',
        tiles: bm.tiles,
        tileSize: bm.tileSize || 256,
        attribution: bm.attribution || '',
        maxzoom: bm.maxzoom || 22,
      });
      self.map.addLayer({ id: 'basemap', type: 'raster', source: 'basemap' },
                        firstUserLayerId(self));
    });
    wrap.appendChild(select);
    return wrap;
  };

  Viewer.prototype.layerListSection = function () {
    var self = this;
    var wrap = el('div');
    wrap.appendChild(el('h4', { text: 'Layers' }));
    if (!this.userLayers.length) {
      wrap.appendChild(el('p', { text: 'No layers yet — pipeline may still be processing.', style: 'color:#777' }));
      return wrap;
    }
    this.userLayers.forEach(function (l) {
      var row = el('div', { className: 'layer-row' });
      var cb = el('input', { type: 'checkbox' });
      cb.checked = self.layerVisibility(l) !== 'none';
      cb.addEventListener('change', function () {
        self.setLayerVisibility(l, cb.checked ? 'visible' : 'none');
      });
      var label = el('label', { text: l.label });
      var slider = el('input', { type: 'range', min: 0, max: 1, step: 0.05 });
      slider.value = self.opacityFor(l);
      slider.addEventListener('input', function () {
        self.setOpacity(l, parseFloat(slider.value));
      });
      row.appendChild(cb);
      row.appendChild(label);
      row.appendChild(slider);
      wrap.appendChild(row);
    });
    return wrap;
  };

  Viewer.prototype.opacityFor = function (layer) {
    var prop = {
      fill: 'fill-opacity', line: 'line-opacity', circle: 'circle-opacity',
      raster: 'raster-opacity', symbol: 'text-opacity',
    }[this.layerType(layer.id)];
    if (!prop) return 1;
    try {
      var value = this.map.getPaintProperty(layer.id, prop);
      return typeof value === 'number' ? value : 1;
    } catch (e) { return 1; }
  };

  Viewer.prototype.setOpacity = function (layer, value) {
    var self = this;
    this.controlLayerIds(layer).forEach(function (layerId) {
      var prop = {
        fill: 'fill-opacity', line: 'line-opacity', circle: 'circle-opacity',
        raster: 'raster-opacity', symbol: 'text-opacity',
      }[self.layerType(layerId)];
      if (!prop || !self.map.getLayer(layerId)) return;
      self.map.setPaintProperty(layerId, prop, value);
    });
  };

  Viewer.prototype.layerType = function (layerId) {
    var l = this.map.getLayer(layerId);
    return l ? l.type : '';
  };

  Viewer.prototype.stylePanel = function () {
    var self = this;
    var wrap = el('div');
    wrap.appendChild(el('h4', { text: 'Style (selected layer)' }));
    if (!this.userLayers.length) return wrap;
    var select = el('select');
    this.userLayers.forEach(function (l) {
      var opt = el('option', { value: l.id, text: l.label });
      select.appendChild(opt);
    });
    wrap.appendChild(select);

    var colorInput = el('input', { type: 'color' });
    wrap.appendChild(colorInput);

    select.addEventListener('change', updateColorInput);
    colorInput.addEventListener('input', function () {
      var layerId = select.value;
      var type = self.layerType(layerId);
      var prop = {
        fill: 'fill-color', line: 'line-color', circle: 'circle-color',
        symbol: 'text-color',
      }[type];
      if (prop) self.map.setPaintProperty(layerId, prop, colorInput.value);
    });
    updateColorInput();

    function updateColorInput() {
      var layerId = select.value;
      var type = self.layerType(layerId);
      var prop = {
        fill: 'fill-color', line: 'line-color', circle: 'circle-color',
        symbol: 'text-color',
      }[type];
      if (!prop) return;
      try {
        var current = self.map.getPaintProperty(layerId, prop);
        if (typeof current === 'string' && /^#/.test(current)) {
          colorInput.value = current;
        }
      } catch (e) { /* ignore */ }
    }
    return wrap;
  };

  Viewer.prototype.actionsRow = function () {
    var self = this;
    var wrap = el('div', { className: 'btn-row' });
    var zoomBtn = el('button', { text: 'Zoom to extent', className: 'ghost' });
    zoomBtn.addEventListener('click', function () { self.zoomToExtent(); });
    wrap.appendChild(zoomBtn);
    if (this.boot.canSave) {
      var saveBtn = el('button', { text: 'Save view' });
      saveBtn.addEventListener('click', function () { self.saveView(saveBtn); });
      wrap.appendChild(saveBtn);
    }
    return wrap;
  };

  Viewer.prototype.zoomToExtent = function () {
    var self = this;
    if (this.firstPMTiles) {
      this.firstPMTiles.getHeader().then(function (header) {
        if (header && typeof header.minLon === 'number') {
          self.map.fitBounds([
            [header.minLon, header.minLat],
            [header.maxLon, header.maxLat],
          ], { padding: 30 });
        }
      }).catch(function () {});
      return;
    }
    // GeoJSON: query rendered features and compute a bbox.
    var bbox = null;
    var features = this.map.queryRenderedFeatures();
    features.forEach(function (f) {
      if (!f.geometry || !f.geometry.coordinates) return;
      eachCoord(f.geometry.coordinates, function (lon, lat) {
        if (!bbox) bbox = [lon, lat, lon, lat];
        if (lon < bbox[0]) bbox[0] = lon;
        if (lat < bbox[1]) bbox[1] = lat;
        if (lon > bbox[2]) bbox[2] = lon;
        if (lat > bbox[3]) bbox[3] = lat;
      });
    });
    if (bbox) this.map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]],
                                 { padding: 40 });
  };

  Viewer.prototype.saveView = function (button) {
    var self = this;
    var center = this.map.getCenter();
    var payload = {
      view_state: {
        version: 1,
        camera: {
          center: [center.lng, center.lat],
          zoom: this.map.getZoom(),
          bearing: this.map.getBearing(),
          pitch: this.map.getPitch(),
        },
        style: this.map.getStyle(),
        ui: {
          basemap: this.basemapKey,
          active_layer_ids: this.userLayers
            .filter(function (l) {
              return self.layerVisibility(l) !== 'none';
            })
            .map(function (l) { return l.id; }),
          opacities: collectLayerOpacities(this),
        },
      },
    };
    if (button) {
      button.disabled = true;
      button.textContent = 'Saving…';
    }
    fetch(this.boot.apiBase + '/view/' + this.boot.viewId + '/save-config', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function (res) {
      return res.json().then(function (body) {
        return { ok: res.ok, body: body };
      });
    }).then(function (result) {
      if (!result.ok || !result.body.success) {
        throw new Error(result.body.error || 'Save failed');
      }
      flashBanner('View saved', 'success');
    }).catch(function (err) {
      flashBanner('Error: ' + err.message, 'error');
    }).finally(function () {
      if (button) {
        button.disabled = false;
        button.textContent = 'Save view';
      }
    });
  };

  // -----------------------------------------------------------------
  // Popup
  // -----------------------------------------------------------------
  Viewer.prototype.attachPopup = function () {
    if (!this.showAttributesPopup) return;
    var self = this;
    this.map.on('click', function (e) {
      var layerIds = self.userLayers.map(function (l) { return l.id; });
      if (!layerIds.length) return;
      var features = self.map.queryRenderedFeatures(e.point, {
        layers: layerIds,
      });
      if (!features.length) return;
      var first = features[0];
      var rows = Object.keys(first.properties || {}).map(function (k) {
        return '<tr><th>' + escapeHtml(k) + '</th><td>' +
               escapeHtml(String(first.properties[k])) + '</td></tr>';
      }).join('');
      new maplibregl.Popup({ className: 'ckanext-maplibre-popup' })
        .setLngLat(e.lngLat)
        .setHTML('<table>' + rows + '</table>')
        .addTo(self.map);
    });
    this.map.on('mousemove', function (e) {
      var layerIds = self.userLayers.map(function (l) { return l.id; });
      if (!layerIds.length) {
        self.map.getCanvas().style.cursor = '';
        return;
      }
      var features = self.map.queryRenderedFeatures(e.point, {
        layers: layerIds,
      });
      self.map.getCanvas().style.cursor = features.length ? 'pointer' : '';
    });
  };

  // -----------------------------------------------------------------
  // Pipeline status polling
  // -----------------------------------------------------------------
  Viewer.prototype.maybePollPipelineStatus = function () {
    var status = this.boot.pipelineStatus || {};
    if (!status || ['done', 'idle', 'skipped'].indexOf(status.status) >= 0) {
      return;
    }
    var resourceId = this.boot.resourceId;
    var attempts = 0;
    var self = this;
    var banner = document.getElementById('ckanext-maplibre-pipeline-banner');
    if (!banner) return;
    banner.style.display = 'block';
    banner.classList.remove('error', 'success');
    banner.textContent = 'Processing dataset (' + status.status + ')…';

    var timer = setInterval(function () {
      attempts++;
      fetch(self.boot.apiBase + '/pipeline/' + resourceId + '/status', {
        credentials: 'same-origin',
      }).then(function (res) { return res.json(); }).then(function (body) {
        var s = (body && body.pipeline) || {};
        if (s.status === 'done') {
          banner.classList.add('success');
          banner.textContent = 'Pipeline finished — reload to see the optimized layer.';
          clearInterval(timer);
        } else if (s.status === 'failed') {
          banner.classList.add('error');
          banner.textContent = 'Pipeline failed: ' + (s.error || 'unknown error');
          clearInterval(timer);
        } else {
          banner.textContent = 'Processing dataset (' + s.status + ')…';
        }
      }).catch(function () {});
      if (attempts > 240) clearInterval(timer); // cap at 12 minutes
    }, 3000);
  };

  // -----------------------------------------------------------------
  // Utilities
  // -----------------------------------------------------------------
  function registerProtocols() {
    if (typeof pmtiles !== 'undefined' && !registerProtocols.pmtilesDone) {
      var protocol = new pmtiles.Protocol();
      maplibregl.addProtocol('pmtiles', protocol.tile);
      registerProtocols.pmtilesDone = true;
    }
    if (typeof MaplibreCOGProtocol !== 'undefined' &&
        !registerProtocols.cogDone) {
      maplibregl.addProtocol('cog', MaplibreCOGProtocol.cogProtocol);
      registerProtocols.cogDone = true;
    }
  }

  function guessVectorSourceLayer(header) {
    if (!header) return '';
    if (Array.isArray(header.layers) && header.layers.length) {
      return header.layers[0].name || header.layers[0];
    }
    return '';
  }

  function cameraOrDefault(boot, key, fallback) {
    var camera = boot.viewState && boot.viewState.camera;
    if (camera && camera[key] !== undefined && camera[key] !== null) {
      return camera[key];
    }
    return fallback;
  }

  function firstUserLayerId(viewer) {
    return viewer.userLayers.length ? viewer.userLayers[0].id : undefined;
  }

  function libsAvailable(boot) {
    if (typeof maplibregl === 'undefined') return false;
    if (resourceListHasFormat(boot, 'pmtiles') &&
        typeof pmtiles === 'undefined') return false;
    if (resourceListHasFormat(boot, 'fgb', 'flatgeobuf') &&
        typeof flatgeobuf === 'undefined') return false;
    if (resourceListHasFormat(boot, 'cog', 'tif', 'tiff', 'geotiff') &&
        typeof MaplibreCOGProtocol === 'undefined') return false;
    if (resourceListHasFormat(boot, 'csv', 'tsv', 'csv-geo-au',
                              'csv-geo-nz', 'csv-geo-us') &&
        typeof Papa === 'undefined') return false;
    return true;
  }

  function resourceListHasFormat(boot) {
    var formats = Array.prototype.slice.call(arguments, 1);
    var resources = (boot && boot.viewableResources) || [];
    return resources.some(function (res) {
      return res && formats.indexOf(res.format) >= 0;
    });
  }

  function buildGeoJsonSourceSpec(spec, fallbackData, enableClustering) {
    var sourceSpec = clone(spec) || { type: 'geojson', data: fallbackData };
    delete sourceSpec._maplibre_loader;
    delete sourceSpec._source_url;
    if (enableClustering) {
      sourceSpec.cluster = true;
      sourceSpec.clusterRadius = 50;
      sourceSpec.clusterMaxZoom = 14;
    }
    return sourceSpec;
  }

  function hasSavedActiveLayerIds(viewState) {
    return !!(viewState && viewState.ui &&
              Array.isArray(viewState.ui.active_layer_ids));
  }

  function buildSavedLayerStateMap(style) {
    var out = {};
    if (!style || !Array.isArray(style.layers)) return out;
    style.layers.forEach(function (layer) {
      if (!layer || !layer.id) return;
      out[layer.id] = {
        paint: clone(layer.paint),
        layout: clone(layer.layout),
        filter: clone(layer.filter),
        minzoom: layer.minzoom,
        maxzoom: layer.maxzoom,
      };
    });
    return out;
  }

  function mergeObjects(base, override) {
    var merged = clone(base) || {};
    Object.keys(override || {}).forEach(function (key) {
      merged[key] = override[key];
    });
    return merged;
  }

  function collectLayerOpacities(viewer) {
    var out = {};
    viewer.userLayers.forEach(function (layer) {
      out[layer.id] = viewer.opacityFor(layer);
    });
    return out;
  }

  function flashBanner(text, kind) {
    var banner = document.getElementById('ckanext-maplibre-pipeline-banner');
    if (!banner) {
      alert(text);
      return;
    }
    banner.style.display = 'block';
    banner.classList.remove('error', 'success');
    if (kind) banner.classList.add(kind);
    banner.textContent = text;
    setTimeout(function () { banner.style.display = 'none'; }, 4000);
  }

  function el(tag, opts) {
    var n = document.createElement(tag);
    opts = opts || {};
    if (opts.className) n.className = opts.className;
    if (opts.text) n.textContent = opts.text;
    if (opts.style) n.setAttribute('style', opts.style);
    if (opts.value !== undefined) n.value = opts.value;
    if (opts.type) n.type = opts.type;
    if (opts.min !== undefined) n.min = opts.min;
    if (opts.max !== undefined) n.max = opts.max;
    if (opts.step !== undefined) n.step = opts.step;
    return n;
  }

  function clone(value) {
    if (value === undefined || value === null) return value;
    try { return JSON.parse(JSON.stringify(value)); }
    catch (e) { return value; }
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
                '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function eachCoord(coords, callback) {
    if (typeof coords[0] === 'number') {
      callback(coords[0], coords[1]);
      return;
    }
    coords.forEach(function (c) { eachCoord(c, callback); });
  }
})();
