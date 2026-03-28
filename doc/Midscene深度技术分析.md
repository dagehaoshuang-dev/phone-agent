# Midscene.js 深度技术分析报告

> 基于源码阅读的技术分析
> 项目：https://github.com/web-infra-dev/midscene
> 与 mobile-use、mobile-mcp、phone-agent 的对比
> 日期：2026-03-27

---

## 一、项目概述

Midscene.js 是字节跳动 Web Infra 团队开发的 **AI 驱动、纯视觉的跨平台 UI 自动化框架**。其核心理念是**完全基于截图让 VLM（视觉语言模型）理解界面**，不依赖 DOM 或 Accessibility Tree。

- 版本：1.6.0 | License：MIT
- 语言：TypeScript | 架构：pnpm + Nx monorepo（27 个 packages）
- 支持平台：Web + Android + iOS + HarmonyOS + macOS + Linux + Windows
- 接口方式：SDK（JS/TS）+ YAML 脚本 + Chrome 扩展 + MCP 协议

---

## 二、核心技术选择：纯视觉路线

### 与其他项目的根本差异

| | Midscene | mobile-use | mobile-mcp | phone-agent |
|---|---|---|---|---|
| **UI 理解** | 纯视觉（截图→VLM） | Accessibility Tree + 截图 | Accessibility Tree + 截图 | Accessibility Tree + 按需截图 |
| **元素定位** | VLM 输出 bbox 坐标 | UI 树 ID/text/bounds | UI 树元素列表 | UI 树编号 |

**Midscene 的纯视觉路线意味着：**
- ✅ 能处理任何可视界面——Web、原生 App、Canvas、游戏、嵌入式 WebView
- ✅ 跨平台统一——只要能截图就能用，无需适配不同平台的无障碍接口
- ❌ 对 VLM 视觉定位精度要求高——小按钮/密集 UI 可能定位偏差
- ❌ Token 消耗更大——每次都发截图（图片 token 远大于文本）
- ❌ 纯文本内容读取不如 Accessibility Tree 可靠

---

## 三、架构设计

### 3.1 Monorepo 结构（27 个包）

```
packages/
├── core/                    # 核心 AI 推理引擎（大脑）
├── shared/                  # 公共工具、类型、环境配置
│
├── web-integration/         # Web：Playwright/Puppeteer 集成
├── android/                 # Android：adb + scrcpy
├── ios/                     # iOS：WebDriverAgent
├── computer/                # 桌面：统一抽象
├── computer-mac/            # macOS 实现
├── computer-linux/          # Linux 实现（支持 xvfb）
├── computer-win/            # Windows 实现
├── harmony/                 # HarmonyOS NEXT：HDC
│
├── web-bridge-mcp/          # 5 个独立 MCP 服务器
├── android-mcp/
├── ios-mcp/
├── computer-mcp/
├── harmony-mcp/
│
├── cli/                     # 命令行工具
├── visualizer/              # 可视化回放报告
├── recorder/                # 操作录制
├── webdriver/               # WebDriver 协议
├── evaluation/              # 评估/基准测试
└── *-playground/            # 各平台 Playground
```

### 3.2 分层架构

```
┌─────────────────────────────────────┐
│  用户接口层                          │
│  SDK / YAML / Chrome扩展 / MCP      │
├─────────────────────────────────────┤
│  Agent 层 (core/agent/)             │
│  任务编排、生命周期管理              │
├─────────────────────────────────────┤
│  AI 模型层 (core/ai-model/)         │
│  Planning / Locate / Extract        │
│  支持: 通用VLM / UI-TARS / AutoGLM  │
├─────────────────────────────────────┤
│  设备抽象层 (core/device/)           │
│  AbstractInterface                   │
│  统一动作空间 (Zod schema)           │
├─────────────────────────────────────┤
│  平台实现层                          │
│  Web / Android / iOS / Desktop / HM │
└─────────────────────────────────────┘
```

---

## 四、核心 AI 推理机制

### 4.1 三条 Planning 路径

Midscene 支持三种模型家族，各有独立的 prompt 和坐标解析逻辑：

