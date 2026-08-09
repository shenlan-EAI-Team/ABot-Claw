# VisualPlaceRecognition

独立的机器人视觉地点识别服务。第一版采用：

```text
参考图 / 查询图
        ↓
DINOv2-SALAD 全局描述向量
        ↓
float32
        ↓
L2 normalization
        ↓
FAISS IndexFlatIP
        ↓
Top-K cosine similarity score
        ↓
matched / ambiguous / unknown / empty_index
```

SALAD 的输出维度由第一次真实推理动态检测，不在业务代码中写死。相似度 `score` 不是概率，也不能解释为概率置信度。

## 服务边界

VisualPlaceRecognition 只保存 `place_id`、`image_id`、图片哈希、描述符版本、派生缓存与 FAISS 映射。地点名称、别名、地图 frame、导航 pose 和业务生命周期仍由 SpatialMemory 管理。该服务是新增旁路能力，不替代：

```text
POST /memory/place/upsert
POST /query/place
现有 VLAC 导航验证
```

当前 SpatialMemory 实现会返回宿主机 `image_path`，但没有 `GET /memory/place/{place_id}/image` 图片下载接口。集成方目前应使用 `/visual-index/images/upload`，或提供 VPR 容器可访问的 HTTP(S) 图片 URL；无需修改 SpatialMemory 现有接口。

## 架构与一致性

```text
SpatialMemory reference image
        ↓ POST /visual-index/images 或 /images/upload
安全图片读取 + SHA-256
        ↓
SALAD（进程内单例，eval + inference_mode）
        ↓ 原始 float32 embedding cache
FaissFlatIPIndex（内部统一 L2 normalization）
        ↓
临时 index.faiss → 原子替换
        ↓
不可变的 FAISS + SQLite 条目顺序映射快照
```

- `app/descriptors/` 只负责生成未由服务层归一化的一维描述符，后续可增加 AnyLoc 或其他实现。
- `app/indexes/faiss_flat_ip.py` 在参考向量加入和查询向量搜索前统一转连续 `float32` 并做 L2 normalization。`IndexFlatIP` 因此执行精确余弦相似度搜索。
- SQLite active 条目按数据库自增 `id` 排序；这一顺序与每次重建时加入 FAISS 的顺序完全一致。FAISS position 仅在一个不可变内存快照内使用，从不作为业务 ID 返回或持久引用。
- 变更先生成新 embedding 和候选 FAISS 文件，再在 SQLite 写事务中安装候选索引；事务或索引步骤失败会恢复旧磁盘索引，旧内存快照继续服务。
- 搜索先取得完整快照引用，索引与 position 映射不会分两步切换。
- embedding 文件名由 `image_id` 哈希、图片 SHA、模型版本哈希和维度组成，文件写入及 FAISS 保存均使用同目录临时文件加 `os.replace()`。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VPR_HOST` | `0.0.0.0` | HTTP 监听地址 |
| `VPR_PORT` | `8030` | HTTP 端口 |
| `VPR_DEVICE` | `auto` | `auto`、`cpu`、`cuda`、`cuda:N` |
| `VPR_DATA_DIR` | `./data` | 持久化根目录 |
| `VPR_INDEX_PATH` | `$VPR_DATA_DIR/index.faiss` | FAISS 文件 |
| `VPR_DATABASE_PATH` | `$VPR_DATA_DIR/index.sqlite3` | SQLite 文件 |
| `VPR_CACHE_DIR` | `$VPR_DATA_DIR/cache` | 图片和 embedding 缓存 |
| `VPR_DESCRIPTOR_BACKEND` | `salad` | 第一版仅支持 `salad` |
| `VPR_DESCRIPTOR_VERSION` | `salad_v1` | 变更后会使旧 embedding 缓存失效 |
| `VPR_TOP_K` | `2` | 默认候选数量 |
| `VPR_UNKNOWN_THRESHOLD` | `0.60` | Top-1 低于此值为 unknown |
| `VPR_AMBIGUOUS_MARGIN` | `0.08` | Top-1 与 Top-2 差值低于此值为 ambiguous |
| `VPR_REQUEST_TIMEOUT_SECONDS` | `15` | HTTP 图片请求超时 |
| `VPR_MAX_IMAGE_BYTES` | `20971520` | URL 和上传图片最大字节数 |
| `VPR_LOG_LEVEL` | `INFO` | 标准 logging 级别 |
| `VPR_ALLOWED_URL_HOSTS` | 空 | 可选逗号分隔 URL host allowlist，支持 `*.example.com` |
| `VPR_LOCAL_IMAGE_ROOTS` | 空 | 可选逗号分隔本地图片允许根目录 |
| `VPR_SALAD_REPO` | `serizba/salad` | Torch Hub repo 或本地 repo 路径 |
| `VPR_SALAD_MODEL` | `dinov2_salad` | Torch Hub entrypoint |
| `TORCH_HOME` | Torch 默认值 | SALAD、DINOv2 和权重缓存目录 |

`auto` 在 `torch.cuda.is_available()` 为真时选择 `cuda:0`，否则选择 CPU。显式请求不存在的 CUDA 设备会使模型保持未就绪，不会静默生成随机向量。

默认阈值只是初始值，必须使用实际相机、真实地点与正负查询图重新校准。只有一张参考图时，对方向、视角、遮挡和光照变化更敏感；建议参考图与查询图来自同一相机、方向相近，并包含完整场景。

## 本地启动

Python 3.10：

```bash
cd Services/VisualPlaceRecognition
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8030
```

`requirements.txt` 对齐当前仓库的 CUDA 12.4 / PyTorch 2.5.1 组合；CUDA wheel 在没有可用 GPU 时也会由 `VPR_DEVICE=auto` 退回 CPU，但占用空间较大。纯 CPU 部署可改用 PyTorch 官方 CPU wheel 安装同版本 `torch`/`torchvision`，FAISS 始终使用 `faiss-cpu`，因为当前索引规模最多约 5 条。

首次真实启动需要网络访问两个 Torch Hub 仓库及模型权重：SALAD repo/checkpoint 和 DINOv2 backbone。下载完成后由 `TORCH_HOME` 缓存。下载失败时 `/health` 仍返回 200，`/visual-index/status` 显示 `ready=false`，索引和搜索接口返回 503。

## Docker 启动

根 Compose 将本服务作为 `services-all-in-one:vpr-fixed` 中的第六个
Supervisor 进程运行，并沿用仓库的 host network 和 NVIDIA GPU 配置：

```bash
cd Services
docker build -f docker/Dockerfile.vpr-fixed \
  -t services-all-in-one:vpr-fixed .
