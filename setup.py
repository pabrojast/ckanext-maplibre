# -*- coding: utf-8 -*-
from setuptools import setup, find_packages
from codecs import open
from os import path

here = path.abspath(path.dirname(__file__))

try:
    with open(path.join(here, 'README.rst'), encoding='utf-8') as f:
        long_description = f.read()
except (IOError, OSError):
    long_description = ''

setup(
    name='ckanext-maplibre',
    version='0.1.1',

    description=(
        'CKAN resource view powered by MapLibre GL JS + PMTiles + '
        'FlatGeobuf + COG. Cloud-native GIS viewer with async '
        'conversion pipeline for large geospatial datasets.'
    ),
    long_description=long_description,

    url='https://github.com/pabrojast/ckanext-maplibre',
    author='Pablo Rojas',
    author_email='pablo.ignacio.rt@gmail.com',

    license='AGPL',

    classifiers=[
        'Development Status :: 3 - Alpha',
        'License :: OSI Approved :: '
        'GNU Affero General Public License v3 or later (AGPLv3+)',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
    ],

    keywords='CKAN maplibre pmtiles flatgeobuf cog geospatial',

    packages=find_packages(exclude=['contrib', 'docs', 'tests*']),
    namespace_packages=['ckanext'],

    install_requires=[],

    include_package_data=True,
    package_data={},
    data_files=[],

    entry_points='''
        [ckan.plugins]
        maplibre=ckanext.maplibre.plugin:MapLibreViewPlugin

        [babel.extractors]
        ckan = ckan.lib.extract:extract_ckan
    ''',

    message_extractors={
        'ckanext': [
            ('**.py', 'python', None),
            ('**.js', 'javascript', None),
            ('**/templates/**.html', 'ckan', None),
        ],
    }
)
