"""
Phone Agent v2 - 高性能单 Agent 手机自动化控制
优化：多动作合批、精简 UI 树、智能等待、上下文压缩
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
MODEL = os.environ.get("PHONE_AGENT_MODEL", "claude-sonnet-4-6")
MAX_STEPS = 40

# ─── 工具定义（精简 schema，减少 token） ───
TOOLS = [
    {
        "name": "actions",
        "description": "执行一个或多个连续手机操作。支持在一次调用中批量执行多个动作，减少往返次数。",
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string", "description": "简要说明这一轮的思路"},
                "steps": {
                    "type": "array",
                    "description": "要执行的操作列表，按顺序执行",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["tap", "tap_xy", "type", "swipe", "back", "home", "launch", "wait"]
                            },
                            "element_id": {"type": "integer", "description": "tap 时的元素编号"},
                            "x": {"type": "integer"}, "y": {"type": "integer"},
                            "text": {"type": "string", "description": "type 时的文本，或 launch 时的 App 名称"},
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
        "description": "获取屏幕截图。仅在 UI 树不足以判断时使用。",
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
            "properties": {"result": {"type": "string", "description": "最终结果"}},
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

SYSTEM_PROMPT = """你是一个高效的手机操控助手，通过工具控制 Android 手机完成任务。

## 核心规则
1. 使用 actions 工具时可以把多个连续操作放在一个 steps 数组里批量执行（如：点击搜索框 → 输入文字 → 点击搜索按钮）
2. 优先用 element_id 点击，避免用坐标
3. 只在 UI 树信息不足时才用 screenshot
4. thought 要简短（<30字）
5. 任务完成立即调用 done
6. 不要犹豫和反复确认，果断执行操作
7. 记住已获取的信息，不要重复去查看

## UI 树格式
[编号] 类型 "文本" (属性)
属性: C=可点击 F=已聚焦 id:资源ID

## 批量操作示例
可以一次执行多步：tap搜索框 → type输入内容 → tap搜索按钮
不确定结果的操作（如页面跳转后）单独执行，等下一轮看到新 UI 树再决策。

