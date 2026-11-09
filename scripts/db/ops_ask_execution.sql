-- Live semantic view: one actual arrival/departure at the home airport per flight.
CREATE SCHEMA IF NOT EXISTS ops_ask_views;

CREATE VIEW ops_ask_views.flight_execution AS
SELECT f.*,
       CASE WHEN direction = 'DEP' THEN actual_departure
            WHEN direction = 'ARR' THEN actual_arrival END AS execution_time
FROM public.std_flight_core f
WHERE deleted_at IS NULL
  AND COALESCE(flight_status, '') <> '取消'
  AND NOT COALESCE(is_virtual, false)
  AND ((direction = 'DEP' AND actual_departure IS NOT NULL)
       OR (direction = 'ARR' AND actual_arrival IS NOT NULL));

COMMENT ON VIEW ops_ask_views.flight_execution IS
    '实际执飞航班：进港按本场实际落地，出港按本场实际起飞；排除取消、删除、虚拟航班；按 execution_time 统计实际发生日。';
