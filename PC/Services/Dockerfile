# ===================================================================
# Services All-in-One Docker Image
# Base: nvidia/cuda:12.4-runtime + Ubuntu 22.04
# Bundles: YOLO (.venv py3.11 cu124), face_recognition (.venv py3.13),
#          SpatialMemory (.venv py3.11), GraspAnything (conda anygrasp py3.11 cu121)
# Entry: supervisord (manages 4 services)
# ===================================================================

FROM nvidia/cuda:12.4.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /app

# ---------------------------------------------------------------------------
# Install system dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
        curl \
        git \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgomp1 \
        ca-certificates \
        openssl \
        libssl3 \
        unzip \
        wget \
        sudo \
        supervisor \
        net-tools \
        wireless-tools \
        build-essential \
        perl \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# OpenSSL 1.1.1w — pre-built binaries are copied from the build context
# (MITM proxies corrupt all Ubuntu mirror libssl1.1 debs; building from
# source in Docker requires external network which is also blocked here).
# The libraries were compiled on the host from openssl-1.1.1w.tar.gz
# (SHA256: cf3098950cb4d853ad95c0841f1f9c9d3dc102dccfcacd521d93925208b76ac8).
# ---------------------------------------------------------------------------
COPY .docker_context_tmp/openssl-1.1 /opt/openssl-1.1
RUN ln -sf /opt/openssl-1.1/libcrypto.so.1.1 /usr/lib/x86_64-linux-gnu/libcrypto.so.1.1 \
    && ln -sf /opt/openssl-1.1/libssl.so.1.1     /usr/lib/x86_64-linux-gnu/libssl.so.1.1     \
    && ln -sf /opt/openssl-1.1/libcrypto.so.1.1 /lib/x86_64-linux-gnu/libcrypto.so.1.1 \
    && ln -sf /opt/openssl-1.1/libssl.so.1.1     /lib/x86_64-linux-gnu/libssl.so.1.1     \
    && ldconfig

ENV LD_LIBRARY_PATH=/opt/openssl-1.1/lib:${LD_LIBRARY_PATH}

# ---------------------------------------------------------------------------
# Python versions (deadsnakes PPA) — must come after OpenSSL so the compiled
# libssl is already on disk and apt can coexist with it.
# ---------------------------------------------------------------------------
RUN add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-dev \
        python3.11-distutils \
        python3.13 \
        python3.13-venv \
        python3.13-dev \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Miniconda (for GraspAnything / anygrasp conda env, and face_recognition Python)
# ---------------------------------------------------------------------------
RUN curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
        -o /tmp/miniconda.sh \
    && bash /tmp/miniconda.sh -b -p /opt/conda \
    && rm /tmp/miniconda.sh

# ---------------------------------------------------------------------------
# Copy environments — paths relative to build context root (/home/xxuz/Services)
# ---------------------------------------------------------------------------

# 1. anygrasp conda env (Python 3.11, torch cu121)
COPY .docker_context_tmp/anygrasp_env /opt/conda/envs/anygrasp

# 2. YOLO venv (Python 3.11, torch cu124)
COPY .docker_context_tmp/YOLO_venv /opt/venvs/yolo

# 3. face_recognition venv (Python 3.13, onnxruntime-gpu)
COPY .docker_context_tmp/face_venv /opt/venvs/face

# 3b. InsightFace buffalo_l model — pre-downloaded to avoid slow GitHub fetch at runtime
COPY .docker_context_tmp/insightface_models/ /root/.insightface/models/

# 4. SpatialMemory venv (Python 3.11)
COPY .docker_context_tmp/spatial_venv /opt/venvs/spatial

# ---------------------------------------------------------------------------
# Fix pyvenv.cfg — the copied venvs contain hardcoded build-machine paths.
# Reset home/executable/command to point to the container's Python installs.
# ---------------------------------------------------------------------------
RUN sed -i 's|^home = .*|home = /opt/conda/envs/anygrasp/bin|' /opt/venvs/yolo/pyvenv.cfg \
    && sed -i 's|^executable = .*|executable = /opt/conda/envs/anygrasp/bin/python3.11|' /opt/venvs/yolo/pyvenv.cfg \
    && sed -i 's|^command = .*|command = /opt/conda/envs/anygrasp/bin/python -m venv /opt/venvs/yolo|' /opt/venvs/yolo/pyvenv.cfg \
    && sed -i 's|^home = .*|home = /opt/conda/envs/anygrasp/bin|' /opt/venvs/spatial/pyvenv.cfg \
    && sed -i 's|^executable = .*|executable = /opt/conda/envs/anygrasp/bin/python3.11|' /opt/venvs/spatial/pyvenv.cfg \
    && sed -i 's|^command = .*|command = /opt/conda/envs/anygrasp/bin/python -m venv /opt/venvs/spatial|' /opt/venvs/spatial/pyvenv.cfg \
    && sed -i 's|^home = .*|home = /opt/conda/bin|' /opt/venvs/face/pyvenv.cfg \
    && sed -i 's|^executable = .*|executable = /opt/conda/bin/python3.13|' /opt/venvs/face/pyvenv.cfg \
    && sed -i 's|^command = .*|command = /opt/conda/bin/python -m venv /opt/venvs/face|' /opt/venvs/face/pyvenv.cfg

# ---------------------------------------------------------------------------
# Symlink Python binaries for convenience
# ---------------------------------------------------------------------------
RUN ln -sf /opt/conda/envs/anygrasp/bin/python /usr/local/bin/anygrasp-python \
    && ln -sf /opt/conda/envs/anygrasp/bin/python /usr/local/bin/yolo-python \
    && ln -sf /opt/conda/envs/anygrasp/bin/python /usr/local/bin/spatial-python \
    && ln -sf /opt/conda/bin/python /usr/local/bin/face-python

# ---------------------------------------------------------------------------
# Copy entrypoint scripts & config
# ---------------------------------------------------------------------------
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY docker/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8013 8015 8016 8022

ENTRYPOINT ["/start.sh"]
