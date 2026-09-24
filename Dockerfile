FROM python:3.11-slim

LABEL maintainer="Genesis Swarm Architect <ryan@genesis.internal>"
LABEL description="GENESIS Multi-Agent Swarm Orchestrator & Autonomous Workbench"

ENV PYTHONUNBUFFERED=1 \
    PORT=8888 \
    DATA_DIR=/app/data \
    DOCKER_CONTAINER=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY assets/ /app/assets/

RUN mkdir -p /app/data /workspace

EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8888/api/status || exit 1

ENTRYPOINT ["python3", "/app/backend/main.py"]
