#!/bin/bash
set -euo pipefail

# ===================================================================
# Container entrypoint: set up env vars then launch supervisord
# ===================================================================

SERVICES_DIR="${SERVICES_DIR:-/services}"

# ---------------------------------------------------------------------------
# Proxy (if host sets these, pass them through)
# ---------------------------------------------------------------------------
export https_proxy="${https_proxy:-${HTTPS_PROXY:-}}"
export http_proxy="${http_proxy:-${HTTP_PROXY:-}}"
export all_proxy="${all_proxy:-${ALL_PROXY:-}}"

# ---------------------------------------------------------------------------
# SSL certificates — the anygrasp conda env was built on a different machine
# and OpenSSL's compiled-in paths (e.g. /home/xxuz/miniconda3/...) do not
# exist inside this container.  Point OpenSSL / Python SSL at certifi's CA bundle.
# ---------------------------------------------------------------------------
export SSL_CERT_FILE="/opt/conda/envs/anygrasp/lib/python3.11/site-packages/certifi/cacert.pem"
export REQUESTS_CA_BUNDLE="${SSL_CERT_FILE}"
export CURL_CA_BUNDLE="${SSL_CERT_FILE}"

# ---------------------------------------------------------------------------
# CUDA / GPU libs — all envs ship their own nvidia packages via pip/conda,
# so we just need the runtime stubs from the base image.
# LD_LIBRARY_PATH is prepended so container libs take priority over
# whatever the host might have mounted at /usr/local/cuda.
# ---------------------------------------------------------------------------
CUDA_PATHS="/usr/local/cuda/lib64:/usr/lib/x86_64-linux-gnu"
for pkg_dir in \
    "/opt/venvs/yolo/lib/python3.11/site-packages/nvidia" \
    "/opt/venvs/face/lib/python3.13/site-packages/nvidia" \
    "/opt/conda/envs/anygrasp/lib/python3.11/site-packages/nvidia"
do
    if [[ -d "${pkg_dir}/cuda_runtime/lib" ]]; then
        for subdir in "${pkg_dir}"/*/lib; do
            if [[ -d "${subdir}" ]]; then
                CUDA_PATHS="${CUDA_PATHS}:${subdir}"
            fi
        done
    fi
done
export LD_LIBRARY_PATH="${CUDA_PATHS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# ---------------------------------------------------------------------------
# OpenSSL 1.1.1 — built from source during image build (MITM proxies corrupt
# all Ubuntu mirror libssl1.1 debs). Prepend so it wins over any other libs.
# ---------------------------------------------------------------------------
export LD_LIBRARY_PATH="/opt/openssl-1.1/lib:${LD_LIBRARY_PATH}"
export PATH="/opt/openssl-1.1/bin:${PATH}"

# ---------------------------------------------------------------------------
# SSL: GraspAnything / anygrasp needs libssl 1.1, ship it inside the env.
# opencv_python.libs contains libcrypto.so.1.1 and libssl.so.1.1 (with hash
# suffixes) — create SONAME symlinks so the dynamic linker can find them.
# ---------------------------------------------------------------------------
OPENCV_LIBS="/opt/conda/envs/anygrasp/lib/python3.11/site-packages/opencv_python.libs"
for dir in \
    "/opt/conda/envs/anygrasp/lib" \
    "${OPENCV_LIBS}"
do
    if [[ -d "${dir}" ]]; then
        [[ ":$LD_LIBRARY_PATH:" != *":${dir}:"* ]] && export LD_LIBRARY_PATH="${dir}:${LD_LIBRARY_PATH}"
        # Create SONAME symlinks for OpenSSL 1.1 libraries
        for name in libcrypto libssl; do
            for f in "${dir}"/${name}-*.so.1.1; do
                [[ -f "${f}" ]] && ln -sf "$(basename "${f}")" "${dir}/${name}.so.1.1" 2>/dev/null && break
            done
        done
    fi
done

# ---------------------------------------------------------------------------
# User site-packages for nvidia libs (some packages install there)
# ---------------------------------------------------------------------------
# (skipped — we copied .venv directly so all libs are under /opt/venvs)

# ---------------------------------------------------------------------------
# Print environment summary
# ---------------------------------------------------------------------------
echo "========================================"
echo "  Services Docker Container"
echo "========================================"
echo "  Services mount: ${SERVICES_DIR}"
echo "  LD_LIBRARY_PATH (CUDA): ${LD_LIBRARY_PATH}"
echo "  SSL_CERT_FILE: ${SSL_CERT_FILE:-not set}"
echo "  Proxy: ${http_proxy:-none} / ${https_proxy:-none}"
echo ""

# ---------------------------------------------------------------------------
# Validate GPU access
# ---------------------------------------------------------------------------
if command -v nvidia-smi &>/dev/null; then
    echo "[GPU] nvidia-smi available:"
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null || echo "  (GPU detected, can't query details)"
else
    echo "[WARN] nvidia-smi not found — GPU may not be accessible."
    echo "       Make sure --gpus all is passed to docker run."
fi
echo ""

# ---------------------------------------------------------------------------
# Tail logs (so docker logs sees output) while supervisord runs in foreground
# ---------------------------------------------------------------------------
mkdir -p /var/log/supervisor
touch /var/log/supervisor/yolo-stdout.log \
       /var/log/supervisor/yolo-stderr.log \
       /var/log/supervisor/face-stdout.log \
       /var/log/supervisor/face-stderr.log \
       /var/log/supervisor/spatial-stdout.log \
       /var/log/supervisor/spatial-stderr.log \
       /var/log/supervisor/grasp-stdout.log \
       /var/log/supervisor/grasp-stderr.log

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
