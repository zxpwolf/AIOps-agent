# TaskPlanner 架构文档

## 概述

TaskPlanner 是 AIOps Agent 的任务分解引擎，负责将用户的自然语言运维请求通过 LLM 分解为结构化的子任务列表，并构建 DAG 依赖图进行拓扑排序，供 Orchestrator 按层并行执行。

## 核心流程

```mermaid
flowchart TD
    A["用户自然语言请求"] --> B["decompose()"]

    subgraph decompose["TaskPlanner.decompose()"]
        B1["1. 构建 LLM Messages"] --> B2["2. LLM chat() 调用"]
        B2 --> B3["3. _parse_subtasks()"]
        B3 --> B4["4. _validate_skill_mapping()"]
    end

    B --> B1
    B4 --> C["TaskPlan 对象"]
    C --> D["topological_sort()"]
    D --> E["list of list of SubTask 分层结果"]
    E --> F["Orchestrator._execute_plan()"]
```

## 步骤详解

### 1. 构建 LLM Messages

```mermaid
flowchart LR
    subgraph Messages["LLM 输入消息列表"]
        M1["system: 角色指令\n你是 AIOps 任务分解助手"]
        M2["system: 上下文信息\nsession_id, resources"]
        M3["system: 可用技能列表\nname + capabilities"]
        M4["user: 原始请求"]
    end
    M1 --> M2 --> M3 --> M4
```

| 消息 | 来源 | 说明 |
|------|------|------|
| system prompt | 硬编码 | 指导 LLM 输出 JSON 格式的子任务列表 |
| 上下文 | `context` 参数 | 可选，包含 session_id、已识别的资源引用 |
| 可用技能 | `SkillRegistry.list_skills()` | 告知 LLM 当前可用的技能及其 capabilities |
| 用户请求 | `user_input` 参数 | 原始自然语言运维请求 |

### 2. LLM 调用（含自动降级）

```mermaid
flowchart TD
    LLM["LLMProviderFactory.chat()"]
    P1["Primary Provider\nqwen3-235b-a22b"]
    P2["Fallback Provider\nDemoProvider"]
    ERR["RuntimeError\n所有 Provider 不可用"]

    LLM --> P1
    P1 -->|成功| OUT["JSON 字符串"]
    P1 -->|失败| P2
    P2 -->|成功| OUT
    P2 -->|失败| ERR
```

### 3. _parse_subtasks() — JSON 解析

```mermaid
flowchart TD
    INPUT["LLM 输出文本"] --> CHECK{"包含代码块?"}

    CHECK -->|"包含 &#96;&#96;&#96;json"| EXTRACT1["提取代码块内容"]
    CHECK -->|"包含 &#96;&#96;&#96;"| EXTRACT2["提取代码块内容"]
    CHECK -->|否| RAW["直接解析"]

    EXTRACT1 --> PARSE["json.loads()"]
    EXTRACT2 --> PARSE
    RAW --> PARSE

    PARSE -->|成功| TYPE{"数据类型?"}
    PARSE -->|失败| EMPTY["返回空列表"]

    TYPE -->|list| BUILD["构建 SubTask 列表"]
    TYPE -->|"dict 含 sub_tasks"| UNWRAP1["解包 sub_tasks"]
    TYPE -->|"dict 含 tasks"| UNWRAP2["解包 tasks"]
    TYPE -->|"单个 dict"| WRAP["包装为列表"]

    UNWRAP1 --> BUILD
    UNWRAP2 --> BUILD
    WRAP --> BUILD

    BUILD --> RESULT["list of SubTask"]
```

**支持的 LLM 输出格式：**

| 格式 | 示例 | 处理方式 |
|------|------|----------|
| JSON 数组 | `[{"task_id":"t1",...}]` | 直接解析 |
| json 代码块 | `` ```json [...] ``` `` | 提取代码块内容 |
| 通用代码块 | `` ``` [...] ``` `` | 提取代码块内容 |
| sub_tasks 包装 | `{"sub_tasks":[...]}` | 解包 sub_tasks 字段 |
| tasks 包装 | `{"tasks":[...]}` | 解包 tasks 字段 |
| 单个 dict | `{"task_id":"t1",...}` | 包装为单元素列表 |
| 无效 JSON | 任意文本 | 返回空列表 |

### 4. _validate_skill_mapping() — 技能映射验证

```mermaid
flowchart TD
    TASKS["SubTask 列表"] --> LOOP{"遍历每个 SubTask"}

    LOOP --> CHECK["SkillRegistry.get_skill(skill_name)"]

    CHECK -->|"返回 SkillInstance"| OK["status = PENDING\n等待执行"]
    CHECK -->|"返回 None"| FAIL["status = FAILED\nerror = 技能未注册"]

    OK --> NEXT["下一个 SubTask"]
    FAIL --> NEXT
    NEXT --> LOOP
```

