# -*- coding: utf-8 -*-
"""兼容启动入口：使用项目虚拟环境执行 `python ask_web.py`。"""
import os
import uvicorn

from app.config import settings


if __name__ == '__main__':
    host = os.environ.get('HBASK_HOST', '0.0.0.0')
    uvicorn.run('app.main:app', host=host, port=settings.port)