docker compose up -d --force-recreate
docker exec services-all-in-one supervisorctl status vpr
```

VPR 使用独立的 `/opt/venvs/vpr`，且只在该子进程的 `LD_LIBRARY_PATH` 中加入
其 wheel 自带的 CUDA 库。运行数据绑定到 `VisualPlaceRecognition/data/`，
Torch Hub 模型绑定到 `VisualPlaceRecognition/model_cache/`；二者均位于可写的
`/services` 挂载之外并跨重启保留。服务固定为一个 Uvicorn worker。
CPU-only Docker 主机可删除 Compose 中的 `gpus: all` 后设置
`VPR_DEVICE=cpu`。完整的一体化构建、诊断和回滚说明见
[`docker/README.vpr-fixed.md`](../docker/README.vpr-fixed.md)。

## API

OpenAPI 文档：`http://127.0.0.1:8030/docs`。

### 健康与状态

```bash
curl http://127.0.0.1:8030/health
curl http://127.0.0.1:8030/visual-index/status
```

`/health` 只检测进程存活；`/visual-index/status` 才包含模型和索引 readiness、动态维度、设备、索引版本和最后重建时间。

### 通过 URL 新增参考图

```bash
curl -i -X POST http://127.0.0.1:8030/visual-index/images \
  -H 'Content-Type: application/json' \
  -d '{
    "place_id":"plc_93751e30613b",
    "image_id":"plc_93751e30613b",
    "image_url":"http://127.0.0.1:8022/path/to/reference.jpg",
    "image_sha256":null
  }'
```

创建返回 201。相同 `image_id`、实际 SHA-256 和描述符版本返回已有结果及 200，不重新推理；相同 ID 的不同内容返回 409，必须使用 PUT。

也可直接上传参考图：

```bash
curl -X POST http://127.0.0.1:8030/visual-index/images/upload \
  -F place_id=plc_93751e30613b \
  -F image_id=plc_93751e30613b \
  -F image=@reference.jpg
```

### 更新和删除

```bash
curl -X PUT http://127.0.0.1:8030/visual-index/images/plc_93751e30613b \
  -H 'Content-Type: application/json' \
  -d '{"image_url":"http://127.0.0.1:8022/new-reference.jpg"}'

curl -X PUT http://127.0.0.1:8030/visual-index/images/plc_93751e30613b/upload \
  -F image=@new-reference.jpg

curl -i -X DELETE \
  http://127.0.0.1:8030/visual-index/images/plc_93751e30613b
```

删除成功返回 204；不存在返回结构化 404。

### 全库搜索

```bash
curl -X POST http://127.0.0.1:8030/visual-index/search \
  -F image=@current.jpg \
  -F top_k=2
```

候选包含 `rank`、`place_id`、`image_id` 和 `score`。空索引是正常 200 响应，`decision=empty_index`。

### 指定地点到达验证

```bash
curl -X POST http://127.0.0.1:8030/visual-index/verify \
  -F target_place_id=plc_93751e30613b \
  -F image=@arrival.jpg
```

验证对全库搜索后按地点保留最佳参考图，返回目标 rank/score、Top-1/Top-2、margin、`verified` 和可解释 reasons。这为将来一个地点对应多张图片预留了地点聚合入口。

