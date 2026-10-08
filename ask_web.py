# -*- coding: utf-8 -*-
"""兼容启动入口：使用项目虚拟环境执行 `python ask_web.py`。"""
import uvicorn

from app.config import settings


if __name__ == '__main__':
    uvicorn.run('app.main:app', host=settings.host, port=settings.port)
