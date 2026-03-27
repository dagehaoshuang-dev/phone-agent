# Open-AutoGLM 深度技术分析报告

> 基于完整源码阅读的技术分析
> 项目：https://github.com/zai-org/Open-AutoGLM
> 与 mobile-use、mobile-mcp、phone-agent、Midscene 的对比
> 日期：2026-03-27

---

## 一、项目概述

Open-AutoGLM 是智谱 AI（Zhipu AI）开发的手机智能助手框架，基于**自研的 AutoGLM-Phone-9B 视觉语言模型**，通过截图理解 + AI 规划 + 设备控制实现端到端手机自动化。

- 版本：0.1.0 | License：Apache 2.0
- 语言：Python | 核心代码：~3000 行
- 支持平台：Android + iOS + HarmonyOS
- 核心模型：AutoGLM-Phone-9B（9B 参数，专为手机 GUI 操作训练）

**研究背景**：
- AutoGLM (2024): 定义 GUI 自主 Agent 架构 (arXiv:2411.00820)
- MobileRL (2025): 移动 GUI Agent 的在线强化学习 (arXiv:2509.18119)

---

## 二、核心技术：专用视觉模型

### 与其他项目的本质区别

Open-AutoGLM 最大的差异化在于**不使用通用 LLM，而是使用专为手机 GUI 操作训练的 9B 视觉模型**。

| | Open-AutoGLM | mobile-use | phone-agent | Midscene |
|---|---|---|---|---|
| **模型** | 自研 AutoGLM-9B | 通用 LLM | 通用 LLM | 通用 VLM |
| **模型训练** | 手机 GUI 专项训练 + RL | 无训练 | 无训练 | 无训练 |
| **UI 理解** | 纯视觉（截图） | Accessibility Tree | Accessibility Tree | 纯视觉 |
| **坐标输出** | 归一化 0-999 | Target fallback | 编号映射 | bbox |

### AutoGLM-Phone-9B 模型特点

| 属性 | 值 |
|------|------|
| 参数量 | 9B |
| 架构 | 基于 GLM-4.1V-9B-Thinking |
| 输入 | 截图（base64）+ 文本指令 |
| 输出 | Thinking 推理过程 + Action 操作指令 |
| 坐标系 | 归一化 (0,0)-(999,999) |
| 显存需求 | 24GB+ |
| 部署 | vLLM/SGlang/第三方 API（z.ai、Novita、Parasail） |

---

## 三、架构设计

### 3.1 整体架构

```
用户指令（自然语言）
       │
       ▼
  PhoneAgent（循环编排）
       │
       ├── 截图 ──→ 设备驱动层（ADB/HDC/WDA）
       │
       ├── 构建消息 ──→ MessageBuilder
       │     └── [System Prompt, 历史对话, 截图, 当前App信息]
       │
       ├── 模型推理 ──→ ModelClient（OpenAI 兼容 API，流式）
       │     └── AutoGLM-Phone-9B
       │
       ├── 解析响应 ──→ thinking + action 分离
       │
       └── 执行动作 ──→ ActionHandler
             ├── Tap(归一化坐标 → 像素坐标)
             ├── Type(ADB Keyboard / WDA Keys)
             ├── Swipe / LongPress / DoubleClick
             ├── Launch(应用包名映射 188+ App)
             ├── Back / Home / Wait
             └── Take_over(人工接管) / finish(结束)
```

### 3.2 Agent 循环

```python
# 简化的核心循环 (agent.py)
while True:
    screenshot = device.get_screenshot()     # 截图
    current_app = device.get_current_app()   # 获取前台应用
    messages = build_messages(screenshot, current_app, task)

    thinking, action = model.request(messages)  # 流式推理

    if action == "finish":
        return result

    action_handler.execute(action)  # 执行操作

    remove_old_images(messages)  # 移除历史截图节省上下文
```

**关键设计**：每步只保留最新截图在上下文中，历史轮次仅保留文本。这有效控制了 token 消耗。

### 3.3 模型推理流程

```
输入:
  System: "你是智能手机Agent...18条操作规则..."
  User: [截图base64, "用户任务\n{current_app: xxx}"]

模型流式输出:
  <think>当前在系统桌面，需要打开淘宝...</think>
  do(action="Launch", app="淘宝")

解析:
  thinking = "当前在系统桌面，需要打开淘宝"
  action = {"action": "Launch", "app": "淘宝"}

执行:
  APP_PACKAGES["淘宝"] → "com.taobao.taobao"
  adb shell monkey -p com.taobao.taobao ...
```

### 3.4 坐标转换

