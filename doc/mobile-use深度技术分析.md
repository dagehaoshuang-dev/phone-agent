# mobile-use 深度技术分析报告

> 基于完整源码阅读 + 实际部署测试的综合分析
> 项目：https://github.com/minitap-ai/mobile-use
> 对比方案：自研 phone-agent（单 Agent 架构）
> 日期：2026-03-27

---

## 一、项目概述

mobile-use 是 Minitap 公司开发的开源多 Agent 移动自动化框架，通过自然语言控制 Android/iOS 设备。AndroidWorld 基准测试 100% 准确率（业界第一）。

- 包名：`minitap-mobile-use` | 版本：3.6.3
- 核心代码：~5000 行 Python | 依赖包：175 个
- 技术栈：LangGraph + LangChain + UIAutomator2 + fb-idb

---

## 二、架构全景

### 2.1 多 Agent 流水线

```
START
  │
  ▼
Planner ──→ Orchestrator ──→ convergence_gate
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                 continue        replan            end
                    │               │               │
                    ▼               ▼               ▼
               Contextor        Planner            END
                    │
                    ▼
                 Cortex
                    │
           ┌───────┴───────┐
           │               │
     review_subgoals  execute_decisions
           │               │
           ▼               ▼
      Orchestrator     Executor
                          │
                    ┌─────┴─────┐
                    │           │
              invoke_tools    skip
                    │           │
                    ▼           ▼
            ExecutorToolNode  Summarizer
                    │           │
                    ▼           ▼
                    └──→ convergence_gate
```

### 2.2 Agent 职责分工

| Agent | 职责 | LLM调用 | 模型（默认） |
|-------|------|---------|-------------|
| **Planner** | 将用户目标拆解为子目标列表 | 每次规划/重规划 1 次 | gpt-5-nano |
| **Orchestrator** | 管理子目标生命周期，决定完成/启动/重规划 | 仅评估子目标完成时调用 | gpt-5-nano |
| **Contextor** | 获取屏幕截图+UI层级，App Lock 验证 | 正常 0 次，App Lock 违规时 1 次 | gpt-5-nano |
| **Cortex** | 核心大脑：分析屏幕状态，产出操作决策 | 每轮 1 次（多模态） | gpt-5 |
| **Executor** | 将 Cortex 决策翻译为工具调用 | 每轮 1 次 | gpt-5-nano |
| **Summarizer** | 裁剪历史消息（>25条时） | 0 次 | 无 |
| **Hopper** | 数据搜索（如包名查找） | 按需 1 次 | gpt-5-nano |
| **Outputter** | 生成最终结构化输出 | 任务结束时 1 次 | gpt-5-nano |

### 2.3 核心设计理念："大脑与手的分离"

Cortex 是"大脑"，负责理解屏幕和做出决策；Executor 是"手"，负责执行具体操作。这种分离的设计意图是：

- Cortex 用最强模型（gpt-5/gemini-3-pro）做复杂推理
- Executor 用小模型（gpt-5-nano）做简单的指令翻译
- 理论上可以节省 token 消耗

**但实际效果存疑**：Executor 仅做指令翻译，完全可以用代码解析 JSON 代替 LLM 调用。

---

## 三、核心实现细节（基于源码）

### 3.1 State 管理

使用 Pydantic BaseModel + LangGraph Annotated reducer：

```python
class State(BaseModel):
    messages: Annotated[list, add_messages]          # 主消息历史
    initial_goal: str                                 # 用户目标
    subgoal_plan: list[Subgoal]                      # 子目标计划
    latest_ui_hierarchy: str | None                   # UI 层级 JSON
    latest_screenshot: str | None                     # 截图 base64
    focused_app_info: str | None                      # 前台应用
    structured_decisions: str | None                  # Cortex 决策
    complete_subgoals_by_ids: list[str]               # 待完成子目标
    executor_messages: Annotated[list, add_messages]  # Executor 独立消息通道
    cortex_last_thought: str | None                   # Cortex 最后思考
    agents_thoughts: list[str]                        # 所有 Agent 思考记录
    scratchpad: dict                                  # 持久 KV 存储
```

**关键设计**：
- `executor_messages` 是独立通道，每轮 Cortex 执行后清空（`REMOVE_ALL_MESSAGES`）
- `latest_ui_hierarchy` 和 `latest_screenshot` 使用 `take_last` reducer，Cortex 读取后设为 None
- `scratchpad` 通过 `save_note`/`read_note` 工具实现跨步骤信息持久化

### 3.2 Cortex 决策机制

Cortex 是整个系统最关键的 Agent，其 prompt（`cortex.md`）定义了：

