"""One local question; no model or network call."""
import json
from app import core

if __name__ == '__main__':
    core.initialize()
    conn = core.connect_db()
    try:
        cur = conn.cursor()
        cur.execute('SHOW transaction_read_only')
        assert cur.fetchone()[0] == 'on', 'The test database connection must be read-only'
        cur.close()
        for question in ['2026年10月9日的计划航班量是多少？','2026-10-09执行航班量','2026-10-09出港航班明细','查询航空公司','最近7天按天计划航班量']:
            result = core.do_ask(conn,question,use_model=False,use_cache=False)
            print(json.dumps({k:result[k] for k in ['ok','question','main_total','answer','sql']},ensure_ascii=False))
            assert result['ok'] and result['main_total'] > 0
            if result.get('drill_token'):
                page = core.fetch_drill_page(conn,result['drill_token'],1)
                assert page['rows'] and page['total'] == result['main_total']
    finally:
        conn.close()
