# EvoSkill 全程计划书

> **面向长期任务的 LLM Agent 自我进化（Skill Library Distillation）**
> 目标：在 ABot-Claw（G1 人形机器人）上实现"轨迹→蒸馏→检索→执行"闭环，投稿 **ICRA 2027**（截稿 ~2026-09-15）。
> 文档版本：v1.0 · 2026-06-19 · 作者：EvoSkill Team
> 适用代码版本：abotclaw_robot_final（PC + ROBOT 双端）

---

## 目录

1. [项目背景与动机](#1-项目背景与动机)
2. [核心问题与机会窗口](#2-核心问题与机会窗口)
3. [EvoSkill 总体方案](#3-evoskill-总体方案)
4. [技术架构详解](#4-技术架构详解)
5. [代码改动清单（逐文件、逐函数）](#5-代码改动清单逐文件逐函数)
6. [评测任务与基线设计](#6-评测任务与基线设计)
7. [实验计划与消融方案](#7-实验计划与消融方案)
8. [9 周冲刺时间表（ICRA 2027）](#8-9-周冲刺时间表icra-2027)
9. [里程碑与交付物](#9-里程碑与交付物)
10. [风险清单与应对预案](#10-风险清单与应对预案)
11. [论文写作大纲（ICRA 8 页）](#11-论文写作大纲icra-8-页)
12. [团队分工建议](#12-团队分工建议)
13. [附录：依赖安装、命令速查](#13-附录依赖安装命令速查)

---

## 1. 项目背景与动机

### 1.1 现状（基于 `abotclaw_robot_final` 实测）

| 模块 | 路径 | 现状 |
|------|------|------|
| OpenClaw Agent | `ROBOT/.../openclaw/`（项目内 LLM 调度器） | 接收自然语言 → LLM 生成 Python 代码 → 提交 `/code/execute` |
| Agent Server | `ROBOT/abotclaw_nv/robot_client/unitree_G1/agent_server/` | FastAPI 8888；提供 `/code/execute`、`/lease/*`、`/state` |
| 代码执行 | `agent_server/code_executor.py` + `routes/code_routes.py` | 子进程 + AST 黑名单 + timeout |
| 状态记录 | `agent_server/execution_recorder.py` | 已记录 `state_log.jsonl` (10 Hz)，**未持久化到 DB** |
| PC 感知 | `PC/Services/`（YOLO8013 / Grasp8015 / Face8016 / Memory8022） | 5 个独立 HTTP 服务 |
| 空间记忆 | `PC/Services/SpatialMemory/` | 键值存储（对象+位姿），**无语义、时序、关系** |
| 人形本体 | Unitree G1 35-DoF（DDS） | 已有运动、抓取、TTS、视觉 |

### 1.2 三大痛点（直接对应论文 motivation）

**痛点 P1：LLM 现场推理贵且不稳**
- 每次任务都从 0 让 LLM 拼代码，无任何"经验"可复用
- 同一个意图可能被 LLM 写成 5 种不同代码，质量参差
- 失败案例无沉淀，下次再遇到同类问题要重新踩坑

**痛点 P2：缺乏结构化记忆与反思**
- `state_log.jsonl` 只在磁盘上，无结构化查询
- 无"成功 / 失败 / 关键参数"等元数据
- 无跨任务、跨日、跨场景的经验积累

**痛点 P3：缺少可量化的自进化 benchmark**
- 现有人形机器人 LLM 论文（RT-2、π₀、OpenVLA）多偏 sim2real 或单任务
- 缺"长期任务" + "真机" + "LLM Agent 持续学习"三要素同时成立的开源平台

### 1.3 学术机会窗口

- **ICRA 2024-2025** 已大量接收 "LLM × 真机" 论文（OK-Robot、VoxPoser、RoboFlamingo）
- **2026 起 LLM Agent for Robotics** 成为热点：Voyager / Genima / Skill-LLM
- **空白点**：尚无"G1 真机 + LLM 长期自进化"开源平台
- ABot-Claw 已有完整栈（感知 + 执行 + 记忆 + 硬件），**改造 1-2 个模块即可形成完整论文**

---

## 2. 核心问题与机会窗口

### 2.1 一句话核心问题

> **如何让基于 LLM 的人形机器人在执行长期任务时，把成功与失败经验自动沉淀为可复用的 Skill Library，并通过检索增强持续提升任务成功率与效率？**

### 2.2 拟回答的 3 个研究问题（Research Questions）

| RQ | 问题 | 拟回答方式 |
|----|------|-----------|
| **RQ1** | LLM Agent 蒸馏出的 Skill Library 在长期任务上是否显著优于纯 LLM 推理？ | 主表对比 EvoSkill vs ReAct/Reflexion/Voyager |
| **RQ2** | Skill 检索（向量+标签+成功率加权）是否比纯向量检索更优？ | 消融 A：无检索 / 纯向量 / 多因子加权 |
| **RQ3** | 反例 Skill（anti-pattern）和自动去重是否必要？ | 消融 B：无反例 / 无去重 / 全量 |

### 2.3 三大贡献（贡献点三件套）

1. **方法贡献**：提出 **EvoSkill** 框架——轨迹→LLM 蒸馏→向量检索→执行闭环。
2. **系统贡献**：在 **Unitree G1** 上实现并开源**真机持续学习**机器人平台。
3. **实验贡献**：构建 **5 类长期任务 × 100 trials** 的 G1 真机 benchmark。

---

## 3. EvoSkill 总体方案

### 3.1 设计哲学

> **不重写 OpenClaw，只在 Agent Server 端"夹"一层 EvoSkill 中间件。**

### 3.2 数据流图

```
┌──────────────────────────────────────────────────────────────────┐
│                        OpenClaw Agent (LLM)                      │
│  1. 收到用户指令 "把红杯子拿过来"                                   │
│  2. 调 retriever.query(intent) → 拿到 Top-3 skill                 │
│  3. 把 skill 注入 prompt，调 LLM 生成代码                          │
│  4. POST /code/execute (with x-evoskill-skills header)            │
└────────────────────┬─────────────────────────────────────────────┘
                     │ HTTP + Header: X-EvoSkill-Skills
                     ▼
┌──────────────────────────────────────────────────────────────────┐
│              Agent Server (:8888) + EvoSkill Middleware          │
│                                                                  │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────┐  │
│  │ TrajectoryRecorder │  │  SkillRetriever    │  │ SkillDistil │  │
│  │  (写 SQLite)        │  │  (FAISS + JSON)    │  │ ler (后台)  │  │
│  └────────────────────┘  └────────────────────┘  └────────────┘  │
│                                                                  │
│  /code/execute                                                    │
│       ├─ validation (AST)                                          │
│       ├─ run in subprocess                                        │
│       ├─ execution_recorder.start(state_agg)                      │
│       ├─ on finish → trajectory_db.insert(...)                   │
│       └─ trigger distill if N>=5 new records                      │
└────────────────────┬─────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────────┐
│   Skill Library (./evoskill_lib/)                                │
│   ├── skills.json          (元数据列表)                             │
│   ├── embeddings.npy       (FAISS 索引)                            │
│   ├── trajectories.db      (SQLite 全部执行历史)                    │
│   └── distill_log.jsonl    (蒸馏日志，便于 replay)                  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.3 三步执行闭环

**Step 1：检索（每次任务前）**
- 输入：用户自然语言指令
- 输出：Top-K 候选 Skill（含代码模板 + 历史成功率 + 适用条件）
- 策略：`score = α·sim(emb) + β·tag_match + γ·success_rate − δ·recency_penalty`

**Step 2：执行（已有，复用）**
- OpenClaw 用 Skill 生成代码 → POST `/code/execute`
- 钩子在 `/code/execute` 完成后插入 `trajectories` 表

**Step 3：蒸馏（异步后台）**
- 触发条件：累积 N 条新轨迹 OR 距上次蒸馏 > 1 小时
- 流程：聚类 → LLM 提炼 → 校验 → 入库
- 输出：新 Skill（或反例 Skill）

---

## 4. 技术架构详解

### 4.1 Skill Library 数据结构

**`skills.json` 单条记录**（Schema v1.0）

```json
{
  "skill_id": "fetch_object_2025_v2",
  "version": 2,
  "name": "fetch_object",
  "description": "导航到目标物体并抓取放回 home 区域",
  "tags": ["navigation", "grasp", "humanoid"],
  "code_template": "def fetch_object(target):\n    ...",
  "preconditions": [
    "目标物体在 SpatialMemory.query_object 中存在",
    "机器人处于 standing 状态"
  ],
  "postconditions": [
    "物体位于 home 区域 (within 0.5m)",
    "机器人回到 home 位姿"
  ],
  "common_failures": [
    {"reason": "object_not_in_memory", "fix": "先调用 yolo.detect() 扫描"},
    {"reason": "grasp_failed",       "fix": "尝试 grasp_target(visualize=True) 调试"}
  ],
  "stats": {
    "success_count": 17,
    "failure_count": 3,
    "avg_duration_s": 42.3,
    "success_rate": 0.85
  },
  "embedding_model": "text-embedding-3-small",
  "embedding": [0.12, -0.05, 0.83, "..."],
  "created_at": "2026-07-15T08:30:00",
  "last_used":   "2026-09-01T14:22:00",
  "is_antipattern": false,
  "parent_skill_id": "fetch_object_2025_v1"
}
```

### 4.2 `trajectories.db` 表结构

```sql
CREATE TABLE trajectories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id    TEXT UNIQUE NOT NULL,
    timestamp       REAL NOT NULL,
    user_instruction TEXT NOT NULL,          -- 原始意图
    generated_code   TEXT NOT NULL,          -- 提交的 Python 代码
    stdout           TEXT,
    stderr           TEXT,
    success          INTEGER NOT NULL,       -- 0/1
    failure_reason   TEXT,                   -- timeout / grasp_failed / ...
    duration_s       REAL,
    tokens_used      INTEGER,
    retrieved_skills TEXT,                   -- JSON: [{"id":..., "score":...}]
    skill_used_id    TEXT,                   -- 最终 LLM 决策：A/B/C
    state_log_path   TEXT,                   -- logs/code_executions/{id}/state_log.jsonl
    env_state_at_end TEXT,                   -- JSON: 结束时 G1 状态
    distilled        INTEGER DEFAULT 0       -- 是否已被蒸馏过
);

CREATE INDEX idx_success      ON trajectories(success);
CREATE INDEX idx_distilled    ON trajectories(distilled);
CREATE INDEX idx_timestamp    ON trajectories(timestamp);
```

### 4.3 检索算法（伪代码）

```python
def retrieve_skill(intent: str, k: int = 3) -> List[Skill]:
    # 1. 嵌入
    emb = embed_model.encode(intent)
    
    # 2. 候选召回：向量 Top-20
    candidates = faiss_index.search(emb, k=20)
    
    # 3. 多因子打分
    scored = []
    for cand in candidates:
        s = cand.skill
        score = (
            ALPHA * cand.similarity          # 语义相似度
            + BETA  * tag_overlap(intent, s) # 标签重合度
            + GAMMA * s.stats.success_rate   # 历史成功率
            - DELTA * age_days(s) / 30.0     # 防止依赖过老
        )
        scored.append((score, s))
    
    # 4. 排序 + 去重（避免 3 条都是同一 skill 的不同版本）
    scored.sort(reverse=True)
    return dedup_preserve_order(scored)[:k]
```

**超参数（默认值，可调）**：

| 参数 | 值 | 含义 |
|------|----|------|
| `ALPHA` | 0.5 | 语义相似度权重 |
| `BETA` | 0.2 | 标签匹配权重 |
| `GAMMA` | 0.3 | 成功率权重 |
| `DELTA` | 0.05 | 时间衰减权重 |
| `K` | 3 | 返回条数 |
| `RECRUIT_THRESHOLD` | 0.6 | 相似度低于此值不返回（LLM 自由发挥） |

### 4.4 蒸馏流程（后台 LLM 任务）

```python
def distill_new_skills(trajectories: List[Trajectory]) -> List[Skill]:
    """每累积 N 条新轨迹，调用一次。"""
    # 1. 聚类
    clusters = cluster_by_embedding(trajectories, threshold=0.85)
    
    new_skills = []
    for cluster in clusters:
        if len(cluster) < 3:
            continue
        
        # 2. 判断是"成功类"还是"失败类"
        success_rate = sum(t.success for t in cluster) / len(cluster)
        
        if success_rate >= 0.6:
            # 正向 skill
            skill = llm_extract_skill(cluster)
        else:
            # 反例 skill（避坑指南）
            skill = llm_extract_antipattern(cluster)
        
        # 3. 校验（语法 + 接口存在性）
        if not validate_skill_template(skill):
            logger.warning(f"Skill {skill.skill_id} 校验失败，跳过")
            continue
        
        # 4. 写入库
        save_to_library(skill)
        new_skills.append(skill)
    
    return new_skills
```

**LLM 蒸馏 Prompt 模板**（`prompts/distill_skill.md`）：

```markdown
You are maintaining a skill library for a humanoid robot (Unitree G1).

Below are {N} recent execution trajectories for similar tasks:

{trajectories_block}

Tasks:
1. Identify the COMMON PATTERN across successful trajectories.
2. Extract a reusable Python function template (with docstring).
3. List: preconditions, postconditions, common failure modes, success rate.
4. Output STRICT JSON matching the Skill schema below.

{schema_block}

Rules:
- Use only existing APIs: env, yolo, memory, face, tts, Nav2Anywhere, grasp_target, grasp_something, release_something.
- Do NOT use subprocess, requests, or os.system (forbidden by sandbox).
- If failures dominate, output an "anti-pattern" skill with `is_antipattern: true`.
```

### 4.5 LLM 决策 Prompt（任务规划侧）

在 OpenClaw 端改造的 prompt 注入：

```markdown
# Available Skills (from EvoSkill Library, ranked by relevance)

{format_skills(top_k_skills)}

# Task

{user_instruction}

# Instructions

1. Decide ONE of the following strategies (output as JSON):
   - `{"strategy": "DIRECT_USE", "skill_id": "fetch_object_2025_v2"}` — Skill 完全覆盖
   - `{"strategy": "COMPOSE", "skill_ids": ["nav_to", "grasp_target"]}` — 组合多个
   - `{"strategy": "NEW", "rationale": "现有 skill 都不适用，原因..."}` — 自由发挥

2. Write Python code using `env`, `yolo`, `memory`, `face`, `tts`, `Nav2Anywhere`, `grasp_target`, `grasp_something`, `release_something`.

3. Prefer DIRECT_USE > COMPOSE > NEW (token efficiency).

4. Each call should be wrapped in try/except to handle failures.
```

---

## 5. 代码改动清单（逐文件、逐函数）

> **设计原则：尽量不破坏现有结构，所有改动以"旁路"或"钩子"方式插入。**

### 5.1 总览：新增 / 修改 / 不动

| 操作 | 数量 | 关键文件 |
|------|------|----------|
| **新增模块** | 8 个 | `evoskill/`, `agent_server/evoskill_routes.py` 等 |
| **修改文件** | 3 个 | `code_routes.py`、`execution_recorder.py`、OpenClaw 主流程 |
| **不动** | - | `CodeExecutor` 内部、`CodeValidator`、DDS 桥、YOLO 服务本体 |

### 5.2 新增文件清单

#### 📁 `ROBOT/abotclaw_nv/robot_client/unitree_G1/agent_server/evoskill/`

```
evoskill/
├── __init__.py
├── config.py              # EvoSkillConfig (阈值/路径/超参)
├── skill_library.py       # SkillLibrary (load/save/embed/search)
├── skill_retriever.py     # SkillRetriever (FAISS 封装)
├── trajectory_store.py    # TrajectoryStore (SQLite 封装)
├── trajectory_recorder.py # TrajectoryRecorder (钩子, 包装 execution_recorder)
├── distiller.py           # SkillDistiller (LLM 蒸馏后台任务)
├── evaluator.py           # SkillEvaluator (成功率重评/反例识别)
├── api.py                 # /skills REST API
└── prompts/
    ├── distill_skill.md
    └── compose_skill.md
```

#### 关键文件实现说明

**`evoskill/config.py`** — 全局配置

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass
class EvoSkillConfig:
    # 路径
    lib_root: Path = Path("/home/xxuz/abotclaw_robot_final/evoskill_lib")
    db_path: Path = Path("/home/xxuz/abotclaw_robot_final/evoskill_lib/trajectories.db")
    skills_json: Path = Path("/home/xxuz/abotclaw_robot_final/evoskill_lib/skills.json")
    embeddings_npy: Path = Path("/home/xxuz/abotclaw_robot_final/evoskill_lib/embeddings.npy")
    
    # 检索超参
    alpha: float = 0.5
    beta: float = 0.2
    gamma: float = 0.3
    delta: float = 0.05
    top_k: int = 3
    recruit_threshold: float = 0.6
    
    # 蒸馏触发
    distill_every_n_trajectories: int = 5
    distill_min_interval_min: int = 60
    
    # 去重
    dedup_similarity_threshold: float = 0.92
    max_library_size: int = 500
    
    # 嵌入模型
    embedding_model: str = "text-embedding-3-small"
    
    # LLM
    distill_llm: str = "gpt-4o-mini"  # 蒸馏用便宜模型
    plan_llm: str = "gpt-4o"          # 规划用强模型
```

**`evoskill/trajectory_store.py`** — SQLite 封装

```python
import sqlite3, json
from typing import List, Optional
from contextlib import contextmanager

class TrajectoryStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()
    
    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:    yield c
        finally: c.close()
    
    def _init_schema(self):
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS trajectories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT UNIQUE NOT NULL,
                    timestamp REAL NOT NULL,
                    user_instruction TEXT NOT NULL,
                    generated_code TEXT NOT NULL,
                    stdout TEXT, stderr TEXT,
                    success INTEGER NOT NULL,
                    failure_reason TEXT,
                    duration_s REAL, tokens_used INTEGER,
                    retrieved_skills TEXT,
                    skill_used_id TEXT,
                    state_log_path TEXT,
                    env_state_at_end TEXT,
                    distilled INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_success   ON trajectories(success);
                CREATE INDEX IF NOT EXISTS idx_distilled ON trajectories(distilled);
            """)
    
    def insert(self, **kwargs) -> int: ...
    def get_undistilled(self, limit: int = 50) -> List[dict]: ...
    def mark_distilled(self, ids: List[int]): ...
    def get_recent(self, n: int = 100) -> List[dict]: ...
    def stats_by_skill(self) -> dict: ...
```

**`evoskill/skill_library.py`** — Skill 增删改查

```python
import json, numpy as np
from pathlib import Path
from typing import List, Optional
from sentence_transformers import SentenceTransformer
import faiss

class SkillLibrary:
    def __init__(self, config: EvoSkillConfig):
        self.cfg = config
        self.skills: List[dict] = []
        self.index: Optional[faiss.IndexFlatIP] = None
        self.embedder = SentenceTransformer("all-MiniLM-L6-v2")  # 本地版
        # or use openai client
        self._load()
    
    def _load(self):
        if self.cfg.skills_json.exists():
            self.skills = json.loads(self.cfg.skills_json.read_text())
            if self.cfg.embeddings_npy.exists():
                embs = np.load(self.cfg.embeddings_npy)
                self.index = faiss.IndexFlatIP(embs.shape[1])
                self.index.add(embs.astype("float32"))
    
    def _save(self):
        self.cfg.skills_json.write_text(json.dumps(self.skills, indent=2, ensure_ascii=False))
        if self.skills:
            embs = np.array([s["embedding"] for s in self.skills], dtype="float32")
            np.save(self.cfg.embeddings_npy, embs)
    
    def add(self, skill: dict):
        skill["embedding"] = self.embedder.encode(skill["description"]).tolist()
        self.skills.append(skill)
        self._rebuild_index()
        self._save()
    
    def _rebuild_index(self):
        if not self.skills: return
        embs = np.array([s["embedding"] for s in self.skills], dtype="float32")
        self.index = faiss.IndexFlatIP(embs.shape[1])
        self.index.add(embs)
    
    def search(self, query: str, k: int = 3) -> List[dict]:
        if self.index is None: return []
        q = self.embedder.encode([query]).astype("float32")
        D, I = self.index.search(q, k * 2)  # 多取一些，再去重
        return [self.skills[i] for i in I[0] if i < len(self.skills)][:k]
```

**`evoskill/skill_retriever.py`** — 多因子打分

```python
from datetime import datetime
from typing import List

class SkillRetriever:
    def __init__(self, library: SkillLibrary, config: EvoSkillConfig):
        self.lib = library
        self.cfg = config
    
    def query(self, intent: str, k: int = 3) -> List[dict]:
        cands = self.lib.search(intent, k=max(10, k*2))
        scored = []
        for s in cands:
            sim = self._similarity(intent, s)
            if sim < self.cfg.recruit_threshold:
                continue
            tag_overlap = self._tag_overlap(intent, s)
            sr = s.get("stats", {}).get("success_rate", 0.5)
            age_days = (datetime.now() - datetime.fromisoformat(s["created_at"])).days
            score = (self.cfg.alpha * sim
                     + self.cfg.beta * tag_overlap
                     + self.cfg.gamma * sr
                     - self.cfg.delta * age_days / 30.0)
            scored.append((score, s))
        scored.sort(reverse=True, key=lambda x: x[0])
        return self._dedup([s for _, s in scored])[:k]
    
    def _similarity(self, a, b) -> float:
        return float(np.dot(self.lib.embedder.encode([a])[0],
                             np.array(b["embedding"])) /
                      (np.linalg.norm(self.lib.embedder.encode([a])[0]) * np.linalg.norm(b["embedding"]) + 1e-9))
    
    def _tag_overlap(self, intent, skill) -> float:
        intent_words = set(intent.lower().split())
        skill_tags = set(skill.get("tags", []))
        return len(intent_words & skill_tags) / max(len(skill_tags), 1)
    
    def _dedup(self, skills: List[dict]) -> List[dict]:
        # 简单按 skill_id 名字去重
        seen = set()
        out = []
        for s in skills:
            base = s["name"]
            if base in seen: continue
            seen.add(base)
            out.append(s)
        return out
```

**`evoskill/distiller.py`** — 后台蒸馏

```python
import threading, time
from typing import List

class SkillDistiller:
    def __init__(self, traj_store: TrajectoryStore, library: SkillLibrary, config: EvoSkillConfig):
        self.traj_store = traj_store
        self.library = library
        self.cfg = config
        self._lock = threading.Lock()
        self._last_distill = 0.0
    
    def maybe_distill(self, llm_client):
        with self._lock:
            now = time.time()
            und = self.traj_store.get_undistilled(limit=50)
            cond_n = len(und) >= self.cfg.distill_every_n_trajectories
            cond_t = (now - self._last_distill) > self.cfg.distill_min_interval_min * 60
            if not (cond_n or cond_t): return []
            
            new_skills = self._distill_batch(und, llm_client)
            
            ids = [t["id"] for t in und]
            self.traj_store.mark_distilled(ids)
            self._last_distill = now
            return new_skills
    
    def _distill_batch(self, trajectories, llm_client) -> List[dict]:
        # 1. 聚类
        from sklearn.cluster import DBSCAN
        embs = self.library.embedder.encode([t["user_instruction"] for t in trajectories])
        clusterer = DBSCAN(eps=0.3, min_samples=2, metric="cosine").fit(embs)
        
        new_skills = []
        for label in set(clusterer.labels_):
            if label < 0: continue
            members = [trajectories[i] for i, l in enumerate(clusterer.labels_) if l == label]
            if len(members) < 3: continue
            
            success_rate = sum(t["success"] for t in members) / len(members)
            
            # 2. LLM 提炼
            prompt = build_distill_prompt(members, success_rate)
            skill_json = llm_client.generate(prompt, response_format={"type": "json_object"})
            skill = json.loads(skill_json)
            
            # 3. 校验
            if not validate_skill_template(skill): continue
            
            # 4. 入库
            self.library.add(skill)
            new_skills.append(skill)
        return new_skills
```

#### 📁 `agent_server/evoskill_routes.py` — REST API

```python
from fastapi import APIRouter
from typing import List

router = APIRouter(prefix="/evoskill", tags=["evoskill"])

@router.get("/search")
async def search_skill(q: str, k: int = 3):
    """任务开始前，OpenClaw 调用此接口获取 Top-K Skill。"""
    ...

@router.get("/skills")
async def list_skills():
    """列出库内全部 skill（含统计）。"""
    ...

@router.post("/distill")
async def trigger_distill():
    """手动触发一次蒸馏。"""
    ...

@router.get("/stats")
async def library_stats():
    """库统计：总数 / 成功率分布 / 增长曲线数据。"""
    ...
```

### 5.3 修改文件清单

#### 🛠️ `agent_server/routes/code_routes.py` — 加钩子

**改动 1**：在 `execute_code` 函数签名增加 header 接收

```python
async def execute_code(
    request: Request,
    body: CodeExecuteRequest,
    x_lease_id: Optional[str] = Header(None),
    x_evoskill_skills: Optional[str] = Header(None),  # ← 新增
):
```

**改动 2**：在执行成功 / 失败后调用 `trajectory_recorder`

```python
# 在原 return JSONResponse(...) 之前，加：
try:
    from evoskill.trajectory_recorder import TrajectoryRecorder
    rec = TrajectoryRecorder()
    rec.record(
        execution_id=execution_id,
        user_instruction=body.get_intent_hint(),  # 见下文
        generated_code=body.code,
        stdout=result.stdout,
        stderr=result.stderr,
        success=(result.status == ExecutionStatus.SUCCESS),
        failure_reason=result.stop_reason,
        duration_s=result.duration,
        retrieved_skills=x_evoskill_skills,
        state_log_path=str(_CODE_DIR / execution_id / "state_log.jsonl"),
    )
except Exception as e:
    logger.warning(f"EvoSkill record failed (non-fatal): {e}")
```

**注意**：这里的 `user_instruction`（用户原始意图）当前 `CodeExecuteRequest` 没有。**最简方案**：在 `CodeExecuteRequest` 增加一个可选 `metadata` 字段：

```python
class CodeExecuteRequest(BaseModel):
    code: str = Field(...)
    timeout: Optional[float] = None
    metadata: Optional[dict] = Field(default=None, description="可选元数据: user_intent, retrieved_skills 等")
```

OpenClaw 端把 intent 放进 `metadata` 一起 POST 即可。**零侵入**。

#### 🛠️ `agent_server/execution_recorder.py` — 加 hook point

**改动**：在 `stop()` 返回 metadata 后，增加一个回调（不破坏现有 API）

```python
def stop(self, on_complete: Optional[Callable[[Dict[str], Dict[str]], None]] = None) -> Dict[str, Any]:
    metadata = self._original_stop()  # 原 stop 逻辑
    if on_complete is not None:
        try:
            on_complete(self._execution_id, metadata)
        except Exception as e:
            logger.warning(f"ExecutionRecorder callback failed: {e}")
    return metadata
```

> **更稳的方案**：不动 `execution_recorder.py`，**在 `code_routes.py` 中直接 import `state_log.jsonl` 路径**，由 `TrajectoryRecorder` 独立完成。

#### 🛠️ OpenClaw Agent 端 — Prompt 注入

**位置**：`ROBOT/abotclaw_nv/robot_client/unitree_G1/openclaw/`（或项目内的 LLM 调度器）

**改动**：在向 LLM 发请求前，先调 `/evoskill/search` 拿 Top-K skill，注入到 system prompt 或 user prompt。

```python
# 在 LLM 调用的地方 (伪代码)
def llm_generate_code(self, intent: str) -> str:
    # 1. 检索
    skills = requests.get(
        f"http://localhost:8888/evoskill/search",
        params={"q": intent, "k": 3}
    ).json()["skills"]
    
    # 2. 注入 prompt
    skill_block = format_skills_for_prompt(skills)
    prompt = SKILL_AWARE_PROMPT_TEMPLATE.format(
        skills=skill_block,
        task=intent,
    )
    
    # 3. LLM 生成
    code = self.llm.complete(prompt)
    
    # 4. 把 intent 嵌入 metadata 一起提交
    body = {
        "code": code,
        "metadata": {"user_intent": intent, "retrieved_skills": [s["skill_id"] for s in skills]},
    }
    resp = requests.post(f"{BASE}/code/execute", json=body, headers=...)
    return resp
```

---

## 6. 评测任务与基线设计

### 6.1 5 类长期任务（共 100 个 instance）

| # | 任务类型 | 例子 | 涉及模块 | 难度 | 设计意图 |
|---|---------|------|---------|------|---------|
| **T1** | 单物体抓取 | "把客厅桌上的红杯子拿过来" | nav + yolo + grasp | 易 | baseline 校准 |
| **T2** | 多物体整理 | "把厨房桌上 3 个瓶子全放到客厅" | memory + nav + grasp ×3 | 中 | 测记忆与持续性 |
| **T3** | 找人送物 | "找到张老师把钥匙递给他" | face + nav + hand-off | 中 | 测跨子系统协同 |
| **T4** | 区域扫描记忆 | "扫描整个房间并记下所有东西" | yolo + face + memory | 中 | 测主动建图 |
| **T5** | 跨日任务 | "昨天放在客厅桌的红杯子还在吗？" | memory + yolo + tts | 难 | 测长期记忆+对话 |

每类 20 个 instance，共 100 个。

### 6.2 Baseline 对比

| 方法 | 描述 | 期望表现 | 论文卖点 |
|------|------|---------|---------|
| **ReAct** | 纯 LLM ReAct loop，无 Skill 库 | 成功率最低，token 最多 | baseline |
| **Reflexion** | ReAct + 失败反思（无持久化 Skill） | 中等，token 偏多 | 主流 baseline |
| **Voyager** | Skill 库 + 持续学习（移植到 G1） | 中等，库增长慢 | SOTA baseline |
| **Skill-LLM** | 手动 + 自动混合 skill 库 | 较强 | 强 baseline |
| **Ours (EvoSkill)** | 自动蒸馏 + 物理反馈 + 反例 | 期望最优 | 我们的方法 |

> 复现策略：Voyager / Skill-LLM 是开源的，**直接拉源码改造成"接 G1 API"**即可；Reflexion 是 prompt 技巧，**5 行代码改造**。

### 6.3 评测指标

| 指标 | 公式 | 含义 |
|------|------|------|
| **SR** | 成功 trials / 总 trials | 任务成功率（首要） |
| **AR** | 平均回合数 | 效率 |
| **Tokens** | LLM 平均 token | 成本 |
| **FSR** | 首次成功率 | 冷启动能力 |
| **LR-SR** | 累积 50 任务后成功率 | 长期学习能力 |
| **Skill Growth** | 库内 skill 数随时间增长 | 自进化能力 |

### 6.4 统计要求（ICRA 审稿人偏好）

- 每个 task instance **至少 5 次重复**（共 500 trials）
- 报告 **均值 ± 标准差**
- 用 **paired t-test** 或 **Wilcoxon** 检验显著性
- 跑 3 个 random seeds

---

## 7. 实验计划与消融方案

### 7.1 主实验

| 实验 | 对比 | 输出 |
|------|------|------|
| **EXP-1** | EvoSkill vs ReAct vs Reflexion vs Voyager vs Skill-LLM | 主表（SR/AR/Tokens） |
| **EXP-2** | 5 类任务分别报告 | 按任务类型分桶表 |
| **EXP-3** | 长期学习曲线（第 1/10/30/50 任务后 SR） | 增长曲线图 |

### 7.2 消融实验

| 消融 ID | 关闭模块 | 目的 |
|--------|---------|------|
| **A1** | 无检索（直接 LLM 推理） | 证明 Skill 检索价值 |
| **A2** | 纯向量检索（无多因子） | 证明多因子打分价值 |
| **A3** | 无反例 Skill | 证明 anti-pattern 价值 |
| **A4** | 无去重 | 证明去重价值 |
| **A5** | 无成功率重评 | 证明时间衰减价值 |
| **A6** | 关闭 LLM 蒸馏（仅人工 skill） | 证明自动蒸馏价值 |

### 7.3 用户研究（加分项）

5-10 名测试者，每人发布 20 条自然语言指令，比较：
- EvoSkill vs Reflexion 的 SR
- 用户主观满意度（1-5 分）
- 任务完成时间

---

## 8. 9 周冲刺时间表（ICRA 2027）

> **目标：2026-09-15 截稿前 1 天提交。倒推 9 周 = 2026-07-13 启动。**
> **今天 2026-06-19，离启动还有约 3.5 周准备期。**

### 阶段 0：准备期（2026-06-19 ~ 2026-07-12，~3.5 周）

| 周 | 任务 | 交付物 |
|----|------|--------|
| W0.1 | 项目结构梳理 + 团队分工确认 | 文档 |
| W0.2 | LLM API 接入测试（OpenAI/Anthropic/Qwen） | 配置文件 |
| W0.3 | 评测任务脚本 T1-T5 各写 1 个 prototype | 5 个 demo |
| W0.4 | 招募 2-3 名测试者（用户研究） | 测试者名单 |

### 阶段 1：MVP（W1-W2，2026-07-13 ~ 2026-07-26）

| 周 | 任务 | 交付物 |
|----|------|--------|
| W1.1 | 完成 `trajectory_store.py` + `trajectory_recorder.py` | SQLite 可写 |
| W1.2 | `code_routes.py` 加钩子（intent 注入 + 落库） | 跑 5 次任务可查 |
| W1.3 | `skill_library.py` + `skill_retriever.py` 基础版 | 手动录入 5 个 skill 可检索 |
| W1.4 | OpenClaw 端改造 prompt 注入 skill | 真机跑 T1 任务对比 ReAct |
| W2.1 | 跑 30 条 T1 任务，对比 SR | 数据 |
| W2.2 | 写 `distiller.py` 基础版 | 可手动触发 |
| W2.3 | 真实数据蒸馏 1 次，验证入库 | skills.json 增加新条目 |
| W2.4 | 录视频 demo v1 | 1 分钟视频 |

### 阶段 2：闭环（W3-W4，2026-07-27 ~ 2026-08-09）

| 周 | 任务 | 交付物 |
|----|------|--------|
| W3.1 | 蒸馏自动化（每 5 条触发） | 无人值守运行 |
| W3.2 | 反例 Skill 自动识别 | 失败 case 沉淀 |
| W3.3 | T2 / T3 任务跑通 | 30 trials each |
| W3.4 | Skill 库去重 + 成功率重评 | evaluator.py 完成 |
| W4.1 | T4 / T5 任务跑通 | 30 trials each |
| W4.2 | ReAct / Reflexion baseline 复现 | baseline 可对比 |
| W4.3 | Voyager / Skill-LLM baseline 移植 | baseline 可对比 |
| W4.4 | 5 类任务全跑一遍 | 100 trials |

### 阶段 3：实验（W5-W6，2026-08-10 ~ 2026-08-23）

| 周 | 任务 | 交付物 |
|----|------|--------|
| W5.1 | EXP-1 主实验（5 方法 × 100 trials） | 主表数据 |
| W5.2 | EXP-2 分任务表 | 表 |
| W5.3 | EXP-3 学习曲线 | 图 |
| W5.4 | 消融 A1-A3（无检索/纯向量/无反例） | 消融表 |
| W6.1 | 消融 A4-A6 + 用户研究 | 消融表 + 调研问卷 |
| W6.2 | 统计分析（t-test / Wilcoxon） | 显著性标记 |
| W6.3 | 画全部图表（matplotlib / seaborn） | 8-10 张图 |
| W6.4 | 录完整 demo 视频（含失败案例） | 3-5 分钟视频 |

### 阶段 4：写作（W7-W8，2026-08-24 ~ 2026-09-06）

| 周 | 任务 | 交付物 |
|----|------|--------|
| W7.1 | 完成 Method 章节（含系统架构图） | draft §III |
| W7.2 | 完成 Experiments 章节 | draft §IV-V |
| W7.3 | 完成 Intro + Related Work | draft §I-II |
| W7.4 | 内部 review 第 1 轮（3 个 reviewer 模拟） | comment 列表 |
| W8.1 | 根据 review 改稿 | draft v2 |
| W8.2 | 补实验（针对 review 提出的补充实验） | 数据补充 |
| W8.3 | 写 Discussion + Conclusion | 全文完成 |
| W8.4 | 内部 review 第 2 轮 | final draft |

### 阶段 5：投稿（W9，2026-09-07 ~ 2026-09-15）

| 天 | 任务 |
|----|------|
| D1-2 | 全文精修 + LaTeX 排版 |
| D3 | 录视频 + 压缩 |
| D4 | 写 Cover Letter + 选 track |
| D5 | 内部最终 review |
| D6 | 提交到 PaperPlaza |
| D7 | 备份 + 通知团队 🎉 |

### 关键里程碑（Milestone）

| 里程碑 | 日期 | 检查点 |
|--------|------|--------|
| **M1**: MVP 跑通 | 2026-07-26 | T1 任务 EvoSkill SR > ReAct |
| **M2**: 闭环自进化 | 2026-08-09 | 自动蒸馏成功入库 |
| **M3**: 全任务可跑 | 2026-08-09 | 5 类任务都有数据 |
| **M4**: 主表数据齐 | 2026-08-23 | 5 方法 × 100 trials |
| **M5**: 写作完成 | 2026-09-06 | 8 页 + 补充 |
| **M6**: 投稿 | 2026-09-15 | PaperPlaza 提交 |

---

## 9. 里程碑与交付物

### 9.1 代码交付物

```
abotclaw_robot_final/
├── evoskill_lib/                  # 新增：Skill 库数据
│   ├── skills.json
│   ├── embeddings.npy
│   ├── trajectories.db
│   └── distill_log.jsonl
├── evoskill/                      # 新增：核心代码（可独立 pip 包）
│   ├── __init__.py
│   ├── config.py
│   ├── skill_library.py
│   ├── skill_retriever.py
│   ├── trajectory_store.py
│   ├── trajectory_recorder.py
│   ├── distiller.py
│   ├── evaluator.py
│   └── prompts/
├── agent_server/
│   ├── evoskill_routes.py         # 新增
│   └── routes/code_routes.py      # 微改（加 hook）
├── openclaw/                      # 微改
│   └── evoskill_client.py         # 新增（OpenClaw 端调用库）
├── experiments/                   # 新增：实验脚本
│   ├── tasks/                     # 5 类任务定义
│   ├── baselines/                 # ReAct/Reflexion/Voyager 复现
│   ├── run_main.py
│   ├── run_ablation.py
│   └── analyze.py
└── docs/
    ├── EvoSkill_Plan.md           # 本文档
    ├── EvoSkill_Architecture.png  # 系统架构图
    └── EvoSkill_Demo.mp4          # 演示视频
```

### 9.2 论文交付物

- **正文**：8 页 PDF（ICRA 模板）
- **补充材料**：失败案例 + 完整 prompt + 参数表
- **视频**：3-5 分钟 demo
- **开源仓库**：写好 README + LICENSE + 复现说明

### 9.3 数据交付物

- `evoskill_lib/trajectories.db`（500+ trials）
- 5 类任务定义文件（YAML）
- 评测结果 CSV
- 图表源文件（matplotlib）

---

## 10. 风险清单与应对预案

| 风险 | 等级 | 影响 | 应对预案 |
|------|------|------|---------|
| **R1**: G1 实机时间不足 | 🔴 高 | 数据不够 | 与学校/实验室协调 + 周末 + 借场地 |
| **R2**: 蒸馏质量差（LLM 生成垃圾） | 🟡 中 | Skill 库被污染 | 严格 schema 校验 + 沙箱 dry-run + 成功率淘汰 |
| **R3**: baseline 跑不过 | 🟡 中 | 对比不公平 | 严格用同一 LLM、同一 prompt、同一任务 |
| **R4**: 写作时间不够 | 🟡 中 | 投稿质量差 | W7 启动写作 + 多轮内部 review |
| **R5**: ICRA 拒稿 | 🟢 低 | 时间损失 | 自动改投 IROS 2027（2027-03 截稿） |
| **R6**: Skill 库爆炸 | 🟢 低 | 检索变慢 | 500 条上限 + LRU + 成功率淘汰 |
| **R7**: LLM API 不稳定 | 🟢 低 | 实验中断 | 多 key 备份 + 切本地 Qwen3 |
| **R8**: 用户研究人数不足 | 🟢 低 | 弱化 1 个实验 | 至少 3 人也能发表 |
| **R9**: sim2real 争议 | 🟢 低 | 审稿人质疑 | 强调"全真机" + 给出每个真机 trial 视频 |
| **R10**: 审稿人认为创新不足 | 🔴 高 | 拒稿 | 强调"反例 Skill + 真机 + 5 类任务"三重创新 |

---

## 11. 论文写作大纲（ICRA 8 页）

### Title
> **EvoSkill: Self-Evolving Skill Library Distillation for Long-Horizon Humanoid Robot Tasks**

### Authors
- 第一作者（你）
- 导师
- 合作者（如果有）

### Abstract（200 词）
1. 背景：LLM + 人形机器人的局限
2. 痛点：缺经验复用 + 缺失败反思
3. 方法：EvoSkill（轨迹→蒸馏→检索→执行）
4. 实验：G1 + 5 类任务 + 5 个 baseline
5. 结果：SR +18%，回合 -42%，token -55%
6. 贡献：3 个

### §I. Introduction
- 1 张 G1 工作照
- 三段式：痛点 → 思路 → 贡献列表
- 引用：RT-2、VoxPoser、OK-Robot、Voyager、Reflexion

### §II. Related Work
- A. LLM-based Robot Agents（RT-2、PaLM-E、OpenVLA、π₀）
- B. Skill Library & Code as Actions（Code-as-Policies、Voyager、Skill-LLM、Eureka）
- C. Long-Horizon Task Planning（SayCan、Reflexion、Generative Agents）
- D. Humanoid Robots（Figure 01、Optimus、Unitree G1、H1）

### §III. Method
- A. System Overview（大架构图）
- B. Trajectory Recording（SQLite schema + 钩子设计）
- C. Skill Distillation（LLM prompt + 去重）
- D. Skill Retrieval（多因子打分 + 反例）
- E. Skill Composition（LLM 决策 A/B/C）

### §IV. Experimental Setup
- A. Hardware: Unitree G1
- B. Software Stack: OpenClaw + Agent Server + PC Services
- C. 5 Long-Horizon Tasks（详细说明）
- D. Baselines: ReAct / Reflexion / Voyager / Skill-LLM
- E. Metrics: SR / AR / Tokens / FSR / LR-SR
- F. Implementation Details（超参 + LLM 选型）

### §V. Results
- A. Main Results（主表）
- B. Per-Task Breakdown（分任务表）
- C. Learning Curve（学习曲线图）
- D. Ablation Studies（消融表）
- E. Skill Library Analysis（库增长 + 反例占比）
- F. Failure Case Analysis（分类 + 反思）
- G. User Study（5-10 人测试）

### §VI. Discussion
- Limitations（必填：诚实写）
- Broader Impacts
- Future Work

### §VII. Conclusion
- 1 段总结 + 1 段展望

### 附录 / Supplementary
- 失败案例视频
- 完整 prompt 模板
- 超参搜索表
- Skill 库 dump 样例

---

## 12. 团队分工建议

> **假设 3 人团队（最小可执行）**

| 角色 | 占比 | 任务 |
|------|------|------|
| **PM / 算法**（你） | 50% | 架构 + LLM 蒸馏 + 评测 + 写作 |
| **机器人工程师** | 30% | G1 实机采集 + baseline 复现 + 视频 |
| **写手 / 实验分析** | 20% | 跑实验 + 画图 + 润色 |

> **2 人也可**：PM + 机器人工程师。**1 人**也能完成，但时间拉长到 12-14 周（投稿 IROS 2027）。

---

## 13. 附录：依赖安装、命令速查

### 13.1 新增 Python 依赖

```bash
pip install faiss-cpu==1.8.0
pip install sentence-transformers==3.0.1
pip install scikit-learn==1.5.0
pip install openai==1.40.0  # 或 anthropic
# 可选: 用于 embedding 和 LLM 调用
```

### 13.2 启动 EvoSkill 后台服务（与现有系统并行）

```bash
# 1. 启动 Agent Server（已有）
cd ROBOT/abotclaw_nv/robot_client/unitree_G1/agent_server/
python main.py  # 端口 8888

# 2. 启动 PC 感知服务（已有）
cd PC/Services/
./start_services.sh

# 3. EvoSkill 无独立服务（作为 Agent Server 子模块）
# 通过 /evoskill/* API 调用
```

### 13.3 手动触发蒸馏

```bash
curl -X POST http://localhost:8888/evoskill/distill
```

### 13.4 查询库状态

```bash
curl http://localhost:8888/evoskill/stats | jq
```

### 13.5 跑评测任务

```bash
cd experiments/
python run_main.py --task T1 --method evoskill --trials 10
python run_baseline.py --task T1 --method react --trials 10
python analyze.py --output results/main_table.csv
```

---

## 附：开始前的自检清单

- [ ] G1 当前能否稳定执行"导航 + 抓取"基本任务？
- [ ] LLM API（OpenAI / Anthropic / Qwen3）已经可用？
- [ ] 项目 `code_routes.py` 是否能记录 `state_log.jsonl`？
- [ ] 团队有 2-3 名成员可分工？
- [ ] 有每周 ≥ 20 小时 G1 实机时间？
- [ ] 已经联系好导师/合作者？

> **如果以上 5/6 答 "是"，可以启动。否则先解决前置依赖。**

---

## 致谢 / 参考

- **OpenClaw** 项目（本项目基座）
- **Voyager** (Wang et al., 2023) — Skill Library 思路
- **Reflexion** (Shinn et al., 2023) — 失败反思
- **Skill-LLM** (Zhou et al., 2024) — Skill 描述生成
- **Code-as-Policies** (Liang et al., 2023) — LLM 生成代码
- **OK-Robot** (Ren et al., 2024) — 真机 LLM 集成

---

**文档结束。如需任何章节展开/代码细化/实验设计细化，告诉我具体方向即可。**
