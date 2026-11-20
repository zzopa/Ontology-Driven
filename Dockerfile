FROM python:3.13-slim

ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG INSTALL_PLAYWRIGHT=true

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN set -eux; \
    ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime; \
    echo "${TZ}" > /etc/timezone; \
    if [ -n "${APT_MIRROR}" ]; then \
        find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) \
            -exec sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" {} +; \
    fi; \
    if [ "${INSTALL_PLAYWRIGHT}" = "true" ]; then \
        apt-get update; \
        apt-get install -y --no-install-recommends \
            libatomic1 \
            fonts-noto-cjk \
            ca-certificates; \
        rm -rf /var/lib/apt/lists/*; \
    fi

COPY requirements.txt /tmp/requirements.txt

RUN set -eux; \
    pip install --no-cache-dir -i "${PIP_INDEX_URL}" -r /tmp/requirements.txt; \
    if [ "${INSTALL_PLAYWRIGHT}" = "true" ]; then \
        playwright install --with-deps chromium; \
    fi; \
    rm -f /tmp/requirements.txt

EXPOSE 5010

CMD ["python", "-X", "utf8", "ask_web.py"]
