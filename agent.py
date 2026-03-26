"""
Phone Agent - 单 Agent 手机自动化控制
用法: python agent.py "打开小红书搜索美食推荐，告诉我前3个帖子标题"
"""

import sys
import time
import json
import base64
import subprocess
import re
from io import BytesIO
from xml.etree import ElementTree

import os

import uiautomator2 as u2
import anthropic

# ─── 配置 ───
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("PHONE_AGENT_MODEL", "claude-sonnet-4-6")
MAX_STEPS = 40

# ─── 工具定义 ───
TOOLS = [
    {
        "name": "tap",
        "description": "点击屏幕上的某个元素。用 element_id 指定 UI 树中的元素编号。",
        "input_schema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "integer", "description": "UI 树中的元素编号"},
                "thought": {"type": "string", "description": "为什么点击这个元素"}
            },
            "required": ["element_id", "thought"]
        }
    },
    {
        "name": "tap_xy",
        "description": "点击屏幕上的指定坐标。仅在 element_id 不可用时使用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "thought": {"type": "string"}
            },
            "required": ["x", "y", "thought"]
        }
    },
    {
        "name": "type_text",
        "description": "在当前聚焦的输入框中输入文本。",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要输入的文本"},
                "thought": {"type": "string"}
            },
            "required": ["text", "thought"]
        }
    },
    {
        "name": "swipe",
        "description": "在屏幕上滑动。",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "description": "滑动方向"},
                "thought": {"type": "string"}
            },
            "required": ["direction", "thought"]
        }
    },
    {
        "name": "press_back",
        "description": "按返回键。",
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string"}
            },
            "required": ["thought"]
        }
    },
    {
        "name": "press_home",
        "description": "按 Home 键回到主屏幕。",
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string"}
            },
            "required": ["thought"]
        }
    },
    {
        "name": "launch_app",
        "description": "启动一个 App。提供 App 名称或包名。",
        "input_schema": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "App 名称（如'小红书'）或包名（如'com.xingin.xhs'）"},
                "thought": {"type": "string"}
            },
            "required": ["app", "thought"]
        }
    },
    {
        "name": "screenshot",
        "description": "获取当前屏幕截图。当 UI 树信息不足以判断屏幕内容时使用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string"}
            },
            "required": ["thought"]
        }
    },
    {
        "name": "wait",
        "description": "等待一段时间让页面加载。",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "等待秒数（1-5）"},
                "thought": {"type": "string"}
            },
            "required": ["seconds", "thought"]
        }
    },
    {
        "name": "done",
        "description": "任务完成，返回最终结果给用户。",
        "input_schema": {
            "type": "object",
            "properties": {
                "result": {"type": "string", "description": "最终结果，直接展示给用户"}
            },
            "required": ["result"]
        }
    }
]

# ─── App 名称 → 包名映射 ───
APP_MAP = {
    "小红书": "com.xingin.xhs",
    "抖音": "com.ss.android.ugc.aweme",
    "微信": "com.tencent.mm",
    "高德地图": "com.autonavi.minimap",
    "百度地图": "com.baidu.BaiduMap",
    "淘宝": "com.taobao.taobao",
    "支付宝": "com.eg.android.AlipayGphone",
    "chrome": "com.android.chrome",
    "设置": "com.android.settings",
}

SYSTEM_PROMPT = """你是一个手机操控助手。你可以通过工具控制一台 Android 手机来完成用户的任务。

## 工作方式
每一轮你会收到手机屏幕的 UI 元素树（带编号），你需要：
1. 分析当前屏幕状态
2. 决定下一步操作
3. 调用一个工具执行

## 重要规则
- 每次只调用一个工具
- 用 element_id 点击元素（优先），而不是坐标
- 输入文字前确保输入框已聚焦（先点击输入框）
- 如果 UI 树信息不够，调用 screenshot 获取截图辅助判断
- 操作后会自动获取新的 UI 树，不需要你手动获取
- 任务完成后必须调用 done 工具返回结果
- 尽量高效，减少不必要的操作步骤
- 如果页面需要加载，使用 wait 等待 1-2 秒
"""


