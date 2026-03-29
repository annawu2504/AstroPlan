# AstroPlan

**面向太空实验室的科学任务规划与重规划智能体**

Python 3.10+ · ReAcTree 层级 LLM 智能体树 · 物理联锁 FSM · 人机协同 HITL

---

## 功能概览

- **层级 LLM 智能体树**：`AgentNode`（局部推理）+ `ControlFlowNode`（Sequence / Fallback / Parallel）+ `SubTreeReplanner`（局部重规划）
- **物理联锁引擎**：基于 FSM 的安全门，读取 `fsm_rules.yaml`，拦截非法状态转换
- **遥测总线**：带时间戳处理乱序数据，超阈值自动触发重规划
- **工作记忆**：强类型 `SharedContext`，全局单一状态源，消除深层节点幻觉
- **MCP 技能注册表**：`@mcp_tool` 装饰器注册执行技能，SpaceWire 带宽感知压缩
- **地面指令接收 / HITL 挂起**：支持抢占式任务与人工审核不可逆操作
- **Web 监控**：WebSocket SSE 实时推送计划树视图

---

## 目录结构

```
AstroPlan/
└── config/
    ├── config.yaml          # LLM / MCP / 编排器全局配置
├── fsm_rules.yaml           # 物理联锁有限状态机规则表
├── requirements.txt         # 依赖声明
├── main.py                  # 入口脚本 (asyncio.run)
└── src/
    ├── types.py             # 全部强类型数据类（禁止裸 dict 跨边界传递）
    ├── core/
    │   ├── config_loader.py # load_config() → AppConfig
    │   ├── mcp_registry.py  # MCPRegistry + @mcp_tool 装饰器
    │   └── environment.py   # LaboratoryEnvironment 顶层编排器
    ├── physics/
    │   └── interlock_engine.py  # InterlockEngine (FSM + 联锁校验)
    ├── execution/
    │   ├── task_ingestor.py     # TaskDataset.parse_requirements()
    │   ├── telemetry_bus.py     # TelemetryBus (流解析 / 阈值检测)
    │   └── hardware_executor.py # HardwareExecutor (TransactionID 异步)
    ├── memory/
    │   ├── working_memory.py    # WorkingMemory → SharedContext
    │   └── milestone_engine.py  # MilestoneEngine (离线索引 / 在线检索)
    ├── control/
    │   └── output_controller.py # OutputController (序列化 / 树视图格式化)
    ├── cognition/
    │   ├── agent_node.py        # AgentNode.execute_decision()
    │   ├── control_flow.py      # ControlFlowNode.evaluate_children()
    │   ├── replanner.py         # SubTreeReplanner.replan()
    │   └── latency_observer.py  # LatencyObserver (时延预估 / 抢占决策)
    └── application/
        ├── ground_command_receiver.py  # GroundCommandReceiver
        ├── hitl_operator.py            # HITLSuspensionOperator
        └── web_monitor.py              # WebMonitor (WebSocket SSE)
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置（可选）

编辑 `config.yaml` 调整 LLM 模型或设置 API Key：

```yaml
llm:
  model: "claude-sonnet-4-6"
  api_key: "${ANTHROPIC_API_KEY}"  # 或直接填写
  use_mock: false                   # true = 不调用 LLM，使用内置 Mock 规划器
```

不设置 API Key 时系统自动退回 Mock 规划器，无需任何外部服务即可运行。

### 3. 运行演示

```bash
python main.py
```

**预期输出（Fluid-Lab-Demo）：**

```
[Fluid-Lab-Demo] 遥测更新: fluid_pump None → IDLE
[Fluid-Lab-Demo] 遥测更新: thermal None → IDLE
[Fluid-Lab-Demo] 遥测更新: camera None → IDLE
[Fluid-Lab-Demo] Mission start: 进行流体实验：激活泵，加热至40°C，启动摄像头记录数据。
[Fluid-Lab-Demo] 🧠 Planner: 生成 3 步计划
    步骤 1: {'skill': 'activate_pump', 'params': {}}
    步骤 2: {'skill': 'heat_to_40', 'params': {}}
    步骤 3: {'skill': 'activate_camera', 'params': {}}
[Fluid-Lab-Demo] 🔄 子系统 'fluid_pump': IDLE → ACTIVE
[Fluid-Lab-Demo] 遥测更新: flow_rate 0.0 → 15.0
[Fluid-Lab-Demo] 🔄 子系统 'thermal': IDLE → HEATING
[Fluid-Lab-Demo] 遥测更新: temperature 20.0 → 40.0
[Fluid-Lab-Demo] 🔄 子系统 'camera': IDLE → ACTIVE
[Fluid-Lab-Demo] 遥测更新: camera_status OFF → RECORDING
执行结果: {'status': 'completed', 'total_steps': 3, 'execution_log': [...]}
```

---

## 核心数据流

```
raw_requirements
    │
    ▼
TaskDataset.parse_requirements()          # Layer 1: 任务解析
    │  nl_global_goal: str
    ▼
LaboratoryEnvironment.run()               # Layer 4: 编排器
    ├─ WorkingMemory.snapshot() → SharedContext
    ├─ AgentNode.execute_decision()        # LLM / Mock 规划
    │      ↳ _expand_to_steps()           # 迭代生成完整步骤序列
    └─ for step in plan:
           ├─ InterlockEngine.validate_action()   # 物理安全门
           ├─ MCPRegistry.call(skill)             # 执行技能
           ├─ WorkingMemory.update_*()            # 更新状态
           ├─ OutputController.format_tree()      # 序列化树视图
           └─ WebMonitor.broadcast()             # 推送监控 UI
```

---

## 重规划机制

| 触发条件 | 路径 |
|---|---|
| 步骤执行失败 | `EventTriggerSignal(source="step_failed")` → `SubTreeReplanner.replan()` |
| 遥测超阈值 | `TelemetryBus.check_threshold()` → `DeviationEvent` → replanner |
| 地面抢占指令 | `GroundCommandReceiver` → `LatencyObserver.should_preempt()` → 从头重规划 |
| 人工审核 | `HITLSuspensionOperator` 挂起执行流，等待 `InterventionSignal` |

---

## 联锁规则配置

`fsm_rules.yaml` 定义每个子系统的合法状态转换：

```yaml
subsystems:
  fluid_pump:
    initial_state: IDLE
    transitions:
      activate_pump:
        from: IDLE
        to: ACTIVE
        requires: {}           # 无前置依赖
```

新增实验设备只需在 `fsm_rules.yaml` 添加条目，无需修改代码。

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

**接入真实 LLM：**

在 `config/config.yaml` 中设置 `use_mock: false` 并提供 `ANTHROPIC_API_KEY`，`AgentNode` 自动切换到真实推理路径。

---

## 依赖

```
pyyaml>=6.0
websockets>=11.0
anthropic>=0.20.0   # 仅真实 LLM 路径需要
numpy>=1.24.0       # MilestoneEngine 向量检索
```

> Mock 规划器模式下仅需 `pyyaml` 和 `websockets`。
