# AstroPlan

**面向太空实验室的科学任务规划与重规划智能体**

- Python 3.10+
- 层次化动态智能体树
- 物理联锁 FSM
- 人机协同 HITL
- 调度器集成接口（`IPlannerService` / `ISchedulerAdapter`）

---

## 功能概览

- **层级 LLM 智能体树**：`AgentNode`（局部推理）+ `ControlFlowNode`（Sequence / Fallback / Parallel）+ `SubTreeReplanner`（局部重规划）
- **物理联锁引擎**：基于 FSM 的安全门，读取 `config/fsm_rules.yaml`，拦截非法状态转换
- **遥测总线**：带时间戳处理乱序数据，超阈值自动触发重规划
- **工作记忆**：强类型 `SharedContext`，全局单一状态源，消除深层节点幻觉
- **MCP 技能注册表**：`@mcp_tool` 装饰器注册执行技能，SpaceWire 带宽感知压缩
- **地面指令接收 / HITL 挂起**：抢占式任务 + 不可逆技能（`execute_main_forming` 等）执行前强制人工审核；`_execute_action()` 拒绝未通过 HITL 的技能
- **被动遥测监控**：`execute_standalone()` 并发运行 `_passive_monitor` 协程，阈值违规时立即中止调度器并触发重规划
- **Web 监控**：WebSocket SSE 实时推送计划树视图；由 `config.yaml:web_monitor.enabled` 控制开关
- **调度器接口**：`IPlannerService` + `ISchedulerAdapter` 定义 AstroPlan ↔ agentos_scheduler 的显式边界
- **独立评测（DAG 级）**：`MockScheduler` 无需真实调度器即可运行完整规划–执行–重规划循环
- **基准评测（环境级）**：`AstroPlanEvaluator` 对接 ALFRED / WAH-NL 模拟器，可插拔 LLM 后端（Ollama / HuggingFace / Anthropic）

---

## 目录结构

```
AstroPlan/
├── config/
│   ├── config.yaml              # LLM / MCP / 编排器全局配置
│   └── fsm_rules.yaml           # 物理联锁有限状态机规则表
├── requirements.txt
├── main.py                      # 独立演示入口 (asyncio.run)
├── tests/
│   └── unit/                    # 10 个单元测试文件 (pytest tests/unit/ -v)
└── src/
    ├── types.py                 # 全部强类型数据类（禁止裸 dict 跨边界传递）
    │
    ├── planner.py               # AstroPlan — IPlannerService 实现（公开 API）
    │
    ├── interfaces/              # 调度器交互显式接口
    │   ├── __init__.py          #   统一 re-export
    │   ├── planner_service.py   #   IPlannerService Protocol
    │   └── scheduler_adapter.py #   ISchedulerAdapter, IStatusReporter, ExecutionSnapshot
    │
    ├── evaluation/              # 评测工具
    │   ├── __init__.py
    │   ├── mock_scheduler.py    #   MockScheduler — ISchedulerAdapter 的本地模拟实现
    │   ├── evaluator.py         #   AstroPlanEvaluator — ALFRED / WAH-NL 端到端评测
    │   └── environments/
    │       ├── alfred_adapter.py #  ThorConnector 适配器（可选 ai2thor 依赖）
    │       └── wah_adapter.py   #   WahUnityEnv 适配器（可选 VirtualHome 依赖）
    │
    ├── core/
    │   ├── config_loader.py     # load_config() → AppConfig
    │   ├── mcp_registry.py      # MCPRegistry + @mcp_tool 装饰器
    │   └── environment.py       # LaboratoryEnvironment（含 plan_mode 标志）
    ├── physics/
    │   └── interlock_engine.py  # InterlockEngine (FSM + 联锁校验)
    ├── execution/
    │   ├── task_ingestor.py     # TaskDataset.parse_requirements()
    │   ├── telemetry_bus.py     # TelemetryBus (流解析 / 阈值检测)
    │   └── hardware_executor.py # HardwareExecutor (TransactionID 异步)
    ├── memory/
    │   ├── working_memory.py    # WorkingMemory → SharedContext
    │   ├── milestone_engine.py  # MilestoneEngine (4-tuple 离线索引 / BM25 检索 / FSM 过滤)
    │   └── skill_library.py     # SkillLibrary (历史执行技能模式提取 / JSON 持久化)
    ├── control/
    │   ├── dag_builder.py       # DAGBuilder（含控制流感知 API + to_plan_response()）
    │   └── output_controller.py # OutputController (序列化 / 树视图格式化)
    ├── cognition/
    │   ├── runnable.py          # RunnableNode Protocol（解耦 AgentNode ↔ ControlFlowNode）
    │   ├── agent_node.py        # AgentNode（plan_mode 感知）
    │   ├── control_flow.py      # ControlFlowNode（Sequence / Fallback / Parallel）
    │   ├── replanner.py         # SubTreeReplanner
    │   ├── latency_observer.py  # LatencyObserver
    │   └── llm_backends.py      # LLM 后端抽象层（Ollama / HuggingFace / Anthropic）
    └── application/
        ├── ground_command_receiver.py
        ├── hitl_operator.py
        └── web_monitor.py       # WebMonitor（实现 IStatusReporter）
```
---