## 常见 App 操作技巧
- **高德地图搜索**：直接在搜索框输入地点名称后点搜索按钮，比点历史记录更可靠。搜索结果列表中第一条通常是主地点，下面的是子地点（停车场、出入口等），优先点击第一条。
- **高德地图详情页按钮区分**：详情页底部通常有"路线"和"打车"两个按钮。"路线"是查看自驾/公交/步行时间的导航规划；"打车"是叫车服务和费用估算。根据用户需求选择正确的按钮。
- **高德地图路线规划**：进入后页面顶部有"公共交通"/"驾车"/"步行"标签页，点击切换查看。如果弹出"选择目的地门"弹窗，直接点"跳过"。
- **小红书搜索**：搜索后注意区分广告帖和普通帖子，广告帖通常有"广告"标签。
- **小红书帖子**：正文如果被截断有"展开"按钮，图片内容需要用 screenshot 工具查看。
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
        self._last_ui_hash = None

    def get_ui_tree(self) -> str:
        """获取精简 UI 树"""
        xml = self.device.dump_hierarchy()
        root = ElementTree.fromstring(xml)
        self.elements = {}
        lines = []
        idx = 0

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
            # 跳过太小的不可见元素
            if (x2 - x1) < 5 or (y2 - y1) < 5:
                continue

            idx += 1
            self.elements[idx] = {"x": cx, "y": cy}

            # 极简格式: [1] Button "搜索" C id:search
            label = text or desc or ""
            flags = ""
            if clickable: flags += "C"
            if focused: flags += "F"
            rid = resource_id.split("/")[-1] if resource_id else ""

            parts = [f"[{idx}]", cls]
            if label:
                # 截断长文本
                parts.append(f'"{label[:40]}"' if len(label) > 40 else f'"{label}"')
            if flags:
                parts.append(flags)
            if rid:
                parts.append(f"id:{rid}")
            lines.append(" ".join(parts))

        tree_str = "\n".join(lines) if lines else "(空)"
        self._last_ui_hash = hash(tree_str)
        return tree_str

    def take_screenshot_b64(self) -> str:
        img = self.device.screenshot()
        img = img.resize((img.width // 3, img.height // 3))  # 缩小到1/3
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=50)
        return base64.standard_b64encode(buf.getvalue()).decode()

    def execute_action(self, action: dict) -> str:
        """执行单个 action"""
        act = action["action"]

        if act == "tap":
            eid = action.get("element_id")
            if eid not in self.elements:
                return f"❌ [{eid}] 不存在"
            el = self.elements[eid]
            self.device.click(el["x"], el["y"])
            return f"✅ tap [{eid}]"

        elif act == "tap_xy":
            self.device.click(action["x"], action["y"])
            return f"✅ tap ({action['x']},{action['y']})"

        elif act == "type":
            self.device.clear_text()
            self.device.send_keys(action["text"])
            return f"✅ type '{action['text']}'"

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
            return f"✅ swipe {d}"

        elif act == "back":
            self.device.press("back")
            return "✅ back"

        elif act == "home":
            self.device.press("home")
            return "✅ home"

        elif act == "launch":
            app = action.get("text", "")
            package = APP_MAP.get(app, app)
            self.device.app_start(package)
            time.sleep(1.5)
            return f"✅ launch {app}"

        elif act == "wait":
            sec = min(action.get("seconds", 1), 3)
            time.sleep(sec)
            return f"✅ wait {sec}s"

        return f"❌ unknown: {act}"

    def execute_actions(self, args: dict) -> str:
        """批量执行多个 action"""
        steps = args.get("steps", [])
        results = []
        needs_page_load = False

        for i, step in enumerate(steps):
            result = self.execute_action(step)
            results.append(result)
            print(f"   {result}")

            act = step["action"]
            # 页面跳转类操作之间加短暂等待
            if act in ("tap", "tap_xy", "launch") and i < len(steps) - 1:
                time.sleep(0.3)
            # 标记是否需要等页面加载
            if act in ("launch", "tap", "tap_xy"):
                needs_page_load = True

        # 最后一个操作后等待页面稳定
        if needs_page_load:
            time.sleep(0.5)

        return " | ".join(results)

    def compress_messages(self, messages: list) -> list:
        """压缩历史消息：用摘要替换中间部分，保留完整上下文"""
        if len(messages) <= 12:
            return messages

        # 提取中间消息的关键操作摘要
        middle = messages[1:-8]
        summary_parts = []
        for msg in middle:
            if msg["role"] == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if hasattr(block, "type"):
                            if block.type == "text" and block.text.strip():
                                summary_parts.append(block.text.strip()[:60])
                            elif block.type == "tool_use":
                                summary_parts.append(f"[调用:{block.name}]")

        summary = "之前已完成的操作摘要：" + " → ".join(summary_parts[-10:]) if summary_parts else ""

        # 第1条(任务) + 摘要 + 最后8条(近期上下文)
        compressed = messages[:1]
        if summary:
            compressed.append({"role": "user", "content": summary})
            compressed.append({"role": "assistant", "content": "好的，我了解之前的操作进度，继续执行任务。"})
        compressed.extend(messages[-8:])
        return compressed

    def run(self, task: str):
        print(f"\n🎯 任务: {task}\n")
        start_time = time.time()
        messages = []
        total_actions = 0

        ui_tree = self.get_ui_tree()
        messages.append({
            "role": "user",
            "content": f"任务: {task}\n\nUI:\n{ui_tree}"
        })

        for step in range(1, MAX_STEPS + 1):
            elapsed = time.time() - start_time
            print(f"\n── 步骤 {step} ({elapsed:.0f}s) ──")

            # 压缩历史消息
            compressed = self.compress_messages(messages)

            response = self.client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=compressed,
            )

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            tool_use = None
            for block in assistant_content:
                if block.type == "text" and block.text.strip():
                    print(f"   🤖 {block.text.strip()[:80]}")
                elif block.type == "tool_use":
                    tool_use = block

            if not tool_use:
                messages.append({"role": "user", "content": "请调用工具执行下一步。"})
                continue

            # ── done ──
            if tool_use.name == "done":
                elapsed = time.time() - start_time
                result = tool_use.input.get("result", "")
                print(f"\n{'='*50}")
                print(f"✅ 任务完成!\n")
                print(result)
                print(f"\n⏱️  总耗时: {elapsed:.1f}秒 ({int(elapsed//60)}分{int(elapsed%60)}秒)")
                print(f"📊 LLM 调用: {step}次 | 设备操作: {total_actions}次")
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
                print(f"   🔧 执行 {n} 个操作:")

                result = self.execute_actions(tool_use.input)

                ui_tree = self.get_ui_tree()
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_use.id,
                                 "content": f"{result}\n\nUI:\n{ui_tree}"}]
                })
                continue

            # fallback
            print(f"   ⚠️ 未知工具: {tool_use.name}")
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
