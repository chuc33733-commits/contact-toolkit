# 通讯录处理工具 · 线上版
# 说明：单进程内存会话（关页不留存），必须单 worker；构建走国内源（pypi.org 直连超时）。
FROM python:3.13-slim-bookworm

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8788

WORKDIR /app

# 依赖层先复制并安装（尽量复用缓存）；系统时区数据 + 国内 pip 源
RUN set -eux; \
    printf '%s\n' \
      'deb http://mirrors.tuna.tsinghua.edu.cn/debian bookworm main' \
      'deb http://mirrors.tuna.tsinghua.edu.cn/debian bookworm-updates main' \
      'deb http://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main' \
      > /etc/apt/sources.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime; \
    echo "$TZ" > /etc/timezone

COPY 线上版/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# 业务代码：原项目可复用逻辑（models/processors/utils/config）+ 线上版 Web 层
COPY models /app/models
COPY processors /app/processors
COPY utils /app/utils
COPY config /app/config
COPY 线上版 /app/线上版
COPY certdown /app/certdown

# 运行期临时区：默认落磁盘目录 /var/scratch（compose 里绑定挂载宿主目录）。
# 不用 /tmp —— tmpfs 会被「原件 + 压缩包」同时存在的场景打满（实测需 ~704M）。
RUN mkdir -p /var/scratch && chmod 700 /var/scratch

# 非 root 运行
RUN useradd -M -u 10001 -s /usr/sbin/nologin appuser
RUN chown appuser:appuser /var/scratch
USER appuser

EXPOSE 8788

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8788/api/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["python", "线上版/run.py"]
