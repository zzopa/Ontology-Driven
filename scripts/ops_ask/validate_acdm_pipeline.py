"""Compare independent Oracle handbook queries with published PostgreSQL metrics."""
import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re

from app import core
from app.config import ROOT, settings
from app.query import Compiler, execute
from app.sync.oracle_source import connect_oracle


def reference_sql(sql):
    sql = sql.strip().strip('"').rstrip(';')
    sql = re.sub(r"TRUNC\(([^()]+)\)\s*=\s*DATE\s*'\d{4}-\d{2}-\d{2}'",
                 lambda m: '(' + m[1] + ' >= :period_start AND ' + m[1] + ' < :period_end)', sql, flags=re.I)
    if re.search(r"\d{4}-\d{2}-\d{2}", sql):
        raise ValueError('参考SQL还有未绑定的示例日期')
    if not re.match(r'^(WITH|SELECT)\b', sql, re.I):
        raise ValueError('参考SQL必须是只读查询')
    return sql


def validate(start, end, output):
    if start > end:
        raise ValueError('校验开始日期不能晚于结束日期')
    if settings.data_source != 'ops_ask':
        raise ValueError('校验仅支持ops_ask数据源')
    source = json.loads((ROOT / 'tests/fixtures/acdm_oracle_reference.json').read_text(encoding='utf-8'))
    core.initialize()
    catalog = core.current_catalog()
    compiler = Compiler(catalog)
    oracle = connect_oracle(settings.oracle)
    oracle.call_timeout = 120000
    pg = core.connect_db(repeatable_read=True)
    checks, cache = [], {}
    try:
        cur = oracle.cursor()
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SELECT TO_CHAR(SYSTIMESTAMP,'YYYY-MM-DD HH24:MI:SS TZH:TZM') FROM DUAL")
        oracle_as_of = cur.fetchone()[0]
        guard = pg.cursor()
        for case in source['cases']:
            code = case['metric']
            guard.execute('SAVEPOINT metric_check')
            try:
                title = case['reference']
                if title not in cache:
                    cur.execute(reference_sql(source['sql'][title]), period_start=datetime.combine(start, datetime.min.time()),
                                period_end=datetime.combine(end + timedelta(days=1), datetime.min.time()))
                    names = [col[0].lower() for col in cur.description]
                    cache[title] = [dict(zip(names, row)) for row in cur.fetchall()]
                records = cache[title]
                if len(records) != 1:
                    raise ValueError('该参考需要明确多日合计规则，不能平均逐日比率')
                expected = records[0][case['field']]
                metric = catalog.metrics[code]
                target, field = metric['base_table'], metric['date_field']
                conditions = [{'col': field, 'op': '>=', 'value': str(start)},
                              {'col': field, 'op': '<', 'value': str(end + timedelta(days=1))}]
                plan = compiler.validate({'subject': target, 'intent': 'metric', 'metric': code,
                                          'conditions': conditions, 'group_by': [], 'focus_tables': []})
                result = execute(pg, compiler.aggregate(plan))
                actual = result[0].get(metric.get('value_alias') or code) if result else None
                same = actual is expected if actual is None or expected is None else abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal('.005')
                checks.append({'stage': 'metric', 'metric': code, 'expected': expected, 'actual': actual, 'ok': same})
                if code in ('total_load_factor', 'dep_load_factor', 'arr_load_factor'):
                    count_field = {'total_load_factor': 'sample_flights', 'dep_load_factor': 'departure_sample_flights',
                                   'arr_load_factor': 'arrival_sample_flights'}[code]
                    expected_n, actual_n = records[0][count_field], result[0].get('n')
                    checks.append({'stage': 'metric_sample_size', 'metric': code, 'expected': expected_n,
                                   'actual': actual_n, 'ok': expected_n == actual_n})
            except Exception as exc:
                guard.execute('ROLLBACK TO SAVEPOINT metric_check')
                checks.append({'stage': 'metric', 'metric': code, 'ok': False, 'error': str(exc)[:350]})
            finally:
                guard.execute('RELEASE SAVEPOINT metric_check')
            print(json.dumps(checks[-1], ensure_ascii=False, default=str), flush=True)
        covered = {case['metric'] for case in source['cases']}
        for code, metric in catalog.metrics.items():
            if metric.get('availability') == 'AVAILABLE' and not code.startswith('benchmark_') and code not in covered:
                checks.append({'stage': 'coverage', 'metric': code, 'ok': False, 'error': '已发布指标缺少独立Oracle对照'})
        load_reference = next(c['reference'] for c in source['cases'] if c['metric'] == 'total_load_factor')
        delivery_checks(pg, catalog, start, end, checks, load_factor_reference=cache.get(load_reference, [{}])[0])
        extended_checks(cur, pg, catalog, source, start, end, checks)
        lineage_checks(cur, pg, start, end, checks)
    finally:
        oracle.close()
        pg.close()
    report = {'start': str(start), 'end': str(end), 'oracle_master': 'FIMS LEFT JOIN ACDM',
              'oracle_as_of': oracle_as_of, 'generated_at': datetime.now(timezone.utc).isoformat(),
              'ontology_version': catalog.version, 'checks': checks, 'passed': sum(c['ok'] for c in checks), 'total': len(checks)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2) + '\n', encoding='utf-8')
    return all(c['ok'] for c in checks)


