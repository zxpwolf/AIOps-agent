# AIOps Agent 日志排查指南

## 日志配置

### 配置文件位置
`config/settings.yaml` - `observability.logging` 部分

### 日志级别
- **DEBUG**: 开发环境，输出所有详细信息（当前配置）
- **INFO**: 生产环境，输出关键操作和错误
- **WARNING**: 仅输出警告和错误
- **ERROR**: 仅输出错误

### 日志格式
当前使用 **JSON 格式**，包含以下字段：
```json
{
  "timestamp": "2026-05-17T01:26:58.045092+00:00",
  "level": "ERROR",
  "logger": "aiops_agent.core.orchestrator",
  "message": "任务执行失败: task_id=t1, skill=monitoring, action=query_metrics, error=...",
  "trace_id": "...",
  "span_id": "...",
  "exception": "..."
}
```

## 关键日志链路

### 1. 请求处理链路

当你发送一个请求时，会看到以下日志序列：

```
# 1. 请求开始
{"level": "INFO", "logger": "aiops_agent.core.orchestrator", 
 "message": "开始处理请求: session_id=xxx, user_id=xxx, input=有几台ECS服务器"}

# 2. 任务分解开始
{"level": "INFO", "logger": "aiops_agent.core.orchestrator", 
 "message": "开始任务分解: input=有几台ECS服务器"}

# 3. LLM 调用
{"level": "INFO", "logger": "aiops_agent.core.task_planner", 
 "message": "调用 LLM 进行任务分解: messages_count=3, user_input=有几台ECS服务器"}

# 4. LLM Provider 调用
{"level": "INFO", "logger": "aiops_agent.llm.provider", 
 "message": "调用主 LLM Provider: name=qwen, messages_count=3"}

# 5. Qwen API 请求
{"level": "INFO", "logger": "aiops_agent.llm.qwen", 
 "message": "Qwen API 请求: model=qwen3.6-plus, messages_count=3, max_tokens=9192, temperature=0.70"}

# 6. LLM 响应
{"level": "INFO", "logger": "aiops_agent.llm.qwen", 
 "message": "Qwen API 响应成功: model=qwen3.6-plus, input_tokens=120, output_tokens=256, finish_reason=stop"}

# 7. 任务分解完成
{"level": "INFO", "logger": "aiops_agent.core.task_planner", 
 "message": "LLM 响应收到: model=qwen3.6-plus, tokens={...}, content_length=256"}
{"level": "INFO", "logger": "aiops_agent.core.task_planner", 
 "message": "解析子任务成功: count=1"}
{"level": "INFO", "logger": "aiops_agent.core.orchestrator", 
 "message": "任务分解完成: plan_id=xxx, sub_tasks=1, tasks=[('t1', 'monitoring', 'query_metrics')]"}
```

### 2. 任务执行链路

```
# 1. 任务开始
{"level": "INFO", "logger": "aiops_agent.core.orchestrator", 
 "message": "开始校验任务: task_id=t1, skill=monitoring, action=query_metrics, parameters={...}"}

# 2. 校验结果
# 成功情况：
{"level": "INFO", "logger": "aiops_agent.core.orchestrator", 
 "message": "开始执行任务: task_id=t1, skill=monitoring, action=query_metrics"}

# 失败情况（校验失败）：
{"level": "ERROR", "logger": "aiops_agent.core.orchestrator", 
 "message": "任务校验失败: task_id=t1, skill=monitoring, errors=['缺少必填参数: action'], parameters={...}"}

# 3. 执行结果
# 成功：
{"level": "INFO", "logger": "aiops_agent.core.orchestrator", 
 "message": "任务执行成功: task_id=t1, skill=monitoring, result_keys=['status', 'data', ...]"}

# 失败：
{"level": "ERROR", "logger": "aiops_agent.core.orchestrator", 
 "message": "任务执行失败: task_id=t1, skill=monitoring, action=query_metrics, error=..., parameters={...}"}
```

## 常见错误排查

### 错误 1: LLM API 调用失败 (HTTP 401)

**日志特征：**
```json
{
  "level": "WARNING",
  "logger": "aiops_agent.llm.provider",
  "message": "主 LLM Provider 'qwen' 调用失败: 通义千问 API 调用失败: HTTP 401 - ..."
}
{
  "level": "ERROR",
  "logger": "aiops_agent.core.task_planner",
  "message": "LLM 任务分解失败: 所有 LLM Provider 均不可用"
}
```

