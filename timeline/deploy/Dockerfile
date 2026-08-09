# Build:  DOCKER_BUILDKIT=1 docker build -f deploy/Dockerfile -t fp-timeline:1.0.0 .
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1 \
    PIP_ROOT_USER_ACTION=ignore \
    WORK_DIR=/work

# ffmpeg la phu thuoc bat buoc cua buoc render.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

# Mang o may dich hay Read timeout giua khi tai wheel -> thu lai nhieu lan.
# Cache mount giu phan da tai, nen lan thu sau khong tai lai tu dau.
RUN --mount=type=cache,target=/root/.cache/pip \
    for i in 1 2 3 4 5; do \
        pip install --retries 10 --timeout 120 --prefer-binary \
            -r requirements.txt && break; \
        echo "pip lan $i that bai, cho 15s roi thu lai"; sleep 15; \
    done; \
    python -c "import fastapi, uvicorn, psycopg, pydantic, numpy, cv2, requests; \
print('deps ok')"

COPY tl/ ./tl/
COPY static/ ./static/
COPY app.py .

RUN useradd -u 10001 -m runner && mkdir -p /work && chown -R runner:runner /work
USER runner

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4)"

# Mot worker: render chay o thread nen va chi cho phep mot lan cung luc.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
