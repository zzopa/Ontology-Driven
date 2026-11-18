from datetime import date,datetime
from decimal import Decimal
import json,gzip
from pathlib import Path
from app.config import settings
from app.sync import oracle_source as src
from app.admin.security import Vault

def encode(value):
 if isinstance(value,datetime): return {'__datetime':value.isoformat()}
 if isinstance(value,Decimal): return {'__decimal':str(value)}
 raise TypeError(type(value).__name__)

conn=src.connect_oracle(settings.oracle);conn.call_timeout=120000
try:
 cur=conn.cursor();cur.execute('SET TRANSACTION READ ONLY')
 data={'captured_at':datetime.now().isoformat(),'start':'2026-10-16','end':'2026-11-17'}
 for label,fn,args in [('fims',src.iter_flight_batches,('fims',)),('acdm',src.iter_flight_batches,('acdm',)),('production',src.iter_production_batches,()),('calc',src.iter_calc_total_batches,()),('bridges',src.iter_air_bridge_batches,()),('nodes',src.iter_safeguard_node_batches,())]:
  data[label]=[r for batch in fn(conn,settings.oracle,*args,mode='BY_EXEC_DATE',exec_date_from=date(2026,10,16),exec_date_to=date(2026,11,17)) for r in batch]
  print(label,len(data[label]),flush=True)
 raw=gzip.compress(json.dumps(data,ensure_ascii=False,default=encode).encode())
 destination=Path(settings.admin_directory)/'backups'/'acdm-pipeline-audit-20261116.json.gz.enc'
 vault=Vault(settings.admin_directory);destination.write_bytes(vault.cipher.encrypt(raw))
 assert vault.cipher.decrypt(destination.read_bytes())==raw
 print('snapshot',str(destination),'bytes',len(raw),flush=True)
finally:conn.close()