**原因：** API Key 无效或过期

**解决方案：**
1. 检查 `config/settings.yaml` 中的 `llm.providers.qwen.api_key`
2. 确认 API Key 在阿里云百炼控制台有效
3. 重启 Agent 使配置生效

---

### 错误 2: 任务参数校验失败

**日志特征：**
```json
{
  "level": "ERROR",
  "logger": "aiops_agent.core.orchestrator",
  "message": "任务校验失败: task_id=t1, skill=monitoring, errors=['缺少必填参数: action'], parameters={'action': 'cloud_monitor_query'}"
}
```

**原因：** LLM 生成的 action 参数与技能期望的不匹配

**排查步骤：**
1. 查看 `parameters` 字段，确认 LLM 生成了什么 action
2. 查看技能定义中的 `capabilities` 列表（在 `main.py` 中）
3. 查看技能的 `execute()` 方法期望的 action 值
4. 检查 `task_planner.py` 中的系统提示词是否正确

**解决方案：**
确保 SkillDefinition.capabilities 与 Skill.execute() 中的 action 值一致

---

### 错误 3: 技能执行失败

**日志特征：**
```json
{
  "level": "ERROR",
  "logger": "aiops_agent.core.orchestrator",
  "message": "任务执行失败: task_id=t1, skill=monitoring, action=query_metrics, error=MCP Server 连接失败, parameters={...}",
  "exception": "Traceback (most recent call last): ..."
}
```

**排查步骤：**
1. 查看 `error` 字段了解失败原因
2. 查看 `exception` 字段获取完整堆栈
3. 查看 `parameters` 确认输入参数是否正确
4. 检查相关 MCP Server 是否正常运行

---

### 错误 4: 无可用 LLM Provider

**日志特征：**
```json
{
  "level": "ERROR",
  "logger": "aiops_agent.llm.provider",
  "message": "所有 LLM Provider 均不可用: primary=qwen, fallback=claude"
}
```

**原因：** 主 Provider 和 Fallback Provider 都失败

**排查步骤：**
1. 查找前面的 WARNING 日志，看哪个 Provider 失败了
2. 检查各 Provider 的 API Key 配置
3. 检查网络连接

## 日志查看技巧

### 1. 实时查看日志
```bash
# 启动 Agent 并查看日志
python src/aiops_agent/main.py 2>&1 | tee logs/agent.log
```

### 2. 搜索特定错误
```bash
# 搜索所有 ERROR 级别日志
grep '"level": "ERROR"' logs/agent.log

# 搜索特定任务的日志
grep 'task_id=t1' logs/agent.log

# 搜索特定技能的日志
grep 'skill=monitoring' logs/agent.log
```

### 3. 使用 jq 格式化 JSON 日志
```bash
# 安装 jq
brew install jq  # macOS
# 或
sudo apt-get install jq  # Linux

# 格式化查看
cat logs/agent.log | jq .

# 只看错误日志
cat logs/agent.log | jq 'select(.level == "ERROR")'
```

### 4. 追踪完整请求链路
```bash
# 通过 trace_id 追踪完整链路
grep 'trace_id=xxx' logs/agent.log

# 通过 session_id 追踪会话
grep 'session_id=xxx' logs/agent.log
```

## 日志文件位置

- **应用日志**: 输出到 stdout（可重定向到文件）
- **审计日志**: `logs/audit/audit-YYYY-MM-DD.jsonl`
- **日志目录**: `logs/`

## 生产环境建议

1. **日志级别**: 改为 `INFO` 或 `WARNING`
2. **日志轮转**: 配置日志文件大小和保留天数
3. **集中式日志**: 启用 SLS 日志服务（配置 `observability.logging.sls_enabled: true`）
4. **监控告警**: 监控 ERROR 级别日志的出现频率

## 调试 Checklist

当遇到问题时，按以下步骤排查：

- [ ] 1. 查找最新的 ERROR 级别日志
- [ ] 2. 确认错误发生的组件（orchestrator / task_planner / llm / skill）
- [ ] 3. 查看错误消息和异常堆栈
- [ ] 4. 检查相关的输入参数（parameters / messages）
- [ ] 5. 追溯上游日志，找到错误的根本原因
- [ ] 6. 检查配置文件是否正确
- [ ] 7. 确认外部服务（LLM API / MCP Server）是否正常
