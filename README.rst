==============
ckanext-maplibre
==============

CKAN resource view powered by **MapLibre GL JS** and the cloud-native GIS
stack (**PMTiles**, **FlatGeobuf**, **COG**). Designed as a drop-in
replacement for ``ckanext-terria_view`` for organizations that need to
visualize **large geospatial datasets** (millions of features, multi-GB
rasters) without freezing the browser.

Features
========

* MapLibre GL JS viewer embedded directly in the resource view (no iframe).
* Native support for ``.pmtiles``, ``.fgb`` / FlatGeobuf, ``.cog`` /
  Cloud-Optimized GeoTIFF, and small ``.geojson``.
* **Async conversion pipeline** (CKAN RQ jobs) that auto-converts uploaded
  Shapefile ZIPs, large GeoJSON and GeoTIFF files into optimized
  cloud-native formats and stores them as sibling resources of the same
  dataset.
* **Save view** — captures full ``map.getStyle()`` + camera state + UI
  state and persists it on the ``resource_view`` so subsequent loads
  restore the user's edits.
* Optional **SLD → MapLibre** translator for migrating QGIS / GeoServer
  styles.
* Token-based proxy for private dataset resources, identical signing
  pattern to ``ckanext-terria_view``.

Requirements
============

* CKAN 2.10+
* Python 3.8+
* Redis (for RQ jobs)
* External binaries (only required if the conversion pipeline is enabled):

  * ``gdal_translate`` and ``ogr2ogr`` — GDAL 3.1+ (for COG output and
    FlatGeobuf driver).
  * ``tippecanoe`` 2.17+ — for direct PMTiles output. Older versions
    fall back to MBTiles + ``pmtiles convert``.

Install
=======

::

    pip install -e .
    pip install -r requirements.txt

Then add ``maplibre`` to ``ckan.plugins`` in your CKAN config and run::

    ckan db upgrade
    ckan jobs worker maplibre

Configuration
=============

::

    ckanext.maplibre.default_title = MapLibre Viewer
    ckanext.maplibre.max_view_state_bytes = 4194304
    ckanext.maplibre.pipeline.enable = true
    ckanext.maplibre.pipeline.max_input_bytes = 10737418240
    ckanext.maplibre.cdn_libs = true

License
=======

AGPL v3 or later.