```python
# 模型输出归一化坐标 (0-999)
element = [499, 182]

# 转换为实际像素
x = element[0] / 1000 * screen_width   # 499/1000*1080 = 539
y = element[1] / 1000 * screen_height  # 182/1000*2400 = 437

# iOS 额外处理 (Retina 3x)
x = x / SCALE_FACTOR  # 539/3 = 180
y = y / SCALE_FACTOR  # 437/3 = 146
```

---

## 四、设备控制层

### 4.1 工厂模式抽象

```python
class DeviceFactory:
    @staticmethod
    def create(device_type: DeviceType):
        if device_type == DeviceType.ADB:
            return AdbDevice(...)
        elif device_type == DeviceType.HDC:
            return HdcDevice(...)
```

iOS 使用独立的 Agent 类（`IOSPhoneAgent`）而非工厂模式。

### 4.2 三平台控制实现

| 操作 | Android (ADB) | iOS (WDA) | HarmonyOS (HDC) |
|------|---------------|-----------|-----------------|
| 截图 | `screencap -p` + pull | GET `/screenshot` | `hdc shell snapshot` |
| 点击 | `input tap x y` | W3C Actions API | `uitest uiInput click` |
| 滑动 | `input swipe` | `/wda/dragfromtoforduration` | `uitest uiInput swipe` |
| 文本 | ADB Keyboard broadcast | WDA `/wda/keys` | `uitest uiInput inputText` |
| 返回 | `input keyevent 4` | 左边缘右滑手势 | `uitest uiInput keyEvent Back` |
| 启动 | `monkey -p pkg` | WDA `/wda/apps/launch` | `aa start -b bundle` |

### 4.3 应用包名映射

预配置了大量应用的包名/BundleID：
- Android：**188+ 个应用**（淘宝、微信、抖音、支付宝...）
- iOS：**200+ 个应用**
- HarmonyOS：**60+ 个应用**

这是 Open-AutoGLM 的一个独特优势——模型输出 `Launch("淘宝")` 后，框架自动解析为正确的包名。

---

## 五、System Prompt 分析

System Prompt 包含 **18 条详细规则**，覆盖了复杂场景的操作指导：

| 规则 | 内容 |
|------|------|
| 基础 | 一次只执行一个动作，不要杜撰或反复重复 |
| 滑动 | 需要指定确切坐标，不要滑到底部导航栏 |
| 文本输入 | 使用 Type 输入文字，不要修改已正确的内容 |
| 内容查看 | 当正在查看有用信息时，finish 并返回详细内容 |
| 购物车 | 如果需要加入购物车但当前页面没有直接按钮，改用底部"购物车"tab |
| 外卖 | 外卖应用（如美团外卖）的具体操作指导 |
| 搜索 | 如果看到搜索结果，不要再次搜索 |
| 弹窗 | 遇到权限弹窗点击"允许"或关闭 |
| 安全 | 不执行可能导致损失的操作（如确认付款） |

**观察**：System Prompt 中有一些非常具体的应用场景规则（如"红果短剧"、"星穹铁道自动战斗"），暗示模型可能针对特定评测场景做了优化。

---

## 六、优点

### 6.1 专用模型优势
AutoGLM-Phone-9B 是专门为手机 GUI 操作训练的 9B 模型，配合 MobileRL 强化学习优化。在手机操作的坐标预测和操作规划上理论精度优于通用模型。

### 6.2 端到端简洁
截图 → 模型推理 → 操作执行，没有多 Agent 协作、没有 Accessibility Tree 解析，链路最短。~3000 行代码实现完整功能。

### 6.3 三平台支持
同时支持 Android、iOS、HarmonyOS，通过工厂模式统一接口。是唯一支持 HarmonyOS 的开源项目。

### 6.4 丰富的应用映射
448+ 个应用的包名/BundleID 预配置，Launch 操作无需额外的 LLM 查找。

### 6.5 上下文优化
自动移除历史截图，只保留最新一张，有效控制 token 消耗和推理成本。

### 6.6 Thinking 可观测
模型输出显式区分 `<think>` 推理过程和 `<answer>` 执行动作，便于调试和理解决策逻辑。

### 6.7 安全机制
- 敏感操作确认回调
- Take_over 人工接管机制
- 黑屏检测

---

## 七、缺点

### 7.1 模型绑定
完全绑定 AutoGLM-Phone-9B，无法使用 Claude、GPT-4V、Qwen 等其他模型。如果 AutoGLM 在某些场景表现不佳，没有替代方案。

### 7.2 纯视觉的固有限制
不解析 Accessibility Tree / UI Hierarchy：
- 文本读取依赖 OCR，不如直接获取准确
- 不可见元素（如长列表后续项）无法感知
- 小元素定位可能不够精确

