"""Bounded read-only relation profiling; samples are not semantic/uniqueness proofs."""
from datetime import datetime, timezone
from time import perf_counter

from app.query import Statement, execute, identifier


def profile_relations(conn,catalog,sample_size=100):
    reports=[]
    overall=perf_counter()
    timeout=getattr(catalog.settings,'relation_probe_timeout_ms',2000)
    budget=getattr(catalog.settings,'relation_probe_budget_seconds',30)
    execute(conn,Statement("SELECT set_config('statement_timeout',%s,true)",(str(timeout),)))
    for edge in catalog.graph['links_all']:
        if perf_counter()-overall>budget:
            reports.append({**edge,'assessment':'未检查：总时间预算已达到；需单独复查'})
            continue
        started=perf_counter()
        source,target=edge['from_table'],edge['to_table']
        def table(name):
            item=catalog.tables[name]
            return identifier(item['schema'])+'.'+identifier(item['name'])
        tests=[]
        type_mismatch=[]
        for left,right in edge['pairs']:
            a,b='s.'+identifier(left),'t.'+identifier(right)
            left_type=next(c['type'] for c in catalog.tables[source]['columns'] if c['name']==left)
            right_type=next(c['type'] for c in catalog.tables[target]['columns'] if c['name']==right)
            if left_type!=right_type:
                a,b=a+'::text',b+'::text'
                type_mismatch.append({'source':left_type,'target':right_type})
            tests.append(a+' = '+b)
        columns=','.join(identifier(p[0]) for p in edge['pairs'])
        nonnull=' AND '.join(identifier(p[0])+' IS NOT NULL' for p in edge['pairs'])
        sql=('WITH sampled AS (SELECT '+columns+' FROM '+table(source)+' WHERE '+nonnull+' LIMIT %s), '
             'counts AS (SELECT (SELECT COUNT(*) FROM (SELECT 1 FROM '+table(target)+' t WHERE '+
             ' AND '.join(tests)+' LIMIT 2) hits) AS matches FROM sampled s) '
             'SELECT COUNT(*) AS sampled, COUNT(*) FILTER (WHERE matches>0) AS matched, '
             'COUNT(*) FILTER (WHERE matches>1) AS multiple_targets FROM counts')
        cursor=conn.cursor()
        cursor.execute('SAVEPOINT relation_probe')
        try:
            row=execute(conn,Statement(sql,(sample_size,)))[0]
        except Exception as exc:
            cursor.execute('ROLLBACK TO SAVEPOINT relation_probe')
            reports.append({**edge,'assessment':'检查未完成，不视为验证通过','error':str(exc)[:200]})
            continue
        finally:
            cursor.execute('RELEASE SAVEPOINT relation_probe')
            cursor.close()
        reports.append({**edge,**row,'type_mismatches':type_mismatch,
            'seconds':round(perf_counter()-started,3),
            'assessment':'需检查目标键或业务粒度' if row['multiple_targets'] else
                         '样本未发现多目标；不代表全量唯一或业务确认'})
    execute(conn,Statement("SELECT set_config('statement_timeout',%s,true)",
                          (str(getattr(catalog.settings,'query_timeout_ms',15000)),)))
    return {'version':catalog.version,'queried_at':datetime.now(timezone.utc).isoformat(),
            'sample_limit':sample_size,'scope':'非空连接键的受限样本；未修改业务库或自动审核规则',
            'relations':reports}