def lineage_checks(oracle, pg, start, end, checks):
    """Compare native source identities, so cancelling row errors cannot hide behind equal totals."""
    since, until = datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())
    comparisons = [
        ('fims', 'SELECT FLIGHT_ID FROM FIMS.TB_DYN_FLIGHT WHERE EXEC_DATE>=:s AND EXEC_DATE<:e',
         "SELECT sr.source_flight_id FROM public.std_flight_source_ref sr JOIN public.std_flight_core c ON c.id=sr.flight_id WHERE sr.source_system='FIMS_ORACLE' AND c.deleted_at IS NULL AND (c.flight_date AT TIME ZONE 'Asia/Shanghai')::date>=%s AND (c.flight_date AT TIME ZONE 'Asia/Shanghai')::date<%s"),
        ('acdm', 'SELECT FLIGHT_ID FROM ACDM.TB_DYN_FLIGHT WHERE EXEC_DATE>=:s AND EXEC_DATE<:e',
         'SELECT "FLIGHT_ID" FROM acdm.tb_dyn_flight WHERE "EXEC_DATE">=%s AND "EXEC_DATE"<%s'),
        ('production', "SELECT UUID FROM FORMFILL.FLIGHT_PRODUCTION_ANALYSIS WHERE AIRPORT_IATA='WUH' AND EXEC_DATE>=:s AND EXEC_DATE<:e",
         "SELECT primary_source_id FROM public.std_flight_production WHERE deleted_at IS NULL AND airport_code='WUH' AND flight_date>=%s AND flight_date<%s"),
        ('calc', 'SELECT FLIGHT_ID FROM FIMS.TBH_FIMS_DYN_CALC_TOTAL WHERE EXEC_DATE>=:s AND EXEC_DATE<:e',
         'SELECT source_flight_id FROM public.std_flight_calc_total WHERE exec_date>=%s AND exec_date<%s'),
        ('bridges', 'SELECT b.FLIGHT_ID,b.ORDER_CD,b.AIR_BRIDGE_ID,b.AIR_BRIDGE_CD FROM FIMS.TB_DYN_AIR_BRIDGE b JOIN FIMS.TB_DYN_FLIGHT f ON f.FLIGHT_ID=b.FLIGHT_ID WHERE f.EXEC_DATE>=:s AND f.EXEC_DATE<:e',
         "SELECT b.source_flight_id,b.order_cd,b.air_bridge_id,b.air_bridge_cd FROM public.std_flight_air_bridge b JOIN public.std_flight_core c ON c.id=b.flight_id WHERE (c.flight_date AT TIME ZONE 'Asia/Shanghai')::date>=%s AND (c.flight_date AT TIME ZONE 'Asia/Shanghai')::date<%s"),
        ('nodes', 'SELECT n.FLIGHT_ID,n.SAFEGUARD_ORDER_CD,n.SAFEGUARD_CODE FROM FIMS.TB_DYN_SAFEGUARD_NODE n JOIN FIMS.TB_DYN_FLIGHT f ON f.FLIGHT_ID=n.FLIGHT_ID WHERE f.EXEC_DATE>=:s AND f.EXEC_DATE<:e',
         "SELECT n.source_flight_id,n.safeguard_order_cd,n.safeguard_code FROM public.std_flight_safeguard_node n JOIN public.std_flight_core c ON c.id=n.flight_id WHERE (c.flight_date AT TIME ZONE 'Asia/Shanghai')::date>=%s AND (c.flight_date AT TIME ZONE 'Asia/Shanghai')::date<%s"),
    ]
    guard = pg.cursor()
    for name, remote_sql, local_sql in comparisons:
        guard.execute('SAVEPOINT lineage_check')
        try:
            oracle.execute(remote_sql, s=since, e=until)
            remote = {tuple(str(v).strip() if v is not None else '' for v in row) for row in oracle.fetchall()}
            guard.execute(local_sql, (start, end + timedelta(days=1)))
            local = {tuple(str(v).strip() if v is not None else '' for v in row) for row in guard.fetchall()}
            checks.append({'stage': 'lineage', 'source': name, 'expected_count': len(remote), 'actual_count': len(local),
                           'missing': sorted(remote - local)[:10], 'extra': sorted(local - remote)[:10], 'ok': remote == local})
        except Exception as exc:
            guard.execute('ROLLBACK TO SAVEPOINT lineage_check')
            checks.append({'stage': 'lineage', 'source': name, 'ok': False, 'error': str(exc)[:350]})
        finally:
            guard.execute('RELEASE SAVEPOINT lineage_check')
        print(json.dumps(checks[-1], ensure_ascii=False, default=str), flush=True)


