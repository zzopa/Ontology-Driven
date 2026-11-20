"""Operations planning contracts: paraphrases, explicit scope, and safe continuation."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
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

    def test_month_and_year_periods_for_turnaround_service_to_beijing(self):
        with patch('app.agent.coordinator.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 18, 16, tzinfo=BEIJING)
            for question, start in (
                ('统计本月截至今天飞北京航班的平均保障时长', '2026-11-01'),
                ('统计一个月飞北京航班的平均保障时长', '2026-10-20'),
                ('查询1个月内飞北京航班的平均保障时长', '2026-10-20'),
                ('最近一个月飞往北京的过站航班平均保障时长', '2026-10-20'),
                ('统计本年飞北京航班的平均保障时长', '2026-01-01'),
                ('飞北京航班一年平均保障时长', '2025-11-19'),
            ):
                with self.subTest(question=question):
                    plan = self.coordinator.plan(question)
                    self.assertEqual(plan.get('metric'), 'avg_turnaround_service_minutes', plan.get('answer'))
                    self.assertEqual(plan['query_context']['raw_question'], question)
                    self.assertEqual({(c['col'], c['op'], c['value']) for c in plan['conditions']}, {
                        ('arr_landin_day', '>=', start), ('arr_landin_day', '<', '2026-11-19'),
                        ('direction', '=', 'DEP'), ('destCity', '=', '北京'), ('airport_code', '=', 'WUH'),
                    })
            followup = self.coordinator.plan('那本月呢？', previous_plan=plan)
            self.assertTrue(followup.get('follow_up'), followup.get('answer'))
            self.assertEqual(followup['query_context']['slots']['period'], ['2026-11-01', '2026-11-19'])
            self.assertEqual(followup['query_context']['slots']['dest_city'], '北京')
            future = self.coordinator.plan('统计一个月后飞北京航班的平均保障时长')
            self.assertEqual(future['clarification']['slot'], 'period')
            self.assertNotIn('origin_city', future['clarification']['confirmed'])

    def test_attribute_followup_binds_the_returned_flight_without_model(self):
        previous = self.coordinator.plan('2026-11-16最早出港实际执行航班是哪个')
        previous['query_context']['result_identity'] = {'id': 54282, 'flight_no': 'TR121', 'direction': 'DEP'}
        chat = Mock(side_effect=AssertionError('attribute follow-up must not call the model'))
        coord = Coordinator(self.catalog, chat=chat)
        for question, field in [('飞机目的地是哪', 'dest_airport'), ('它的目的机场是哪里？', 'dest_airport'),
                                ('这班航班的登机口是几号', 'gate_code')]:
            with self.subTest(question=question):
                plan = coord.plan(question, previous_plan=previous)
                self.assertEqual(plan.get('answer_field'), field)
                self.assertTrue(plan['follow_up'])
                self.assertNotIn('flight_sequence', plan)
                self.assertNotIn('limit', plan)
                self.assertIn({'table': ACTUAL, 'col': 'id', 'op': '=', 'value': 54282}, plan['conditions'])
                self.assertEqual(plan['query_context']['slots']['flight'], 'TR121')
                sql = Compiler(self.catalog).detail(plan, [], 50, 200)
                self.assertIn(54282, sql.params)
                self.assertIn('2026-11-16T00:00:00+08:00', sql.params)
        chat.assert_not_called()
        self.assertNotIn('flight', previous['query_context']['slots'])

    def test_attribute_followup_requires_unique_result_and_keeps_new_queries_separate(self):
        previous = self.coordinator.plan('2026-11-16最早出港实际执行航班是哪个')
        result = self.coordinator.plan('飞机目的地是哪', previous_plan=previous)
        self.assertEqual(result['outcome'], 'clarification_needed')
        self.assertEqual(result['clarification']['slot'], 'identifier')
        previous['query_context']['result_identity'] = {'id': 54282, 'flight_no': 'TR121', 'direction': 'DEP'}
        for question in ['明天飞机目的地是哪', 'CZ3357目的地是哪', '目的地是北京吗', '今天最早出港实际执行航班是哪个']:
            self.assertIsNone(self.coordinator._record_follow_up(question, previous, datetime.now(BEIJING)))

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
        self.assertEqual({c['expected']['subject'] for c in cases}, set(self.catalog.entities))
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

    def test_ground_service_node_wording_keeps_flight_date_and_extra_conditions(self):
        for flight in ('CZ3357', 'CZ8318'):
            for wording in ('地服保障节点甘特图', '地面保障节点', '保障流程', '保障时间轴'):
                with self.subTest(flight=flight, wording=wording):
                    plan = self.coordinator.plan(f'2026-11-10 {flight} 的{wording}')
                    self.assertEqual(plan.get('intent'), 'turnaround', plan)
                    self.assertEqual(plan['query_identifier'], flight)
                    self.assertEqual(plan['flight_date'], '2026-11-10')
        extra = self.coordinator.plan('2026-11-10 CZ3357 的地服保障节点甘特图，只看国际航班')
        self.assertEqual(extra.get('outcome'), 'clarification_needed')

    def test_followup_inherits_turnaround_date_airline_flight_and_pronoun(self):
        # Turn 1: turnaround flight query
        t1 = self.coordinator.plan('2026-11-10 CZ3357 的过站保障')
        self.assertEqual(t1['intent'], 'turnaround')
        self.assertEqual(t1['query_identifier'], 'CZ3357')
        self.assertEqual(t1['flight_date'], '2026-11-10')

        # Turn 2: date delta "11月9号的呢" & day-only "9号的呢" inherit CZ3357 & turnaround intent
        t2 = self.coordinator.plan('11月9号的呢', previous_plan=t1)
        self.assertEqual(t2['intent'], 'turnaround')
        self.assertEqual(t2['query_identifier'], 'CZ3357')
        self.assertTrue(t2['flight_date'].endswith('-11-09'))
        self.assertTrue(t2.get('follow_up'))

        t2_dom = self.coordinator.plan('9号的呢', previous_plan=t1)
        self.assertEqual(t2_dom['intent'], 'turnaround')
        self.assertEqual(t2_dom['query_identifier'], 'CZ3357')
        self.assertEqual(t2_dom['flight_date'], '2026-11-09')

        t2_prev_day = self.coordinator.plan('上一天呢', previous_plan=t2_dom)
        self.assertEqual(t2_prev_day['flight_date'], '2026-11-08')

        # Turn 3: switching tail number via elliptical follow-up clears previous flight slot
        t3 = self.coordinator.plan('B20AE的呢', previous_plan=t2)
        self.assertEqual(t3['intent'], 'turnaround')
        self.assertEqual(t3['query_identifier'], 'B20AE')

        # Pronoun follow-up reuses flight identifier
        t4 = self.coordinator.plan('这班航班2026-11-08的过站保障', previous_plan=t1)
        self.assertEqual(t4['intent'], 'turnaround')
        self.assertEqual(t4['query_identifier'], 'CZ3357')
        self.assertEqual(t4['flight_date'], '2026-11-08')

        # Airline swap follow-up clears stale airline filter and runs deterministically
        extra_refs = [*REFS, {'role': 'airline', 'term': '东航', 'value': 'MU', 'table': 'dim_airline', 'field': 'iata_code', 'column': 'airline_code'}]
        coord = Coordinator(self.catalog, references=extra_refs)
        m1 = coord.plan('昨天南航出港实际执行了多少班')
        m2 = coord.plan('东航的呢', previous_plan=m1)
        self.assertEqual(next(c['value'] for c in m2['conditions'] if c['col'] == 'airline_code'), 'MU')
        self.assertEqual(next(c['value'] for c in m2['conditions'] if c['col'] == 'direction'), 'DEP')

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

    def test_general_report_wording_does_not_collide_with_tongyong_airline_reference(self):
        refs = [*REFS, {'role':'airline','term':'通用','value':'GP','table':'dim_airline','field':'iata_code','column':'airline_code'}]
        coord = Coordinator(self.catalog, references=refs)
        for q in ('生成昨天 A-CDM 通用报告', '生成昨天 A-CDM 通用报'):
            plan = coord.plan(q)
            self.assertEqual(plan['intent'], 'acdm_report')
            self.assertEqual(plan['report_kind'], 'general')
            self.assertEqual(plan['report_period_type'], 'day')
            self.assertNotIn('references', plan['query_context']['slots'])
        airline_plan = coord.plan('昨天通用出港实际执行了多少班')
        self.assertEqual(airline_plan['intent'], 'count')
        self.assertIn({'table':'ops_ask_views.flight_execution','col':'airline_code','op':'=','value':'GP'}, airline_plan['conditions'])

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

    def test_turnaround_phrasing_routes_directly_and_clarification_keeps_question_context(self):
        for q, expected_flight in (
            ('今天MU2472 过站保障情况如何', 'MU2472'),
            ('今天MU2472 加油和保洁作业完成了吗？', 'MU2472'),
        ):
            plan = self.coordinator.plan(q)
            self.assertEqual(plan['intent'], 'turnaround')
            self.assertEqual(plan['query_identifier'], expected_flight)

        pending = self.coordinator.plan('昨天CZ3357 航班情况如何')
        self.assertEqual(pending['clarification']['slot'], 'basis')
        resolved = self.coordinator.plan('实际执行（按实际起飞/落地）', clarification=pending['clarification'], choice_id='1')
        self.assertEqual(resolved['subject'], ACTUAL)
        self.assertEqual(resolved['intent'], 'detail')
        self.assertEqual(resolved['query_context']['question'], '昨天CZ3357 航班情况如何')

        dead_end = {**pending['clarification'], 'slot': None, 'options': []}
        fresh = self.coordinator.plan('今天MU2472 过站保障情况如何', clarification=dead_end)
        self.assertEqual(fresh['intent'], 'turnaround')
        self.assertEqual(fresh['query_identifier'], 'MU2472')

        # Duplicate identifier condition emitted with CORE table by model should deduplicate cleanly
        anchor = datetime.now(BEIJING)
        validated = self.coordinator.validate(
            {'subject': ACTUAL, 'intent': 'detail',
             'conditions': [{'table': CORE, 'col': 'flight_no', 'op': '=', 'value': 'MU2472'}],
             'grounding': [{'table': CORE, 'col': 'flight_no', 'quote': 'MU2472'}]},
            '昨天MU2472 实际航班情况如何',
            {'period': ['2026-11-10', '2026-11-11'], 'flight': 'MU2472', 'basis': 'actual'},
            anchor,
        )
        self.assertEqual(validated['subject'], ACTUAL)
        self.assertTrue(any(c['table'] == ACTUAL and c['col'] == 'flight_no' and c['value'] == 'MU2472' for c in validated['conditions']))

    def test_flight_detail_wording_resolves_actual_and_planned_without_model(self):
        coord = Coordinator(self.catalog, chat=Mock(side_effect=AssertionError('detail query must not call model')))
        pending = coord.plan('MU2593昨天航班详情')
        self.assertEqual(pending['outcome'], 'clarification_needed')
        self.assertEqual(pending['clarification']['slot'], 'basis')

        actual_plan = coord.plan('实际执行（按实际起飞/落地）', clarification=pending['clarification'], choice_id='1')
        self.assertEqual(actual_plan['subject'], ACTUAL)
        self.assertEqual(actual_plan['intent'], 'detail')
        self.assertTrue(any(c['table'] == ACTUAL and c['col'] == 'flight_no' and c['value'] == 'MU2593' for c in actual_plan['conditions']))
        self.assertTrue(any(c['table'] == ACTUAL and c['col'] == 'execution_time' for c in actual_plan['conditions']))

        planned_plan = coord.plan('计划航班（按计划业务日）', clarification=pending['clarification'], choice_id='2')
        self.assertEqual(planned_plan['subject'], CORE)
        self.assertEqual(planned_plan['intent'], 'detail')
        self.assertTrue(any(c['table'] == CORE and c['col'] == 'flight_no' and c['value'] == 'MU2593' for c in planned_plan['conditions']))
        self.assertTrue(any(c['table'] == CORE and c['col'] == 'flight_date' for c in planned_plan['conditions']))

        dyn_plan = coord.plan('昨天MU2593实际航班动态')
        self.assertEqual(dyn_plan['subject'], ACTUAL)
        self.assertEqual(dyn_plan['intent'], 'detail')

    def test_multi_turn_follow_up_preserves_prior_metric_and_topic(self):
        first = self.coordinator.plan('昨天放行正常率是多少？')
        self.assertEqual(first['metric'], 'release_on_time_rate')
        changed_day = self.coordinator.plan('改查今天', previous_plan=first)
        self.assertEqual(changed_day['metric'], 'release_on_time_rate')
        today_str = datetime.now(BEIJING).date().isoformat()
        self.assertTrue(any(c['op'] == '>=' and c['value'] == today_str for c in changed_day['conditions']))
        dep_only = self.coordinator.plan('只看出港', previous_plan=first)
        self.assertEqual(dep_only['metric'], 'release_on_time_rate')
        self.assertTrue(any(c['col'] == 'direction' and c['value'] == 'DEP' for c in dep_only['conditions']))

    def test_seven_turn_follow_up_chain_without_residual_leaks(self):
        refs = [*REFS, {'role': 'airline', 'term': '东航', 'value': 'MU', 'table': 'dim_airline', 'field': 'iata_code', 'column': 'airline_code'}]
        coord = Coordinator(self.catalog, references=refs)
        anchor_today = datetime.now(BEIJING).date()
        day_before = (anchor_today - timedelta(days=2)).isoformat()
        p1 = coord.plan('昨天南航出港实际执行了多少班')
        p2 = coord.plan('东航的呢', previous_plan=p1)
        self.assertEqual(next(c['value'] for c in p2['conditions'] if c['col'] == 'airline_code'), 'MU')
        self.assertNotIn('南航', p2['query_context']['question'])

        p3 = coord.plan('进港呢', previous_plan=p2)
        self.assertEqual(p3['subject'], ACTUAL)
        self.assertEqual(p3['intent'], 'count')
        self.assertEqual(next(c['value'] for c in p3['conditions'] if c['col'] == 'airline_code'), 'MU')
        self.assertEqual(next(c['value'] for c in p3['conditions'] if c['col'] == 'direction'), 'ARR')

        p4 = coord.plan('看明细', previous_plan=p3)
        self.assertEqual(p4['subject'], ACTUAL)
        self.assertEqual(p4['intent'], 'detail')
        self.assertEqual(next(c['value'] for c in p4['conditions'] if c['col'] == 'airline_code'), 'MU')

        p5 = coord.plan('前天呢', previous_plan=p4)
        self.assertEqual(p5['intent'], 'detail')
        self.assertTrue(any(c['op'] == '>=' and c['value'].startswith(day_before) for c in p5['conditions']))

        p6 = coord.plan('有几班', previous_plan=p5)
        self.assertEqual(p6['subject'], ACTUAL)
        self.assertEqual(p6['intent'], 'count')

        p7 = coord.plan('计划航班呢', previous_plan=p6)
        self.assertEqual(p7['subject'], CORE)
        self.assertEqual(p7['intent'], 'count')
        self.assertEqual(next(c['value'] for c in p7['conditions'] if c['col'] == 'airline_code'), 'MU')
        self.assertEqual(next(c['value'] for c in p7['conditions'] if c['col'] == 'direction'), 'ARR')

    def test_metric_dimension_topn_swap_and_detail_follow_ups(self):
        coord = Coordinator(self.catalog, chat=Mock(side_effect=AssertionError('metric follow-ups must not call model')), references=REFS)
        first = coord.plan('昨天放行正常率是多少？')
        self.assertEqual(first['metric'], 'release_on_time_rate')

        by_airline = coord.plan('各航司前5名呢', previous_plan=first)
        self.assertEqual(by_airline['metric'], 'release_on_time_rate')
        self.assertEqual(by_airline.get('limit'), 5)
        self.assertTrue(any('airline' in str(g) for g in by_airline.get('group_by', [])))

        prev_day = (datetime.now(BEIJING).date() - timedelta(days=2)).isoformat()
        shifted = coord.plan('前天呢', previous_plan=by_airline)
        self.assertEqual(shifted['metric'], 'release_on_time_rate')
        self.assertEqual(shifted.get('limit'), 5)
        self.assertTrue(any(c['op'] == '>=' and prev_day in str(c['value']) for c in shifted['conditions']))

        swapped = coord.plan('换成靠桥率', previous_plan=shifted)
        self.assertEqual(swapped['metric'], 'bridge_stand_rate')
        self.assertTrue(any(c['op'] == '>=' and prev_day in str(c['value']) for c in swapped['conditions']))

        detail = coord.plan('看明细', previous_plan=first)
        self.assertNotIn(detail.get('outcome'), ('clarification_needed', 'unsupported'))
        self.assertEqual(detail['intent'], 'detail')
        self.assertIsNone(detail.get('metric'))

    def test_clarification_short_breakout_slotless_recovery_and_pronouns(self):
        pending = self.coordinator.plan('CZ8318地服保障情况')
        self.assertEqual(pending['clarification']['slot'], 'period')

        for q, expected_intent, expected_subject in (
            ('昨天南航多少班', 'clarification_needed', None),
            ('昨天南航有多少航班', 'clarification_needed', 'basis'),
            ('能问什么', 'answered', None),
            ('列出航空公司', 'detail', 'dim_airline'),
            ('昨天放行正常率', 'metric', 'mv_acdm_dyn_flight_detail'),
        ):
            out = self.coordinator.plan(q, clarification=pending['clarification'])
            if expected_intent == 'clarification_needed':
                self.assertEqual(out['outcome'], 'clarification_needed')
                self.assertEqual(out['clarification']['original_question'], q)
                self.assertEqual(out['clarification']['slot'], expected_subject)
            elif expected_intent == 'answered':
                self.assertEqual(out['outcome'], 'answered')
            else:
                self.assertEqual(out['intent'], expected_intent)
                self.assertEqual(out['subject'], expected_subject)

        # Free-text month-day / day-of-month / dot-date answers inside active or slotless clarification
        month_day = f'{datetime.now(BEIJING).year:04d}-11-10'
        for answer_text in ('11月10号', '11.10', '2026年11月10号'):
            resolved = self.coordinator.plan(answer_text, clarification=pending['clarification'])
            self.assertEqual(resolved['intent'], 'turnaround')
            self.assertEqual(resolved['query_identifier'], 'CZ8318')
            self.assertTrue(resolved['flight_date'].endswith('-11-10'))

        slotless = {**pending['clarification'], 'slot': None, 'options': []}
        recovered = self.coordinator.plan('11月10号', clarification=slotless)
        self.assertEqual(recovered['intent'], 'turnaround')
        self.assertEqual(recovered['query_identifier'], 'CZ8318')
        self.assertEqual(recovered['flight_date'], month_day)
        self.assertNotIn('补充条件', recovered['query_context']['question'])

        # Pronouns ("这班", "它") expand without gluing digits onto flight/aircraft IDs
        by_pronoun = self.coordinator.plan('这班11月10号呢', previous_plan=recovered)
        self.assertEqual(by_pronoun['intent'], 'turnaround')
        self.assertEqual(by_pronoun['query_identifier'], 'CZ8318')
        self.assertEqual(by_pronoun['flight_date'], month_day)

        b_plan = self.coordinator.plan('今天B20AE过站保障情况')
        b_pronoun = self.coordinator.plan('那它11月10号呢', previous_plan=b_plan)
        self.assertEqual(b_pronoun['intent'], 'turnaround')
        self.assertEqual(b_pronoun['query_identifier'], 'B20AE')
        self.assertEqual(b_pronoun['flight_date'], month_day)

        # Demonstrative flight switch ("这个CZ3357") inherits prior turnaround context cleanly,
        # and standalone ("这个CZ3357" -> "实际执行" -> "昨天") plans deterministically without calling model.
        switched = self.coordinator.plan('这个CZ3357', previous_plan=recovered)
        self.assertEqual(switched['intent'], 'turnaround')
        self.assertEqual(switched['query_identifier'], 'CZ3357')
        self.assertEqual(switched['flight_date'], month_day)

        no_model = Coordinator(self.catalog, chat=Mock(side_effect=AssertionError('must not invoke model')), references=REFS)
        s1 = no_model.plan('这个CZ3357')
        self.assertEqual(s1['clarification']['slot'], 'basis')
        s2 = no_model.plan('实际执行（按实际起飞/落地）', clarification=s1['clarification'], choice_id='1')
        self.assertEqual(s2['clarification']['slot'], 'period')
        s3 = no_model.plan('昨天', clarification=s2['clarification'], choice_id='1')
        self.assertEqual(s3['subject'], ACTUAL)
        self.assertEqual(s3['intent'], 'detail')
        self.assertTrue(any(c['col'] == 'flight_no' and c['value'] == 'CZ3357' for c in s3['conditions']))

    def test_validate_normalizes_execution_time_calendar_day_variants(self):
        anchor = datetime.now(BEIJING)
        expected = {'period': ['2026-11-11', '2026-11-12'], 'basis': 'actual', 'flight': 'CZ3357'}
        for conds in (
            [{'table': ACTUAL, 'col': 'execution_time', 'op': '>=', 'value': '2026-11-11T00:00:00Z'}],
            [{'table': ACTUAL, 'col': 'execution_time', 'op': '<=', 'value': '2026-11-11T23:59:59'}],
            [{'table': ACTUAL, 'col': 'execution_time', 'op': '=', 'value': '2026-11-11'}],
        ):
            plan = self.coordinator.validate(
                {'subject': ACTUAL, 'intent': 'detail', 'conditions': conds, 'grounding': []},
                '2026-11-11 CZ3357实际执行',
                expected,
                anchor,
            )
            self.assertIn({'table': ACTUAL, 'col': 'execution_time', 'op': '>=', 'value': '2026-11-11T00:00:00+08:00'}, plan['conditions'])
            self.assertIn({'table': ACTUAL, 'col': 'execution_time', 'op': '<', 'value': '2026-11-12T00:00:00+08:00'}, plan['conditions'])
        with self.assertRaisesRegex(ValueError, '计划改变了明确条件：execution_time'):
            self.coordinator.validate(
                {'subject': ACTUAL, 'intent': 'detail',
                 'conditions': [{'table': ACTUAL, 'col': 'execution_time', 'op': '>=', 'value': '2026-11-08T00:00:00+08:00'}],
                 'grounding': []},
                '2026-11-11 CZ3357实际执行',
                expected,
                anchor,
            )

    def test_model_receives_conversation_history(self):
        captured = {}

        def fake_chat(system_prompt, user_payload, **_kwargs):
            captured['payload'] = json.loads(user_payload)
            return json.dumps({'clarification': '请补充筛选条件'}, ensure_ascii=False)

        coord = Coordinator(self.catalog, chat=fake_chat, references=REFS)
        history = [
            {'question': '昨天南航出港实际执行了多少班', 'outcome': 'answered', 'subject': ACTUAL, 'intent': 'count', 'metric': None, 'slots': {}},
        ]
        prev = coord.plan('昨天南航出港实际执行了多少班')
        result = coord.plan('昨天出港实际执行航班中尚未定义状态的明细', previous_plan=prev, history=history)
        self.assertEqual(result['outcome'], 'clarification_needed')
        self.assertEqual(captured['payload']['conversation_history'], history)

    def test_coverage_is_object_specific_and_never_assumes_completeness(self):
        plan = self.coordinator.plan('昨天南航出港实际执行了多少班')
        with patch.object(core,'execute',return_value=[{'first_date':'2026-01-01','last_date':'2026-12-01'}]) as execute:
            result = core.local_coverage(None,self.catalog,plan)
        self.assertFalse(result['complete'])
        self.assertEqual(result['table'],ACTUAL)
        self.assertIn('execution_time',execute.call_args.args[1].sql)


class ClarificationStoreTests(unittest.TestCase):
    def test_only_single_answered_flight_adds_record_identity(self):
        with TemporaryDirectory() as directory:
            store = AdminStore(Path(directory))
            for outcome, total in [('answered', 1), ('answered', 2), ('no_data', 0)]:
                with self.subTest(outcome=outcome, total=total):
                    conversation, message = store.begin_question('u', '最早航班', 'test')
                    store.complete_question(message, {
                        'outcome': outcome, 'main_total': total,
                        'main_rows': [{'id': 54282, 'flight_no': 'TR121', 'direction': 'DEP', 'dest_airport': 'SIN'}] if total else [],
                        'plan': {'subject': ACTUAL, 'intent': 'detail', 'query_context': {'slots': {}, 'result_identity': {'id': 'stale'}}},
                    })
                    plan = store.latest_completed_plan(conversation, 'u')
                    if total == 1:
                        self.assertEqual(plan['query_context']['result_identity'], {'id': 54282, 'flight_no': 'TR121', 'direction': 'DEP'})
                    else:
                        self.assertNotIn('result_identity', plan['query_context'])

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

    def test_recent_conversation_turns_and_failed_retry_keep_clarification(self):
        with TemporaryDirectory() as directory:
            store = AdminStore(Path(directory))
            conversation, m1 = store.begin_question('u', 'CZ8318地服保障情况', 'test')
            store.complete_question(m1, {
                'ok': True, 'outcome': 'clarification_needed',
                'clarification': {'slot': 'period', 'question': '请确认日期', 'options': [{'id': '1', 'label': '昨天', 'value': ['2026-11-11', '2026-11-12']}]},
            })
            _, m_failed = store.begin_question('u', '昨天', 'test', conversation)
            store.complete_question(m_failed, error='临时错误')
            self.assertEqual(store.clarification(conversation, 'u', m1)['slot'], 'period')

            _, m2 = store.begin_question('u', '昨天', 'test', conversation)
            store.complete_question(m2, {
                'ok': True, 'outcome': 'no_data',
                'plan': {'subject': CORE, 'intent': 'turnaround', 'query_context': {'question': 'CZ8318地服保障情况', 'slots': {'flight': 'CZ8318'}}},
            })
            _, m3 = store.begin_question('u', '11月10号', 'test', conversation)
            store.complete_question(m3, {
                'ok': True, 'outcome': 'answered',
                'plan': {'subject': CORE, 'intent': 'turnaround', 'query_context': {'question': 'CZ8318地服保障情况', 'slots': {'flight': 'CZ8318', 'period': ['2026-11-10', '2026-11-11']}}},
            })
            turns = store.recent_conversation_turns(conversation, 'u', limit=6)
            self.assertEqual([t['question'] for t in turns], ['CZ8318地服保障情况', '昨天', '11月10号'])
            self.assertEqual([t['outcome'] for t in turns], ['clarification_needed', 'no_data', 'answered'])


class CoordinatorExecutionTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace
        self.catalog = ops_catalog()
        self.settings = replace(self.catalog.settings,coordinator_enabled=True)

    def test_screenshot_attribute_followup_roundtrips_through_store_and_database(self):
        previous = Coordinator(self.catalog).plan('2026-11-16最早出港实际执行航班是哪个')
        row = {'id': 54282, 'flight_no': 'TR121', 'direction': 'DEP', 'dest_airport': 'SIN'}
        with TemporaryDirectory() as directory:
            store = AdminStore(directory)
            conversation, message = store.begin_question('u', '最早航班', 'test')
            store.complete_question(message, {'outcome': 'answered', 'plan': previous, 'main_total': 1, 'main_rows': [row]})
            previous = store.latest_completed_plan(conversation, 'u')
        def execute(_conn, statement):
            if statement.sql.startswith('SELECT MIN('):
                return [{'first_date': '2026-11-16', 'last_date': '2026-11-16'}]
            if statement.sql.startswith('EXPLAIN'):
                return []
            return [{'bucket': '__main', 'row_data': row, 'total_count': 1}]
        with patch.object(core, 'settings', self.settings), patch.object(core, 'current_catalog', return_value=self.catalog), \
             patch.object(core, 'load_ops_references', return_value=[]), patch.object(core, 'execute', side_effect=execute), \
             patch.object(core, '_single_flight_visual_plan', side_effect=lambda plan, *_args: plan), \
             patch.object(core, 'model_available', return_value=True), patch.object(core, 'chat') as model:
            result = core.do_ask(None, '飞机目的地是哪', previous_plan=previous)
        self.assertEqual(result['outcome'], 'answered')
        self.assertIn('TR121 的目的机场是 SIN', result['answer'])
        self.assertEqual(result['main_total'], 1)
        self.assertIn(54282, result['sql_params'])
        model.assert_not_called()

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

    def test_single_flight_detail_upgrades_visual_meta_to_turnaround_gantt(self):
        fake_row = {
            'id': 51167, 'flight_no': 'MU2593', 'flight_date': '2026-11-10', 'direction': 'DEP',
            'aircraft_reg': 'B5475', 'stand_code': '315', 'gate_code': '15',
            'execution_time': '2026-11-10T17:30:00+08:00',
        }
        fake_td = {
            'flight_id': 51167, 'flight_pair': 'MU2593', 'craft_no': 'B5475', 'stand': '315', 'gate': '15',
            'flight_date': '2026-11-10', 'query_identifier': 'MU2593', 'selection_note': '指定业务日：2026-11-10',
            'source_table': 'std_flight_core', 'id_column': 'id', 'source_label': 'ops_ask 标准数据',
            'source_row': fake_row, 'process_row': {},
            'operations': [{'id': 'boarding', 'name': '旅客登机', 'status': 'completed', 'icon': '',
                            'actual_start': '17:00', 'actual_end': '17:20', 'plan_start': None, 'plan_end': None}],
            'pipeline': [], 'flight_nodes': [{'key': 'atot', 'name': '实际起飞', 'status': 'completed'}],
            'milestones': {}, 'timeline_range': '17:00 - 17:30', 'time_ticks': ['17:00', '17:10', '17:20', '17:30'],
            'conclusion': {'summary': 'test', 'meta': {'has_delay': False, 'evidence': 'test'}},
        }
        def execute(_conn, statement):
            sql = statement.sql
            if sql.startswith('SELECT MIN('):
                return [{'first_date': '2026-10-18', 'last_date': '2026-11-11'}]
            if sql.startswith('EXPLAIN'):
                return []
            if 'flight_execution' in sql:
                return [{'bucket': '__main', 'row_data': fake_row, 'total_count': 1}]
            return []
        with patch.object(core, 'settings', self.settings), patch.object(core, 'current_catalog', return_value=self.catalog), \
             patch.object(core, 'load_ops_references', return_value=[]), patch.object(core, 'execute', side_effect=execute), \
             patch('app.turnaround_standard.get_standard_turnaround_data', return_value=fake_td) as get_td:
            result = core.do_ask(object(), '2026-11-10 MU2593 实际航班详情', use_model=False)
        self.assertEqual(result['outcome'], 'answered')
        self.assertEqual(result['visual_meta']['kind'], 'turnaround_gantt')
        self.assertEqual(result['visual_meta']['flight_pair'], 'MU2593')
        self.assertEqual(len(result['visual_meta']['operations']), 1)
        self.assertNotIn('turnaround_data', result['plan'])
        self.assertEqual(get_td.call_args.kwargs.get('flight_id'), 51167)


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
                     patch.object(main.business_access,'load_access',return_value=SimpleNamespace(roles=('admin',), revision='legacy', user_id='u')),
                     patch.object(main,'settings',replace(main.settings,data_source='ops_ask')),
                     patch('app.config.settings',replace(main.settings,data_source='ops_ask')),
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

    def test_screenshot_turnaround_no_data_then_free_date_followup_and_switch_flight(self):
        catalog = ops_catalog()
        test_settings = replace(catalog.settings, coordinator_enabled=True)
        yesterday_str = (datetime.now(BEIJING).date() - timedelta(days=1)).isoformat()
        nov10_str = f'{datetime.now(BEIJING).year:04d}-11-10'

        def fake_turnaround(query_identifier, flight_date, flight_id=None, **_kwargs):
            if flight_date == yesterday_str:
                raise ValueError(f'当前数据库的标准航班及 ACDM 保障记录中未找到 {query_identifier} 在 {flight_date} 的记录')
            return {
                'flight_id': 1001, 'flight_pair': query_identifier, 'craft_no': 'B20AE',
                'stand': '101', 'gate': '10', 'flight_date': flight_date,
                'query_identifier': query_identifier, 'selection_note': f'指定业务日：{flight_date}',
                'source_table': 'std_flight_core', 'id_column': 'id', 'source_label': 'ops_ask 标准数据',
                'source_row': {'id': 1001, 'flight_no': query_identifier, 'flight_date': flight_date},
                'process_row': {}, 'operations': [], 'pipeline': [],
                'flight_nodes': [], 'milestones': {}, 'timeline_range': '', 'time_ticks': [],
                'conclusion': {'summary': f'{query_identifier} 保障正常', 'meta': {'has_delay': False, 'evidence': '正常'}},
            }

        with patch.object(core, 'settings', test_settings), patch.object(core, 'current_catalog', return_value=catalog), \
             patch.object(core, 'load_ops_references', return_value=REFS), \
             patch.object(core, 'execute', side_effect=lambda _conn, stmt: [{'first_date': '2026-10-01', 'last_date': '2026-11-12'}] if stmt.sql.startswith('SELECT MIN(') else []), \
             patch('app.turnaround_standard.get_standard_turnaround_data', side_effect=fake_turnaround):
            # Turn 1: initial turnaround query clarifies period
            r1 = self.main.run_question(self.main.prepare_question(self.request, {'question': 'CZ8318地服保障情况'}))
            self.assertEqual(r1['outcome'], 'clarification_needed')
            self.assertEqual(r1['clarification']['slot'], 'period')

            # Turn 2: select "昨天" -> no_data with preserved clean plan (never clarification_needed)
            r2 = self.main.run_question(self.main.prepare_question(self.request, {
                'question': '昨天', 'conversation_id': r1['conversation_id'],
                'clarification_of': r1['message_id'], 'choice_id': '1',
            }))
            self.assertEqual(r2['outcome'], 'no_data')
            self.assertIn('未找到 CZ8318', r2['answer'])
            self.assertEqual(r2['plan']['intent'], 'turnaround')
            self.assertEqual(r2['plan']['query_identifier'], 'CZ8318')
            self.assertEqual(r2['plan']['query_context']['question'], 'CZ8318地服保障情况')

            # Turn 3: free-text date "11月10号" -> inherits CZ8318 turnaround and finds data
            r3 = self.main.run_question(self.main.prepare_question(self.request, {
                'question': '11月10号', 'conversation_id': r1['conversation_id'],
            }))
            self.assertEqual(r3['outcome'], 'answered')
            self.assertEqual(r3['plan']['query_identifier'], 'CZ8318')
            self.assertEqual(r3['plan']['flight_date'], nov10_str)

            # Turn 4: pronoun / flight swap follow-up on Turn 3
            r4 = self.main.run_question(self.main.prepare_question(self.request, {
                'question': 'MU2472呢', 'conversation_id': r1['conversation_id'],
            }))
            self.assertEqual(r4['outcome'], 'answered')
            self.assertEqual(r4['plan']['query_identifier'], 'MU2472')
            self.assertEqual(r4['plan']['flight_date'], nov10_str)

            # Turn 5: "前天" follow-up switches date cleanly from Turn 4
            r5 = self.main.run_question(self.main.prepare_question(self.request, {
                'question': '这班11.10呢', 'conversation_id': r1['conversation_id'],
            }))
            self.assertEqual(r5['outcome'], 'answered')
            self.assertEqual(r5['plan']['query_identifier'], 'MU2472')
            self.assertEqual(r5['plan']['flight_date'], nov10_str)

    def test_turnaround_multiple_flights_prompts_flight_selection_and_resolves(self):
        catalog = ops_catalog()
        test_settings = replace(catalog.settings, coordinator_enabled=True)

        def fake_turnaround(query_identifier, flight_date, flight_id=None, **_kwargs):
            if query_identifier == 'MU255':
                raise ValueError('标准航班表中有多个航班（MU2554、MU2556），请改用具体航班号或机号查询')
            return {
                'flight_id': 2002, 'flight_pair': query_identifier, 'craft_no': 'B5475',
                'stand': '202', 'gate': '12', 'flight_date': flight_date,
                'query_identifier': query_identifier, 'selection_note': f'指定业务日：{flight_date}',
                'source_table': 'std_flight_core', 'id_column': 'id', 'source_label': 'ops_ask 标准数据',
                'source_row': {'id': 2002, 'flight_no': query_identifier, 'flight_date': flight_date},
                'process_row': {}, 'operations': [], 'pipeline': [],
                'flight_nodes': [], 'milestones': {}, 'timeline_range': '', 'time_ticks': [],
                'conclusion': {'summary': f'{query_identifier} 保障正常', 'meta': {'has_delay': False, 'evidence': '正常'}},
            }

        with patch.object(core, 'settings', test_settings), patch.object(core, 'current_catalog', return_value=catalog), \
             patch.object(core, 'load_ops_references', return_value=REFS), \
             patch.object(core, 'execute', side_effect=lambda _conn, stmt: [{'first_date': '2026-10-01', 'last_date': '2026-11-12'}] if stmt.sql.startswith('SELECT MIN(') else []), \
             patch('app.turnaround_standard.get_standard_turnaround_data', side_effect=fake_turnaround):
            r1 = self.main.run_question(self.main.prepare_question(self.request, {'question': '2026-11-10 MU255地服保障情况'}))
            self.assertEqual(r1['outcome'], 'clarification_needed')
            self.assertEqual(r1['clarification']['slot'], 'flight')
            self.assertEqual([o['value'] for o in r1['clarification']['options']], ['MU2554', 'MU2556'])
            self.assertEqual(r1['clarification']['original_question'], '2026-11-10 MU255地服保障情况')

            r2 = self.main.run_question(self.main.prepare_question(self.request, {
                'question': 'MU2554', 'conversation_id': r1['conversation_id'],
                'clarification_of': r1['message_id'], 'choice_id': '1',
            }))
            self.assertEqual(r2['outcome'], 'answered')
            self.assertEqual(r2['plan']['query_identifier'], 'MU2554')
            self.assertEqual(r2['plan']['flight_date'], '2026-11-10')


class AdditionalMultiTurnMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        from unittest.mock import patch
        from app.config import load_settings
        from app.ontology import Catalog

        with patch.dict('os.environ', {'HBASK_DATA_SOURCE': 'ops_ask'}):
            settings = load_settings()
        schema = json.loads(Path(settings.schema_path).read_text(encoding='utf-8'))
        doc = json.loads(Path(settings.ontology_path).read_text(encoding='utf-8'))
        cls.catalog = Catalog(schema, doc, settings)
        cls.refs = [
            {
                'term': '东航',
                'role': 'airline',
                'table': 'dim_airline',
                'field': 'iata_code',
                'value': 'MU',
                'binding': 'airline',
                'column': 'airline_code',
            }
        ]

    def setUp(self):
        self.coord = Coordinator(self.catalog, references=self.refs)

    def test_turnaround_and_flight_detail_roundtrip_preserves_id_and_period(self):
        p1 = self.coord.plan('2026-11-10 CZ3357地服保障情况')
        self.assertEqual(p1.get('intent'), 'turnaround')
        self.assertEqual(p1.get('flight_date'), '2026-11-10')

        p2 = self.coord.plan('这班实际航班动态', previous_plan=p1)
        self.assertEqual(p2.get('subject'), ACTUAL)
        self.assertEqual(p2.get('intent'), 'detail')
        conds2 = json.dumps(p2.get('conditions', []), ensure_ascii=False)
        self.assertIn('CZ3357', conds2)
        self.assertIn('2026-11-10', conds2)

        p3 = self.coord.plan('看保障情况', previous_plan=p2)
        self.assertEqual(p3.get('intent'), 'turnaround')
        self.assertEqual(p3.get('query_identifier'), 'CZ3357')
        self.assertEqual(p3.get('flight_date'), '2026-11-10')

        p4 = self.coord.plan('航班动态呢', previous_plan=p3)
        self.assertEqual(p4.get('outcome'), 'clarification_needed')
        self.assertEqual(p4['clarification']['slot'], 'basis')
        p5 = self.coord.plan('实际执行', clarification=p4['clarification'], choice_id='1')
        self.assertEqual(p5.get('subject'), ACTUAL)
        self.assertEqual(p5.get('intent'), 'detail')
        self.assertIn('2026-11-10', json.dumps(p5.get('conditions', []), ensure_ascii=False))

    def test_sequence_followups_switch_earliest_and_keep_airline(self):
        p1 = self.coord.plan('昨天最后一班出港实际执行航班是哪个')
        self.assertEqual(p1.get('flight_sequence'), 'last')
        p2 = self.coord.plan('进港呢', previous_plan=p1)
        self.assertEqual(p2.get('flight_sequence'), 'last')
        self.assertIn('ARR', json.dumps(p2.get('conditions', []), ensure_ascii=False))
        p3 = self.coord.plan('最早一班呢', previous_plan=p2)
        self.assertEqual(p3.get('flight_sequence'), 'first')
        self.assertEqual(p3.get('limit'), 1)
        self.assertIn('ARR', json.dumps(p3.get('conditions', []), ensure_ascii=False))
        p4 = self.coord.plan('东航的呢', previous_plan=p3)
        self.assertEqual(p4.get('flight_sequence'), 'first')
        self.assertIn('MU', json.dumps(p4.get('conditions', []), ensure_ascii=False))

    def test_metric_name_containing_takeoff_to_detail_followup(self):
        p1 = self.coord.plan('2026-11-10地服起飞正常率是多少')
        self.assertEqual(p1.get('metric'), 'ground_takeoff_on_time_rate')
        self.assertNotIn('basis', p1['query_context']['slots'])
        p2 = self.coord.plan('看明细', previous_plan=p1)
        self.assertEqual(p2.get('intent'), 'detail')
        self.assertEqual(p2.get('subject'), 'mv_acdm_dyn_flight_detail')
        self.assertIsNone(p2.get('metric'))

    def test_acdm_report_kind_followup_switches_kind_and_preserves_period(self):
        p1 = self.coord.plan('2026-11-10 ACDM通用报告')
        self.assertEqual(p1.get('intent'), 'acdm_report')
        self.assertEqual(p1.get('report_kind'), 'general')
        self.assertEqual(p1.get('report_start'), '2026-11-10')

        p2 = self.coord.plan('换成地服运行简报', previous_plan=p1)
        self.assertEqual(p2.get('intent'), 'acdm_report')
        self.assertEqual(p2.get('report_kind'), 'ground_briefing')
        self.assertEqual(p2.get('report_start'), '2026-11-10')

        p3 = self.coord.plan('保障节点质量排行呢', previous_plan=p2)
        self.assertEqual(p3.get('intent'), 'acdm_report')
        self.assertEqual(p3.get('report_kind'), 'safeguard_board')
        self.assertEqual(p3.get('report_start'), '2026-11-10')

    def test_dual_track_normalizes_typos_and_shorthand_date_to_fast_path(self):
        def fake_chat(prompt, question, **kwargs):
            if '11-12' in question and '时常' in question:
                return '查询2026-11-12的平均保障时长'
            return question

        coord = Coordinator(self.catalog, chat=fake_chat, references=self.refs)
        plan = coord.plan('查11-12的平均保障时常')
        self.assertIsNone(plan.get('outcome'))
        self.assertEqual(plan.get('subject'), 'mv_acdm_turnaround_pair')
        self.assertEqual(plan.get('metric'), 'avg_turnaround_service_minutes')
        self.assertEqual(plan.get('query_context', {}).get('raw_question'), '查11-12的平均保障时常')
        self.assertEqual(plan.get('query_context', {}).get('normalized_question'), '查询2026-11-12的平均保障时长')
        conds = json.dumps(plan.get('conditions', []), ensure_ascii=False)
        self.assertIn('2026-11-12', conds)
        self.assertIn('2026-11-13', conds)

    def test_ground_duration_colloquial_normalizes_to_turnaround_without_basis_prompt(self):
        def fake_chat(prompt, question, **kwargs):
            if '地面存放' in question or '停了多久' in question:
                return '查询CA8203今天的过站平均保障时长'
            return question

        coord = Coordinator(self.catalog, chat=fake_chat, references=self.refs)
        # Fast-path resolution
        plan = coord.plan('CA8203今天在地面存放多久')
        self.assertIsNone(plan.get('outcome'))
        self.assertEqual(plan.get('subject'), 'mv_acdm_turnaround_pair')
        self.assertEqual(plan.get('metric'), 'avg_turnaround_service_minutes')
        self.assertEqual(plan.get('query_context', {}).get('raw_question'), 'CA8203今天在地面存放多久')
        self.assertIn(plan.get('query_context', {}).get('normalized_question'),
                      ('CA8203今天在地面存放多久', '查询CA8203今天的过站平均保障时长'))

        # Model-normalized colloquial query
        def fake_chat_complex(prompt, question, **kwargs):
            if '地上待了多久' in question:
                return '查询CA8203今天的过站平均保障时长'
            return question

        coord2 = Coordinator(self.catalog, chat=fake_chat_complex, references=self.refs)
        plan2 = coord2.plan('CA8203今天在地上待了多久')
        self.assertIsNone(plan2.get('outcome'))
        self.assertEqual(plan2.get('subject'), 'mv_acdm_turnaround_pair')
        self.assertEqual(plan2.get('metric'), 'avg_turnaround_service_minutes')
        self.assertEqual(plan2.get('query_context', {}).get('normalized_question'), '查询CA8203今天的过站平均保障时长')

    def test_report_generation_short_date_and_followup(self):
        coord = Coordinator(self.catalog, references=self.refs)

        # 1. Direct query with short date "11-15日"
        p1 = coord.plan('生成11-15日地服简报')
        self.assertIsNone(p1.get('outcome'))
        self.assertEqual(p1.get('intent'), 'acdm_report')
        self.assertEqual(p1.get('report_kind'), 'ground_briefing')
        self.assertEqual(p1.get('report_start'), '2026-11-15')
        self.assertEqual(p1.get('report_end'), '2026-11-15')

        # 2. Direct query with "11月15日"
        p2 = coord.plan('生成11月15日地服简报')
        self.assertIsNone(p2.get('outcome'))
        self.assertEqual(p2.get('report_kind'), 'ground_briefing')
        self.assertEqual(p2.get('report_start'), '2026-11-15')

        # 3. Follow-up "前天的" inheriting ground briefing
        p_fu = coord.plan('前天的', previous_plan=p1)
        self.assertIsNone(p_fu.get('outcome'))
        self.assertEqual(p_fu.get('intent'), 'acdm_report')
        self.assertEqual(p_fu.get('report_kind'), 'ground_briefing')

        # 4. Clarification flow: "生成地服简报" -> "昨天"
        c1 = coord.plan('生成地服简报')
        self.assertEqual(c1.get('outcome'), 'clarification_needed')
        c2 = coord.plan('昨天', clarification=c1.get('clarification'))
        self.assertIsNone(c2.get('outcome'))
        self.assertEqual(c2.get('intent'), 'acdm_report')
        self.assertEqual(c2.get('report_kind'), 'ground_briefing')

        # 5. Unsupported extra filter on report
        p_extra = coord.plan('生成11-15日地服简报 东方航空')
        self.assertEqual(p_extra.get('outcome'), 'unsupported')
        self.assertIn('暂不支持附加筛选', p_extra.get('answer'))

    def test_ground_briefing_accepts_its_standard_cutoff_and_rate_breakdown(self):
        coord = Coordinator(self.catalog, references=self.refs)
        question = '生成2026-11-09地服运行简报，明确列出截至22:00的出港执行量及正常率分子、分母。'
        for text in (question, '2026-11-09地服简报截至22点',
                     '生成2026-11-09地服简报，给出截至22点的出港实际执行架次和放行与起飞正常率的分子和分母。',
                     '帮我做一份11月9日的地服简报，22点前实际起飞了多少班？放行和起飞各有多少班正常、总共多少班参与计算？',
                     '我想看2026年11月9日地服简报，把到22时为止的两套口径写清楚，分子分母也分别写出来。',
                     '2026-11-09地服值班简报，麻烦把全场和地服分别统计到晚10点，出港总数、放行正常数和起飞正常数都写上。',
                     '2026-11-09地服简报，从零点至晚上十点起飞了几架？分母也给一下。'):
            with self.subTest(text=text):
                result = coord.plan(text)
                self.assertEqual(result.get('intent'), 'acdm_report', result.get('answer'))
                self.assertEqual(result['report_kind'], 'ground_briefing')
                self.assertEqual((result['report_start'], result['report_end']), ('2026-11-09', '2026-11-09'))
        for text in (question.replace('22:00', '21:00'), question.replace('22:00', '22:01'),
                     question.rstrip('。') + '，只看出港', question.rstrip('。') + '，只看南航',
                     '2026-11-09地服简报，晚十点半前出港执飞多少架次？',
                     '2026-11-09地服简报，晚十点之后的放行正常数量。',
                     '2026-11-09地服简报，18:00至22:00的起飞正常数。',
                     '2026-11-09地服简报，22点前出港执行量，放行正常率高于97%的航司。'):
            with self.subTest(text=text):
                self.assertEqual(coord.plan(text).get('outcome'), 'unsupported')

    def test_dimension_breakdown_fast_path_and_model_fallback(self):
        cat = ops_catalog()
        coord = Coordinator(cat)

        # 1. Fast Path: Direct rule match without calling model
        q = '2026-11-02至2026-11-15，按国内、国际、地区分别统计实际执行架次。'
        plan = coord.plan(q)
        self.assertIsNone(plan.get('outcome'))
        self.assertEqual(plan.get('subject'), 'mv_acdm_dyn_flight_detail')
        self.assertEqual(plan.get('metric'), 'operated_flight_count')
        self.assertEqual(plan.get('group_by'), ['attribute'])
        conds = {c['col']: (c['op'], c['value']) for c in plan.get('conditions', [])}
        self.assertEqual(conds.get('real_exec_day'), ('<', '2026-11-16'))

        # 2. Model fallback path: model rewrites colloquial query to standard query
        fake_chat = lambda prompt, question, **kwargs: '2026-11-02至2026-11-15按航司统计实际执行航班架次'
        coord_with_model = Coordinator(cat, chat=fake_chat)
        q_colloquial = '2026-11-02至2026-11-15各个航司的实际航班量对比'
        plan_fallback = coord_with_model.plan(q_colloquial)
        self.assertIsNone(plan_fallback.get('outcome'))
        self.assertEqual(plan_fallback.get('metric'), 'operated_flight_count')
        self.assertEqual(plan_fallback.get('group_by'), ['airline'])
        self.assertEqual(plan_fallback.get('query_context', {}).get('normalized_question'), '2026-11-02至2026-11-15按航司统计实际执行航班架次')

        # 3. True unsupported multi-query without dimension grouping is still rejected
        p_bad = coord.plan('分别统计南航与国航')
        self.assertEqual(p_bad.get('outcome'), 'unsupported')

    def test_display_field_clause_fast_path_without_clarification_or_model(self):
        cat = ops_catalog()
        chat = Mock(side_effect=AssertionError('pure display-field clause must stay on rule fast path'))
        coord = Coordinator(cat, chat=chat)

        # 1. Explicit actual arrival + display fields ("计划落地" inside display clause must not conflict with "实际进港")
        q = '列出2026-11-09实际进港的MU2632，每次运行分别显示源航班ID、计划落地和实际落地时间。'
        plan = coord.plan(q)
        self.assertIsNone(plan.get('outcome'))
        self.assertEqual(plan['subject'], ACTUAL)
        self.assertEqual(plan['intent'], 'detail')
        eq_conds = {c['col']: c['value'] for c in plan['conditions'] if c['op'] == '='}
        self.assertEqual(eq_conds.get('flight_no'), 'MU2632')
        self.assertEqual(eq_conds.get('direction'), 'ARR')
        chat.assert_not_called()

        # 2. Ambiguous basis still asks clarification on turn 1, and resolves on turn 2 without LLM or corrupting "计划落地"
        q_ambig = '列出2026-11-09进港的MU2632，每次运行分别显示源航班ID、计划落地和实际落地时间。'
        c1 = coord.plan(q_ambig)
        self.assertEqual(c1.get('outcome'), 'clarification_needed')
        self.assertEqual(c1['clarification']['slot'], 'basis')
        c2 = coord.plan('实际执行（按实际起飞/落地）', clarification=c1['clarification'], choice_id='1')
        self.assertIsNone(c2.get('outcome'))
        self.assertEqual(c2['subject'], ACTUAL)
        self.assertIn('计划落地', c2['query_context']['question'])
        self.assertNotIn('实际执飞落地', c2['query_context']['question'])
        chat.assert_not_called()

    def test_display_field_clause_guardrails_and_lexicon_extension(self):
        from app.agent.coordinator_lexicon import (
            DISPLAY_FIELD_GROUPS,
            RESIDUAL_VOCABULARY_GROUPS,
            register_display_fields,
            register_residual_terms,
            strip_display_field_clauses,
        )

        cat = ops_catalog()
        # Filter clauses must never be stripped as pure display clauses
        for unsafe_q in (
            '列出2026-11-09实际进港的MU2632，显示机位不为空的记录',
            '列出2026-11-09实际进港航班，显示延误超过30分钟的记录',
            '2026-11-09，列出航空公司',
        ):
            stripped, removed = strip_display_field_clauses(unsafe_q, catalog=cat)
            self.assertEqual(stripped, unsafe_q)
            self.assertEqual(removed, [])

        # Dynamic lexicon registration works immediately
        register_display_fields('custom_test', '测试自定义列')
        register_residual_terms('custom_test', '测试自定义虚词')
        try:
            stripped, removed = strip_display_field_clauses('列出2026-11-09实际进港的MU2632，显示测试自定义列', catalog=cat)
            self.assertEqual(stripped, '列出2026-11-09实际进港的MU2632')
            self.assertTrue(removed)
        finally:
            DISPLAY_FIELD_GROUPS.pop('custom_test', None)
            RESIDUAL_VOCABULARY_GROUPS.pop('custom_test', None)

    def test_delay_and_stand_questions_from_user(self):
        # 1. 延误原因中主分类的占比（按异常原因分类展示）
        q1 = '武汉天河机场本月延误原因中主分类的占比是多少？按异常原因分类展示'
        p1 = self.coord.plan(q1)
        self.assertIsNone(p1.get('outcome'))
        self.assertEqual(p1['subject'], 'mv_acdm_dyn_flight_detail')
        self.assertEqual(p1['metric'], 'flight_delay_count')
        self.assertIn('abnormal_reason', p1['group_by'])

        # 2. 放行延误最长Top10航班（按延误时长降序展示）
        q2 = '今年武汉天河机场放行延误时间最长的10个航班是哪些？按延误时长降序展示'
        p2 = self.coord.plan(q2)
        self.assertIsNone(p2.get('outcome'))
        self.assertEqual(p2['subject'], 'mv_acdm_dyn_flight_detail')
        self.assertEqual(p2['intent'], 'detail')
        self.assertEqual(p2['limit'], 10)
        self.assertEqual(p2['order_by'], [{'field': 'clearance_delay_time', 'direction': 'desc'}])

        # 3. 外保障航班按天展示并给出近机位与远机位占比
        q3 = '本月武汉天河机场外保障航班按天展示并给出近机位与远机位占比'
        p3 = self.coord.plan(q3)
        self.assertIsNone(p3.get('outcome'))
        self.assertEqual(p3['subject'], 'mv_acdm_dyn_flight_detail')
        self.assertIn('date', p3['group_by'])
        self.assertIn('stand_type', p3['group_by'])