class PhoneAgent:
    def __init__(self, device_serial=None):
        # 连接设备
        if device_serial:
            self.device = u2.connect(device_serial)
        else:
            self.device = u2.connect()
        info = self.device.info
        print(f"📱 已连接: {self.device.serial}")
        print(f"   分辨率: {info['displayWidth']}x{info['displayHeight']}")

        self.width = info["displayWidth"]
        self.height = info["displayHeight"]
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.elements = {}  # element_id → element_info

    def get_ui_tree(self) -> str:
        """获取 UI 树并格式化为带编号的文本"""
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

            # 只保留有意义的元素
            if not text and not desc and not clickable:
                continue

            # 解析坐标
            bounds_match = re.findall(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
            if not bounds_match:
                continue
            x1, y1, x2, y2 = map(int, bounds_match[0])
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

            # 跳过屏幕外的元素
            if cx < 0 or cy < 0 or cx > self.width or cy > self.height:
                continue

            idx += 1
            self.elements[idx] = {"x": cx, "y": cy, "bounds": (x1, y1, x2, y2)}

            # 格式化输出
            label = text or desc or ""
            attrs = []
            if clickable:
                attrs.append("可点击")
            if focused:
                attrs.append("已聚焦")
            if resource_id:
                short_id = resource_id.split("/")[-1]
                attrs.append(f"id:{short_id}")

            attr_str = f" ({', '.join(attrs)})" if attrs else ""
            label_str = f' "{label}"' if label else ""
            lines.append(f"[{idx}] {cls}{label_str}{attr_str}")

        return "\n".join(lines) if lines else "(屏幕上没有可识别的 UI 元素)"

    def take_screenshot_b64(self) -> str:
        """截图并返回 base64"""
        img = self.device.screenshot()
        # 缩小以节省 tokens
        img = img.resize((img.width // 2, img.height // 2))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=60)
        return base64.standard_b64encode(buf.getvalue()).decode()

    def execute_tool(self, name: str, args: dict) -> str:
        """执行工具并返回结果"""
        thought = args.get("thought", "")
        if thought:
            print(f"   💭 {thought}")

        if name == "tap":
            eid = args["element_id"]
            if eid not in self.elements:
                return f"❌ 元素 [{eid}] 不存在，请检查编号"
            el = self.elements[eid]
            self.device.click(el["x"], el["y"])
            return f"✅ 已点击元素 [{eid}] 坐标({el['x']}, {el['y']})"

        elif name == "tap_xy":
            self.device.click(args["x"], args["y"])
            return f"✅ 已点击坐标({args['x']}, {args['y']})"

        elif name == "type_text":
            self.device.clear_text()
            self.device.send_keys(args["text"])
            return f"✅ 已输入: {args['text']}"

        elif name == "swipe":
            d = args["direction"]
            cx, cy = self.width // 2, self.height // 2
            dist = self.height // 3
            swipes = {
                "up": (cx, cy + dist, cx, cy - dist),
                "down": (cx, cy - dist, cx, cy + dist),
                "left": (cx + dist, cy, cx - dist, cy),
                "right": (cx - dist, cy, cx + dist, cy),
            }
            self.device.swipe(*swipes[d], duration=0.3)
            return f"✅ 已向{d}滑动"

        elif name == "press_back":
            self.device.press("back")
            return "✅ 已按返回键"

        elif name == "press_home":
            self.device.press("home")
            return "✅ 已回到主屏幕"

        elif name == "launch_app":
            app = args["app"]
            package = APP_MAP.get(app, app)
            self.device.app_start(package)
            time.sleep(2)
            return f"✅ 已启动 {app} ({package})"

        elif name == "screenshot":
            return "__SCREENSHOT__"

        elif name == "wait":
            sec = min(args.get("seconds", 2), 5)
            time.sleep(sec)
            return f"✅ 已等待 {sec} 秒"

        elif name == "done":
            return "__DONE__"

        return f"❌ 未知工具: {name}"

    def run(self, task: str):
        """主循环"""
        print(f"\n🎯 任务: {task}\n")
        start_time = time.time()

        messages = []

        # 获取初始 UI 树
        ui_tree = self.get_ui_tree()
        messages.append({
            "role": "user",
            "content": f"任务: {task}\n\n当前屏幕 UI 元素:\n{ui_tree}"
        })

        for step in range(1, MAX_STEPS + 1):
            print(f"\n── 步骤 {step}/{MAX_STEPS} ──")

            # 调用 LLM
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            # 处理响应
            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # 找到 tool_use block
            tool_use = None
            for block in assistant_content:
                if block.type == "text" and block.text.strip():
                    print(f"   🤖 {block.text.strip()[:100]}")
                elif block.type == "tool_use":
                    tool_use = block

            if not tool_use:
                print("   ⚠️  LLM 没有调用工具，重试...")
                messages.append({
                    "role": "user",
                    "content": "请调用一个工具来执行下一步操作。"
                })
                continue

            print(f"   🔧 {tool_use.name}({json.dumps(tool_use.input, ensure_ascii=False)[:120]})")

            # 检查是否完成
            if tool_use.name == "done":
                elapsed = time.time() - start_time
                result = tool_use.input.get("result", "")
                print(f"\n{'='*50}")
                print(f"✅ 任务完成!")
                print(f"\n{result}")
                print(f"\n⏱️  总耗时: {elapsed:.1f} 秒 ({int(elapsed//60)}分{int(elapsed%60)}秒)")
                print(f"📊 总步骤: {step}")
                print(f"{'='*50}")

                # 构造 tool_result 以保持消息格式正确
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_use.id, "content": "任务已完成"}]
                })
                return result

            # 执行工具
            result = self.execute_tool(tool_use.name, tool_use.input)

            # 截图请求
            if result == "__SCREENSHOT__":
                img_b64 = self.take_screenshot_b64()
                print("   📸 已截图")
                time.sleep(0.5)
                ui_tree = self.get_ui_tree()
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": tool_use.id, "content": [
                            {"type": "text", "text": f"截图已获取。当前 UI 元素:\n{ui_tree}"},
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}}
                        ]}
                    ]
                })
                continue

            print(f"   {result}")

            # 获取新的 UI 树
            time.sleep(0.8)
            ui_tree = self.get_ui_tree()

            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_use.id, "content": f"{result}\n\n当前屏幕 UI 元素:\n{ui_tree}"}]
            })

        elapsed = time.time() - start_time
        print(f"\n⚠️ 达到最大步骤数 {MAX_STEPS}，任务未完成")
        print(f"⏱️ 耗时: {elapsed:.1f} 秒")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python agent.py \"你的任务指令\"")
        sys.exit(1)

    task = sys.argv[1]
    agent = PhoneAgent()
    agent.run(task)
