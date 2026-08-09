#!/usr/bin/env bash
# ===================================================================
# Build script for services-all-in-one Docker image.
# Copies environments into a temp directory to avoid Docker COPY
# symlink traversal limitations, then builds the image.
# ===================================================================

set -euo pipefail

BUILD_ROOT="/home/xxuz/Services"
IMAGE_NAME="${IMAGE_NAME:-services-all-in-one}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
CTX_TMP="${BUILD_ROOT}/.docker_context_tmp"

# ---------------------------------------------------------------------------
# Cleanup function — only cleans up if build fails; on success the context
# is kept so subsequent incremental builds reuse the cache.
# ---------------------------------------------------------------------------
cleanup() {
    if [[ -d "${CTX_TMP}" ]]; then
        local status
        status=$(cat "${CTX_TMP}/.build_status" 2>/dev/null || echo "1")
        if [[ "${status}" -ne 0 ]]; then
            echo "  Build failed — cleaning up temp context..."
            rm -rf "${CTX_TMP}"
        else
            echo "  Build succeeded — keeping context for next incremental build."
        fi
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Step 0: Copy environments (follow symlinks, dereference)
# ---------------------------------------------------------------------------
echo "[0/5] Copying environments into build context (follows symlinks)..."
rm -rf "${CTX_TMP}"
mkdir -p "${CTX_TMP}"

echo "  Copying YOLO .venv (6.1GB)..."
rsync -aL /home/xxuz/Services/YOLO/.venv/ "${CTX_TMP}/YOLO_venv"
echo "  Copying face_recognition .venv (3.5GB)..."
rsync -aL /home/xxuz/Services/face_recognition_insightface/.venv/ "${CTX_TMP}/face_venv"
echo "  Copying face_recognition InsightFace models (276MB)..."
mkdir -p "${CTX_TMP}/insightface_models"
rsync -aL /home/xxuz/.insightface/models/ "${CTX_TMP}/insightface_models/"
echo "  Copying SpatialMemory .venv (141MB)..."
rsync -aL /home/xxuz/Services/SpatialMemory/.venv/ "${CTX_TMP}/spatial_venv"
echo "  Copying anygrasp conda env (7.5GB)..."
rsync -aL /home/xxuz/miniconda3/envs/anygrasp/ "${CTX_TMP}/anygrasp_env"
echo "  Copying pre-built OpenSSL 1.1.1w libraries..."
mkdir -p "${CTX_TMP}/openssl-1.1"
rsync -aL /home/xxuz/Services/.docker_context/openssl-1.1/ "${CTX_TMP}/openssl-1.1/"
echo "  [OK] All environments copied"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Show copy sizes
# ---------------------------------------------------------------------------
echo "[1/5] Copy sizes:"
du -sh "${CTX_TMP}"/{YOLO_venv,face_venv,spatial_venv,anygrasp_env}
echo ""

# ---------------------------------------------------------------------------
# Step 2: Validate source paths exist
# ---------------------------------------------------------------------------
echo "[2/5] Validating source paths..."
for path in \
    "${BUILD_ROOT}/docker/supervisord.conf" \
    "${BUILD_ROOT}/docker/start.sh" \
    "${BUILD_ROOT}/Dockerfile"
do
    if [[ ! -e "${path}" ]]; then
        echo "  [ERROR] Missing: ${path}"
        exit 1
    fi
    echo "  [OK] ${path}"
done
echo ""

# ---------------------------------------------------------------------------
# Step 3: Build Docker image
# ---------------------------------------------------------------------------
echo "[3/5] Building Docker image (this may take a while)..."
cd "${BUILD_ROOT}"
set +e
sudo docker build \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    -f "${BUILD_ROOT}/Dockerfile" \
    "${BUILD_ROOT}"
echo $? > "${CTX_TMP}/.build_status"
set -e

# ---------------------------------------------------------------------------
echo ""
echo "========================================"
echo "  Build complete!"
echo "  Image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "========================================"
echo ""
echo "Next steps:"
echo "  1. Save image:  sudo docker save ${IMAGE_NAME}:${IMAGE_TAG} | gzip > services.tar.gz"
echo "  2. Load on another machine:  sudo docker load < services.tar.gz"
echo "  3. Run:  cd ${BUILD_ROOT} && sudo docker-compose -f docker-compose.yml up -d"
echo ""
