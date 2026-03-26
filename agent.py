"""
Phone Agent v3 - 极速单 Agent 手机自动化控制
优化：双模型策略、激进合批、智能 UI 树、首轮截图
用法: python agent.py "打开小红书搜索美食推荐，告诉我前3个帖子标题"
"""

import sys
import time
import json
import base64
import re
import os
from io import BytesIO
from xml.etree import ElementTree

import uiautomator2 as u2
import anthropic

# ─── 配置 ───
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL_FAST = os.environ.get("PHONE_AGENT_MODEL_FAST", "claude-haiku-4-5-20251001")
MODEL_SMART = os.environ.get("PHONE_AGENT_MODEL_SMART", "claude-sonnet-4-6")
MAX_STEPS = 40

# ─── 工具定义 ───
TOOLS = [
    {
        "name": "actions",
        "description": "执行一个或多个连续手机操作。尽量把确定性的连续操作放在一起批量执行。",
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string", "description": "简要思路(<20字)"},
                "steps": {
                    "type": "array",
                    "description": "操作列表，按顺序执行",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["tap", "tap_xy", "type", "swipe", "back", "home", "launch", "wait"]
                            },
                            "element_id": {"type": "integer"},
                            "x": {"type": "integer"}, "y": {"type": "integer"},
                            "text": {"type": "string"},
                            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                            "seconds": {"type": "number"}
                        },
                        "required": ["action"]
                    }
                }
            },
            "required": ["thought", "steps"]
        }
    },
    {
        "name": "screenshot",
        "description": "获取屏幕截图。仅在 UI 树不足以判断视觉内容时使用（如图片、图表）。",
        "input_schema": {
            "type": "object",
            "properties": {"thought": {"type": "string"}},
            "required": ["thought"]
        }
    },
    {
        "name": "done",
        "description": "任务完成，返回结果。",
        "input_schema": {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"]
        }
    }
]

# ─── App 映射 ───
APP_MAP = {
    "小红书": "com.xingin.xhs", "抖音": "com.ss.android.ugc.aweme",
    "微信": "com.tencent.mm", "高德地图": "com.autonavi.minimap",
    "百度地图": "com.baidu.BaiduMap", "淘宝": "com.taobao.taobao",
    "支付宝": "com.eg.android.AlipayGphone", "chrome": "com.android.chrome",
    "设置": "com.android.settings",
}

SYSTEM_PROMPT = """你是一个极速手机操控助手。你的目标是用最少的步骤完成任务。

## 核心规则
1. **激进合批**：把所有确定性连续操作放在一个 actions 调用里。例如：launch app + wait 2s 是一步；tap搜索框 + type文字 + tap搜索按钮 是一步。
2. 优先用 element_id 点击
3. 只在需要看图片/视觉内容时才用 screenshot，纯文本信息从 UI 树读取
4. thought 极简(<20字)
5. 完成立即 done，不要多余确认
6. 果断行动，不犹豫不反复

## UI 树格式
[编号] 类型 "文本" (C=可点击 F=聚焦 id:资源ID)

## App 操作技巧
- **切换 App**：永远用 launch 命令直接启动 App，不要手动在桌面找图标。launch 支持中文名如"高德地图""小红书"。
- **高德地图**：直接搜索框输入地点名搜索。搜索结果第一条是主地点，点击进入详情。详情页"路线"=导航规划(驾车/公交时间)，"打车"=叫车费用。路线页顶部标签切换驾车/公交。弹窗选门→点跳过。
- **小红书**：区分广告帖(有广告标签)和普通帖。图片内容需 screenshot 查看。
"""


