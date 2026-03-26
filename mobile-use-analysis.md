# mobile-use 技术分析报告

> 基于 https://github.com/minitap-ai/mobile-use 实际部署与测试的分析总结
> 对比方案：自研 phone-agent（单 Agent 架构）
> 日期：2026-03-26

---

## 一、项目概述

mobile-use 是 Minitap 公司开发的开源 AI Agent 框架，通过自然语言控制 Android/iOS 设备。它是首个在 AndroidWorld 基准测试上达到 100% 准确率的框架。

- GitHub: https://github.com/minitap-ai/mobile-use
- Stars: ~2.4k | License: Apache 2.0
- 语言: Python | 包名: minitap-mobile-use

---

## 二、核心实现原理

### 2.1 多 Agent 架构

mobile-use 采用 **5 个专职 Agent 协作** 的架构，通过 LangGraph 编排工作流：

```
用户指令（自然语言）
       │
       ▼
  ┌─────────────┐
  │   Planner    │  将任务拆解为子目标（subgoals）
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │ Orchestrator │  调度子目标执行顺序，判断是否需要重新规划
  └──────┬──────┘
         ▼
  ┌──────────────────────────────────┐
  │         执行循环（每个子目标）      │
  │                                  │
  │  Contextor → 获取屏幕状态         │
  │      │       (UI树 + 截图)        │
  │      ▼                           │
  │  Cortex → 分析屏幕，做出决策       │
  │      │    (点击什么/输入什么)       │
  │      ▼                           │
  │  Executor → 调用工具执行操作       │
  │      │      (tap/swipe/input)     │
  │      ▼                           │
  │  回到 Contextor 检查结果          │
  └──────────────────────────────────┘
         │
         ▼
  Orchestrator 判断子目标是否完成
  → 完成则进入下一个子目标
  → 失败则触发 Planner 重新规划
```

### 2.2 屏幕感知方式

mobile-use 通过两种方式获取屏幕信息：

1. **无障碍树（Accessibility Tree）**（主要方式）
   - 通过 UIAutomator2（Android）或 fb-idb（iOS）获取
   - 包含所有 UI 元素的文本、坐标、resource_id、可点击性等属性
   - LLM 直接推理结构化文本，无需计算机视觉

2. **截图（Screenshot）**（辅助方式）
   - 每轮都会截图发送给 LLM
   - 用于补充无障碍树无法表达的视觉信息
   - 消耗大量 tokens

### 2.3 设备控制层

```
┌─────────────────────────────────┐
│         mobile-use Agent        │
├─────────────────────────────────┤
│      clients/ (通信层)           │
│  ├── UIAutomator2 (Android)     │  ← ADB 连接
│  └── fb-idb (iOS)               │  ← Xcode 工具链
├─────────────────────────────────┤
│      controllers/ (操作层)       │
│  ├── tap(x, y)                  │
│  ├── swipe(方向)                 │
│  ├── input_text(文本)            │
│  ├── launch_app(包名)            │
│  ├── press_button(按键)          │
│  └── screenshot()               │
└─────────────────────────────────┘
```

### 2.4 LLM 集成

- 通过 LangChain 封装 LLM 调用
- 使用 `with_structured_output()` 强制 LLM 返回结构化 JSON
- 支持多 Provider：OpenAI、Google、Vertex AI、OpenRouter、xAI
- 每个 Agent 可独立配置不同的模型
- 配置文件：`llm-config.override.jsonc`

### 2.5 技术栈

| 组件 | 技术 |
|------|------|
| Agent 编排 | LangGraph |
| LLM 封装 | LangChain |
| 设备控制（Android） | UIAutomator2 + ADB |
| 设备控制（iOS） | fb-idb + xcrun simctl |
| 配置管理 | Pydantic Settings |
| 日志/追踪 | PostHog + LangSmith |

---

## 三、优点

### 3.1 功能完整性
- **跨平台支持**：同时支持 Android 真机/模拟器 和 iOS 模拟器
- **多 LLM 支持**：OpenAI、Google、本地模型等，可按 Agent 独立配置
- **结构化输出**：通过 `--output-description` 参数支持 JSON 等格式化输出
- **MCP Server**：可作为 MCP 服务被 Claude Code、Cursor 等工具调用

### 3.2 鲁棒性
- **自动重规划**：子目标失败时 Planner 会重新制定计划
- **Fallback 机制**：主模型失败时自动切换备用模型
- **多轮验证**：Orchestrator 会验证子目标是否真正完成

### 3.3 可扩展性
- **模块化设计**：每个 Agent 职责单一，可独立替换
- **自定义工具**：可扩展 Executor 支持的工具集
- **SDK 接口**：提供 Python SDK 可编程调用

### 3.4 基准测试成绩
- AndroidWorld 基准测试 100% 准确率（业界第一）
- 任务分解策略在复杂长流程任务上表现优异

---

## 四、缺点

### 4.1 性能问题（最大痛点）

**每步操作需要 5 次 LLM 调用：**