### 7.3 每步单动作
每步只执行一个操作，无合批机制。对于确定性的连续操作（如 tap→type→tap），需要 3 步 3 次模型推理。

### 7.4 iOS 配置门槛高
需要 Xcode + WebDriverAgent + libimobiledevice，配置步骤多且容易出错。

### 7.5 部署成本高
自部署需要 24GB+ 显存的 NVIDIA GPU，或使用第三方 API（收费）。对比 phone-agent 可使用任意 LLM API。

### 7.6 未完成功能
`Note` 和 `Call_API` 两个动作是空实现（placeholder）。

### 7.7 硬编码问题
- iOS `SCALE_FACTOR=3` 硬编码，不适配非 3x Retina 设备
- 返回手势坐标硬编码
- 无测试用例

### 7.8 Android 截图效率低
使用 `screencap → pull` 二步法，不如 `adb exec-out screencap -p` 管道传输高效。

---

## 八、五项目全景对比

### 8.1 技术路线

| | Open-AutoGLM | mobile-use | mobile-mcp | phone-agent | Midscene |
|---|---|---|---|---|---|
| **模型** | 专用 VLM (9B) | 通用 LLM | 无 | 通用 LLM | 通用/专用 VLM |
| **UI 理解** | 纯视觉 | Acc. Tree + 截图 | Acc. Tree | Acc. Tree + 按需截图 | 纯视觉 |
| **架构** | 单 Agent 循环 | 5 Agent 协作 | MCP 工具层 | 单 Agent 循环 | SDK 框架 |
| **代码量** | ~3000 行 | ~5000 行 | 3324 行 | ~400 行 | 大型 monorepo |

### 8.2 Open-AutoGLM 与 phone-agent 的直接对比

两者架构最相似（都是单 Agent 循环），核心差异在于 UI 理解方式：

| | Open-AutoGLM | phone-agent |
|---|---|---|
| **UI 理解** | 截图 → VLM | Accessibility Tree（文本） |
| **元素定位** | VLM 输出归一化坐标 | 编号 → 坐标映射 |
| **模型** | 专用 9B VLM | 通用 LLM（Qwen/Claude） |
| **Token/步** | 高（每步发截图） | 低（文本 UI 树） |
| **合批** | 不支持 | 支持（一次多动作） |
| **应用映射** | 448+ 预配置 | 手动字典 ~10 个 |
| **部署成本** | 24GB GPU 或付费 API | 任意 LLM API |
| **HarmonyOS** | ✅ | ❌ |

### 8.3 适用场景

| 场景 | 最佳选择 |
|------|---------|
| 需要最高操作精度（专用模型） | **Open-AutoGLM** |
| HarmonyOS 自动化 | **Open-AutoGLM** |
| 快速/低成本自动化 | **phone-agent** |
| 多模型灵活切换 | **phone-agent** |
| Web UI 测试 | **Midscene** |
| MCP 标准集成 | **mobile-mcp** |
| 超长复杂任务 | **mobile-use** |

---

## 九、结论

Open-AutoGLM 代表了**"专用模型 + 端到端视觉"**的技术路线——用专门训练的 9B VLM 直接从截图理解界面并输出操作坐标。这与 phone-agent 的"通用模型 + Accessibility Tree"路线形成鲜明对比。

**核心权衡**：
- 专用模型在手机 GUI 操作场景可能更精准，但部署成本高（24GB GPU）且不可替换
- 通用模型 + Accessibility Tree 更灵活、更便宜、更快，但无法处理无标签 UI

**2026 年的判断**：通用 LLM 的推理能力已经足够强，Accessibility Tree 提供的结构化信息比纯视觉更高效可靠。专用模型的优势主要在**无 Accessibility 数据的场景**（如游戏、Canvas）和**需要极高精度的场景**（如精确到像素的操作）。

Open-AutoGLM 的最大价值在于其**研究贡献**——AutoGLM 论文和 MobileRL 方法为 GUI Agent 领域提供了重要的学术参考。448+ 应用的包名映射和详细的 System Prompt 也是实用的工程资产。

---

## 十、四种设计哲学总结

| 项目 | 哲学 | 一句话 |
|------|------|--------|
| **Open-AutoGLM** | 专用模型 + 纯视觉 | "训练一个专家来操作手机" |
| **Midscene** | 通用 VLM + 纯视觉 | "用眼睛看世界" |
| **mobile-use** | 通用 LLM + 多 Agent + 无障碍树 | "团队协作完成任务" |
| **mobile-mcp** | 无 AI + MCP 工具层 | "提供好工具，决策交给你" |
| **phone-agent** | 通用 LLM + 单 Agent + 无障碍树 | "一个聪明人搞定一切" |
