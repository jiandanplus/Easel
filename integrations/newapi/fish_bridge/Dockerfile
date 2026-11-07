FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FISH_BRIDGE_STORAGE_DIR=/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    useradd --uid 10001 --create-home bridge && \
    mkdir /data && chown 10001:10001 /data && chmod 0700 /data
COPY app.py ./
USER 10001:10001
EXPOSE 8787
# Docker-private template explicitly overrides the bind address. No ports are published.
CMD ["uvicorn", "app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8787", "--workers", "1", "--limit-concurrency", "16", "--backlog", "32", "--no-access-log"]
