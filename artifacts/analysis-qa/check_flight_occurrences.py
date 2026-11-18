from app.sync import pg_sink, transforms
from datetime import datetime

c = pg_sink.connect_pg_write()
try:
 q = c.cursor()
 for name in ['std_flight_core','std_flight_source_ref','std_flight_indicator','std_flight_process','std_flight_production']:
  q.execute('CREATE TEMP TABLE '+name+' (LIKE public.'+name+' INCLUDING ALL)')
  if name == 'std_flight_process': continue
  q.execute('CREATE TEMP SEQUENCE '+name+'_test_ids')
  q.execute("ALTER TABLE "+name+" ALTER COLUMN id SET DEFAULT nextval('pg_temp."+name+"_test_ids')")
 c.commit()
 def draft(sid, acdm=False):
  raw={'FLIGHT_ID':sid,'AIRLINE_TWO_CHAR_CD':'MU','FLIGHT_NO':'2632','EXEC_DATE':datetime(2026,11,9),'IS_OFF_IN':'A','FLIGHT_TASK_CD':'W/Z','REAL_LANDIN_TIME':datetime(2026,11,9,0,35) if sid==4826291 else datetime(2026,11,9,17,48)}
  return transforms.map_flight_draft(raw,'ACDM_ORACLE' if acdm else 'FIMS_ORACLE',acdm)
 first=draft(4826291)
 first['biz_key']='WUH:2026-11-09:ARR:MU2632'
 assert pg_sink.upsert_flight_batch(c,[first])['inserted']==1
 q.execute("INSERT INTO std_flight_source_ref(flight_id,source_system,source_flight_id,created_at,last_seen_at) VALUES(1,'FIMS_ORACLE','4827375',now(),now())")
 c.commit()
 assert pg_sink.upsert_flight_batch(c,[draft(4827375)])['inserted']==1
 result=pg_sink.upsert_flight_batch(c,[draft(4826291,True),draft(4827375,True)])
 assert result['updated']==2 and result['failed']==0,result
 q.execute('SELECT COUNT(*),COUNT(DISTINCT primary_source_id) FROM std_flight_core')
 assert q.fetchone()==[2,2]
 result=pg_sink.upsert_flight_batch(c,[draft(4826291),draft(4827375),draft(4826291,True),draft(4827375,True)])
 assert result['inserted']==0 and result['updated']==4 and result['failed']==0,result
 q.execute('SELECT COUNT(DISTINCT flight_id) FROM std_flight_source_ref')
 assert q.fetchone()==[2]
 print('PostgreSQL temporary-table integration: distinct occurrences, legacy alias repair, cross-source merge, repeat ingestion: OK')
finally:c.close()
