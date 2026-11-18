"""Explicitly synthetic airport benchmarks, queried through the normal catalog."""
from datetime import date, timedelta
from hashlib import sha256
import random

TABLE = 'airport_benchmark_daily'
DATASET = 'airport-demo-20261116-v1'
AIRPORTS = {'WUH': '武汉天河', 'CSX': '长沙黄花', 'NKG': '南京禄口', 'CGO': '郑州新郑'}
METRICS = {
    'benchmark_flight_count': ('执行航班量', '架次', 'SUM(c.flight_count)',
                               ['航班量', '执行航班量', '航班架次', '执行量', '实际航班量', '执飞量']),
    'benchmark_passengers': ('旅客吞吐量', '人次', 'SUM(c.passenger_volume)', ['旅客吞吐量', '客运量', '旅客量']),
    'benchmark_load_factor': ('客座率', '%', 'ROUND(100.0 * SUM(c.passenger_volume) / NULLIF(SUM(c.seat_capacity), 0), 2)', ['客座率', '上座率']),
    'benchmark_execution_rate': ('国内执行率', '%', 'ROUND(100.0 * SUM(c.domestic_operated) / NULLIF(SUM(c.domestic_planned), 0), 2)', ['国内执行率']),
    'benchmark_seats': ('单机可供座位数', '座/架次', 'ROUND(SUM(c.seat_capacity)::numeric / NULLIF(SUM(c.flight_count), 0), 2)', ['单机可供座位数', '单机座位数']),
}


def demo_rows(start=date(2026, 10, 16), end=date(2026, 11, 15)):
    if start > end:
        raise ValueError('开始日期不能晚于结束日期')
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        for code, base in [('CSX', 480), ('NKG', 540), ('CGO', 420)]:
            rng = random.Random(int.from_bytes(sha256(f'{DATASET}:{day}:{code}'.encode()).digest()[:8], 'big'))
            flights = round(base * (1.04 if day.weekday() >= 4 else .98) * rng.uniform(.94, 1.06))
            seats = flights * rng.randint(155, 185)
            passengers = round(seats * rng.uniform(.76, .91))
            domestic = round(flights * rng.uniform(.82, .94))
            planned = domestic + rng.randint(4, 22)
            yield (day, code, AIRPORTS[code], flights, passengers, seats, planned, domestic,
                   True, DATASET, '固定种子生成的模拟测试数据，不代表机场实际运行')


def seed(conn):
    """Create one new table and upsert only rows owned by this demo dataset."""
    cur = conn.cursor()
    try:
        cur.execute('''CREATE TABLE IF NOT EXISTS public.airport_benchmark_daily (
            stat_date date NOT NULL, airport_code varchar(3) NOT NULL, airport_name text NOT NULL,
            flight_count integer NOT NULL CHECK (flight_count >= 0),
            passenger_volume bigint NOT NULL CHECK (passenger_volume >= 0),
            seat_capacity bigint NOT NULL CHECK (seat_capacity >= passenger_volume),
            domestic_planned integer NOT NULL CHECK (domestic_planned >= 0),
            domestic_operated integer NOT NULL CHECK (domestic_operated BETWEEN 0 AND domestic_planned),
            is_simulated boolean NOT NULL, dataset_id text NOT NULL, source_note text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stat_date, airport_code))''')
        cur.execute("COMMENT ON TABLE public.airport_benchmark_daily IS '机场每日对标；必须保留模拟标识与来源；首批长沙南京郑州为模拟测试数据'")
        cur.executemany('''INSERT INTO public.airport_benchmark_daily
            (stat_date,airport_code,airport_name,flight_count,passenger_volume,seat_capacity,
             domestic_planned,domestic_operated,is_simulated,dataset_id,source_note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (stat_date,airport_code) DO UPDATE SET
              airport_name=EXCLUDED.airport_name,flight_count=EXCLUDED.flight_count,
              passenger_volume=EXCLUDED.passenger_volume,seat_capacity=EXCLUDED.seat_capacity,
              domestic_planned=EXCLUDED.domestic_planned,domestic_operated=EXCLUDED.domestic_operated,
              source_note=EXCLUDED.source_note
            WHERE airport_benchmark_daily.is_simulated AND airport_benchmark_daily.dataset_id=EXCLUDED.dataset_id''',
            list(demo_rows()))
        cur.execute('SELECT COUNT(*) FROM public.airport_benchmark_daily WHERE dataset_id=%s AND is_simulated', (DATASET,))
        count = cur.fetchone()[0]
        if count != 93:
            raise ValueError(f'模拟数据校验失败：应为93条，实际{count}条；请检查日期机场键是否被其他数据占用')
        return count
    finally:
        cur.close()