**输入组装**（5 条消息）：
1. `SystemMessage`：渲染后的 prompt 模板，包含目标、子目标计划、工具列表
2. `HumanMessage`：设备信息（平台、分辨率、日期、前台应用）
3. `AIMessage`：所有 agents_thoughts（历史操作记录）
4. `HumanMessage`：UI 层级 JSON 全文
5. `HumanMessage`：压缩截图（多模态图片）

**输出**（structured output）：
```python
class CortexOutput(BaseModel):
    decisions: str | None              # JSON 操作指令
    decisions_reason: str | None       # 决策理由
    goals_completion_reason: str | None # 目标完成理由
    complete_subgoals_by_ids: list[str] # 标记完成的子目标
```

**Cortex prompt 核心规则**：
- 两种感知：UI Hierarchy（精确定位）+ Screenshot（视觉上下文）
- Target 必须包含完整信息：resource_id + bounds + text（三级 fallback）
- "不可预测操作"（back、launch_app）必须独占一轮
- 滑动需指定精确坐标和方向

### 3.3 Executor 工具执行

Executor 使用 LangChain 的 `bind_tools()` 绑定 15 个工具：

```python
llm = get_llm(ctx, "executor").bind_tools(
    tools, parallel_tool_calls=(provider != "google")
)
```

**ExecutorToolNode 关键设计**：
- **串行执行**工具调用，一个失败后续全部 abort
- 每次工具执行发送 telemetry 事件
- 工具返回 LangGraph `Command` 直接更新 State

### 3.4 Target 三级 Fallback

所有交互工具（tap、long_press、focus_and_input_text）使用统一的定位系统：

```
优先级1：bounds 坐标直接点击 → 成功率最高
    ↓ 失败
优先级2：resource_id 匹配元素 → 通过 ID 查找
    ↓ 失败
优先级3：text 文本匹配 → 在 UI 树中搜索文本
    ↓ 全部失败
返回错误
```

### 3.5 launch_app 的特殊处理

launch_app 不是简单的包名调用，而是涉及一次额外的 LLM 调用：

```
用户说"打开小红书"
    → list_packages_async() 获取所有已安装包名
    → 调用 Hopper Agent（LLM）从包名列表中查找 "com.xingin.xhs"
    → device.app_start("com.xingin.xhs")
```

**问题**：每次 launch_app 都要调用一次 Hopper LLM，而包名映射完全可以用字典解决。

### 3.6 截图处理流程

```
UIAutomator2.screenshot() → PNG 原始数据
    → PIL Image 解码
    → JPEG quality=50 压缩
    → base64 编码
    → 作为多模态消息发给 Cortex
```

**每轮都截图**，无论 UI 树是否已经提供了足够信息。

### 3.7 LLM Structured Output

每个 Agent 使用 LangChain 的 `with_structured_output()`：

```python
# Planner
llm = get_llm(ctx, "planner").with_structured_output(PlannerOutput)

# Cortex
llm = get_llm(ctx, "cortex").with_structured_output(CortexOutput)

# Orchestrator
llm = get_llm(ctx, "orchestrator").with_structured_output(OrchestratorOutput)
```

底层实现依赖 OpenAI 的 `response_format` 参数，不同模型兼容性差异极大（详见缺点章节）。

### 3.8 Fallback 机制

```python
async def with_fallback(main_call, fallback_call, none_should_fallback=True):
    try:
        result = await main_call()
        if result is None and none_should_fallback:
            return await fallback_call()  # None 也触发 fallback
        return result
    except Exception:
        return await fallback_call()      # 异常触发 fallback
```

每个 Agent 都有 main + fallback 两个模型，失败自动切换。

---

## 四、优点

### 4.1 任务分解与重规划

**Planner + Orchestrator** 的组合实现了：
- 复杂任务自动拆解为可管理的子目标
- 子目标失败时自动触发重规划（replan）
- 子目标完成状态的验证
- 这是 AndroidWorld 100% 准确率的关键——超长任务（50+ 步）不会迷失方向

### 4.2 多级容错

四层容错机制：
1. **LLM 层**：main + fallback 双模型
2. **定位层**：bounds → resource_id → text 三级 Target fallback
3. **任务层**：子目标 FAILURE 触发 replan
4. **设备层**：连接断开自动重连

### 4.3 跨平台统一接口

`MobileDeviceController` Protocol 定义了约 15 个抽象方法，Android/iOS/云设备均实现相同接口：
- 本地 Android：ADB + UIAutomator2
- 本地 iOS：IDB（模拟器）/ WDA（真机）
- 云设备：Limrun WebSocket 隧道 / BrowserStack

### 4.4 工具系统设计

`ToolWrapper` 模式统一了工具定义：
- 15 个内置工具覆盖常见操作
- `scratchpad`（save_note/read_note）实现跨步骤信息持久化
- 工具通过闭包捕获上下文，类型安全
- 可选的视频录制工具（需 Gemini 模型）

