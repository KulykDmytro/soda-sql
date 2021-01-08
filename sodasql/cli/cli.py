#  Copyright 2020 Soda
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#   http://www.apache.org/licenses/LICENSE-2.0
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
import logging
import os
import sys
from typing import Optional, AnyStr, List

import click
import yaml

from sodasql.cli.file_system import FileSystemSingleton
from sodasql.scan.scan import Scan
from sodasql.scan.sql_metric import SqlMetric
from sodasql.scan.warehouse import Warehouse
from tests.common.logging_helper import LoggingHelper

LoggingHelper.configure_for_cli()


SODA_SQL_VERSION = 'Soda CLI version 2.0.0 beta'


class IndentingDumper(yaml.Dumper):
    """
    yaml.dump hack to get indentation.
    see also https://stackoverflow.com/questions/25108581/python-yaml-dump-bad-indentation
    """
    def increase_indent(self, flow=False, indentless=False):
        return super(IndentingDumper, self).increase_indent(flow, False)


@click.group()
def main():
    pass


@main.command(help='Creates a new warehouse directory and prepares credentials in your ~/.soda/env_vars.yml '
                   'Nothing will be overwritten or removed, only added if it does not exist yet. '
                   'WAREHOUSE_DIR is a directory that contains all soda related yaml config files for one warehouse. '
                   'WAREHOUSE_TYPE is one of {postgres, snowflake, redshift, bigquery, athena}')
@click.argument('warehouse_dir')
@click.argument('warehouse_type')
@click.option('-n', '--warehouse_name',     required=False, default=None, help='The warehouse name')
@click.option('-d', '--database', required=False, default=None, help='The database name to use for the connection')
@click.option('-u', '--username', required=False, default=None, help='The username to use for the connection')
@click.option('-p', '--password', required=False, default=None, help='The password to use for the connection')
def create(warehouse_dir: str,
           warehouse_type: str,
           warehouse_name: Optional[str],
           database: Optional[str],
           username: Optional[str],
           password: Optional[str]):
    try:
        """
        Creates a project directory and ensures a profile is present
        """
        logging.info(SODA_SQL_VERSION)
        file_system = FileSystemSingleton.INSTANCE


        if not warehouse_name:
            warehouse_dir_parent, warehouse_dir_name = file_system.split(warehouse_dir)
            warehouse_name = warehouse_name if warehouse_name else warehouse_dir_name

        from sodasql.scan.dialect import Dialect, ALL_WAREHOUSE_TYPES
        dialect = Dialect.create_for_warehouse_type(warehouse_type)

        if not dialect:
            logging.info(f"Invalid warehouse type {warehouse_type}, use one of {str(ALL_WAREHOUSE_TYPES)}")
            return 1

        if file_system.file_exists(warehouse_dir):
            logging.info(f"Warehouse directory {warehouse_dir} already exists")
        else:
            logging.info(f"Creating warehouse directory {warehouse_dir} ...")
            file_system.mkdirs(warehouse_dir)

        if not file_system.is_dir(warehouse_dir):
            logging.info(f"Warehouse path {warehouse_dir} is not a directory")
            return 1

        configuration_params = {}
        if isinstance(database, str):
            configuration_params['database'] = database
        if isinstance(username, str):
            configuration_params['username'] = username
        if isinstance(password, str):
            configuration_params['password'] = password
        connection_properties = dialect.default_connection_properties(configuration_params)
        warehouse_env_vars_dict = dialect.default_env_vars(configuration_params)

        warehouse_yml_file = file_system.join(warehouse_dir, 'warehouse.yml')
        if file_system.file_exists(warehouse_yml_file):
            logging.info(f"Warehouse configuration file {warehouse_yml_file} already exists")
        else:
            logging.info(f"Creating warehouse configuration file {warehouse_yml_file} ...")
            warehouse_dict = {
                'name': warehouse_name,
                'connection': connection_properties
            }
            warehouse_yml_str = yaml.dump(warehouse_dict, default_flow_style=False, sort_keys=False)
            file_system.file_write_from_str(warehouse_yml_file, warehouse_yml_str)

        dot_soda_dir = file_system.join(file_system.user_home_dir(), '.soda')
        if not file_system.file_exists(dot_soda_dir):
            file_system.mkdirs(dot_soda_dir)

        env_vars_file = file_system.join(dot_soda_dir, 'env_vars.yml')
        env_vars_yml_str = ''
        env_vars_file_exists = file_system.file_exists(env_vars_file)
        if env_vars_file_exists:
            env_vars_yml_str = file_system.file_read_as_str(env_vars_file)

            warehouse_env_vars_dict = None

            existing_env_vars_yml_dict = yaml.load(env_vars_yml_str, Loader=yaml.FullLoader)
            if isinstance(existing_env_vars_yml_dict, dict) and warehouse_name in existing_env_vars_yml_dict:
                logging.info(f"Warehouse section {warehouse_name} already exists in {env_vars_file}.  Skipping...")
                warehouse_env_vars_dict = None

        if warehouse_env_vars_dict:
            warehouse_env_vars_dict = {
                warehouse_name: warehouse_env_vars_dict
            }

            if len(env_vars_yml_str) > 0:
                env_vars_yml_str += '\n'

            env_vars_yml_str += yaml.dump(warehouse_env_vars_dict,
                                          default_flow_style=False,
                                          sort_keys=False)

            if env_vars_file_exists:
                logging.info(f"Adding env vars for {warehouse_name} to {env_vars_file}")
            else:
                logging.info(f"Creating {env_vars_file} with example env vars in section {warehouse_name}")

            file_system.file_write_from_str(env_vars_file, env_vars_yml_str)

        logging.info(f"Review warehouse.yml by running command")
        logging.info(f"  open {warehouse_yml_file}")
        logging.info(f"Review section {warehouse_name} in ~/.soda/env_vars.yml by running command")
        logging.info(f"  open ~/.soda/env_vars.yml")
        logging.info(f"Then run")
        logging.info(f"  soda init {warehouse_dir}")
    except Exception as e:
        logging.exception(f'Exception: {str(e)}')
        return 1