def delivery_checks(pg, catalog, start, end, checks, *, load_factor_reference):
    """Check real question planning and reports against the independent source values."""
    from app.acdm_reports import build_general_report
    values = {c['metric']: c.get('expected') for c in checks if c.get('stage') == 'metric' and 'expected' in c}
    questions = {
        'planned_flight_count': '计划航班架次', 'operated_flight_count': '实际执行航班架次',
        'dep_flight_count': '出港执飞架次', 'arr_flight_count': '进港执飞架次',
        'origin_flight_count': '始发航班架次', 'flight_execution_rate': '航班执行率',
        'pax_total_sum': '旅客吞吐量', 'dep_pax_sum': '出港旅客量', 'arr_pax_sum': '进港旅客量',
        'cargo_mail_sum': '货邮吞吐量', 'available_seat_sum': '可用座位数',
        'total_load_factor': '综合客座率', 'takeoff_on_time_rate': '起飞正常率',
        'arr_on_time_rate': '进港正常率', 'release_on_time_rate': '放行正常率',
        'bridge_stand_rate': '进港靠桥率', 'avg_turnaround_service_minutes': '过站平均保障时长',
    }
    guard = pg.cursor()
    for metric, words in questions.items():
        guard.execute('SAVEPOINT delivery_check')
        try:
            question = f'{start}至{end}{words}是多少'
            result = core.do_ask(pg, question, use_model=False, use_cache=False, day_mode='CALENDAR')
            cfg = catalog.metrics[metric]
            alias = cfg.get('value_alias') or metric
            actual = (result.get('main_rows') or [{}])[0].get(alias)
            expected = values.get(metric)
            same = actual is expected if actual is None or expected is None else abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal('.005')
            checks.append({'stage': 'question', 'metric': metric, 'question': question,
                           'expected': expected, 'actual': actual, 'outcome': result.get('outcome'),
                           'ok': same and result.get('outcome') in ('answered', 'no_data')})
        except Exception as exc:
            guard.execute('ROLLBACK TO SAVEPOINT delivery_check')
            checks.append({'stage': 'question', 'metric': metric, 'ok': False, 'error': str(exc)[:350]})
        finally:
            guard.execute('RELEASE SAVEPOINT delivery_check')
        print(json.dumps(checks[-1], ensure_ascii=False, default=str), flush=True)
    guard.execute('SAVEPOINT report_check')
    try:
        report = build_general_report(pg, start_date=start, end_date=end)
        summary = report['summary']
        fields = {'planned_flight_count': 'planned_all', 'operated_flight_count': 'operated_all',
                  'dep_flight_count': 'dep_operated', 'arr_flight_count': 'arr_operated',
                  'pax_total_sum': 'total_pax_sql', 'cargo_mail_sum': 'cargo_mail_kg',
                  'available_seat_sum': 'total_seats', 'total_load_factor': 'total_load_factor',
                  'flight_execution_rate': 'execution_rate', 'takeoff_on_time_rate': 'takeoff_on_time_rate',
                  'arr_on_time_rate': 'arr_on_time_rate', 'release_on_time_rate': 'release_on_time_rate',
                  'origin_dep_on_time_rate': 'origin_dep_on_time_rate', 'bridge_stand_rate': 'bridge_rate_arr',
                  'block_off_on_time_rate': 'block_off_on_time_rate', 'avg_turnaround_service_minutes': 'avg_service_min'}
        for metric, field in fields.items():
            expected, actual = values.get(metric), summary.get(field)
            same = actual is expected if actual is None or expected is None else abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal('.005')
            checks.append({'stage': 'report', 'metric': metric, 'expected': expected, 'actual': actual, 'ok': same})
        for field, reference_field in [('load_factor_pax', 'total_pax'), ('load_factor_sample_flights', 'sample_flights'),
                                       ('total_seats', 'total_seat')]:
            expected, actual = load_factor_reference.get(reference_field), summary.get(field)
            checks.append({'stage': 'report', 'field': field, 'expected': expected, 'actual': actual, 'ok': actual == expected})
        expected_excluded = Decimal(str(values['pax_total_sum'])) - Decimal(str(load_factor_reference['total_pax']))
        checks.append({'stage': 'report', 'field': 'load_factor_excluded_pax', 'expected': expected_excluded,
                       'actual': summary.get('load_factor_excluded_pax'), 'ok': expected_excluded == summary.get('load_factor_excluded_pax')})
    except Exception as exc:
        guard.execute('ROLLBACK TO SAVEPOINT report_check')
        checks.append({'stage': 'report', 'ok': False, 'error': str(exc)[:350]})
    finally:
        guard.execute('RELEASE SAVEPOINT report_check')