### 4.5 SDK 设计

Builder 模式提供了清晰的编程接口：
```python
config = (Builders.AgentConfig
    .with_default_profile(profile)
    .for_device(DevicePlatform.ANDROID, "device_id")
    .build())
agent = Agent(config=config)
await agent.run_task(task.build())
```

### 4.6 Prompt 工程

Agent prompt 采用 Jinja2 模板 + Markdown 文件分离：
- 每个 Agent 的 prompt 是独立的 `.md` 文件
- 支持动态注入：平台信息、工具列表、子目标状态
- 易于独立迭代和测试

---

## 五、缺点

### 5.1 性能问题（最大痛点）

**每轮操作的 LLM 调用分布**：

```
Contextor: 0 次（纯数据采集）
Cortex:    1 次（多模态，最慢）
Executor:  1 次（工具调用翻译）
Orchestr.: 0-1 次（子目标评估时调用）
─────────────────────────────
每轮合计:  2-3 次 LLM 调用
```

加上 Planner（规划/重规划）和 Hopper（launch_app 包名查找），一个 20 步的任务实际 LLM 调用约 50-60 次。

| 阶段 | 耗时 |
|------|------|
| Cortex LLM（多模态） | 3-5 秒 |
| Executor LLM | 1-2 秒 |
| Orchestrator LLM | 1-2 秒 |
| 截图+UI树获取 | 1-2 秒 |
| 工具执行 | 0.5-1 秒 |
| **单轮合计** | **6-12 秒** |

**实测**：跨 App 任务（小红书→高德地图）耗时 5+ 分钟。

**对比 phone-agent**：单 Agent 架构每步仅 1 次 LLM 调用（3-5 秒），同等任务 1.5 分钟完成。

### 5.2 过度设计

源码分析揭示了多处过度抽象：

1. **Executor Agent 可用代码替代**：Cortex 输出结构化 JSON 决策后，Executor 只做 JSON → 工具调用的翻译，完全可以用代码解析而不需要 LLM。每次 Executor 调用浪费 1-2 秒 + tokens。

2. **Hopper Agent 可用字典替代**：launch_app 每次调用 Hopper LLM 查找包名。实际上包名映射是确定性的（"小红书" → "com.xingin.xhs"），一个字典就能解决。

3. **Contextor 不需要是 Agent**：正常流程中 Contextor 不调用 LLM，只是获取截图和 UI 树——这是一个函数调用，不需要 Agent 抽象。

4. **LangGraph + LangChain 依赖过重**：引入 175 个 Python 依赖包。核心功能（获取 UI 树 → LLM 决策 → 执行操作）可以用 ~300 行代码实现。

### 5.3 模型兼容性差（实测验证）

`with_structured_output()` 依赖 OpenAI 的 `response_format` 参数，各模型实现差异导致严重兼容问题：

| 模型 | 结果 | 失败原因 |
|------|------|----------|
| **OpenAI GPT-5.4** | ✅ 正常 | 原生支持 |
| **OpenAI GPT-4o** | ⚠️ 受限 | 低 Tier 账户频繁 rate limit |
| **Gemini 2.5** | ❌ 不可用 | Cortex 返回空内容，无限重规划循环 |
| **DeepSeek** | ❌ 不可用 | 不支持 `response_format` 参数 |
| **Qwen 3.5 Plus** | ❌ 部分不可用 | Qwen 要求 prompt 含"json"才能用 `response_format: json_object`，Hopper/Orchestrator 的 prompt 不满足 |

**根因**：LangChain 的 `with_structured_output()` 在底层使用 `response_format` 参数。各模型对此参数支持差异大，但 mobile-use 没有做兼容性处理。

**对比 phone-agent**：使用 OpenAI 标准的 `function calling`（tool_use）接口，Qwen/Claude/GPT 均完美兼容。

### 5.4 截图策略浪费

源码确认：**每轮 Contextor 都获取截图并发送给 Cortex**，无论 UI 树是否已经提供了足够信息。

- 截图经 JPEG quality=50 压缩后仍约 20-50KB
- 占每次 Cortex 请求 30-50% 的 token
- 没有"按需截图"机制

**对比 phone-agent**：默认只发 UI 树（纯文本），Agent 主动请求时才截图。实测仅 20-30% 的步骤需要截图。

### 5.5 代码质量问题

源码中发现的具体 bug 和设计问题：

1. **Summarizer bug**：`return` 在 `for` 循环内部，导致只处理一条消息就返回，消息裁剪功能实际无效
2. **`wait_for_delay` 阻塞**：使用 `time.sleep()` 而不是 `asyncio.sleep()`，会阻塞事件循环
3. **控制器代码重复**：`_extract_bounds`、`find_element`、`_get_current_foreground_package` 在 Android/iOS/Limrun 三处几乎相同
4. **MobileUseContext 过重**：承载了设备信息+所有客户端引用+回调+LLM配置，违反单一职责
5. **`tools/utils.py` 过大**：369 行混合了元素查找、坐标处理、焦点管理等多种关注点

