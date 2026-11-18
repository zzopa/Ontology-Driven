from datetime import date
import json
from app.config import settings
from app.sync import runner,pg_sink,oracle_source
p=pg_sink.connect_pg_write();o=oracle_source.connect_oracle(settings.oracle);o.call_timeout=120000
try:
 o.cursor().execute('SET TRANSACTION READ ONLY')
 span=dict(mode='BY_EXEC_DATE',exec_date_from=date(2026,10,16),exec_date_to=date(2026,11,17))
 for name,fn,args in [('fims',runner.run_flight_sync,('fims',)),('acdm',runner.run_flight_sync,('acdm',)),('production',runner.run_production_sync,()),('calc',runner.run_calc_total_sync,()),('safeguards',runner.run_safeguard_sync,())]:
  print('starting',name,flush=True)
  result=fn(p,o,settings.oracle,settings.sync_service,*args,**span)
  print(name,json.dumps(result,ensure_ascii=False,default=str),flush=True)
finally:p.close();o.close()
