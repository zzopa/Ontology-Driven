"""Exercise the original ontology pipeline with the selected intranet V3."""
import json
from app import core, llm
from app.admin.service import get_store

if __name__ == '__main__':
    llm.set_provider(get_store().model)
    core.initialize()
    conn = core.connect_db()
    try:
        for question in ['请帮我统计2026年10月9日的计划航班总数','2026年10月9日实际执行了多少个进港航班？']:
            result = core.do_ask(conn,question,use_model=True,use_cache=False)
            print(json.dumps({k:result[k] for k in ['ok','question','main_total','parse','answer']},ensure_ascii=False),flush=True)
            assert result['ok'] and result['main_total'] > 0
    finally:
        conn.close()
