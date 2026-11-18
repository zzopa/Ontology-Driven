"""Create and verify 93 synthetic benchmark rows, then publish their catalog."""
from copy import deepcopy
import json

from app import benchmarks, core, ops_governance
from app.config import settings
from app.sync.pg_sink import connect_pg_write


def main():
    if settings.data_source != 'ops_ask':
        raise ValueError('请使用 ops_ask 数据源运行')
    conn = connect_pg_write()
    try:
        count = benchmarks.seed(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    core.initialize()
    state = core.ontology_document()
    document = benchmarks.register(deepcopy(state['document']))
    core.update_ontology(document, state['revision'])
    for key, metric in document['metrics'].items():
        if key.startswith('benchmark_'):
            ops_governance.save_metric({'code': key, **metric})
    for capability in document['capabilities']:
        if capability.get('code') in ('airport.benchmark', 'acdm.analysis', 'acdm.forecast'):
            ops_governance.save_capability(capability)
    ops_governance.publish_catalog_to_ontology('system-airport-demo', '发布机场模拟对标、复合分析与短期预测能力')
    print(json.dumps({'table': benchmarks.TABLE, 'rows': count, 'dataset_id': benchmarks.DATASET,
                      'is_simulated': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