## 调度器集成接口

### IPlannerService（`src/interfaces/planner_service.py`）

AstroPlan 对外暴露的服务接口。agentos_scheduler 通过此接口调用规划器。

```python
from src.interfaces import IPlannerService

class AstroPlan:          # 位于 src/planner.py，满足 IPlannerService Protocol
    async def plan(self, request: PlanRequest) -> PlanResponse: ...
    async def execute_standalone(self, mission: str, *, scheduler=None, reporter=None) -> ExecutionResult: ...
```

**交互模型（拉取式）**

```
Scheduler ──POST /planner/plan──▶ AstroPlan.plan(PlanRequest)
                                        │
                               ◀── PlanResponse ──
```

AstroPlan 在 `plan()` 调用期间不主动回调调度器；监控钩子由可选的 `IStatusReporter` 处理。

### ISchedulerAdapter（`src/interfaces/scheduler_adapter.py`）

AstroPlan 在独立模式下调用的执行环境接口。集成模式由真实调度器适配器实现，独立模式由 `MockScheduler` 实现。

```python
class ISchedulerAdapter(Protocol):
    async def submit_plan(self, response: PlanResponse) -> None: ...
    async def get_execution_snapshot(self, revision_id: str) -> ExecutionSnapshot: ...
    async def await_terminal_event(self) -> ExecutionSnapshot: ...
```

### IStatusReporter（`src/interfaces/scheduler_adapter.py`）

可选监控钩子，AstroPlan 在规划关键事件时调用。`WebMonitor` 可实现此接口接收 SSE 推送。

```python
class IStatusReporter(Protocol):
    async def on_plan_generated(self, response: PlanResponse) -> None: ...
    async def on_replan_triggered(self, failed_lineage: str, current_revision_id: str) -> None: ...
    async def on_mission_completed(self, result: ExecutionResult) -> None: ...
```

---

## 核心数据类型（`src/types.py`）

| 类型 | 用途 |
|---|---|
| `PlanNode` | 规划 DAG 的一个节点（含 `lineage_id` / `required_roles` / `tool_hints` / `interruptible`） |
| `PlanRequest` | POST /planner/plan 请求体（初次规划或重规划快照） |
| `PlanResponse` | AstroPlan 返回的完整 DAG（`revision_id` + `nodes` + `edges`） |
| `ExecutionNodeRef` | 节点执行状态引用（Scheduler → AstroPlan，用于重规划） |
| `NodeStatus` | 节点生命周期枚举：`PENDING / RUNNING / COMPLETED / FAILED / SKIPPED` |
| `ExecutionSnapshot` | `ISchedulerAdapter` 返回的执行状态快照 |
| `SharedContext` | 智能体树内全局观察状态（单一数据源） |
| `AgentDecision` | AgentNode 输出（Think / Act / Expand） |
| `EventTriggerSignal` | 重规划触发信号（统一来源：地面指令 / 遥测 / HITL） |
| `ExecutionResult` | 任务最终结果（status / steps / log） |
| `Milestone` | 4-tuple 里程碑（`task_vector` + `state_description` + `trajectory` + `constraints`） |
| `AtomicSkillRecord` | 轨迹片段中的单步技能记录（强类型，替代裸 dict） |
| `TaskVector` | 任务向量（BM25 关键词 + 可选稠密向量） |
| `MilestoneStateDescription` | 里程碑状态描述（FSM 状态 + 已完成技能 + 描述） |
| `TrajectoryFragment` | 轨迹片段（有序 `AtomicSkillRecord` 列表 + 成功率 + 观测次数） |
| `PhysicalConstraints` | 物理约束（FSM 前置/后置条件 + 安全阈值） |

---

## 执行流程（MVP）

