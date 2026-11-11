"""Operations planning contracts: paraphrases, explicit scope, and safe continuation."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from app import core
from app.agent.coordinator import ACTUAL, BEIJING, CORE, Coordinator
from app.admin.store import AdminStore
from app.config import load_settings
from app.ontology import Catalog
from app.query import Compiler


REFS = [{'role':'airline','term':'南航','value':'CZ','table':'dim_airline','field':'iata_code','column':'airline_code'}]


def ops_catalog():
    with patch.dict('os.environ', {'HBASK_DATA_SOURCE':'ops_ask'}):
        settings = load_settings()
    return Catalog(json.loads(settings.schema_path.read_text(encoding='utf-8')),
                   json.loads(settings.ontology_path.read_text(encoding='utf-8')), settings)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.catalog = ops_catalog()
        self.coordinator = Coordinator(self.catalog, references=REFS)

    def test_actual_count_preserves_airline_direction_and_event_day(self):
        plan = self.coordinator.plan('昨天南航出港实际执行了多少班')
        self.assertEqual(plan['subject'], ACTUAL)
        self.assertEqual(plan['intent'], 'count')
        conditions = {c['col']:c for c in plan['conditions'] if c['op']=='='}
        self.assertEqual(conditions['direction']['value'], 'DEP')
        self.assertEqual(conditions['airline_code']['value'], 'CZ')
        self.assertFalse(any(c['col']=='flight_date' for c in plan['conditions']))
        sql = Compiler(self.catalog).aggregate(plan)
        self.assertIn('execution_time', sql.sql)
        self.assertIn('CZ', sql.params)

    def test_first_last_paraphrases_have_equivalent_plans(self):
        questions = ['昨天最晚实际起飞的出港航班', '昨天最晚起飞的航班是哪班',
                     '昨日哪一班最晚从本场飞走', '昨天最后一个出港实际执飞航班',
                     '查看昨天最后一班实际离港航班']
        plans = [self.coordinator.plan(q) for q in questions]
        for plan in plans:
            self.assertEqual(plan['subject'], ACTUAL)
            self.assertEqual(plan['limit'], 1)
            self.assertEqual(plan['order_by'], [{'field':'execution_time','direction':'desc'}])
            self.assertEqual(plan['conditions'], plans[0]['conditions'])

    def test_ambiguous_basis_and_direction_are_separate_confirmations(self):
        result = self.coordinator.plan('昨天最后一班航班是哪个')
        self.assertEqual(result['clarification']['slot'], 'basis')
        result = self.coordinator.plan('实际执行', clarification=result['clarification'], choice_id='1')
        self.assertEqual(result['clarification']['slot'], 'direction')
        plan = self.coordinator.plan('出港', clarification=result['clarification'], choice_id='1')
        self.assertEqual(plan['subject'], ACTUAL)
        self.assertEqual(plan['limit'], 1)

    def test_confirmation_can_be_free_text_and_preserves_relative_date(self):
        result = self.coordinator.plan('昨天最后一班航班是哪个')
        pending = result['clarification']
        pending['anchor'] = '2026-11-10T23:59:59+08:00'
        pending['confirmed']['period'] = ['2026-11-09','2026-11-10']
        result = self.coordinator.plan('实际执行', clarification=pending)
        plan = self.coordinator.plan('出港', clarification=result['clarification'])
        self.assertEqual(plan['conditions'][0]['value'], '2026-11-09T00:00:00+08:00')

    def test_new_complete_colloquial_question_replaces_pending_clarification(self):
        pending = self.coordinator.plan('昨天最后一班航班是哪个')
        plan = self.coordinator.plan('昨天南航出港实际执行了多少班', clarification=pending['clarification'])
        self.assertEqual(plan['intent'], 'count')
        self.assertEqual(plan['subject'], ACTUAL)
        self.assertFalse(plan.get('order_by'))
        self.assertIn({'table':ACTUAL,'col':'airline_code','op':'=','value':'CZ'}, plan['conditions'])

    def test_dimension_list_does_not_require_a_date(self):
        self.assertEqual(self.coordinator.plan('列出航空公司')['subject'], 'dim_airline')

    def test_calendar_year_is_not_a_flight_identifier(self):
        plan = self.coordinator.plan('查询2026-11-09ACDM保障作业记录明细')
        self.assertFalse(any(c['col']=='FLIGHT_NO' for c in plan['conditions']))
        self.assertNotIn('flight',plan['query_context']['slots'])

    def test_dimension_date_cannot_silently_disappear(self):
        result = self.coordinator.plan('昨天航空公司数量')
        self.assertEqual(result['outcome'],'clarification_needed')
        self.assertIn('时间映射',result['answer'])

    def test_negated_airline_has_an_exclusion_predicate(self):
        expected = self.coordinator.plan('昨天不是南航的出港实际航班')
        # Extra wording may need a model, but its saved conditions must retain negation.
        if expected.get('clarification'):
            self.assertEqual(expected['clarification']['confirmed']['references'][0]['op'],'!=')
        else:
            self.assertEqual(next(c['op'] for c in expected['conditions'] if c['col']=='airline_code'),'!=')

    def test_all_objects_have_identity_and_capability_definitions(self):
        cards = self.catalog.query_capabilities()
        self.assertGreaterEqual(len(cards), 17)
        for table, card in cards.items():
            self.assertTrue(card['identity_fields'], table)
            self.assertTrue(card['semantics'].get('limits'), table)
            self.assertIn('count', card['operations'])
        self.assertNotIn('sys_user', cards)

    def test_baseline_matrix_covers_every_object_with_five_variants(self):
        cases=json.loads((Path(__file__).parent/'ops_coordinator_cases.json').read_text(encoding='utf-8'))
        self.assertEqual({c['expected']['subject'] for c in cases},set(self.catalog.entities))
        self.assertGreaterEqual(sum(c['split']=='holdout' for c in cases)/len(cases),1/3)
        for case in cases:
            if case['split']!='baseline':
                continue
            with self.subTest(case=case['id']):
                plan=self.coordinator.plan(case['question'])
                self.assertEqual(plan.get('subject'),case['expected']['subject'])
                self.assertEqual(plan.get('intent'),case['expected']['intent'])
                if case['expected'].get('filter'):
                    self.assertIn(case['expected']['filter'],[{k:c[k] for k in ('col','op','value')} for c in plan['conditions']])

    def test_unique_business_name_from_model_is_resolved_to_catalog_key(self):
        model=Mock(return_value=json.dumps({'subject':'航空公司','intent':'detail','conditions':[]}))
        plan=Coordinator(self.catalog,model).plan('查看航空公司详细资料')
        self.assertEqual(plan['subject'],'dim_airline')

    def test_model_cannot_inject_execution_metadata(self):
        model=Mock(return_value=json.dumps({'subject':'dim_aircraft','intent':'detail','conditions':[],
                    'flight_sequence':'last','turnaround_data':{'conclusion':'编造'},'sql':'SELECT secrets'}))
        plan=Coordinator(self.catalog,model).plan('查看飞机详细资料')
        self.assertNotIn('flight_sequence',plan)
        self.assertNotIn('turnaround_data',plan)
        self.assertNotIn('sql',plan)

    def test_model_cannot_change_sort_or_add_an_unrequested_metric(self):
        for extra in ({'order_by':[{'field':'code','direction':'desc'}]},
                      {'intent':'count','metric':'dep_delay_count'},
                      {'intent':'count','time_bucket':{'field':'flight_date','grain':'day'}}):
            with self.subTest(extra=extra):
                model=Mock(return_value=json.dumps({'subject':CORE,'intent':'detail','conditions':[],**extra}))
                result=Coordinator(self.catalog,model).plan('查看2026-11-09计划航班主事实详细资料')
                self.assertEqual(result['outcome'],'clarification_needed')

    def test_null_metric_is_not_reported_as_zero(self):
        from app.evidence import fallback_claims
        plan={'subject':CORE,'intent':'metric'}
        evidence={'facts':[{'id':'A1','kind':'aggregate','table':CORE,'name':'航班','values':{'total':None,'n':0},
                            'metric':{'name':'比例','unit':'%','field':'total'}}]}
        _,answer=fallback_claims(self.catalog,plan,evidence,0)
        self.assertIn('未能计算',answer)
        self.assertNotIn('为 0',answer)

    def test_model_receives_full_directory_when_no_keyword_matches(self):
        model = Mock(side_effect=[json.dumps({'subject':'dim_aircraft'}),
                                  json.dumps({'subject':'dim_aircraft','intent':'detail','conditions':[]})])
        plan = Coordinator(self.catalog, model).plan('看看登记过的机身档案')
        self.assertEqual(plan['subject'], 'dim_aircraft')
        self.assertEqual(model.call_count, 2)
        for table in self.catalog.entities:
            self.assertIn(table, model.call_args_list[0].args[0])

    def test_incomplete_keyword_match_can_route_to_another_business_object(self):
        model=Mock(side_effect=[json.dumps({'subject':'std_flight_resource'}),
                                json.dumps({'subject':'std_flight_resource','intent':'count','conditions':[]})])
        plan=Coordinator(self.catalog,model).plan('昨天机位使用多少次')
        self.assertEqual(plan['subject'],'std_flight_resource')
        self.assertEqual(model.call_count,2)

    def test_invented_filters_are_rejected_with_a_bounded_repair(self):
        bad = {'subject':'dim_airline','intent':'detail',
               'conditions':[{'col':'iata_code','op':'=','value':'EVIL'}],
               'grounding':[{'col':'iata_code','quote':'航司'}]}
        model = Mock(return_value=json.dumps(bad))
        result = Coordinator(self.catalog, model).plan('查看航司详细资料')
        self.assertEqual(result['outcome'], 'clarification_needed')
        self.assertEqual(model.call_count, 3)

    def test_unknown_table_cannot_be_selected(self):
        model = Mock(return_value=json.dumps({'subject':'sys_user'}))
        result = Coordinator(self.catalog, model).plan('给我后台账户清单')
        self.assertEqual(result['outcome'], 'clarification_needed')

    def test_model_cannot_replace_actual_with_planned(self):
        bad = {'subject':CORE,'intent':'count','conditions':[]}
        with self.assertRaisesRegex(ValueError, '实际执行'):
            self.coordinator.validate(bad,'昨天出港实际执行多少班',
                {'basis':'actual','direction':'DEP','period':['2026-11-09','2026-11-10']}, datetime.now(BEIJING))

    def test_followup_replaces_direction_and_date_without_other_conversation(self):
        previous = self.coordinator.plan('昨天南航出港实际执行了多少班')
        plan = self.coordinator.plan('进港呢', previous_plan=previous)
        self.assertEqual(next(c['value'] for c in plan['conditions'] if c['col']=='direction'), 'ARR')
        self.assertEqual(next(c['value'] for c in plan['conditions'] if c['col']=='airline_code'), 'CZ')
        standalone = self.coordinator.plan('进港呢')
        self.assertEqual(standalone['outcome'], 'clarification_needed')

    def test_full_question_does_not_inherit_previous_filters(self):
        previous = self.coordinator.plan('昨天南航出港实际执行了多少班')
        plan = self.coordinator.plan('今天进港实际执行多少班', previous_plan=previous)
        self.assertFalse(any(c['col']=='airline_code' for c in plan['conditions']))

    def test_model_unavailable_never_discards_unknown_conditions(self):
        result = self.coordinator.plan('昨天出港实际执行航班中尚未定义状态的明细')
        self.assertEqual(result['outcome'], 'clarification_needed')

    def test_transport_failure_is_not_a_successful_planning_step(self):
        from app.execution import ExecutionTrace
        trace=ExecutionTrace()
        with self.assertRaises(TimeoutError):
            Coordinator(self.catalog,Mock(side_effect=TimeoutError()),trace=trace).plan('查看飞机详细资料')
        self.assertEqual(trace.steps['coordinator']['state'],'failed')

    def test_turnaround_planning_does_not_connect_to_database(self):
        with patch('app.core.connect_db', side_effect=AssertionError('planning must not query')):
            plan = self.coordinator.plan('查询2026-11-09 B20AE过站甘特图')
        self.assertEqual(plan['intent'], 'turnaround')
        self.assertNotIn('turnaround_data', plan)

    def test_adjacent_date_and_identifier_are_independent(self):
        plan=self.coordinator.plan('查询2026-11-09B20AE过站甘特图')
        self.assertEqual(plan['query_identifier'],'B20AE')
        self.assertEqual(plan['flight_date'],'2026-11-09')

    def test_multiple_identifiers_are_not_reduced_to_the_first(self):
        plan=self.coordinator.plan('2026-11-09实际执飞航班CA1234、MU5678的明细')
        self.assertIn({'table':ACTUAL,'col':'flight_no','op':'in','value':['CA1234','MU5678']},plan['conditions'])

    def test_numeric_aircraft_registration_is_not_a_flight_number(self):
        plan=self.coordinator.plan('查询2026-11-09B12345过站甘特图')
        self.assertEqual(plan['query_identifier'],'B12345')
        self.assertNotIn('flight',plan['query_context']['slots'])

    def test_business_table_word_is_not_an_aircraft_registration(self):
        plan = self.coordinator.plan('统计2026-11-10DYN_AIR_BRIDGE数量')
        self.assertNotIn('aircraft', plan['query_context']['slots'])
        self.assertFalse(any(c['col'] == 'aircraft_reg' for c in plan['conditions']))

    def test_report_requires_confirmed_dates_and_cannot_drop_a_filter(self):
        pending = self.coordinator.plan('生成ACDM通用报告')
        self.assertEqual(pending['clarification']['slot'], 'period')
        plan = self.coordinator.plan('2026-11-09', clarification=pending['clarification'])
        self.assertEqual(plan['report_start'], '2026-11-09')
        self.assertEqual(plan['report_end'], '2026-11-09')
        self.assertEqual(self.coordinator.plan('生成昨天南航ACDM通用报告')['outcome'], 'unsupported')
        self.assertEqual(self.coordinator.plan('生成近7天地服公司运行简报')['outcome'], 'clarification_needed')

    def test_undeclared_actual_scope_cannot_count_all_raw_rows(self):
        plan = self.coordinator.validate({'subject':'acdm.tb_dyn_flight','intent':'count','conditions':[]},
            '昨天实际执行了多少架次', {'basis':'actual','period':['2026-11-09','2026-11-10']}, datetime.now(BEIJING))
        self.assertEqual(plan['outcome'], 'clarification_needed')

    def test_metric_cannot_switch_to_another_business_object(self):
        with self.assertRaisesRegex(ValueError, '业务对象'):
            self.coordinator.validate({'subject':CORE,'intent':'metric','metric':'release_on_time_rate','conditions':[]},
                '昨天放行正常率', {'period':['2026-11-09','2026-11-10']}, datetime.now(BEIJING))

    def test_metric_aircraft_type_is_not_misread_as_a_flight_number(self):
        plan = self.coordinator.plan('昨天B738国际出港起飞正常率是多少')
        self.assertEqual(plan['metric'], 'takeoff_on_time_rate')
        self.assertIn({'table':'mv_acdm_dyn_flight_detail','col':'craft_type_icao_cd','op':'=','value':'B738'}, plan['conditions'])
        self.assertFalse(any(c['col'] == 'flight_no' for c in plan['conditions']))

    def test_turnaround_date_range_and_extra_filters_are_not_dropped(self):
        for question in ('最近7天B20AE过站甘特图','2026-11-09B20AE取消航班的过站甘特图'):
            with self.subTest(question=question):
                self.assertEqual(self.coordinator.plan(question)['outcome'],'clarification_needed')

    def test_catalog_overlay_changes_runtime_version(self):
        before = self.catalog.refresh_version()
        self.catalog.metrics['flight_count']['description']='口径修正'
        self.assertNotEqual(before, self.catalog.refresh_version())

    def test_expired_clarification_restarts_without_executing_old_choice(self):
        result = self.coordinator.plan('昨天最后一班航班是哪个')
        result['clarification']['catalog_version']='old'
        resumed = self.coordinator.plan('实际', clarification=result['clarification'], choice_id='1')
        self.assertEqual(resumed['outcome'], 'clarification_needed')
        self.assertEqual(resumed['clarification']['slot'], 'restart')

    def test_invalid_choice_is_a_parameter_error(self):
        result = self.coordinator.plan('昨天最后一班航班是哪个')
        with self.assertRaisesRegex(ValueError,'选项无效'):
            self.coordinator.plan('实际',clarification=result['clarification'],choice_id='999')

    def test_coverage_is_object_specific_and_never_assumes_completeness(self):
        plan = self.coordinator.plan('昨天南航出港实际执行了多少班')
        with patch.object(core,'execute',return_value=[{'first_date':'2026-01-01','last_date':'2026-12-01'}]) as execute:
            result = core.local_coverage(None,self.catalog,plan)
        self.assertFalse(result['complete'])
        self.assertEqual(result['table'],ACTUAL)
        self.assertIn('execution_time',execute.call_args.args[1].sql)


class ClarificationStoreTests(unittest.TestCase):
    def test_pending_and_unsupported_results_do_not_replace_successful_plan(self):
        with TemporaryDirectory() as directory:
            store = AdminStore(Path(directory))
            conversation, message = store.begin_question('u','第一问','test')
            store.complete_question(message,{'ok':True,'plan':{'subject':'a'},'outcome':'answered'})
            _, pending = store.begin_question('u','第二问','test',conversation)
            store.complete_question(pending,{'ok':True,'outcome':'clarification_needed','clarification':{'question':'日期？'}})
            self.assertEqual(store.latest_completed_plan(conversation,'u'),{'subject':'a'})
            self.assertEqual(store.clarification(conversation,'u',pending)['question'],'日期？')
            with self.assertRaises(Exception):
                store.clarification(conversation,'other',pending)
            _, newer = store.begin_question('u','新问题','test',conversation)
            store.complete_question(newer,{'ok':True,'outcome':'unsupported'})
            with self.assertRaisesRegex(ValueError,'澄清已结束'):
                store.clarification(conversation,'u',pending)


class CoordinatorExecutionTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace
        self.catalog = ops_catalog()
        self.settings = replace(self.catalog.settings,coordinator_enabled=True)

    def test_normal_clarification_does_not_execute_a_business_query(self):
        with patch.object(core,'settings',self.settings), patch.object(core,'current_catalog',return_value=self.catalog), \
             patch.object(core,'load_ops_references',return_value=[]), patch.object(core,'execute') as execute:
            result = core.do_ask(None,'昨天最后一班航班是哪个',use_model=False)
        self.assertEqual(result['outcome'],'clarification_needed')
        self.assertTrue(result['ok'])
        execute.assert_not_called()

    def test_actual_query_runs_without_plan_cache_or_optional_model_analysis(self):
        def execute(_conn,statement):
            if statement.sql.startswith('SELECT MIN('):
                return [{'first_date':'2026-01-01','last_date':'2026-12-01'}]
            if statement.sql.startswith('EXPLAIN'):
                return []
            return [{'n':3}]
        events = []
        with patch.object(core,'settings',self.settings), patch.object(core,'current_catalog',return_value=self.catalog), \
             patch.object(core,'load_ops_references',return_value=REFS), patch.object(core,'execute',side_effect=execute), \
             patch.object(core,'remember_plan') as cache, patch.object(core,'chat') as model:
            result = core.do_ask(None,'昨天南航出港实际执行了多少班',use_model=False,on_event=events.append)
        self.assertEqual(result['outcome'],'answered')
        self.assertEqual(result['main_total'],3)
        self.assertIn('3',result['answer'])
        self.assertIn('execution_time',result['sql'])
        self.assertIn('CZ',result['sql_params'])
        self.assertFalse(result['coverage_assessment']['complete'])
        self.assertEqual(result['answer'].strip(),''.join(e['delta'] for e in events if e['type']=='answer_delta').strip())
        cache.assert_not_called()
        model.assert_not_called()

    def test_zero_rows_are_no_data_with_local_coverage_limit(self):
        def execute(_conn,statement):
            if statement.sql.startswith('SELECT MIN('):
                return [{'first_date':None,'last_date':None}]
            if statement.sql.startswith('EXPLAIN'):
                return []
            return [{'n':0}]
        with patch.object(core,'settings',self.settings), patch.object(core,'current_catalog',return_value=self.catalog), \
             patch.object(core,'load_ops_references',return_value=[]),patch.object(core,'execute',side_effect=execute):
            result = core.do_ask(None,'昨天出港实际执行了多少班',use_model=False)
        self.assertEqual(result['outcome'],'no_data')
        self.assertIn('本地已同步记录',result['answer'])


class ClarificationApiTests(unittest.TestCase):
    def setUp(self):
        from app import main
        from types import SimpleNamespace
        self.main = main
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AdminStore(self.temp.name,{'url':'http://127.0.0.1:9999/v1/chat/completions','model':'test','key':'','retention_days':90})
        self.request = SimpleNamespace(headers={'x-hbask-request':'1'},base_url='http://127.0.0.1:8088/')
        for mock in (patch.object(main.admin_service,'get_store',return_value=self.store),
                     patch.object(main.business_access,'query_identity',return_value={'owner':'u','user':{'id':'u'}}),
                     patch.object(main.business_access,'load_access',return_value=SimpleNamespace(roles=('admin',))),
                     patch.object(main,'settings',replace(main.settings,data_source='ops_ask')),
                     patch.object(core,'connect_db',return_value=SimpleNamespace(close=lambda:None))):
            mock.start(); self.addCleanup(mock.stop)

    def test_normal_result_roundtrip_and_server_owned_clarification(self):
        pending = {'ok':True,'outcome':'clarification_needed','answer':'计划还是实际？',
                   'clarification':{'question':'计划还是实际？','options':[{'id':'1','label':'实际','value':'actual'}]}}
        prepared = self.main.prepare_question(self.request,{'question':'昨天航班量'})
        with patch.object(core,'do_ask',return_value=pending):
            result = self.main.run_question(prepared)
        follow = self.main.prepare_question(self.request,{'question':'实际','conversation_id':result['conversation_id'],
                    'clarification_of':result['message_id'],'choice_id':'1','clarification':{'forged':True}})
        self.assertEqual(follow['clarification']['question'],'计划还是实际？')
        self.assertNotIn('forged',follow['clarification'])
        with patch.object(core,'do_ask',return_value={'ok':True,'outcome':'answered','plan':{'subject':ACTUAL}}) as query:
            final = self.main.run_question(follow)
        self.assertEqual(final['outcome'],'answered')
        self.assertEqual(query.call_args.kwargs['choice_id'],'1')
        saved = self.store.conversation(result['conversation_id'],'u')
        self.assertEqual(saved['messages'][0]['result']['outcome'],'clarification_needed')

    def test_stream_treats_unsupported_as_normal_terminal_result(self):
        prepared = self.main.prepare_question(self.request,{'question':'跨库比较'})
        with patch.object(core,'do_ask',return_value={'ok':True,'outcome':'unsupported','answer':'首版不支持跨库'}):
            events = [json.loads(line) for line in self.main.stream_question(prepared)]
        self.assertEqual(events[-1]['type'],'result')
        self.assertEqual(events[-1]['data']['outcome'],'unsupported')
        self.assertFalse(any(e['type']=='error' for e in events))
