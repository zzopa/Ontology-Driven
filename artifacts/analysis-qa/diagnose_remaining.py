from app import core
from app.config import settings
from app.sync.oracle_source import connect_oracle
from datetime import datetime
core.initialize()
o=connect_oracle(settings.oracle);o.call_timeout=120000;q=o.cursor();q.execute('SET TRANSACTION READ ONLY')
p=core.connect_db(repeatable_read=True);c=p.cursor()
try:
 q.execute("SELECT f.FLIGHT_ID,f.AIRLINE_TWO_CHAR_CD,f.CRAFT_NO,a.EXEC_DATE,a.FLIGHT_NO,a.TAKEOFF_AIRPORT_IATA,a.ARRIVAL_AIRPORT_IATA,a.ARR_DELAY_TIME FROM FIMS.TB_DYN_FLIGHT f JOIN ACDM.TB_DYN_FLIGHT a ON a.FLIGHT_ID=f.FLIGHT_ID WHERE f.FLIGHT_TASK_CD<>'A/N' AND NVL(a.ABNORMAL_FLIGHT_STAT_CD,'?')<>'CAN' AND ((f.IS_OFF_IN='D' AND a.REAL_TAKEOFF_TIME>=:s AND a.REAL_TAKEOFF_TIME<:e) OR(f.IS_OFF_IN='A' AND a.REAL_LANDIN_TIME>=:s AND a.REAL_LANDIN_TIME<:e))",s=datetime(2026,10,17),e=datetime(2026,11,16))
 flights={str(r[0]):r for r in q.fetchall()}
 c.execute("SELECT flight_id,arr_delay_time FROM public.mv_acdm_dyn_flight_detail WHERE is_operated_flight='Y' AND real_exec_day BETWEEN '2026-10-17' AND '2026-11-15'")
 for fid,delay in c.fetchall():
  expected=flights[str(fid)][-1]
  if expected is not None and float(expected or 0)!=float(delay or 0):print('DELAY',fid,expected,delay)
 q.execute("SELECT f.FLIGHT_ID,SUM(NVL(fp.ADULT_COUNT,0)+NVL(fp.CHILDREN_COUNT,0)+NVL(fp.BABY_COUNT,0)),SUM(fp.AVAILABLE_SEAT),SUM(NVL(fp.GOODS,0)),LISTAGG(fp.UUID,',') WITHIN GROUP(ORDER BY fp.UUID) FROM FIMS.TB_DYN_FLIGHT f JOIN ACDM.TB_DYN_FLIGHT a ON a.FLIGHT_ID=f.FLIGHT_ID LEFT JOIN FORMFILL.FLIGHT_PRODUCTION_ANALYSIS fp ON fp.EXEC_DATE=a.EXEC_DATE AND fp.CRAFT_NO=f.CRAFT_NO AND fp.LEG=a.TAKEOFF_AIRPORT_IATA||'-'||a.ARRIVAL_AIRPORT_IATA AND fp.AIRLINE_TWO_CHAR_CD||fp.FLIGHT_NO=a.FLIGHT_NO AND fp.AVAILABLE_SEAT<>0 WHERE f.FLIGHT_TASK_CD<>'A/N' AND NVL(a.ABNORMAL_FLIGHT_STAT_CD,'?')<>'CAN' AND ((f.IS_OFF_IN='D' AND a.REAL_TAKEOFF_TIME>=:s AND a.REAL_TAKEOFF_TIME<:e) OR(f.IS_OFF_IN='A' AND a.REAL_LANDIN_TIME>=:s AND a.REAL_LANDIN_TIME<:e)) GROUP BY f.FLIGHT_ID",s=datetime(2026,10,17),e=datetime(2026,11,16))
 prod={str(r[0]):r[1:] for r in q.fetchall()}
 c.execute("SELECT flight_id,COALESCE(fpa_adult_count,0)+COALESCE(fpa_children_count,0)+COALESCE(fpa_baby_count,0),fpa_available_seat,fpa_goods FROM public.mv_acdm_dyn_flight_detail WHERE is_operated_flight='Y' AND real_exec_day BETWEEN '2026-10-17' AND '2026-11-15'")
 for fid,pax,seats,goods in c.fetchall():
  v=prod[str(fid)]
  if any(float(a or 0)!=float(b or 0) for a,b in zip(v[:3],(pax,seats,goods))):print('PROD',fid,flights[str(fid)][1:7], 'ORACLE',v, 'PG',(pax,seats,goods))
finally:o.close();p.close()
