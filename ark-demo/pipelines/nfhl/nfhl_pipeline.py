# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Load NFHL into BigQuery
"""

import os
import json
import datetime

os.environ['OGR_ORGANIZE_POLYGONS'] = 'SKIP'

def parse_gcs_url (gcs_url):
    [full_path, suffix] = gcs_url.split('.')
    basename = full_path.split('/')[-1]
    [prefix, fips, release] = basename.split('_')
    gdb_name = '{}.gdb'.format(basename)

    release_date = datetime.datetime.strptime(release, '%Y%m%d')

    return release_date, gdb_name

"""
Fix GDB datetime fields
see https://desktop.arcgis.com/en/arcmap/latest/manage-data/tables/fundamentals-of-date-fields.htm
"""
def format_gdb_datetime(element, schema):
    from datetime import datetime
    props, geom = element
    dt_fields = []
    for field in schema:
        if field['type'] == 'DATETIME':
            dt_fields.append(field['name'])

    for field in dt_fields:
        if props[field] is not None:
            dt_in = datetime.strptime(props[field], '%Y-%m-%dT%H:%M:%S%z')
            props[field] = dt_in.replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')

    return props, geom


def convert_to_wkt(element):
    from shapely.geometry import shape

    props, geom = element

    return {
        **props,
        'geom': shape(geom).wkt
    }


def filter_weird(element):
    from shapely.geometry import shape
    props, geom = element

    logging.info('filter_weird {} {}'.format(props, geom))

    shape_geom = shape(geom)

    if shape_geom.type in ['Polygon', 'MultiPolygon'] and shape_geom.area == 0:
        return False

    if shape_geom.type == 'Polygon' and shape_geom.length / shape_geom.area > 1e6:
        return False

    return True


def run(pipeline_options, gcs_url, layer=None, dataset=None):
    import apache_beam as beam
    from apache_beam.io.gcp.internal.clients import bigquery as beam_bigquery

    from geobeam.io import GeodatabaseSource
    from geobeam.fn import make_valid, filter_invalid, format_record

    release_date, gdb_name = parse_gcs_url(gcs_url)

    if layer is not None:
        nfhl_layers = [layer]
    else:
        nfhl_layers = json.load(open('nfhl_layers.json'))

    with beam.Pipeline(options=pipeline_options) as p:
        for layer in nfhl_layers:
            layer_schema = json.loads(open(layer + '.json').read())
            (p
             | 'Read ' + layer >> beam.io.Read(GeodatabaseSource(gcs_url,
                 layer_name=layer,
                 gdb_name=gdb_name))
             | 'MakeValid ' + layer >> beam.Map(make_valid)
             | 'FilterInvalid ' + layer >> beam.Filter(filter_invalid)
             | 'FormatGDBDatetimes ' + layer >> beam.Map(format_gdb_datetime, layer_schema)
             #| 'FormatRecords ' + layer >> beam.Map(format_record)
             | 'ConvertToWKT' + layer >> beam.Map(convert_to_wkt)
             | 'WriteToBigQuery ' + layer >> beam.io.WriteToBigQuery(
                   beam_bigquery.TableReference(projectId='geo-solution-demos', datasetId=dataset, tableId=layer),
                   method=beam.io.WriteToBigQuery.Method.FILE_LOADS,
                   write_disposition=beam.io.BigQueryDisposition.WRITE_TRUNCATE,
                   create_disposition=beam.io.BigQueryDisposition.CREATE_NEVER)
            )


if __name__ == '__main__':
    import logging
    import argparse
    from apache_beam.options.pipeline_options import PipelineOptions

    logging.getLogger().setLevel(logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument('--gcs_url', type=str)
    parser.add_argument('--layer', type=str, default=None)
    parser.add_argument('--dataset', type=str, default='nfhl')
    known_args, pipeline_args = parser.parse_known_args()

    pipeline_options = PipelineOptions(pipeline_args)

    run(pipeline_options, known_args.gcs_url, known_args.layer, known_args.dataset)