| 路径 | 适配模型 | 特点 |
|------|----------|------|
| 通用 VLM | Qwen3-VL, Doubao-vision, Gemini-3-pro | 最通用，XML 格式响应 |
| UI-TARS | ByteDance UI-TARS-1.5-7B | 字节自研专用模型，UI 操作优化 |
| AutoGLM | AutoGLM | 归一化坐标系 [0,999] |

### 4.2 通用 VLM Planning 流程

```
用户指令 + 截图
    │
    ▼
System Prompt 构建:
  - 角色定义: "expert to manipulate UI"
  - 动作空间: Zod schema 序列化为文本
  - XML 响应格式规范
  - 多轮对话示例（5 轮表单填写样例）
  - Deep Think 子目标管理
    │
    ▼
多模态消息组装:
  [system_prompt, 截图(base64), 用户指令, 历史对话, 记忆/子目标]
    │
    ▼
OpenAI 兼容接口调用 VLM
    │
    ▼
XML 响应解析:
  <thought>思考过程</thought>
  <action-type>Tap</action-type>
  <action-param-json>{"locate":{"prompt":"Submit button","bbox":[100,200,150,230]}}</action-param-json>
  <complete>false</complete>
    │
    ▼
Bbox 坐标转换 → 实际像素坐标
    │
    ▼
执行动作 → 截新图 → 循环
```

### 4.3 元素定位（AiLocateElement）

纯视觉定位，**不依赖 DOM 或 Accessibility Tree**：

```
截图 + 自然语言描述 ("the Submit button")
    → 发送给 VLM
    → VLM 返回 bbox [x1, y1, x2, y2]
    → 点击 bbox 中心点
```

**Section-first 定位优化**：
1. 先用 `AiLocateSection` 粗定位目标所在区域
2. 裁剪放大该区域
3. 再精确定位元素
4. 提高小元素的定位精度

### 4.4 数据提取（AiExtractElementInfo）

```
截图 + 可选 DOM + 自然语言数据需求
    → VLM 分析界面内容
    → 返回结构化 JSON 数据
    → 响应格式: <thought> + <data-json> + <errors>
```

数据提取时**可选择性引入 DOM** 提高准确率——这是纯视觉路线的唯一例外。

### 4.5 统一动作空间

所有平台共享一套动作定义，使用 **Zod schema** 描述参数：

| 动作 | 平台 | 参数 |
|------|------|------|
| Tap | 全平台 | locate (prompt + bbox) |
| DoubleClick | 全平台 | locate |
| RightClick | Web/桌面 | locate |
| Hover | Web/桌面 | locate |
| Input | 全平台 | locate + text + mode(replace/typeOnly/clear) |
| KeyboardPress | 全平台 | keys (组合键) |
| Scroll | 全平台 | direction + distance + locate(可选) |
| DragAndDrop | 全平台 | from_locate + to_locate |
| LongPress | 移动端 | locate + duration |
| Swipe | 移动端 | from + to + duration |
| Pinch | 移动端 | scale + locate |
| Sleep | 全平台 | seconds |

**关键设计**：`locate` 字段由 LLM 输出 `{prompt: "元素描述", bbox: [x1,y1,x2,y2]}`，VLM 同时给出自然语言描述和视觉坐标。

### 4.6 Deep Think 模式

复杂任务的子目标管理：

```xml
<update-plan-content>
  1. Open settings [DONE]
  2. Navigate to WiFi [IN PROGRESS]
  3. Connect to network [TODO]
</update-plan-content>
<mark-sub-goal-done>1</mark-sub-goal-done>
```

配合对话历史压缩 `compressHistory(50, 20)` 防止上下文溢出。

---

## 五、平台实现

### 5.1 设备抽象 (`AbstractInterface`)

所有平台只需实现三个核心方法：
```typescript
abstract class AbstractInterface {
    screenshotBase64(): Promise<string>     // 截图
    size(): Promise<{width, height}>        // 屏幕尺寸
    actionSpace(): DeviceAction[]           // 支持的动作列表
}
```

AI 核心逻辑**完全平台无关**——只要能截图和执行动作，就能接入。

### 5.2 各平台实现