```
Contextor(LLM) → Cortex(LLM) → Executor(LLM) → Orchestrator(LLM) → [Planner(LLM)]
```

| 指标 | 数据 |
|------|------|
| 单步耗时 | 3-8 秒 |
| 简单任务总耗时 | 2-5 分钟 |
| 复杂跨 App 任务 | 5-10 分钟 |
| 每步 token 消耗 | ~5000-10000 tokens（含截图） |

**对比：单 Agent 方案每步仅 1 次 LLM 调用，耗时 1-2 秒。**

### 4.2 模型兼容性差

- **Gemini 不兼容**：`with_structured_output()` 在 Gemini 2.5 Flash/Pro 上 Cortex 返回空内容，完全无法工作
- **DeepSeek 不兼容**：不支持 `response_format` 参数
- **OpenAI 低额度受限**：截图导致 token 量大，低 Tier 账户频繁触发 rate limit
- **实际可用组合有限**：测试中只有 OpenAI GPT-4o+ 和 GPT-5.4 系列能稳定工作

### 4.3 过度设计

- **5 个 Agent 拆分过细**：Contextor 只做截图/获取 UI 树，完全可以是一个函数调用
- **LangChain/LangGraph 依赖过重**：引入 175 个 Python 依赖包
- **Cortex/Executor 分离不必要**：决策和执行可以在一次 LLM 调用中完成

### 4.4 截图策略浪费

- 每轮都发送截图给 LLM，即使 UI 树已经包含足够信息
- 截图约占每次请求 30-50% 的 token 消耗
- 没有"按需截图"机制

### 4.5 错误处理不足

- Cortex 返回空内容时没有有效的恢复机制，只能反复重试
- 重规划循环可能陷入死循环（实测中出现过 20+ 次无效重规划）
- 设备断开（锁屏/USB 断开）时直接崩溃，无重连机制

### 4.6 安装和配置复杂

- 需要 Python 3.12+、uv、ADB 等多个前置依赖
- LLM 配置涉及 `.env` + `llm-config.override.jsonc` 两个文件
- 默认配置依赖 OpenAI，没有开箱即用的免费方案

---

## 五、与自研 phone-agent 对比

### 5.1 架构对比

```
mobile-use:
  用户 → Planner → Orchestrator → [Contextor → Cortex → Executor] × N → 结果
  每步 5 次 LLM 调用

phone-agent:
  用户 → [获取UI树 → LLM(思考+工具调用)] × N → 结果
  每步 1 次 LLM 调用
```

### 5.2 实测对比（同一任务：小红书搜索 → 高德地图导航）

| 指标 | mobile-use | phone-agent |
|------|-----------|-------------|
| 总耗时 | ~5 分钟 | **2分38秒** |
| 总步骤 | 30+ 轮 | **24 步** |
| LLM 调用次数 | 60+ 次 | **24 次** |
| Token 消耗 | ~150K+ | ~50K |
| 代码量 | ~5000 行 | **~300 行** |
| 依赖包数 | 175 个 | **27 个** |
| 模型兼容性 | 仅 OpenAI | 任意支持 tool_use 的模型 |
| 结果准确性 | 有时读错内容 | 准确 |

### 5.3 各自适用场景

**mobile-use 更适合：**
- 需要极高准确率的基准测试场景
- 超长复杂任务（50+ 步），需要任务分解和重规划
- 需要跨平台（Android + iOS）统一支持
- 企业级部署，需要完善的日志追踪和监控

**phone-agent 更适合：**
- 日常自动化任务（速度优先）
- 快速原型验证
- 成本敏感场景（token 消耗少 3 倍）
- 需要自定义扩展的场景（代码简单易改）

---

## 六、改进建议

如果要基于 mobile-use 的思路做改进，建议：

1. **合并 Agent**：将 Contextor + Cortex + Executor 合并为单一 Agent，每步只调 1 次 LLM
2. **按需截图**：默认只发 UI 树，LLM 认为信息不足时主动请求截图
3. **元素编号**：给 UI 元素编号，LLM 直接引用编号而非坐标，减少定位错误
4. **去 LangChain**：直接使用 LLM SDK 的原生 tool_use，减少抽象层和依赖
5. **流式执行**：支持 LLM 在一次响应中返回多个连续操作
6. **缓存 UI 树**：相邻两步如果 UI 树没变化，跳过重复获取

---

## 七、结论

mobile-use 作为一个开源框架，在功能完整性和基准测试成绩上表现优秀，其多 Agent 任务分解的思路对处理复杂长流程有价值。但在实际使用中，**过度设计导致性能低下、模型兼容性差、成本高**是其主要瓶颈。

对于大多数实际应用场景，**单 Agent + 工具循环**的轻量方案在速度、成本、可维护性上都显著优于多 Agent 架构。核心原因是：现代大模型（Claude Sonnet 4.6、GPT-5.4 等）的单次推理能力已经足够强，不需要将"观察、思考、执行"拆分给不同的 Agent。

> "最好的架构不是最复杂的架构，而是用最少的组件解决问题的架构。"