```
main.py
  │
  ├─ InterlockEngine.from_yaml()      物理联锁 FSM
  ├─ MCPRegistry + _register_demo_skills()   技能注册（含真实副作用）
  ├─ AstroPlan(cfg, interlock, registry)     规划器
  └─ MockScheduler(registry)                 本地执行模拟器
       │
       └─ planner.execute_standalone(mission)
              │
              ▼
         AstroPlan.plan(PlanRequest)         plan_mode=True（干跑）
              │  AgentNode 树决策
              │  DAGBuilder 累积动作
              ▼
         PlanResponse(revision_id, nodes, edges)
              │
              ▼
         MockScheduler.submit_plan()         接收 DAG
              │
              ▼
         MockScheduler.await_terminal_event()  ←─┐
              │  按拓扑顺序执行前沿节点            │ 并发
              │  registry.call(skill, params) → 真实副作用
              │                               _passive_monitor()
              │                               每1s采样遥测阈值
              │                               违规→request_abort()─┘
              ├── 全部成功 → ExecutionResult(completed)
              │
              └── 某节点失败 → ExecutionSnapshot(failed=[...])
                       │
                       └─ AstroPlan.plan(replan PlanRequest)  → 新 revision
                                │
                                └─ ... 循环直至成功或超出 max_replan_depth
```

## plan_mode 标志

`LaboratoryEnvironment.plan_mode: bool`，由 `AstroPlan` 在每次 `plan()` 调用前设置：

| 模式 | 值 | `AgentNode._execute_action()` 行为 |
|---|---|---|
| 规划（干跑） | `True` | 跳过 MCP/HW 派发，仅调用 `dag.register_action()`，返回 `True` |
| 执行（MockScheduler） | `False`（默认） | HITL 检查 → Interlock 校验 → MockScheduler 调用 `registry.call(skill, params)` |

> **注意**：执行阶段由 MockScheduler 负责，不经过 `LaboratoryEnvironment.run()`。`plan_mode=False` 路径现包含 HITL 挂起门：`_NON_INTERRUPTIBLE_SKILLS` 中的技能须经 `HITLSuspensionOperator.suspend()` 批准后方可派发。

---

## DAGBuilder 控制流感知 API

`DAGBuilder` 新增控制流上下文支持，以正确编码 Parallel / Fallback 语义：

```python
dag = DAGBuilder(revision_id="rev_001", mission_id="mission_abc")

# Sequence（默认）：线性链
dag.set_context("sequence")
dag.register_action("activate_pump", {}, "fluid_pump", lineage_id="abc123")

# Parallel：扇出，共享前驱
dag.set_context("parallel", parallel_predecessor="act_1")
dag.register_action("heat_to_40",     {}, "thermal", lineage_id="def456")
dag.register_action("activate_camera", {}, "camera", lineage_id="ghi789")

# Fallback：仅注册第一备选
dag.set_context("fallback")
dag.register_action("primary_skill", {}, "sys_a", lineage_id="jkl012")
dag.register_action("backup_skill",  {}, "sys_a", lineage_id="mno345")  # 被丢弃

# 生成 PlanResponse
response = dag.to_plan_response()   # 含 validate()（拓扑排序）
```

---

## 独立评测

无需真实调度器或 Worker 即可运行完整规划–执行–重规划循环：

```python
from src.planner import AstroPlan
from src.evaluation import MockScheduler
from src.core.config_loader import load_config
from src.physics.interlock_engine import InterlockEngine
from src.core.mcp_registry import MCPRegistry

cfg      = load_config()
interlock = InterlockEngine.from_yaml("config/fsm_rules.yaml", cfg.lab_id)
registry = MCPRegistry()
planner  = AstroPlan(cfg, interlock, registry)
# 所有协作对象在构造时立即初始化；配置错误此时即抛出。

# 无故障基线
result = await planner.execute_standalone("进行流体实验...")

# 含人工故障注入（30% 概率）的压力测试
scheduler = MockScheduler(registry, failure_rate=0.3, seed=42)
result = await planner.execute_standalone("进行流体实验...", scheduler=scheduler)

metrics = {
    "status":           result.status,
    "total_steps":      result.total_steps,
    "replan_count":     scheduler.total_failures,
    "nodes_executed":   scheduler.total_nodes_executed,
}
```

---

## 集成工作流（调度器 ↔ AstroPlan）

