import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.acdm_ontology import parse_question
from app.config import load_settings, ROOT


class ACDMOntologyTests(unittest.TestCase):
    def test_original_planner_recognizes_full_day(self):
        from app.planner import Planner
        from datetime import date,timedelta
        planner = Planner.__new__(Planner)
        self.assertEqual(planner.period('2026年10月9日计划航班总数'),'2026-10-09')
        self.assertEqual(planner.period('2026-10-09实际执行航班'),'2026-10-09')
        self.assertEqual(planner.period('昨天计划航班'),(date.today()-timedelta(days=1)).isoformat())
        self.assertEqual(planner.period('2026年10月计划航班'),'2026-10')
        with self.assertRaises(ValueError):
            planner.period('2026年2月30日')
    def setUp(self):
        profile = json.loads((ROOT/'data/acdm/profile.json').read_text(encoding='utf-8'))
        document = json.loads((ROOT/profile['files']['ontology']).read_text(encoding='utf-8'))
        self.catalog = SimpleNamespace(overrides=document, entities=document['entities'])

    def test_switch_keeps_identity_and_separates_files(self):
        with patch.dict('os.environ',{'HBASK_DATA_SOURCE':'hbairport'}):
            original = load_settings()
        with patch.dict('os.environ',{'HBASK_DATA_SOURCE':'acdm'}):
            acdm = load_settings()
        self.assertEqual(acdm.identity_db, original.db)
        self.assertNotEqual(acdm.db['database'], original.db['database'])
        self.assertNotEqual(acdm.ontology_path, original.ontology_path)
        self.assertNotEqual(acdm.memory_path, original.memory_path)
        self.assertNotEqual(acdm.admin_directory, original.admin_directory)
        self.assertFalse(acdm.external_models_enabled)

    def test_date_direction_and_flight_conditions(self):
        plan = parse_question('2026年10月9日出港MU2503航班明细',self.catalog)
        self.assertEqual(plan['subject'],'TEST.flight_plan')
        self.assertEqual([c['value'] for c in plan['conditions']],['2026-10-09','2026-10-10','D','MU2503'])
        self.assertEqual(parse_question('2026-10-09执行航班量',self.catalog)['subject'],'TEST.flight_execution')

    def test_unrecognized_and_outside_dates_never_expand_query(self):
        for question in ['2026-10-09国航计划航班量','2025-10-09计划航班量','计划航班量','2026-10-09进港和出港计划航班量']:
            with self.subTest(question=question), self.assertRaises(ValueError):
                parse_question(question,self.catalog)

    def test_model_calls_blocked_before_network(self):
        from app import llm
        with patch.object(llm,'settings',SimpleNamespace(external_models_enabled=False)), \
             patch.object(llm,'current_model',return_value={'url':'https://api.siliconflow.cn/v1/chat/completions','model':'test','key':'test'}), \
             patch.object(llm,'open_request') as network:
            self.assertFalse(llm.model_available())
            with self.assertRaises(ValueError):
                llm.chat('test','test')
            with self.assertRaises(ValueError):
                llm.chat_stream('test','test',lambda _:None)
            network.assert_not_called()

    def test_local_model_allowed_and_remote_hosts_denied(self):
        from app import llm
        with patch.object(llm,'settings',SimpleNamespace(permitted_model_origins=('http://172.26.197.154:3000',))):
            for url in ['http://localhost:11434/v1/chat/completions','http://127.0.0.1:1234/v1/chat/completions','http://[::1]:8000/v1/chat/completions']:
                self.assertTrue(llm.local_model({'url':url}))
            for url in ['https://api.siliconflow.cn/v1/chat/completions','http://192.168.1.1/v1','http://localhost.example.com/v1','file:///etc/passwd']:
                self.assertFalse(llm.local_model({'url':url}))
            self.assertTrue(llm.local_model({'url':'http://172.26.197.154:3000/v1/chat/completions'}))
            self.assertFalse(llm.local_model({'url':'http://172.26.197.154:3001/v1/chat/completions'}))

    def test_acdm_only_allows_admin_query_access(self):
        from app import business_access
        from fastapi import HTTPException
        with patch.object(business_access,'settings',SimpleNamespace(data_source='acdm')):
            with patch.object(business_access,'identity',return_value={'access':SimpleNamespace(roles=('user',))}):
                with self.assertRaises(HTTPException) as error:
                    business_access.query_identity(None)
                self.assertEqual(error.exception.status_code,403)
            with patch.object(business_access,'identity',return_value={'owner':'test-admin','access':SimpleNamespace(roles=('admin',))}):
                value = business_access.query_identity(None)
                self.assertIsNone(value['access'])
                self.assertEqual(value['owner'],'test-admin')
