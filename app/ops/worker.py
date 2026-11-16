"""Explicit standalone watcher: python -m app.ops.worker --terminal T2."""
import argparse
from contextlib import contextmanager
import logging
import os
from threading import Event
from time import monotonic

from .api import OPS_MODE, get_service, terminal_name
from .domain import iso, now

logger = logging.getLogger(__name__)


@contextmanager
def singleton(path):
    with open(path, 'a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            if not handle.read(1):
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('此航站楼值守进程已启动') from exc
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def sync_sources():
    from app.admin.data_sync import oracle_config, sync_config
    from app.sync import oracle_source, pg_sink, runner
    oracle = pg = None
    try:
        cfg, sync_cfg = oracle_config(), sync_config()
        oracle, pg = oracle_source.connect_oracle(cfg), pg_sink.connect_pg_write()
        for source in ('oracle-fims', 'oracle-acdm'):
            result = runner.run_flight_sync(pg, oracle, cfg, sync_cfg, source, mode='INCREMENTAL')
            if result.get('failed'):
                raise RuntimeError('航班增量采集存在失败记录')
        result = runner.run_safeguard_sync(pg, oracle, cfg, sync_cfg, mode='INCREMENTAL')
        if result.get('failed'):
            raise RuntimeError('节点增量采集存在失败记录')
    finally:
        if oracle:
            oracle.close()
        if pg:
            pg.close()


def run_once(service, terminal, sync=True):
    error = None
    if sync and service.mode == 'live':
        try:
            sync_sources()
        except Exception:
            logger.exception('源端增量采集失败')
            error = '增量采集失败，请检查源端连接及日志'
    service.store.set_state('worker:' + terminal, {'heartbeat': iso(now()), 'error': error, 'sync_enabled': sync})
    value = service.snapshot(terminal, persist=True)
    service.store.set_state('worker:' + terminal, {'heartbeat': iso(now()), 'source_fresh': value['source']['fresh'], 'error': error, 'sync_enabled': sync})
    return value


def main():
    parser = argparse.ArgumentParser(description='独立运行值守进程，默认影子值守，不随问答服务自动启动')
    parser.add_argument('--terminal', default='T2', type=terminal_name)
    parser.add_argument('--interval', type=int, default=30)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--no-sync', action='store_true', help='仅扫描现有副本，不更新源端新鲜度')
    parser.add_argument('--mode', choices=('replay', 'live'), default='replay', help='默认历史预演（不超过已确认的数据日期）；live 才启用真实源端增量采集')
    args = parser.parse_args()
    if not 10 <= args.interval <= 3600:
        parser.error('扫描间隔应为10至3600秒')
    logging.basicConfig(level=logging.INFO)
    OPS_MODE.set(args.mode)
    service = get_service()
    stop = Event()
    with singleton(service.store.path.parent / ('watch-' + args.terminal + '-' + args.mode + '.lock')):
        try:
            while True:
                started = monotonic()
                try:
                    value = run_once(service, args.terminal, args.mode == 'live' and not args.no_sync)
                    logger.info('%s 扫描完成：%d航班、%d告警', args.terminal, len(value['flights']), len(value['alerts']))
                except Exception:
                    logger.exception('值守扫描失败；不解除已有告警')
                    service.store.set_state('worker:' + args.terminal, {'heartbeat': iso(now()), 'error': '扫描失败，请检查日志', 'source_fresh': False})
                if args.once:
                    break
                stop.wait(max(0, args.interval - (monotonic() - started)))
        except KeyboardInterrupt:
            logger.info('值守已停止；保留全部运行台账')


if __name__ == '__main__':
    main()
