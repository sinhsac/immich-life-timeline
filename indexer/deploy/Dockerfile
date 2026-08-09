# Build:  DOCKER_BUILDKIT=1 docker build -f deploy/Dockerfile -t fp-indexer:1.0.0 .
# Model KHONG nam trong image. Mount qua PVC/hostPath de doi model khong phai
# build lai.
#
# Hai stage:
#   builder  co compiler, vi insightface 0.7.3 chi phat hanh sdist -> phai build
#   runtime  chi copy venv sang, khong mang theo gcc cho gon va do be mat tan cong

# ---------------------------------------------------------------- builder
FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential python3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt .

# Mang o may dich hay Read timeout giua khi tai wheel. Cache mount giu lai
# phan da tai duoc nen moi lan thu lai khong phai tai tu dau.
RUN --mount=type=cache,target=/root/.cache/pip \
    for i in 1 2 3 4 5; do \
        pip install --retries 10 --timeout 120 --prefer-binary \
            -r requirements.txt && break; \
        echo "pip lan $i that bai, cho 15s roi thu lai"; sleep 15; \
    done; \
    python -c "import numpy, cv2, onnxruntime, insightface, psycopg, requests; \
print('deps ok')"

# ---------------------------------------------------------------- runtime
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_DIR=/models \
    INSIGHTFACE_HOME=/models \
    PATH="/opt/venv/bin:$PATH"

# libglib/libgomp: opencv va onnxruntime can, ke ca ban headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY idx/ ./idx/
COPY tools/ ./tools/
COPY job.py .

# Chay bang user thuong: chi can doc /models, ghi /tmp/fp-cache.
RUN useradd -u 10001 -m runner && mkdir -p /models /tmp/fp-cache \
    && chown -R runner:runner /tmp/fp-cache
USER runner

ENTRYPOINT ["python", "job.py"]