```
1. Scheduler 收到任务指令

2. Scheduler 调用 AstroPlan：
   POST /planner/plan {mission_context: "...", current_revision_id: null, ...}
   → PlanResponse(revision_id="rev_001", nodes=[...], edges=[...])

3. Scheduler 将 PlanResponse 转换为内部 DAGTaskGraph 并提交执行

4. Workers 执行前沿节点；Scheduler 追踪 completed / running / failed

5. 节点失败时：
   Scheduler 再次调用 AstroPlan，附带执行状态快照：
   POST /planner/plan {
     mission_context: "...",
     current_revision_id: "rev_001",
     completed_nodes: [...],
     failed_nodes: [{node_id: "rev001_n3", lineage_id: "activate_camera", error: "..."}]
   }
   → PlanResponse(revision_id="rev_002", ...)  ← 冻结已完成节点，重建失败子树

6. Scheduler 用新 DAG 继续执行，直至全部完成
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置（可选）

编辑 `config/config.yaml`：

```yaml
llm:
  model: "claude-sonnet-4-6"
  api_key: "${ANTHROPIC_API_KEY}"
  use_mock: false   # 无 API Key 时自动回退内置规划逻辑
```

### 3. 运行单元测试

```bash
pytest tests/unit/ -v --tb=short          # 全部单元测试
pytest tests/unit/ -k "interruptible or passive" -v   # P1-A + P2 回归
```

### 5. 运行演示

```bash
# 默认 Fluid-Lab-Demo（mock 规划器，无 GPU 需求）
python main.py

# 指定实验室
python main.py --lab fiber-composite-lab
python main.py --lab microbio-sampling-lab

# ALFRED/WAH 兼容基准评测
python main.py --benchmark --lab fiber-composite-lab
python main.py --benchmark --lab all   # 全部三个实验室
```

**预期输出（Fluid-Lab-Demo，mock 规划器）：**

```
[Fluid-Lab-Demo] Mission: 进行流体实验：激活泵，加热至40°C，启动摄像头记录数据。

[AstroPlan] rev_001: 3 node(s), 2 edge(s)
[MockScheduler] Accepted rev_001: 3 node(s)
[MockScheduler] ✓ activate_pump  (lineage=<hash>)
[MockScheduler] ✓ heat_to_40     (lineage=<hash>)
[MockScheduler] ✓ activate_camera (lineage=<hash>)

[Fluid-Lab-Demo] 执行结果:
  status       : completed
  total_steps  : 3
  revisions    : ['rev_001']
  nodes run    : 3
  failures     : 0
```

### 6. 本地 LLM 推理（可选）

```

**验证中的模型：**

| 模型 | VRAM | 
|---|---|
| `Qwen/Qwen2.5-3B-Instruct` | ~6 GB | 
| `meta-llama/Llama-3.1-8B-Instruct` | ~16 GB (4-bit: ~5 GB) | 
| `google/gemma-4-E2B` | ~5 GB | 


---

## 扩展指南

**添加新技能：**

```python
@registry.mcp_tool
def centrifuge_spin(params: dict) -> dict:
    interlock.apply_action("centrifuge_spin")
    memory.update_subsystem_state("centrifuge", "SPINNING")
    return {"status": "ok", "rpm": params.get("rpm", 3000)}
```

**实现真实调度器适配器：**

```python
from src.interfaces import ISchedulerAdapter, ExecutionSnapshot
from src.types import PlanResponse

class AgentOSSchedulerAdapter:
    """Wraps agentos_scheduler HTTP API as ISchedulerAdapter."""

    async def submit_plan(self, response: PlanResponse) -> None:
        await http_client.post("/dag/submit", json=response_to_dict(response))

    async def get_execution_snapshot(self, revision_id: str) -> ExecutionSnapshot:
        data = await http_client.get(f"/dag/snapshot/{revision_id}")
        return parse_snapshot(data)

    async def await_terminal_event(self) -> ExecutionSnapshot:
        return await websocket_client.wait_for_terminal()
```

---

## 基准评测（ALFRED / WAH-NL）

`AstroPlanEvaluator` 将 AstroPlan 接入 ReAcTree 的标准评测环境，支持与 ReAcTree 基线直接比较。

### 评测架构

```
config/eval_config.yaml
        │
AstroPlanEvaluator
        ├── LLM 后端（src/cognition/llm_backends.py）
        │       ├── OllamaBackend    — 本地 Ollama REST（llama3.1:8b / qwen2.5:3b …）
        │       ├── HuggingFaceBackend — 本地 transformers pipeline
        │       └── AnthropicBackend — Claude API
        │
        └── 环境适配器（src/evaluation/environments/）
                ├── AlfredAdapter   — 包装 ReAcTree ThorConnector（ai2thor）
                └── WahAdapter      — 包装 ReAcTree WahUnityEnv（VirtualHome）
```

