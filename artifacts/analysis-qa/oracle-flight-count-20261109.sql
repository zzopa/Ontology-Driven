-- Read-only verification: 2026-11-09, Asia/Shanghai wall-clock timestamps.
-- Queried 2026-11-16: departures 250, arrivals 256, total 506.
-- Keep separate flight occurrences with different PLAN_TIME values.
WITH operated AS (
    SELECT DISTINCT f.AIRLINE_TWO_CHAR_CD, f.FLIGHT_NO, f.PLAN_TIME, f.IS_OFF_IN
    FROM FIMS.TB_DYN_FLIGHT f
    JOIN ACDM.TB_DYN_FLIGHT a ON a.FLIGHT_ID = f.FLIGHT_ID
    WHERE NVL(f.FLIGHT_TASK_CD, '?') <> 'A/N'
      AND NVL(a.ABNORMAL_FLIGHT_STAT_CD, '?') <> 'CAN'
      AND (
          (f.IS_OFF_IN = 'D'
           AND a.REAL_TAKEOFF_TIME >= DATE '2026-11-09'
           AND a.REAL_TAKEOFF_TIME < DATE '2026-11-10')
          OR
          (f.IS_OFF_IN = 'A'
           AND a.REAL_LANDIN_TIME >= DATE '2026-11-09'
           AND a.REAL_LANDIN_TIME < DATE '2026-11-10')
      )
)
SELECT SUM(CASE WHEN IS_OFF_IN = 'D' THEN 1 ELSE 0 END) AS DEPARTURES,
       SUM(CASE WHEN IS_OFF_IN = 'A' THEN 1 ELSE 0 END) AS ARRIVALS,
       COUNT(*) AS TOTAL
FROM operated;