class PhoneAgent:
    def __init__(self, device_serial=None):
        if device_serial:
            self.device = u2.connect(device_serial)
        else:
            self.device = u2.connect()
        info = self.device.info
        self.width = info["displayWidth"]
        self.height = info["displayHeight"]
        print(f"📱 已连接: {self.device.serial} ({self.width}x{self.height})")
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.elements = {}
        self._needs_screenshot = False  # 标记下一轮是否需要截图

    def get_ui_tree(self) -> str:
        """获取精简 UI 树，更激进地过滤无用元素"""
        xml = self.device.dump_hierarchy()
        root = ElementTree.fromstring(xml)
        self.elements = {}
        lines = []
        idx = 0
        seen_texts = set()

        for node in root.iter():
            text = node.attrib.get("text", "").strip()
            desc = node.attrib.get("content-desc", "").strip()
            cls = node.attrib.get("class", "").split(".")[-1]
            clickable = node.attrib.get("clickable") == "true"
            focused = node.attrib.get("focused") == "true"
            bounds_str = node.attrib.get("bounds", "")
            resource_id = node.attrib.get("resource-id", "")

            if not text and not desc and not clickable:
                continue

            m = re.findall(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m[0])
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

            if cx < 0 or cy < 0 or cx > self.width or cy > self.height:
                continue
            if (x2 - x1) < 5 or (y2 - y1) < 5:
                continue

            # 去重：相同文本+相近位置的元素只保留一个
            label = text or desc or ""
            dedup_key = f"{label[:20]}_{cx//50}_{cy//50}"
            if label and dedup_key in seen_texts and not clickable:
                continue
            if label:
                seen_texts.add(dedup_key)

            idx += 1
            self.elements[idx] = {"x": cx, "y": cy}

            # 极简格式
            flags = ""
            if clickable: flags += "C"
            if focused: flags += "F"
            rid = resource_id.split("/")[-1] if resource_id else ""

            parts = [f"[{idx}]"]
            if label:
                parts.append(f'"{label[:35]}"' if len(label) > 35 else f'"{label}"')
            else:
                parts.append(cls)
            if flags:
                parts.append(flags)
            if rid and len(rid) < 25:
                parts.append(rid)
            lines.append(" ".join(parts))

        return "\n".join(lines) if lines else "(空)"

    def take_screenshot_b64(self) -> str:
        img = self.device.screenshot()
        img = img.resize((img.width // 3, img.height // 3))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=45)
        return base64.standard_b64encode(buf.getvalue()).decode()

    def execute_action(self, action: dict) -> str:
        act = action["action"]

        if act == "tap":
            eid = action.get("element_id")
            if eid not in self.elements:
                return f"❌ [{eid}]不存在"
            el = self.elements[eid]
            self.device.click(el["x"], el["y"])
            return f"✅tap[{eid}]"

        elif act == "tap_xy":
            self.device.click(action["x"], action["y"])
            return f"✅tap({action['x']},{action['y']})"

        elif act == "type":
            self.device.clear_text()
            self.device.send_keys(action["text"])
            return f"✅type'{action['text']}'"

        elif act == "swipe":
            d = action.get("direction", "up")
            cx, cy = self.width // 2, self.height // 2
            dist = self.height // 3
            swipes = {
                "up": (cx, cy + dist, cx, cy - dist),
                "down": (cx, cy - dist, cx, cy + dist),
                "left": (cx + dist, cy, cx - dist, cy),
                "right": (cx - dist, cy, cx + dist, cy),
            }
            self.device.swipe(*swipes[d], duration=0.3)
            return f"✅swipe_{d}"

        elif act == "back":
            self.device.press("back")
            return "✅back"

        elif act == "home":
            self.device.press("home")
            return "✅home"

        elif act == "launch":
            app = action.get("text", "")
            package = APP_MAP.get(app, app)
            self.device.app_start(package)
            time.sleep(1.2)
            self._needs_screenshot = True  # App 启动后下一轮自动截图
            return f"✅launch:{app}"

        elif act == "wait":
            sec = min(action.get("seconds", 1), 3)
            time.sleep(sec)
            return f"✅wait{sec}s"

        return f"❌{act}"

    def execute_actions(self, args: dict) -> str:
        steps = args.get("steps", [])
        results = []

        for i, step in enumerate(steps):
            result = self.execute_action(step)
            results.append(result)
            print(f"   {result}")
            # 轻微等待让 UI 稳定
            if step["action"] in ("tap", "tap_xy") and i < len(steps) - 1:
                time.sleep(0.2)

        # 最后一个操作后短暂等待
        last_act = steps[-1]["action"] if steps else ""
        if last_act in ("tap", "tap_xy", "launch"):
            time.sleep(0.4)

        return " | ".join(results)

    def choose_model(self, messages: list) -> str:
        """根据上下文选择模型：有截图时用 smart，纯 UI 树用 fast"""
        # 检查最近的消息是否包含截图
        last_msg = messages[-1] if messages else {}
        content = last_msg.get("content", "")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    # tool_result 里可能嵌套 content
                    inner = item.get("content", "")
                    if isinstance(inner, list):
                        for sub in inner:
                            if isinstance(sub, dict) and sub.get("type") == "image":
                                return MODEL_SMART
        return MODEL_FAST

    def compress_messages(self, messages: list) -> list:
        if len(messages) <= 12:
            return messages

        middle = messages[1:-8]
        summary_parts = []
        for msg in middle:
            if msg["role"] == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if hasattr(block, "type") and block.type == "tool_use":
                            if block.name == "actions":
                                thought = block.input.get("thought", "")
                                summary_parts.append(thought)
                            elif block.name == "done":
                                summary_parts.append("[完成]")

        summary = "已完成: " + " → ".join(summary_parts[-8:]) if summary_parts else ""

        compressed = messages[:1]
        if summary:
            compressed.append({"role": "user", "content": summary})
            compressed.append({"role": "assistant", "content": "了解，继续。"})
        compressed.extend(messages[-8:])
        return compressed

    def run(self, task: str):
        print(f"\n🎯 任务: {task}\n")
        start_time = time.time()
        messages = []
        total_actions = 0
        llm_calls = {"fast": 0, "smart": 0}

        ui_tree = self.get_ui_tree()
        messages.append({
            "role": "user",
            "content": f"任务: {task}\n\nUI:\n{ui_tree}"
        })

        for step in range(1, MAX_STEPS + 1):
            elapsed = time.time() - start_time
            model = self.choose_model(messages)
            model_tag = "⚡" if model == MODEL_FAST else "🧠"
            print(f"\n── 步骤 {step} ({elapsed:.0f}s) {model_tag} ──")

            compressed = self.compress_messages(messages)

            response = self.client.messages.create(
                model=model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=compressed,
            )

            if model == MODEL_FAST:
                llm_calls["fast"] += 1
            else:
                llm_calls["smart"] += 1

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            tool_use = None
            for block in assistant_content:
                if block.type == "text" and block.text.strip():
                    print(f"   🤖 {block.text.strip()[:80]}")
                elif block.type == "tool_use":
                    tool_use = block

            if not tool_use:
                messages.append({"role": "user", "content": "调用工具执行。"})
                continue

            # ── done ──
            if tool_use.name == "done":
                elapsed = time.time() - start_time
                result = tool_use.input.get("result", "")
                print(f"\n{'='*50}")
                print(f"✅ 任务完成!\n")
                print(result)
                print(f"\n⏱️  总耗时: {elapsed:.1f}秒 ({int(elapsed//60)}分{int(elapsed%60)}秒)")
                print(f"📊 LLM: {step}次 (⚡Haiku:{llm_calls['fast']} 🧠Sonnet:{llm_calls['smart']}) | 操作: {total_actions}次")
                print(f"{'='*50}")
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_use.id, "content": "done"}]
                })
                return result

            # ── screenshot ──
            if tool_use.name == "screenshot":
                thought = tool_use.input.get("thought", "")
                print(f"   📸 {thought[:50]}")
                img_b64 = self.take_screenshot_b64()
                ui_tree = self.get_ui_tree()
                self._needs_screenshot = False
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_use.id, "content": [
                        {"type": "text", "text": f"UI:\n{ui_tree}"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}}
                    ]}]
                })
                continue

            # ── actions ──
            if tool_use.name == "actions":
                thought = tool_use.input.get("thought", "")
                steps_list = tool_use.input.get("steps", [])
                n = len(steps_list)
                total_actions += n
                print(f"   💭 {thought}")
                print(f"   🔧 {n}个操作:")

                result = self.execute_actions(tool_use.input)

                # App 启动后自动附带截图
                if self._needs_screenshot:
                    img_b64 = self.take_screenshot_b64()
                    ui_tree = self.get_ui_tree()
                    self._needs_screenshot = False
                    print(f"   📸 自动截图(新App)")
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_use.id, "content": [
                            {"type": "text", "text": f"{result}\n\nUI:\n{ui_tree}"},
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}}
                        ]}]
                    })
                else:
                    ui_tree = self.get_ui_tree()
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_use.id,
                                     "content": f"{result}\n\nUI:\n{ui_tree}"}]
                    })
                continue

            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_use.id, "content": "未知工具"}]
            })

        elapsed = time.time() - start_time
        print(f"\n⚠️ 达到最大步数，耗时: {elapsed:.1f}秒")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python agent.py \"你的任务指令\"")
        sys.exit(1)
    PhoneAgent().run(sys.argv[1])
