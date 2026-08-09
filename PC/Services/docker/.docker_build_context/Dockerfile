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
        python3.11 \
        python3.11-venv \
        python3.11-dev \
        python3.11-distutils \
        python3.13 \
        python3.13-venv \
        python3.13-dev \
        python3.13-distutils \
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
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Miniconda (for GraspAnything / anygrasp conda env)
# ---------------------------------------------------------------------------
RUN curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
        -o /tmp/miniconda.sh \
    && bash /tmp/miniconda.sh -b -p /opt/conda \
    && rm /tmp/miniconda.sh

# ---------------------------------------------------------------------------
# Copy environments — paths relative to build context root (/)
# ---------------------------------------------------------------------------

# 1. anygrasp conda env (Python 3.11, torch cu121)
COPY miniconda3/envs/anygrasp /opt/conda/envs/anygrasp

# 2. YOLO venv (Python 3.11, torch cu124)
COPY Services/YOLO/.venv /opt/venvs/yolo

# 3. face_recognition venv (Python 3.13, onnxruntime-gpu)
COPY Services/face_recognition_insightface/.venv /opt/venvs/face

# 4. SpatialMemory venv (Python 3.11)
COPY Services/SpatialMemory/.venv /opt/venvs/spatial

# ---------------------------------------------------------------------------
# Symlink Python binaries for convenience
# ---------------------------------------------------------------------------
RUN ln -sf /opt/conda/envs/anygrasp/bin/python /usr/local/bin/anygrasp-python \
    && ln -sf /opt/venvs/yolo/bin/python /usr/local/bin/yolo-python \
    && ln -sf /opt/venvs/face/bin/python /usr/local/bin/face-python \
    && ln -sf /opt/venvs/spatial/bin/python /usr/local/bin/spatial-python

# ---------------------------------------------------------------------------
# Copy entrypoint scripts & config
# ---------------------------------------------------------------------------
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8013 8015 8016 8022

ENTRYPOINT ["/start.sh"]