@main.command(help='Finds tables in the warehouse and based on the contents, creates initial scan.yml files.'
                   'WAREHOUSE_DIR is the warehouse directory containing a warehouse.yml file')
@click.argument('warehouse_dir')
def init(warehouse_dir: str):
    """
    Finds tables in the warehouse and based on the contents, creates initial scan.yml files.
    """
    logging.info(SODA_SQL_VERSION)
    file_system = FileSystemSingleton.INSTANCE

    try:
        logging.info(f'Initializing {warehouse_dir} ...')

        from sodasql.scan.warehouse import Warehouse
        warehouse: Warehouse = read_warehouse(warehouse_dir)

        logging.info('Querying warehouse for tables')
        rows = warehouse.sql_fetchall(warehouse.dialect.sql_tables_metadata_query())
        first_table_name = rows[0][0] if len(rows) > 0 else None
        for row in rows:
            table_name = row[0]
            table_dir = file_system.join(warehouse_dir, table_name)
            if not file_system.file_exists(table_dir):
                logging.info(f'Creating table directory {table_dir}')
                file_system.mkdirs(table_dir)
            else:
                logging.info(f'Directory {table_dir} aleady exists')

            table_scan_yaml_file = file_system.join(table_dir, 'scan.yml')

            if file_system.file_exists(table_scan_yaml_file):
                logging.info(f"Scan file {table_scan_yaml_file} already exists")
            else:
                logging.info(f"Creating {table_scan_yaml_file} ...")
                from sodasql.scan.scan_configuration_parser import KEY_TESTS, KEY_METRICS, KEY_TABLE_NAME
                scan_yaml_dict = {
                    KEY_TABLE_NAME: table_name,
                    KEY_METRICS: [
                        'row_count',
                        'missing_count', 'missing_percentage', 'values_count', 'values_percentage',
                        'valid_count', 'valid_percentage', 'invalid_count', 'invalid_percentage',
                        'min', 'max', 'avg', 'sum', 'min_length', 'max_length', 'avg_length'
                    ],
                    KEY_TESTS: {
                        'must have rows': 'row_count > 0'
                    }
                }
                scan_yml_str = yaml.dump(scan_yaml_dict,
                                         sort_keys=False,
                                         Dumper=IndentingDumper,
                                         default_flow_style=False)
                file_system.file_write_from_str(table_scan_yaml_file, scan_yml_str)

        logging.info(f"Next run 'soda scan {warehouse_dir} {first_table_name}' to calculate measurements and run tests")

    except Exception as e:
        logging.exception(f'Exception: {str(e)}')
        return 1
    finally:
        if warehouse:
            warehouse.close()


@main.command(help='Computes all measurements and runs all tests on one table.  Exit code 0 means all tests passed.'
                   'Non zero exist code means tests have failed or an exception occured.  '
                   'If the project has a Soda cloud account configured, '
                   'measurements and test results will be uploaded. '
                   'WAREHOUSE_DIR is the warehouse directory containing a warehouse.yml file '
                   'TABLE is the name of the table to be scanned')