def register(document):
    document['entities'][TABLE] = {
        'name': '机场每日对标（含模拟测试数据）', 'queryable': True,
        'aliases': ['机场对标', '机场对比', '机场日数据', '对标数据'],
        'identity_fields': ['stat_date', 'airport_code'],
        'bindings': {'time': 'stat_date', 'code': 'airport_code', 'name': 'airport_name'},
        'query_semantics': {'date_required': True, 'time_label': '自然日',
                            'limits': ['长沙、南京、郑州首批记录为模拟测试数据；以is_simulated和dataset_id核验来源']},
        'attributes': {'is_simulated': {'label': '模拟测试数据'}, 'dataset_id': {'label': '测试数据集'},
                       'stat_date': {'label': '统计日期'}, 'airport_code': {'label': '机场代码'},
                       'airport_name': {'label': '机场'}, 'flight_count': {'label': '执行架次'},
                       'passenger_volume': {'label': '旅客人数'}, 'seat_capacity': {'label': '座位总数'},
                       'domestic_planned': {'label': '国内计划架次'}, 'domestic_operated': {'label': '国内执行架次'},
                       'source_note': {'label': '来源说明'}},
    }
    for key, (name, unit, expression, aliases) in METRICS.items():
        document.setdefault('metrics', {})[key] = {
            'name': '对标' + name, 'aliases': ['对标' + a for a in aliases], 'unit': unit,
            'domain': 'BENCHMARK', 'availability': 'AVAILABLE', 'status': 'PUBLISHED',
            'aggregation': 'rate' if unit == '%' else 'avg' if unit == '座/架次' else 'sum',
            'base_table': TABLE, 'date_field': 'stat_date', 'aggregate_expression': expression,
            'value_alias': key, 'required_fields': ['stat_date', 'airport_code', 'is_simulated'],
            'formula_desc': '自然日按分子分母汇总；is_simulated=true的记录为模拟测试数据',
        }
    document['metrics']['benchmark_home_seats'] = {
        'name': '本场单机可供座位数', 'aliases': ['本场单机可供座位数'], 'unit': '座/架次',
        'availability': 'AVAILABLE', 'status': 'PUBLISHED', 'domain': 'BENCHMARK',
        'base_table': 'mv_acdm_dyn_flight_detail', 'date_field': 'real_exec_day',
        'aggregate_expression': "ROUND(SUM(COALESCE(c.fpa_available_seat,0))::numeric / NULLIF(COUNT(*),0),2)",
        'value_alias': 'benchmark_home_seats', 'required_fields': ['fpa_available_seat', 'real_exec_day'],
        'default_filters': {'is_operated_flight': 'Y'},
        'formula_desc': '本地已执飞航班可用座位数/已执飞架次，仅反映已同步记录',
    }
    capabilities = document.setdefault('capabilities', [])
    for code, name, domain, tables in [
        ('airport.benchmark', '机场对标（含模拟测试数据）', 'BENCHMARK', [TABLE]),
        ('acdm.analysis', '受控多指标与同环比分析', 'ANALYSIS', []),
        ('acdm.forecast', '短期预测与滚动回测（试验）', 'FORECAST', []),
    ]:
        existing = next((c for c in capabilities if c.get('code') == code), None)
        if existing:
            existing['domain'] = domain
            continue
        capabilities.append({'code': code, 'name': name,
            'status': 'AVAILABLE', 'phase': 1, 'domain': domain,
            'coverageJson': {'tables': tables}, 'fallbackMessage': name + '当前未开放'})
    return document