| 平台 | 底层工具 | 截图方式 | 特殊处理 |
|------|---------|---------|---------|
| Web (Playwright) | CDP | page.screenshot() | 可选 DOM 注入 |
| Web (Puppeteer) | CDP | page.screenshot() | 可选 DOM 注入 |
| Android | adb + scrcpy | adb screencap | yadb 自定义 IME |
| iOS | WebDriverAgent | WDA /screenshot | - |
| macOS | 原生 API | screencapture | - |
| Linux | xdotool + xvfb | import/scrot | 支持虚拟显示 |
| Windows | 原生 API | - | - |
| HarmonyOS | HDC | hdc screenshot | - |

### 5.3 MCP 集成

每个平台有独立的 MCP 服务器包，暴露 Agent 动作为 MCP tools：
- `@midscene/web-bridge-mcp`
- `@midscene/android-mcp`
- `@midscene/ios-mcp`
- `@midscene/computer-mcp`
- `@midscene/harmony-mcp`

---

## 六、优点

### 6.1 纯视觉路线的统一性
一套 AI 逻辑处理所有平台，无需为每个平台适配不同的无障碍接口。Web、原生 App、Canvas、游戏都能自动化。

### 6.2 跨平台覆盖最广
7 个平台（Web + Android + iOS + HarmonyOS + macOS + Linux + Windows），是四个项目中覆盖面最广的。

### 6.3 声明式动作空间
Zod schema 定义动作参数，自动序列化到 prompt。新增动作只需定义 schema，AI 推理层自动适配。

### 6.4 多模型适配
同一框架支持通用 VLM、UI-TARS、AutoGLM 三种模型路径，每种有独立的 prompt 和坐标解析。

### 6.5 开发者工具链完善
- 可视化回放报告（Visualizer）
- Chrome 扩展
- YAML 脚本模式
- 操作录制（Recorder）
- 各平台 Playground

### 6.6 缓存系统
支持 read-only/read-write/write-only 策略，加速重复任务执行。

### 6.7 Section-first 定位
先粗定位区域再精确定位，有效解决小元素/密集 UI 的定位精度问题。

---

## 七、缺点

### 7.1 纯视觉路线的固有限制
- **Token 消耗大**：每次都发截图，图片 token 远大于文本。Accessibility Tree 的文本表示通常只需 1-3K tokens，截图需要 5-20K tokens。
- **定位精度受限**：VLM 的 bbox 输出可能有几像素偏差，密集 UI（如小按钮紧密排列）容易点错。
- **文本内容读取不可靠**：从截图 OCR 读取文本不如直接从 Accessibility Tree 获取准确，尤其是小字体、低对比度场景。
- **不可见内容无法处理**：Accessibility Tree 可以获取屏幕外元素（如长列表的后续项），纯视觉只能看到当前屏幕。

### 7.2 架构复杂度高
27 个 packages 的 monorepo，对开发者理解和贡献造成较高门槛。核心逻辑分散在多个包中。

### 7.3 模型依赖度高
纯视觉路线对 VLM 的视觉理解和坐标输出能力要求极高。模型能力不足时（如小模型），定位精度会显著下降。

### 7.4 速度劣势
每次决策都需要发送截图给 VLM，图片处理+传输+推理的延迟通常比纯文本（Accessibility Tree）方案高 50-100%。

### 7.5 调试困难
纯视觉定位失败时，很难判断是 VLM 理解错误还是坐标偏差。而 Accessibility Tree 方案可以直接对比元素 ID/text 排查问题。

---

## 八、四项目全景对比

### 8.1 定位与架构

| 维度 | Midscene | mobile-use | mobile-mcp | phone-agent |
|------|----------|-----------|------------|-------------|
| **定位** | 跨平台 UI 自动化框架 | 移动端多 Agent 框架 | 移动端 MCP 工具服务器 | 移动端单 Agent 脚本 |
| **语言** | TypeScript | Python | TypeScript | Python |
| **代码量** | 大型 monorepo | ~5000 行 | 3324 行 | ~400 行 |
| **AI 逻辑** | 内置（core 包） | 内置（5 个 Agent） | 无（客户端侧） | 内置（1 个 Agent） |

### 8.2 技术路线对比