---

## 六、与 phone-agent 架构对比

### 6.1 架构对比

```
mobile-use（每步 2-3 次 LLM 调用）:
  Contextor(截图+UI树) → Cortex(LLM决策) → Executor(LLM翻译) → 工具执行
  + Orchestrator(LLM评估子目标) + Planner(LLM重规划)

phone-agent（每步 1 次 LLM 调用）:
  获取UI树 → LLM(思考+工具调用) → 执行
```

### 6.2 实测对比

**任务：小红书搜索 → 高德地图导航**

| 指标 | mobile-use | phone-agent |
|------|-----------|-------------|
| 总耗时 | ~5 分钟 | **1分28秒** |
| LLM 调用 | 60+ 次 | **21 次** |
| 设备操作 | 30+ 次 | **14 次** |
| 代码量 | ~5000 行 | **~400 行** |
| 依赖包 | 175 个 | **27 个** |

**任务：三 App 联动（小红书→高德→大众点评）**

| 指标 | mobile-use | phone-agent |
|------|-----------|-------------|
| 模型兼容 | 仅 OpenAI GPT-5.4 | Qwen/Claude/GPT 均可 |
| 总耗时 | 无法完成（Qwen 不兼容） | **1分34秒** |

### 6.3 关键设计差异

| 设计点 | mobile-use | phone-agent |
|--------|-----------|-------------|
| LLM 调用方式 | `with_structured_output()` | OpenAI `function calling` |
| 截图策略 | 每轮必截 | 按需截图 |
| App 启动 | LLM 查包名（Hopper） | 字典映射 |
| 决策+执行 | 分离为 Cortex + Executor | 合并为一次调用 |
| 子目标管理 | Planner + Orchestrator | 无（LLM 自行管理） |
| 消息历史 | 25 条上限 + Summarizer | 12 条上限 + 摘要压缩 |
| UI 树格式 | 完整 XML JSON | 精简编号格式 |
| 元素定位 | Target 三级 fallback | 编号直接映射坐标 |

---

## 七、适用场景分析

### mobile-use 更适合

- **基准测试**：多 Agent 协作+重规划在 50+ 步超长任务上更稳定
- **企业级部署**：完善的 SDK、Builder API、telemetry、多平台支持
- **iOS 支持**：同时支持 iOS 模拟器和真机（通过 WDA）
- **云设备**：内置 Limrun 和 BrowserStack 支持
- **可审计性**：每个 Agent 的 thought 被完整记录

### phone-agent 更适合

- **日常自动化**：速度优先的场景（快 3 倍）
- **成本敏感**：token 消耗少 3 倍
- **多模型支持**：需要使用 Qwen/DeepSeek 等国产模型
- **快速原型**：~400 行代码易于理解和修改
- **简单部署**：27 个依赖包，无复杂框架

---

## 八、改进建议

如果基于 mobile-use 做优化：

1. **合并 Cortex + Executor**：用 `bind_tools()` 让 Cortex 直接产出工具调用，省掉 Executor 的 LLM 调用
2. **按需截图**：默认只发 UI 树，Cortex 请求时才截图
3. **字典替代 Hopper**：launch_app 的包名映射用硬编码字典
4. **修复 Summarizer bug**：`return` 应在 `for` 循环外部
5. **使用 function calling 替代 structured output**：提升模型兼容性
6. **精简 UI 树**：过滤不可见/重复元素，截断长文本
7. **异步 wait**：`time.sleep()` → `asyncio.sleep()`
8. **去除 LangChain 依赖**：直接使用 OpenAI SDK 的原生 tool_use

---

## 九、结论

mobile-use 是一个**架构精良但过度设计**的框架。它的多 Agent 协作理念在理论上优雅——Planner 规划、Cortex 思考、Executor 执行——但在实际应用中，这种分离带来的开销（每步 2-3 次 LLM 调用、175 个依赖、模型兼容性差）远大于收益。

核心原因：**2026 年的大模型（Claude Sonnet/Opus、GPT-5.4、Qwen 3.5 Plus）单次推理能力已经足够强，能在一次调用中完成"观察屏幕 → 分析状态 → 决定操作 → 生成工具调用"的完整链路**。不需要拆分为多个 Agent 分别处理。

mobile-use 的价值在于其完整的工程基础设施（SDK、多平台、云设备、telemetry），适合需要这些企业级能力的场景。但对于大多数移动自动化需求，**单 Agent + 工具循环**的方案在速度、成本、可维护性上都是更优的选择。