## topological_sort() — DAG 拓扑排序

```mermaid
flowchart TD
    PLAN["TaskPlan.sub_tasks"] --> BUILD["构建 DAG"]

    subgraph DAG["DAG 数据结构"]
        IN["in_degree: task_id → 入度"]
        DEP["dependents: task_id → 下游任务列表"]
        MAP["task_map: task_id → SubTask"]
    end

    BUILD --> IN
    BUILD --> DEP
    BUILD --> MAP

    IN --> BFS["BFS 分层"]

    subgraph BFS_DETAIL["BFS 拓扑排序"]
        L0["Level 0: in_degree=0 的任务"]
        L1["Level 1: 依赖 Level 0 的任务"]
        LN["Level N: 依赖 Level N-1 的任务"]
    end

    BFS --> L0 --> L1 --> LN

    LN --> RESULT["list of list of SubTask\n每层内可并行执行"]
```

## 数据流示例

```mermaid
sequenceDiagram
    participant User as 用户
    participant TP as TaskPlanner
    participant LLM as LLM Provider
    participant SR as SkillRegistry
    participant Orch as Orchestrator

    User->>TP: decompose("ECS CPU 100%, 查监控、排查、评估扩容")

    Note over TP: 1. 构建 Messages
    TP->>SR: list_skills()
    SR-->>TP: [monitoring, troubleshooting, change_management]

    Note over TP: 2. 调用 LLM
    TP->>LLM: chat(messages)
    LLM-->>TP: JSON 子任务列表

    Note over TP: 3. 解析 JSON
    TP->>TP: _parse_subtasks() → 3 个 SubTask

    Note over TP: 4. 验证技能映射
    TP->>SR: get_skill("monitoring") → OK
    TP->>SR: get_skill("troubleshooting") → OK
    TP->>SR: get_skill("change_management") → OK

    TP-->>Orch: TaskPlan(sub_tasks=[t1, t2, t3])

    Note over Orch: 5. 拓扑排序
    Orch->>TP: topological_sort(plan)
    TP-->>Orch: [[t1], [t2], [t3]]

    Note over Orch: 6. 按层执行
    Orch->>Orch: Level 0: gather(t1)
    Orch->>Orch: Level 1: gather(t2)
    Orch->>Orch: Level 2: gather(t3)
```

## SubTask 状态流转

```mermaid
stateDiagram-v2
    [*] --> PENDING: _parse_subtasks() 创建

    PENDING --> FAILED: _validate 技能未注册
    PENDING --> RUNNING: Orchestrator 开始执行

    RUNNING --> COMPLETED: 执行成功
    RUNNING --> FAILED: 执行异常
    RUNNING --> CANCELLED: 依赖任务失败

    FAILED --> PENDING: 允许重试

    COMPLETED --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
```

## 关键依赖

```mermaid
graph LR
    TP["TaskPlanner"]

    TP --> LLM["LLMProviderFactory"]
    LLM --> Qwen["QwenProvider\nqwen3-235b-a22b"]
    LLM --> Demo["DemoProvider\n关键词匹配"]
    LLM --> Claude["ClaudeProvider"]
    LLM --> GPT["GPTProvider"]

    TP --> SR["SkillRegistry"]
    SR --> S1["monitoring\n监控诊断"]
    SR --> S2["troubleshooting\n故障排查"]
    SR --> S3["change_management\n变更管理"]
```

## 核心接口

```python
class TaskPlanner:
    def __init__(self, llm_factory: LLMProviderFactory, skill_registry: SkillRegistry):
        ...

    async def decompose(self, user_input: str, context: dict = None) -> TaskPlan:
        """主入口: 自然语言 → TaskPlan"""

    def topological_sort(self, plan: TaskPlan) -> list[list[SubTask]]:
        """DAG 拓扑排序: TaskPlan → 分层可并行任务"""

    def _parse_subtasks(self, llm_output: str, plan_id: str) -> list[SubTask]:
        """解析 LLM 输出为 SubTask 列表"""

    async def _validate_skill_mapping(self, sub_tasks: list[SubTask]) -> list[SubTask]:
        """验证每个 SubTask 的 skill_name 是否已注册"""
```

## 核心数据模型

```python
class SubTask:
    task_id: str           # 唯一标识 (t1, t2, ...)
    skill_name: str        # 目标技能名称
    action: str            # 具体操作
    parameters: dict       # 操作参数
    dependencies: list     # 依赖的 task_id 列表
    status: TaskStatus     # PENDING / RUNNING / COMPLETED / FAILED / CANCELLED
    result: dict | None    # 执行结果
    error: str | None      # 错误信息

class TaskPlan:
    plan_id: str           # UUID
    user_request: str      # 原始用户请求
    sub_tasks: list        # SubTask 列表
    context: dict          # 上下文信息
    status: TaskStatus     # 整体状态
```
