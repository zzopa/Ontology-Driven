import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import business_access
from app.config import load_settings
from app.ontology import Catalog
from app.management import OntologyStore
from app.acdm_ontology import parse_question


class OpsAskTests(unittest.TestCase):
    def setUp(self):
        with patch.dict('os.environ', {'HBASK_DATA_SOURCE': 'ops_ask'}):
            self.settings = load_settings()
        self.document = json.loads(self.settings.ontology_path.read_text(encoding='utf-8'))

    def test_source_keeps_identity_and_isolates_runtime(self):
        with patch.dict('os.environ', {'HBASK_DATA_SOURCE': 'hbairport'}):
            original = load_settings()
        with patch.dict('os.environ', {'HBASK_DATA_SOURCE': 'acdm'}):
            acdm = load_settings()
        self.assertEqual(self.settings.db['database'], 'ops_ask')
        self.assertEqual(self.settings.db['startup_params'], {'TimeZone': 'Asia/Shanghai'})
        self.assertEqual(self.settings.identity_db, original.db)
        for field in ('ontology_path', 'schema_path', 'memory_path', 'admin_directory'):
            self.assertNotEqual(getattr(self.settings, field), getattr(original, field))
            self.assertNotEqual(getattr(self.settings, field), getattr(acdm, field))
        self.assertFalse(self.settings.external_models_enabled)
        self.assertEqual(self.settings.host, '127.0.0.1')

    def test_only_standard_tables_can_be_queried(self):
        names = ['std_flight_core', 'dim_airline', 'sys_user', 'sync_job_state', 'future_table']
        tables = [{'table': name, 'name': name, 'columns': [
            {'name': 'id', 'type': 'bigint', 'nullable': False}],
            'primary_key': ['id']} for name in names]
        catalog = Catalog({'tables': tables, 'foreign_keys': []}, self.document, self.settings)
        self.assertEqual(set(catalog.entities), {'std_flight_core', 'dim_airline'})

    def test_only_declared_acdm_source_is_opened(self):
        tables = [{'table': name, 'name': name.split('.')[-1], 'schema': 'acdm',
                   'columns': [{'name': col, 'type': 'text', 'nullable': True}
                               for col in ['FLIGHT_ID', 'FLIGHT_NO', 'EXEC_DATE']],
                   'primary_key': ['FLIGHT_ID']}
                  for name in ['acdm.tb_dyn_flight', 'acdm.unregistered_table']]
        catalog = Catalog({'tables': tables, 'foreign_keys': []}, self.document, self.settings)
        self.assertEqual(set(catalog.entities), {'acdm.tb_dyn_flight'})

    def test_composite_resource_relations_preserve_airport_scope(self):
        for target, code in [('dim_stand', 'stand_code'), ('dim_gate', 'gate_code')]:
            edge = next(edge for edge in self.document['logical_links']
                        if edge['pairs'][0][1].startswith(target + '.'))
            self.assertEqual(edge['pairs'], [
                ['std_flight_core.airport_code', target + '.airport_code'],
                ['std_flight_core.' + code, target + '.' + code]])

    def test_link_relation_selects_the_owning_flight(self):
        tables = []
        for name, fields in [('std_flight_core', ['id']),
                             ('std_flight_link', ['id', 'flight_id', 'linked_flight_id'])]:
            tables.append({'table': name, 'name': name, 'primary_key': ['id'],
                           'columns': [{'name': field, 'type': 'bigint', 'nullable': False}
                                       for field in fields]})
        foreign_keys = [{'table': 'std_flight_link', 'column': field,
                         'ref_table': 'std_flight_core', 'ref_column': 'id'}
                        for field in ['flight_id', 'linked_flight_id']]
        catalog = Catalog({'tables': tables, 'foreign_keys': foreign_keys},
                          self.document, self.settings)
        edge = catalog.path('std_flight_core', 'std_flight_link')[0]
        self.assertEqual(edge['pairs'], [['flight_id', 'id']])
        del catalog.overrides['relations']['std_flight_core']['std_flight_link']['pairs']
        with self.assertRaisesRegex(ValueError, '多个关联路径'):
            catalog.path('std_flight_core', 'std_flight_link')

    def test_ops_data_requires_original_admin(self):
        with patch.object(business_access, 'settings', SimpleNamespace(data_source='ops_ask')):
            for roles in [('user',), ('admin',)]:
                with self.subTest(roles=roles), patch.object(business_access, 'identity',
                        return_value={'owner': 'test', 'access': SimpleNamespace(roles=roles)}):
                    if roles == ('admin',):
                        self.assertIsNone(business_access.query_identity(None)['access'])
                    else:
                        with self.assertRaises(HTTPException) as error:
                            business_access.query_identity(None)
                        self.assertEqual(error.exception.status_code, 403)

    def test_production_rows_are_not_exposed_as_additive_metrics(self):
        production = self.document['entities']['std_flight_production']
        self.assertEqual(production['metrics'], {})
        self.assertIn('不能直接累加', production['attributes']['qty_scope']['enums']['FLIGHT_REPEAT'])

    def test_invalid_relation_selector_is_rejected_before_saving(self):
        store = OntologyStore(self.settings.ontology_path, self.settings)
        for pairs in [None, [], [['broken']], [['a.id', 1]]]:
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                store.validate({'entities': {}, 'relations': {'a': {'b': {'pairs': pairs}}}},
                               {'tables': [], 'foreign_keys': []})

    def test_execution_questions_use_actual_event_day_and_direction(self):
        catalog = SimpleNamespace(overrides=self.document, entities=self.document['entities'])
        today = datetime(2026, 11, 9, tzinfo=timezone(timedelta(hours=8)))
        with patch('app.acdm_ontology.datetime') as clock:
            clock.now.return_value = today
            for question, direction in [('昨天航班执飞情况', None),
                                        ('昨天出港航班执飞情况', 'DEP'),
                                        ('昨天进港执行航班量', 'ARR')]:
                with self.subTest(question=question):
                    plan = parse_question(question, catalog)
                    self.assertEqual(plan['subject'], 'ops_ask_views.flight_execution')
                    if '情况' in question:
                        self.assertEqual(plan['intent'], 'count')
                        self.assertEqual(plan['group_by'], ['direction'])
                    self.assertEqual(plan['conditions'][:2], [
                        {'col': 'execution_time', 'op': '>=', 'value': '2026-11-08'},
                        {'col': 'execution_time', 'op': '<', 'value': '2026-11-09'}])
                    if direction:
                        self.assertEqual(plan['conditions'][2],
                                         {'col': 'direction', 'op': '=', 'value': direction})
                    else:
                        self.assertEqual(len(plan['conditions']), 2)
            with self.assertRaises(ValueError):
                parse_question('昨天南方航空航班执飞情况', catalog)

    def test_execution_view_requires_explicit_metadata_registration(self):
        self.assertEqual(
            self.settings.metadata_views,
            (
                'ops_ask_views.flight_execution',
                'public.mv_acdm_dyn_flight_detail',
                'public.mv_acdm_turnaround_pair',
                'ops_ask_views.flight_hourly_movements',
            ),
        )



    def test_global_metrics_and_dimensions_in_catalog(self):
        schema = json.loads(self.settings.schema_path.read_text(encoding='utf-8'))
        catalog = Catalog(schema, self.document, self.settings)
        self.assertIn('bridge_stand_rate', catalog.metrics)
        self.assertIn('flight_count', catalog.metrics)
        self.assertIn('dep_delay_count', catalog.metrics)
        self.assertIn('hour', catalog.dimensions)
        self.assertIn('airline', catalog.dimensions)

    def test_metric_sql_compilation_and_planner_matching(self):
        from app.query import Compiler
        from app.planner import Planner
        schema = json.loads(self.settings.schema_path.read_text(encoding='utf-8'))
        catalog = Catalog(schema, self.document, self.settings)
        compiler = Compiler(catalog)
        planner = Planner(catalog)

        # 1. Test Planner matching global metric
        local = planner.rules('昨天靠桥率是多少？')
        self.assertEqual(local['intent'], 'metric')
        self.assertEqual(local['metric'], 'bridge_stand_rate')
        self.assertEqual(local['subject'], 'std_flight_core')

        # 2. Test Compiler SQL generation for bridge_stand_rate
        stmt = compiler.aggregate(local)
        self.assertIn('bridge_stand_rate', stmt.sql)
        self.assertIn('dim_stand', stmt.sql)
        self.assertIn('flight_date', stmt.sql)

        # 3. Test Compiler SQL generation with dimension grouping
        dim_plan = {
            'subject': 'std_flight_core',
            'intent': 'metric',
            'metric': 'flight_count',
            'conditions': [{'table': 'std_flight_core', 'col': 'flight_date', 'op': 'prefix', 'value': '2026-11-08'}],
            'group_by': ['airline']
        }
        dim_stmt = compiler.aggregate(dim_plan)
        self.assertIn('GROUP BY c.airline_code', dim_stmt.sql)
        self.assertIn('ORDER BY "flight_count" DESC', dim_stmt.sql)

if __name__ == '__main__':
    unittest.main()
