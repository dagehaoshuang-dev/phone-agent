# 移动端 AI 自动化项目 Benchmark 与测试案例收集

> 收集自 mobile-use、mobile-mcp、Midscene、Open-AutoGLM 四个开源项目
> 日期：2026-03-27

---

## 一、Benchmark 数据集总览

| 项目 | 使用的 Benchmark | 成绩 | 论文 |
|------|-----------------|------|------|
| **mobile-use** | AndroidWorld | **100%**（首个满分） | arXiv:2602.07787 (2026) |
| **mobile-mcp** | 无 | - | - |
| **Midscene** | ScreenSpot-v2 + 自建评测体系 | >50%（ScreenSpot-v2） | - |
| **Open-AutoGLM** | 论文中有评测（仓库内无） | 见论文 | arXiv:2411.00820, arXiv:2509.18119 |

---

## 二、mobile-use 测试案例

### 2.1 AndroidWorld Benchmark

- **成绩**：100% 完成率，业界首个满分
- **排行榜**：[Google Sheets](https://docs.google.com/spreadsheets/d/1cchzP9dlTZ3WXQTfYNhh3avxoLipqHN75v1Tb86uhHo/)
- **论文**："Do Multi-Agents Dream of Electric Screens? Achieving Perfect Accuracy on AndroidWorld Through Task Decomposition"

### 2.2 示例任务

| 任务 | 类型 | 复杂度 |
|------|------|--------|
| "Go to settings and tell me my current battery level" | 基础操作 | 简单 |
| "Open Gmail, find all unread emails, and list their sender and subject line" | 数据抓取 | 中等 |
| "Open Gmail, find first 3 unread emails, and list their sender and subject line" | 数据抓取 | 中等 |
| "Open settings app, find the apps section, tap on it and search for Reddit" | 导航搜索 | 中等 |
| "Find the first 3 unread emails in Gmail"（JSON 输出） | 结构化输出 | 中等 |
| 打开 YouTube → 搜索 "Python tutorial" → 录屏 → 播放视频 → 转录内容 | 视频工作流 | 复杂 |

### 2.3 单元测试覆盖

| 测试文件 | 测试项 | 数量 |
|----------|--------|------|
| test_idb_client.py | iOS 模拟器集成（初始化/截图/层级/完整流程） | 4 |
| test_outputter.py | 输出格式（Pydantic/Dict/自然语言） | 3 |
| test_utils.py | 光标移动 + 元素聚焦 | 10 |
| test_ui_hierarchy.py | UI 层级解析（文本/ID/聚焦/bounds） | 7 |
| test_limrun.py | Limrun 云设备控制 | 5 |

### 2.4 CI 自动化

- iOS 测试：macOS-14 + iPhone 15 模拟器 (iOS 17.2)

---

## 三、mobile-mcp 测试案例

### 3.1 无正式 Benchmark

该项目不包含任何正式的 benchmark 评测。

### 3.2 单元测试

仅 1 个测试文件 `mobilecli.test.ts`，测试 `Mobilecli` 类：

| 测试组 | 测试项 | 数量 |
|--------|--------|------|
| getVersion | 版本字符串格式、无效路径错误 | 4 |
| getDevices | 设备过滤（平台/类型/离线/组合） | 5 |

### 3.3 README 示例任务（丰富）

| 任务 | 复杂度 | App |
|------|--------|-----|
| YouTube 搜索视频 → 点赞 → 评论 → 通过 WhatsApp 分享 | 高 | YouTube + WhatsApp |
| 应用商店找免费 Pomodoro app → 下载 → 注册 → 启动计时器 → 返回评分 5 星 | 高 | App Store |
| Substack 搜索 AI 文章 → 高亮 → 保存阅读列表 → 评论 | 高 | Substack |
| ClassPass 搜索明天瑜伽课 → 预订 → 设置定时器 | 高 | ClassPass |
| Eventbrite 搜索 AI meetup → 注册 → 设置日历提醒 | 中 | Eventbrite |
| 查天气 → 通过 WhatsApp/Telegram/Slack 发送给联系人 | 中 | 天气 + 通讯 |
| Zoom 安排会议 → 复制邀请链接 → Gmail 发送 | 中 | Zoom + Gmail |

---

## 四、Midscene 测试案例

### 4.1 ScreenSpot-v2 Benchmark

- **数据集**：HuggingFace `Voxel51/ScreenSpot-v2`
- **目标**：移动 UI 元素定位评测
- **通过标准**：准确率 > 50%
- **判定逻辑**：预测 rect 是否在 ground truth bounding box 内

### 4.2 自建评测体系（最完整）

#### Locator 评测（元素定位）—— 7 个页面 44 个用例

**antd-carousel（Ant Design 轮播）—— 9 个用例：**
| 用例 | 描述 |
|------|------|
| 1 | '最简单的用法' 下方五个 icon，左侧第一个 |
| 2 | '最简单的用法' 下方五个 icon，左侧第二个 |
| 3 | '最简单的用法' 下方五个 icon，左侧第三个 |
| 4 | '最简单的用法' 下方五个 icon，左侧第四个 |
| 5 | '最简单的用法' 下方五个 icon，最右侧 |
| 6 | 全屏幕右上角三个 icon，左侧第一个 |
| 7 | 全屏幕右上角三个 icon，左侧第二个 |
| 8 | 全屏幕右上角三个 icon，左侧第三个 |
| 9 | '代码演示' 右侧三个 icon 按钮中，最中间的按钮 |

**todo（Todo 应用）—— 6 个用例：**
| 用例 | 描述 |
|------|------|
| 1 | Input current time in the task box |
| 2 | Input 'Study Rust tomorrow' in the task box |
| 3 | 任务列表中的第二项名称 |
| 4 | 第二项任务右边的删除按钮 |
| 5 | 任务列表中第三项左边的勾选按钮 |
| 6 | 任务列表下面的 Completed 状态按钮 |

**online_order（在线点单）—— 6 个用例：**
| 用例 | 描述 |
|------|------|
| 1 | Top left menu bar icon |
| 2 | 左上角语言切换按钮 |
| 3 | Top right shopping cart |
| 4 | The text indicating the price of the upper drink |
| 5 | 最下面一种饮料的选择规格按钮 |
| 6 | Bottom right Customer service button (rounded icon) |

**online_order_list（饮品列表）—— 4 个用例：**
| 用例 | 描述 |
|------|------|
| 1 | '清爽不喝腻' 下面第二个饮品的名称 |
| 2 | 多肉葡萄的选择规格按钮 |
| 3 | 多肉葡萄的价格 |
| 4 | 左侧导航栏 '要雪糕' |

**taobao（淘宝）—— 6 个用例：**
| 用例 | 描述 | Deep Think |
|------|------|-----------|
| 1 | 商品搜索框 | ❌ |
| 2 | 搜索按钮 | ❌ |
| 3 | 产品分类里面的：男鞋（文字） | ✅ |
| 4 | 右侧 '立即登录' 下方的收藏夹 icon | ✅ |
| 5 | 最右侧五个悬浮按钮的第二个 | ✅ |
| 6 | 顶部工具栏的购物车 icon | ✅ |

**aweme-login（抖音登录）—— 7 个用例：**
| 用例 | 描述 |
|------|------|
| 1 | 密码登录 |
| 2 | 扫码登录 |
| 3 | 手机号输入框 |
| 4 | 验证码输入框 |
| 5 | 发送验证码按钮 |
| 6 | 登录按钮 |
| 7 | X 关闭按钮 |

**aweme-play（抖音播放）—— 6 个用例：**
| 用例 | 描述 |
|------|------|
| 1 | 左下角暂停按钮 |
| 2 | 点赞（爱心）按钮 |
| 3 | 评论按钮 |
| 4 | 书签收藏按钮 |
| 5 | 分享按钮 |
| 6 | 右下角区域声音按钮 |

#### Planning 评测（动作规划）—— 12+ 个用例

| 用例 | 描述 | 场景 |
|------|------|------|
| 1 | "type 'hello' in the input box, sleep 5s, hit enter" | todo-vl |
| 2 | "if there are five entries in the list, click the delete button of the second entry and wait 3s. Otherwise, do nothing." | todo-vl |
| 3 | "type 'hello' in the input box" | todo-vl |
| 4 | "click OK"（预期报错，无 OK 按钮） | todo-vl |
| 5 | "if there is an 'OK' button, click it"（预期不执行） | todo-vl |
| 6 | "if there is an 'OK' button, click it. If not, try again in next step" | todo-vl |
| 7 | "Move the mouse to the second item and click delete" | todo-vl |
| 8 | "在任务框 input 输入今天学习 JS，按回车键" | todo-vl |
| 9 | "Click the 'clear completed' button"（带 action_context） | todo-vl |
| 10-12 | 多步上下文延续测试 | todo-vl |
| 13+ | aweme-login-vl、antd-form-vl、antd-tooltip-vl 场景 | 多页面 |

#### Assertion 评测（页面断言）—— 10 个用例

| 用例 | 断言内容 | 期望 |
|------|---------|------|
| 1 | "there are three tabs named 'Menu', 'Reviews', 'Merchant'" | TRUE |
| 2 | "there is a shopping bag icon on the top right" | TRUE |
| 3 | "the 'select option' button is blue" | FALSE |
| 4 | "the tab name on the right of 'Reviews' is 'Merry'" | FALSE |
| 5 | "three tabs named 'Home', 'Order', 'Profile'" | FALSE |
| 6 | "shopping bag icon on the top left" | FALSE |
| 7 | "homepage icon on the top right instead of shopping bag" | FALSE |
| 8 | "左侧有个菜单写着'要简单'" | TRUE |
| 9 | "左侧有个菜单写着'要米饭'" | FALSE |
| 10 | "有一杯饮料的名字是多肉忙忙" | FALSE |

#### Section Locator 评测（区域定位）—— 4 个用例

| 用例 | 描述 |
|------|------|
| 1 | "the version info on the top right corner" |
| 2 | "'位置有12个方向' 上面的一圈按钮" |
| 3 | "'位置有12个方向' 上面的 Top 按钮" |
| 4 | "a series of buttons under 'show/hide' switch" |

### 4.3 评测页面数据

14 个页面截图：antd-carousel, antd-form, antd-pagination, antd-tooltip, aweme-login, aweme-play, githubstatus, image-only, online_order, online_order_list, taobao, todo, todo-input-with-value, visualstudio

---

## 五、Open-AutoGLM 测试案例

### 5.1 无仓库内 Benchmark

仓库中没有独立的 benchmark 评测代码。评测数据在引用的论文中：
- AutoGLM (arXiv:2411.00820)
- MobileRL (arXiv:2509.18119)

### 5.2 示例任务

| 任务 | 语言 | App |
|------|------|-----|
| "打开小红书搜索美食攻略" | 中文 | 小红书 |
| "打开淘宝搜索无线耳机并加入购物车" | 中文 | 淘宝 |
| "打开美团搜索附近的火锅店" | 中文 | 美团 |
| "打开高德地图查看实时路况" | 中文 | 高德地图 |
| "打开大众点评搜索附近的咖啡店" | 中文 | 大众点评 |
| "打开bilibili搜索Python教程" | 中文 | B站 |
| "打开微信查看消息" | 中文 | 微信 |
| "Open Chrome browser" | 英文 | Chrome |
| "Open Maps and search for nearby coffee shops" | 英文 | Google Maps |
| "Open eBay and search for wireless earphones" | 英文 | eBay |

### 5.3 支持的应用规模

| 平台 | 预配置应用数 |
|------|------------|
| Android | 188+ |
| iOS | 200+ |
| HarmonyOS | 60+ |
| 英文 App | 50+ |

---

## 六、Benchmark 对比分析

### 6.1 评测维度

| 评测维度 | mobile-use | mobile-mcp | Midscene | Open-AutoGLM |
|----------|-----------|------------|----------|--------------|
| 端到端任务完成 | ✅ AndroidWorld 100% | ❌ | ❌ | 论文中有 |
| 元素定位精度 | ❌ | ❌ | ✅ ScreenSpot-v2 + 自建 | 论文中有 |
| 动作规划准确 | ❌ | ❌ | ✅ 自建 Planning 评测 | ❌ |
| 页面理解/断言 | ❌ | ❌ | ✅ 自建 Assertion 评测 | ❌ |
| 单元测试 | 中等 (29 个) | 少 (9 个) | 丰富 (70+ 个) | 无 |

### 6.2 评测成熟度排名

1. **Midscene**：最完整的自建评测体系（Locator + Planning + Assertion + Section + ScreenSpot-v2），CI 自动化
2. **mobile-use**：有正式外部 Benchmark（AndroidWorld 100%），但自建测试较少
3. **Open-AutoGLM**：有论文级评测但仓库内无代码
4. **mobile-mcp**：几乎无评测

### 6.3 对 phone-agent 的启示

phone-agent 目前没有任何 benchmark。可以借鉴：

1. **从 Midscene 借鉴**：
   - 元素定位评测：给定页面截图+自然语言描述，验证是否点击正确元素
   - 动作规划评测：给定任务描述，验证生成的操作序列是否合理
   - 页面断言评测：验证 Agent 对页面状态的理解是否正确

2. **从 mobile-use 借鉴**：
   - AndroidWorld 作为端到端评测基准
   - 完整任务场景的成功率测试

3. **自建评测**：
   - 跨 App 任务完成率（小红书→高德→大众点评）
   - 任务耗时对比
   - LLM 调用次数/Token 消耗
   - 操作准确率（无误操作步骤占比）