| 维度 | Midscene | mobile-use | mobile-mcp | phone-agent |
|------|----------|-----------|------------|-------------|
| **UI 理解** | 纯视觉（VLM） | Accessibility Tree | Accessibility Tree | Accessibility Tree |
| **元素定位** | VLM bbox | Target fallback | 元素列表 | 编号映射 |
| **截图使用** | 每次必发 | 每次必发 | 按需返回客户端 | 按需 |
| **模型要求** | VLM（视觉能力） | 文本 LLM | 无 | 文本 LLM |
| **Token 消耗** | 高（图片） | 高（图片+UI 树） | 取决于客户端 | 低（文本为主） |

### 8.3 平台覆盖

| 平台 | Midscene | mobile-use | mobile-mcp | phone-agent |
|------|----------|-----------|------------|-------------|
| Web | ✅ (Playwright/Puppeteer) | ❌ | ❌ | ❌ |
| Android | ✅ | ✅ | ✅ | ✅ |
| iOS | ✅ (WDA) | ⚠️ (模拟器) | ✅ (真机+模拟器) | ❌ |
| macOS | ✅ | ❌ | ❌ | ❌ |
| Linux | ✅ | ❌ | ❌ | ❌ |
| Windows | ✅ | ❌ | ❌ | ❌ |
| HarmonyOS | ✅ | ❌ | ❌ | ❌ |

### 8.4 适用场景

| 场景 | 最佳选择 | 原因 |
|------|---------|------|
| Web UI 测试 | **Midscene** | Playwright/Puppeteer 集成，纯视觉处理 SPA |
| 跨平台统一自动化 | **Midscene** | 7 个平台一套框架 |
| Canvas/游戏自动化 | **Midscene** | 纯视觉不依赖 Accessibility |
| 移动端快速自动化 | **phone-agent** | 最快（1.5 分钟跨 App），最简单 |
| MCP 工具集成 | **mobile-mcp** | 标准协议，安全设计 |
| 超长复杂移动任务 | **mobile-use** | 多 Agent 重规划 |
| 成本敏感场景 | **phone-agent** | Token 消耗最低 |
| 高精度文本读取 | **mobile-mcp/phone-agent** | Accessibility Tree 文本更准确 |

---

## 九、关键技术洞察

### 9.1 视觉路线 vs 无障碍树路线

这是 UI 自动化领域最核心的技术选择：

**Midscene 选择纯视觉**：
- 优势：平台统一、处理任意 UI
- 代价：Token 高、精度受限、速度慢

**其他三个项目选择无障碍树**：
- 优势：精确、Token 低、速度快
- 代价：平台适配成本、无法处理无标签 UI

**2026 年的判断**：两条路线将长期共存。无障碍树路线在标准 App 自动化中仍占优（更快更准更便宜），但纯视觉路线在 Web 测试、跨平台统一、Canvas/游戏等场景中不可替代。

### 9.2 Midscene 对 phone-agent 的启发

1. **Zod schema 声明式动作空间**：比硬编码工具定义更优雅，新增动作自动适配
2. **Section-first 定位**：可以在 phone-agent 截图分析时借鉴
3. **缓存系统**：重复任务场景的加速方案
4. **可视化回放**：调试和演示的利器

---

## 十、结论

Midscene.js 代表了 UI 自动化的**纯视觉路线**——用 VLM 的"眼睛"代替 Accessibility Tree 的"结构化描述"来理解界面。这是一个有远见的技术选择：随着 VLM 能力持续提升，纯视觉方案的精度问题会逐步缓解，而其跨平台统一性的优势将越发明显。

但在 2026 年当下，对于**移动端自动化**这个具体场景，Accessibility Tree 路线仍然是更优选择——更快、更准、更便宜。Midscene 的真正优势在 Web UI 测试和跨平台统一自动化场景。

四个项目本质上代表了四种不同的设计哲学：
- **Midscene**：纯视觉 + 跨平台框架 → "用眼睛看世界"
- **mobile-use**：多 Agent + 无障碍树 → "团队协作完成任务"
- **mobile-mcp**：薄工具层 + MCP 标准 → "提供好工具，决策交给你"
- **phone-agent**：单 Agent + 无障碍树 → "一个聪明人搞定一切"