`EnvMCPBridge`（evaluator.py 内）替换 MCPRegistry，将所有 NL 技能调用路由至环境适配器，无需预注册 ALFRED / WAH 动态技能集。模拟器未安装时自动降级为 `_MockEnvAdapter`。

### 指标

| 指标 | ALFRED | WAH-NL | 说明 |
|---|---|---|---|
| `success_rate_pct` | ✓ | — | 任务完成率（%） |
| `avg_goal_success_rate` | — | ✓ | 目标条件满足率 |
| `avg_subgoal_success_rate` | — | ✓ | 子目标满足率（0–1） |
| `avg_replan_count` | ✓ | ✓ | 平均重规划次数 |
| `avg_tree_max_depth` | ✓ | ✓ | 平均树展开深度 |
| `lineage_stability` | ✓ | ✓ | lineage_id 跨 revision 稳定性（AstroPlan 特有） |

### 快速开始

```bash
# 1. 配置（编辑 config/eval_config.yaml 选择 backend 和 dataset）
#    默认：WAH-NL + Ollama llama3.1:8b

# 2. 运行（mock 模式，无需模拟器）
python -m src.evaluation.evaluator --config config/eval_config.yaml
# 在 eval_config.yaml 中设置 backend.backend: mock

# 3. 完整 WAH-NL 评测（需 VirtualHome + Ollama）
ollama serve &
python -m src.evaluation.evaluator --config config/eval_config.yaml
```

结果保存至 `outputs/eval/metrics.json` 和 `per_task.json`。

### 与 ReAcTree 的设计差异

| 维度 | ReAcTree | AstroPlan |
|---|---|---|
| env 注入方式 | 节点构造时绑定（有状态节点） | `EnvMCPBridge` 经 MCPRegistry 注入（节点无状态） |
| LLM 生成约束 | `guidance.select`（受限解码） | JSON 解析 + 降级（无 `guidance` 依赖） |
| 节点调用参数 | 6 个位置参数 | `NodeRunContext` + 2 个标量 |
| 指标命名 | `success_rate`, `goal_success_rate` | 同名，可直接对比 |

---

## 依赖

**核心（必须）**
```
pyyaml>=6.0
websockets>=11.0
```

**可选 — 真实 LLM**
```
anthropic>=0.20.0        # Anthropic Claude
transformers>=4.44.0     # HuggingFace 本地推理
accelerate>=0.34.0       # HF 多卡分布
bitsandbytes             # 8-bit 量化（可选）
```

**可选 — 基准评测环境**
```
ai2thor==2.1.0           # ALFRED 模拟器
# VirtualHome 通过 git submodule 安装：
# cd ReAcTree && git submodule update --init virtualhome && pip install -e virtualhome/
```

**可选 — 其他**
```
numpy>=1.24.0            # MilestoneEngine 向量检索
```

> Mock 规划器 + Mock 环境模式下仅需 `pyyaml`。

---

## 故障排查

| 症状 | 原因 | 解决方案 |
|---|---|---|
| `ValueError: model type 'gemma4' not recognized` | transformers 版本过旧 | `pip install -U transformers`（需 ≥ 4.51） |
| `[AstroPlan] rev_001: 0 node(s), 0 edge(s)` | LLM 返回 Think 或解析失败 | 查看 `[AgentNode]` 日志行；降低 temperature（0.1）；检查模型是否为 instruct 版本 |
| `[AgentNode] No JSON object in LLM response` | 模型未遵循 JSON 格式 | 确认使用 instruct 模型（非 base）；AstroPlan 会自动重试并回退 mock 规划器 |
| `CUDA out of memory` | 模型超出显存 | 设置 `load_in_4bit: true` 或使用更小的模型 |
| `ImportError: bitsandbytes` | 缺少量化包 | `pip install bitsandbytes>=0.43.0` |
| `GatedRepoError` (Gemma/Llama) | 需要 HF 许可证 | 在 huggingface.co 接受许可，然后 `huggingface-cli login` |
| benchmark GC=0 for all tasks | LLM 0 节点 + 无 mock 回退（旧版） | 升级至最新代码，mock 回退已内置 |
| `ControlFlowNode: Unknown control_type` | LLM 返回小写 "sequence" | 已修复：控制类型现自动归一化 |