@click.argument('warehouse_dir')
@click.argument('table')
@click.option('--timeslice', required=False, default=None, help='The timeslice')
def scan(warehouse_dir: str, table_dir_name: str, timeslice: str = None, timeslice_variables: dict = None):
    """
    Scans a table by executing queries, computes measurements and runs tests
    """
    logging.info(SODA_SQL_VERSION)

    try:
        logging.info(f'Scanning {table_dir_name} in {warehouse_dir} ...')

        warehouse: Warehouse = read_warehouse(warehouse_dir)

        from sodasql.scan.scan import Scan
        scan: Scan = read_scan(warehouse_dir=warehouse_dir, table_dir_name=table_dir_name, warehouse=warehouse)

        from sodasql.scan.scan_result import ScanResult
        scan_result: ScanResult = scan.execute()

        for measurement in scan_result.measurements:
            logging.info(measurement)
        for test_result in scan_result.test_results:
            logging.info(test_result)

        logging.info(f'{len(scan_result.measurements)} measurements computed')
        logging.info(f'{len(scan_result.test_results)} tests executed')
        if scan_result.has_failures():
            logging.info(f'{scan_result.failures_count()} tests failed!')
        else:
            logging.info(f'All is good. No tests failed.')
        sys.exit(scan_result.failures_count())

    except Exception as e:
        logging.exception(f'Scan failed: {str(e)}')
        sys.exit(1)
    finally:
        if warehouse:
            warehouse.close()


def read_warehouse(warehouse_dir: AnyStr):
    file_system = FileSystemSingleton.INSTANCE
    if isinstance(warehouse_dir, str):
        warehouse_yaml_file = file_system.join(warehouse_dir, 'warehouse.yml')
        if not file_system.file_exists(warehouse_yaml_file):
            logging.info(f'{warehouse_yaml_file} does not exist')
        elif not file_system.is_file(warehouse_yaml_file):
            logging.info(f'{warehouse_yaml_file} is not a file')
        else:
            warehouse_yaml_str = file_system.file_read_as_str(warehouse_yaml_file)
            if warehouse_yaml_str:
                warehouse_dict = parse_yaml(warehouse_yaml_str, warehouse_yaml_file)

                from sodasql.scan.warehouse_configuration_parser import WarehouseConfigurationParser
                soda_project_parser = WarehouseConfigurationParser(warehouse_dict, warehouse_yaml_file)
                soda_project_parser.log()
                soda_project_parser.assert_no_warnings_or_errors()

                from sodasql.scan.warehouse_configuration import WarehouseConfiguration
                warehouse_configuration: WarehouseConfiguration = soda_project_parser.warehouse_configuration

                from sodasql.scan.warehouse import Warehouse
                return Warehouse(warehouse_configuration)
            else:
                logging.info(f'Failed to read warehouse yaml file: {warehouse_yaml_file}')
    else:
        logging.info(f'warehouse_dir is not a string: {str(type(warehouse_dir))}')


def read_scan(warehouse_dir: str, table_dir_name: str, warehouse: Warehouse):
    file_system = FileSystemSingleton.INSTANCE
    table_dir_path = file_system.join(warehouse_dir, table_dir_name)
    scan_yaml_path = file_system.join(table_dir_path, 'scan.yml')

    scan_yaml_path: AnyStr
    file_system = FileSystemSingleton.INSTANCE

    scan_yaml_str = file_system.file_read_as_str(scan_yaml_path)
    scan_dict = parse_yaml(scan_yaml_str, scan_yaml_path)

    from sodasql.scan.scan_configuration_parser import ScanConfigurationParser
    scan_configuration_parser = ScanConfigurationParser(scan_dict, scan_yaml_path)
    scan_configuration_parser.log()
    scan_configuration_parser.assert_no_warnings_or_errors()

    sql_metrics: List[SqlMetric] = []
    for table_dir_file in file_system.list_dir(table_dir_path):
        if not table_dir_file.endswith(os.sep + 'scan.yml'):
            sql_metric = read_sql_metric(table_dir_file)
            sql_metrics.append(sql_metric)

    return Scan(warehouse=warehouse,
                scan_configuration=scan_configuration_parser.scan_configuration,
                sql_metrics=sql_metrics,
                soda_client=None)


def read_sql_metric(sql_metric_path):
    file_system = FileSystemSingleton.INSTANCE
    sql_metric_yaml_str = file_system.file_read_as_str(sql_metric_path)
    sql_metric_dict = parse_yaml(sql_metric_yaml_str, sql_metric_path)

    from sodasql.scan.sql_metric_configuration_parser import SqlMetricConfigurationParser
    sql_metric_parser = SqlMetricConfigurationParser(sql_metric_dict, sql_metric_path)
    sql_metric_parser.log()
    sql_metric_parser.assert_no_warnings_or_errors()
    return sql_metric_parser.sql_metric


def parse_yaml(warehouse_yaml_str: AnyStr, file_name: AnyStr):
    try:
        return yaml.parse(warehouse_yaml_str, Loader=yaml.FullLoader)
    except Exception as e:
        logging.error(f'Parsing yaml file {file_name} failed: {str(e)}')