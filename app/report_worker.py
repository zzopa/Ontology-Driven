"""Explicit report worker: python -m app.report_worker [--once]."""
import argparse
import logging
from threading import Event

from app import core, reporting
from app.admin.store import AdminStore
from app.config import settings


def main():
    parser = argparse.ArgumentParser(description='PDF报告与站内订阅后台进程')
    parser.add_argument('--once', action='store_true', help='调度并处理当前队列后退出')
    args = parser.parse_args()
    if settings.data_source != 'ops_ask':
        parser.error('报告后台仅支持ops_ask')
    logging.basicConfig(level=logging.INFO)
    store = AdminStore(settings.admin_directory, recover=False)
    core.initialize()
    stop = Event()
    try:
        while True:
            try:
                reporting.schedule_due(store)
                while reporting.process_one(store):
                    pass
                reporting.cleanup(store)
            except Exception:
                logging.exception('报告后台轮询失败；下轮重试')
            if args.once:
                break
            stop.wait(30)
    except KeyboardInterrupt:
        logging.info('报告后台已停止，任务保留')


if __name__ == '__main__':
    main()