### 全量重建

```bash
curl -X POST http://127.0.0.1:8030/visual-index/rebuild
```

该接口会验证所有 active embedding；缓存损坏或模型版本变化时，优先从本地参考图缓存恢复，否则重新读取原 URL。任一 active 条目恢复失败则保留旧索引并返回失败 ID。此接口属于内部管理接口，生产部署应在网关层增加鉴权和网络限制。

### 错误结构

```json
{
  "error": {
    "code": "IMAGE_DOWNLOAD_FAILED",
    "message": "Unable to download reference image",
    "details": {}
  }
}
```

错误不会返回内部绝对路径、堆栈、模型 token 或图片内容；完整异常只写入服务日志。

## 与 SpatialMemory 配合

保存地点的最小增量流程：

```text
D455 拍照 + 当前 pose
        ↓
SpatialMemory POST /memory/place/upsert
        ↓ 获得 place_id
可选调用 VPR POST /visual-index/images/upload
```

图片反向识别：

```text
当前照片 → /visual-index/search → place_id
        → SpatialMemory 现有查询接口 → 地点名称和 pose
```

到达验证：

```text
/query/place 获取 pose → 原导航/VLAC 流程不变
        → 到达后 D455 当前图 → /visual-index/verify
```

VPR 索引失败或删除视觉条目不会删除 SpatialMemory 地点。

## 持久化与故障恢复

运行时文件：

```text
data/index.sqlite3
data/index.faiss
data/cache/embeddings/*.npy
data/cache/images/*
```

SQLite 保存视觉条目 ID、源 URL、SHA-256、缓存路径、descriptor backend/version/dimension、active 状态、时间和最后错误；不保存地点名称、别名或 pose。

- 删除或损坏 `index.faiss`：重启会在模型可用时自动从 SQLite + embedding cache 重建，也可调用 `/visual-index/rebuild`。
- 升级模型：修改 `VPR_DESCRIPTOR_VERSION` 后重启或调用 rebuild；旧 embedding 会失效并从图片缓存重新提取。
- SQLite 与 FAISS 数量/维度不一致：启动记录错误并安全重建；成功前不发布不一致快照。
- 原图片 URL 失效：有效 embedding 仍可重建 FAISS；若 embedding 也失效，则尝试本地图片缓存。两者都失效时返回失败条目并保留旧索引。
- SQLite 是元数据源；不要仅复制 `index.faiss` 而不复制数据库和缓存。

## 图片输入安全

- URL 只接受 `http`/`https`，拒绝 `file://`、空 host 和 URL 中的用户名密码。
- 重定向后的 URL 也会重新校验。
- 使用超时、Content-Length/流式字节上限、Pillow 实际格式验证和 RGB 转换。
- 部署需要访问局域网 SpatialMemory，因此默认不全面禁止私网；生产环境应设置 `VPR_ALLOWED_URL_HOSTS=spatial-memory,127.0.0.1` 或准确服务域名，并在网络策略层限制出站访问。仅 host allowlist 不能替代完整 DNS rebinding/SSRF 防护。
- 本地路径默认关闭；只有 `VPR_LOCAL_IMAGE_ROOTS` 下路径和服务自身图片缓存可读。
- 查询上传图片只在内存中解码，不持久保存。

## 测试

测试使用临时 SQLite/FAISS、确定性 mock descriptor 和 httpx ASGI transport，不访问真实 `data/`，也不下载 SALAD：

```bash
cd Services/VisualPlaceRecognition
.venv/bin/python -m pytest -q
```

覆盖 DecisionService 边界、FAISS 归一化/排序/保存加载、repository CRUD、幂等/冲突/更新/删除、缓存复用、版本失效、失败保留旧快照，以及 health/status/index/search/verify/rebuild 错误路径。

## 已知限制

- 首次模型启动依赖网络，模型与 DINOv2 权重较大。
- CPU SALAD 推理明显慢于 CUDA；FAISS 的 5 条精确搜索成本可忽略。
- 每地点单张参考图的视角鲁棒性有限，当前没有局部特征或 LightGlue 二次验证。
- 阈值尚未在本机器人/相机数据上校准。
- 第一版按单进程、低并发服务设计；写操作串行，内存索引不应由多个独立 Uvicorn worker 共同管理。
- JSON 的优先集成方式需要调用方提供可访问图片 URL；当前 SpatialMemory 没有对应图片 GET 接口。

## 许可证提示

模型通过 [SALAD 官方仓库](https://github.com/serizba/salad) 的 Torch Hub `dinov2_salad` 入口加载；官方评估使用 322×322 resize 与 ImageNet mean/std。SALAD 仓库目前标注 GPL-3.0。FAISS、PyTorch、DINOv2 及其模型权重各自有独立许可证和使用条款。产品化、分发镜像或商用前必须单独评估所有代码、模型和数据许可证；本说明不代表已完成法律审查。