def extended_checks(oracle, pg, catalog, source, start, end, checks):
    from app.acdm_reports import build_ground_briefing, build_safeguard_and_resource_board
    from app.agent.operational_day import apply_operational_day_to_plan
    compiler = Compiler(catalog)
    values = {c['metric']: c.get('expected') for c in checks if c.get('stage') == 'metric' and 'expected' in c}
    since = datetime.combine(start, datetime.min.time())
    until = datetime.combine(end + timedelta(days=1), datetime.min.time())
    guard = pg.cursor()

    def record(name, expected, actual):
        def normalized(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float, Decimal)):
                return Decimal(str(value)).quantize(Decimal('.01'))
            if isinstance(value, dict):
                return {k: normalized(v) for k, v in value.items()}
            if isinstance(value, list):
                return sorted((normalized(v) for v in value), key=str)
            return str(value) if isinstance(value, (date, datetime)) else value
        checks.append({'stage': 'extended', 'name': name, 'expected': expected, 'actual': actual,
                       'ok': normalized(expected) == normalized(actual)})
        print(json.dumps(checks[-1], ensure_ascii=False, default=str), flush=True)

    def metric_rows(code, group_by=(), conditions=(), operational=False):
        cfg = catalog.metrics[code]
        field = cfg['date_field']
        plan = {'subject': cfg['base_table'], 'intent': 'metric', 'metric': code, 'group_by': list(group_by),
                'conditions': [{'col': field, 'op': '>=', 'value': str(start)},
                               {'col': field, 'op': '<', 'value': str(end + timedelta(days=1))}, *conditions], 'focus_tables': []}
        if operational:
            plan = apply_operational_day_to_plan(plan, catalog, day_mode='OPERATIONAL')
        return execute(pg, compiler.aggregate(compiler.validate(plan)))

    guard.execute('SAVEPOINT extended_check')
    try:
        board = build_safeguard_and_resource_board(pg, start_date=start, end_date=end)
        for code, field in [('operated_flight_count', 'operated_all'), ('air_bridge_usage_count', 'air_bridge_usage_count'),
                            ('avg_air_bridge_minutes', 'avg_air_bridge_minutes'), ('safeguard_node_recorded_count', 'safeguard_node_recorded_count')]:
            record('safeguard_board.' + code, values.get(code), board['summary'].get(field))
        record('safeguard_board.coverage_at_most_100', True,
               all(0 <= r['entry_rate_pct'] <= 100 for r in board['rows'] if r.get('entry_rate_pct') is not None))
        expected = (Decimal(str(values['available_seat_sum'])) / Decimal(str(values['operated_flight_count']))).quantize(Decimal('.01')) if values.get('operated_flight_count') else None
        record('benchmark_home_seats', expected, metric_rows('benchmark_home_seats')[0]['benchmark_home_seats'])

        facts = """WITH facts AS (
            SELECT f.AIRLINE_TWO_CHAR_CD AS airline_code,f.IS_OFF_IN AS dir,f.ATTRIBUTE AS attribute,
                f.CRAFT_TYPE_ICAO_CD AS craft_type,a.TAKEOFF_ON_TIME,a.RELEASE_ON_TIME,
                CASE WHEN f.IS_OFF_IN='D' THEN a.REAL_TAKEOFF_TIME ELSE a.REAL_LANDIN_TIME END AS actual_time
            FROM FIMS.TB_DYN_FLIGHT f JOIN ACDM.TB_DYN_FLIGHT a ON a.FLIGHT_ID=f.FLIGHT_ID
            WHERE f.FLIGHT_TASK_CD<>'A/N' AND NVL(a.ABNORMAL_FLIGHT_STAT_CD,'?')<>'CAN'
        ) """
        for dimension, oracle_expr, local_alias in [
            ('direction', "CASE WHEN dir='D' THEN 'DEP' ELSE 'ARR' END", 'direction'),
            ('airline', 'airline_code', 'airline_code'),
            ('date', "TO_CHAR(actual_time,'YYYY-MM-DD')", 'flight_date'),
            ('peak_hour', "TO_CHAR(TRUNC(actual_time,'HH'),'YYYY-MM-DD HH24:MI')", 'peak_hour'),
        ]:
            oracle.execute(facts + f'SELECT {oracle_expr},COUNT(*) FROM facts WHERE actual_time>=:s AND actual_time<:e GROUP BY {oracle_expr}', s=since, e=until)
            expected = {str(k): int(v) for k, v in oracle.fetchall()}
            code = 'peak_hour_movements' if dimension == 'peak_hour' else 'operated_flight_count'
            actual = {str(r[local_alias]): r[code] for r in metric_rows(code, [dimension])}
            record('group.' + dimension, expected, actual)
        oracle.execute(facts + "SELECT COUNT(*) FROM facts WHERE actual_time>=:s AND actual_time<:e AND dir='D' AND attribute='2401' AND craft_type='B738'", s=since, e=until)
        record('filter.international_b738_departure', oracle.fetchone()[0], metric_rows('operated_flight_count', conditions=[
            {'col': 'direction', 'op': '=', 'value': 'DEP'}, {'col': 'attribute', 'op': '=', 'value': '2401'},
            {'col': 'craft_type_icao_cd', 'op': '=', 'value': 'B738'}])[0]['operated_flight_count'])
        for code in ('planned_flight_count', 'operated_flight_count', 'flight_execution_rate'):
            case = next(c for c in source['cases'] if c['metric'] == code)
            hour = int(catalog.operational_day['startHour'])
            oracle.execute(reference_sql(source['sql'][case['reference']]), period_start=since + timedelta(hours=hour),
                           period_end=until + timedelta(hours=hour))
            expected = dict(zip([c[0].lower() for c in oracle.description], oracle.fetchone()))[case['field']]
            actual = metric_rows(code, operational=True)[0][code]
            record('operational.' + code, expected, actual)

        # Ground duty briefing has its own 22:00 cutoff, even when the requested audit spans multiple days.
        cutoff_start = datetime.combine(end, datetime.min.time())
        oracle.execute(facts + """SELECT COUNT(CASE WHEN dir='D' THEN 1 END),
            COUNT(CASE WHEN dir='D' AND TAKEOFF_ON_TIME='Y' THEN 1 END),
            COUNT(CASE WHEN dir='D' AND RELEASE_ON_TIME='Y' THEN 1 END)
            FROM facts WHERE actual_time>=:s AND actual_time<:e""", s=cutoff_start, e=cutoff_start + timedelta(hours=22))
        dep, takeoff, release = oracle.fetchone()
        brief = build_ground_briefing(pg, target_date=end)['summary']
        for field, expected in [('all_dep_22', dep), ('all_takeoff_norm_22', takeoff), ('all_release_norm_22', release)]:
            record('ground_briefing.' + field, expected, brief.get(field))
    except Exception as exc:
        guard.execute('ROLLBACK TO SAVEPOINT extended_check')
        checks.append({'stage': 'extended', 'ok': False, 'error': str(exc)[:350]})
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)
    finally:
        guard.execute('RELEASE SAVEPOINT extended_check')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=date.fromisoformat, default=date(2026, 11, 9))
    parser.add_argument('--end', type=date.fromisoformat, default=date(2026, 11, 9))
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/analysis-qa/acdm-pipeline-check.json')
    args = parser.parse_args()
    raise SystemExit(0 if validate(args.start, args.end, args.output) else 1)
