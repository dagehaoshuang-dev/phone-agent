"""
Phone Agent v2 — 基于 mobile-mcp 的通用手机自动化 Agent

设计思路：
- LLM 直接驱动 mobile-mcp 原子操作，每轮执行一个动作
- 每次动作后自动刷新屏幕元素，作为下一轮输入
- 屏幕元素精简为带编号的文本列表，LLM 通过编号引用元素坐标
- 支持多步规划：LLM 可一次返回多个动作顺序执行

用法:
  python agent_mcp.py "打开高德地图搜索深圳市民中心，获取驾车和公交到达时间"
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from openai import OpenAI

# 自动加载 .env
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── 配置 ──

API_KEY = os.environ.get("PHONE_AGENT_API_KEY", "")
BASE_URL = os.environ.get(
    "PHONE_AGENT_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
MODEL = os.environ.get("PHONE_AGENT_MODEL", "qwen3.5-plus")
MAX_TURNS = 25


# ── MCP 传输层 ──

class MCPTransport:
    """stdio JSON-RPC 传输，管理 mobile-mcp 子进程"""

    def __init__(self):
        cmd = os.environ.get("MCP_CMD", "npx")
        args = os.environ.get("MCP_ARGS", "-y,@mobilenext/mobile-mcp@latest").split(",")
        self._seq = 0
        self._proc = subprocess.Popen(
            [cmd, *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        # MCP 握手
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "phone-agent-v2", "version": "2.0"},
        })

    def _rpc(self, method: str, params: dict) -> dict:
        self._seq += 1
        req = json.dumps({"jsonrpc": "2.0", "id": self._seq, "method": method, "params": params})
        self._proc.stdin.write(req + "\n")
        self._proc.stdin.flush()
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise ConnectionError("mobile-mcp 进程已退出")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if resp.get("id") == self._seq:
                if "error" in resp:
                    raise RuntimeError(resp["error"].get("message", str(resp["error"])))
                return resp.get("result", {})

    def call_tool(self, name: str, arguments: dict = None) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return "".join(
            c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
        )

    def close(self):
        self._proc.terminate()
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()


# ── 屏幕解析 ──

def parse_screen(raw: str) -> tuple:
    """
    将 mobile-mcp 返回的元素 JSON 解析为:
      - display: 给 LLM 看的精简文本
      - index:   {编号: (cx, cy)} 坐标映射
    """
    try:
        body = raw.split(": ", 1)[1] if ": " in raw else raw
        elements = json.loads(body)
    except (json.JSONDecodeError, IndexError):
        return raw[:1500], {}

    lines = []
    index = {}
    n = 0
    seen = set()

    for el in elements:
        text = (el.get("text") or "").strip()
        label = (el.get("label") or "").strip()
        display = text or label
        if not display:
            continue

        co = el.get("coordinates", {})
        w, h = co.get("width", 0), co.get("height", 0)
        if w < 8 or h < 8:
            continue
        cx = co.get("x", 0) + w // 2
        cy = co.get("y", 0) + h // 2

        key = f"{display[:25]}|{cx // 40}|{cy // 40}"
        if key in seen:
            continue
        seen.add(key)

        n += 1
        index[n] = (cx, cy)

        widget = el.get("type", "").rsplit(".", 1)[-1]
        tag = ""
        if "EditText" in widget:
            tag = " [输入框]"
        elif "Button" in widget:
            tag = " [按钮]"

        show = display if len(display) <= 35 else display[:35] + "…"
        lines.append(f"[{n}] ({cx},{cy}) \"{show}\"{tag}")

    return "\n".join(lines) or "(空白屏幕)", index


# ── LLM 工具定义 ──

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "act",
            "description": (
                "执行一组手机操作（按顺序）。每个操作是一个对象。"
                "操作执行完毕后会自动返回最新的屏幕元素列表。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thinking": {
                        "type": "string",
                        "description": "简要分析当前屏幕状态和下一步计划（<50字）。务必在此记录已从屏幕获取到的关键数据（地址、时间、价格等），因为历史屏幕数据会被裁剪。",
                    },
                    "ops": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": [
                                        "tap", "long_press", "type", "enter",
                                        "swipe_up", "swipe_down", "swipe_left", "swipe_right",
                                        "back", "home", "launch", "wait",
                                    ],
                                },
                                "id": {
                                    "type": "integer",
                                    "description": "元素编号（tap/long_press 时使用，坐标从屏幕元素列表获取）",
                                },
                                "x": {"type": "integer", "description": "直接指定 x 坐标（无编号时使用）"},
                                "y": {"type": "integer", "description": "直接指定 y 坐标"},
                                "text": {"type": "string", "description": "type 要输入的文本 / launch 的包名"},
                                "seconds": {"type": "number", "description": "wait 等待秒数，默认1"},
                            },
                            "required": ["op"],
                        },
                    },
                },
                "required": ["thinking", "ops"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "任务完成，输出最终结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "description": "结构化的任务结果"},
                },
                "required": ["result"],
            },
        },
    },
]

SYSTEM = """\
你是手机自动化助手。你根据屏幕元素列表决定操作，完成用户任务后调用 finish 返回结果。

## 屏幕元素格式
[编号] (x,y) "文本" [可选标签]
编号用于 tap/long_press 的 id 字段；坐标为元素中心。

## 操作说明
- tap: 通过 id（元素编号）或 (x,y) 点击
- long_press: 长按
- type: 输入文本到当前聚焦的输入框（先 tap 输入框再 type）
- enter: 按回车/确认键
- swipe_up/down/left/right: 滑动屏幕
- back / home: 系统按键
- launch: 启动 App，text 字段传包名
- wait: 等待，默认1秒

## 常见包名
高德地图 com.autonavi.minimap | 小红书 com.xingin.xhs | 微信 com.tencent.mm
大众点评 com.dianping.v1 | 美团 com.sankuai.meituan | Chrome com.android.chrome
设置 com.android.settings | 相机 com.google.android.GoogleCamera

## 要求
1. 尽量合批：确定性的连续操作放在一次 act 调用的 ops 数组里
2. thinking 里记录已获取的关键信息——历史屏幕数据会被裁剪，只有 thinking 会保留
3. 信息够了就 finish，不要反复确认
4. 每个 App 只进一次，离开前取完所有数据
5. 列表数据不够时 swipe_up 加载更多
"""


# ── Agent ──

class Agent:
    def __init__(self):
        self.mcp = MCPTransport()
        self.llm = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        self.device_id = self._discover_device()
        self.elem_index = {}

    # ── 设备发现 ──

    def _discover_device(self) -> str:
        raw = self.mcp.call_tool("mobile_list_available_devices")
        try:
            data = json.loads(raw)
            devices = data if isinstance(data, list) else data.get("devices", [])
        except json.JSONDecodeError:
            devices = []
        if not devices:
            self.mcp.close()
            sys.exit("❌ 未检测到设备，请连接手机或启动模拟器")
        d = devices[0]
        did = d["id"]
        print(f"📱 {d.get('name', did)}  {d.get('platform', '')} {d.get('version', '')}  [{did}]")
        return did

    # ── 获取屏幕 ──

    def refresh_screen(self) -> str:
        raw = self.mcp.call_tool("mobile_list_elements_on_screen", {"device": self.device_id})
        display, self.elem_index = parse_screen(raw)
        return display

    # ── 执行单个 op ──

    def _exec_op(self, op: dict) -> str:
        name = op.get("op", "")
        d = self.device_id

        # 解析坐标：优先用 id 查元素编号，否则用 x/y
        def coords():
            eid = op.get("id")
            if eid and eid in self.elem_index:
                return self.elem_index[eid]
            x, y = op.get("x"), op.get("y")
            if x is not None and y is not None:
                return int(x), int(y)
            raise ValueError(f"tap/long_press 需要 id 或 (x,y)")

        if name == "tap":
            x, y = coords()
            self.mcp.call_tool("mobile_click_on_screen_at_coordinates", {"device": d, "x": x, "y": y})
            return f"tap({x},{y})"

        if name == "long_press":
            x, y = coords()
            self.mcp.call_tool("mobile_long_press_on_screen_at_coordinates", {"device": d, "x": x, "y": y})
            return f"long_press({x},{y})"

        if name == "type":
            txt = op.get("text", "")
            try:
                self.mcp.call_tool("mobile_type_keys", {"device": d, "text": txt, "submit": False})
            except Exception:
                # fallback: 通过 ADBKeyboard 广播输入（支持中文）
                subprocess.run(
                    ["adb", "-s", d, "shell", "am", "broadcast",
                     "-a", "ADB_INPUT_TEXT", "--es", "msg", txt],
                    capture_output=True, timeout=5,
                )
            return f"type(\"{txt}\")"

        if name == "enter":
            self.mcp.call_tool("mobile_press_button", {"device": d, "button": "ENTER"})
            return "enter"

        if name in ("swipe_up", "swipe_down", "swipe_left", "swipe_right"):
            direction = name.split("_")[1]
            self.mcp.call_tool("mobile_swipe_on_screen", {"device": d, "direction": direction})
            return f"swipe_{direction}"

        if name == "back":
            self.mcp.call_tool("mobile_press_button", {"device": d, "button": "BACK"})
            return "back"

        if name == "home":
            self.mcp.call_tool("mobile_press_button", {"device": d, "button": "HOME"})
            return "home"

        if name == "launch":
            pkg = op.get("text", "")
            self.mcp.call_tool("mobile_launch_app", {"device": d, "packageName": pkg})
            time.sleep(2)
            return f"launch({pkg})"

        if name == "wait":
            sec = min(op.get("seconds", 1), 5)
            time.sleep(sec)
            return f"wait({sec}s)"

        return f"unknown({name})"

    # ── 执行 act 调用 ──

    def execute_act(self, args: dict) -> str:
        ops = args.get("ops", [])
        ops = [o for o in ops if isinstance(o, dict) and "op" in o][:12]
        results = []
        for i, op in enumerate(ops):
            try:
                r = self._exec_op(op)
                results.append(f"✅ {r}")
                print(f"     {r}")
            except Exception as e:
                results.append(f"❌ {op.get('op')}: {e}")
                print(f"     ❌ {e}")
            # 操作间微等待
            if op["op"] in ("tap", "long_press", "type") and i < len(ops) - 1:
                time.sleep(0.3)

        # 最后刷新屏幕
        time.sleep(0.5)
        screen = self.refresh_screen()
        return "\n".join(results) + "\n\n屏幕元素:\n" + screen

    # ── 压缩历史 ──

    @staticmethod
    def _compress(messages: list) -> list:
        if len(messages) <= 8:
            return messages
        # 保留: system[0] + 首条user[1] + 最近6条
        mid = messages[2:-4]
        thoughts = []
        for m in mid:
            if m["role"] == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if tc["function"]["name"] == "act":
                        try:
                            a = json.loads(tc["function"]["arguments"])
                            t = a.get("thinking", "")
                            if t:
                                thoughts.append(t)
                        except json.JSONDecodeError:
                            pass
        out = messages[:2]
        if thoughts:
            out.append({"role": "user", "content": "历史摘要: " + " → ".join(thoughts[-8:])})
            out.append({"role": "assistant", "content": "好的，继续。"})
        out.extend(messages[-4:])
        return out

    # ── 主循环 ──

    def run(self, task: str):
        if not API_KEY:
            self.mcp.close()
            sys.exit("❌ 请设置环境变量 PHONE_AGENT_API_KEY (通义千问 API Key)")
        print(f"🎯 任务: {task}")
        print(f"🤖 模型: {MODEL}\n")
        t0 = time.time()
        n_llm = 0
        n_ops = 0
        n_errors = 0

        screen = self.refresh_screen()
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"任务: {task}\n\n屏幕元素:\n{screen}"},
        ]

        for turn in range(1, MAX_TURNS + 1):
            elapsed = time.time() - t0
            print(f"── 第{turn}轮 ({elapsed:.1f}s) ──")

            compressed = self._compress(messages)
            try:
                resp = self.llm.chat.completions.create(
                    model=MODEL, messages=compressed, tools=TOOLS, max_tokens=1024,
                )
                n_llm += 1
            except Exception as e:
                n_errors += 1
                print(f"  ❌ LLM: {e}")
                if n_errors >= 3:
                    print("连续失败 3 次，退出")
                    self.mcp.close()
                    return
                time.sleep(2)
                continue
            n_errors = 0  # 成功则重置

            msg = resp.choices[0].message
            # 记录 assistant 消息
            amsg = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                amsg["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]
            messages.append(amsg)

            if not msg.tool_calls:
                messages.append({"role": "user", "content": "请调用工具。"})
                continue

            tc = msg.tool_calls[0]
            fn = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": "JSON 解析失败"})
                continue

            if fn == "finish":
                elapsed = time.time() - t0
                result = args.get("result", "")
                print(f"\n{'═' * 60}")
                print(f"✅ 完成\n\n{result}")
                print(f"\n⏱  {elapsed:.1f}s  |  LLM {n_llm}次  |  操作 {n_ops}次")
                print(f"{'═' * 60}")
                self.mcp.close()
                return result

            if fn == "act":
                thinking = args.get("thinking", "")
                ops = args.get("ops", [])
                n_ops += len([o for o in ops if isinstance(o, dict)])
                print(f"  💭 {thinking}")
                tool_result = self.execute_act(args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})
                continue

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": f"未知工具 {fn}"})

        elapsed = time.time() - t0
        print(f"\n⚠️  达到 {MAX_TURNS} 轮上限 ({elapsed:.1f}s)")
        self.mcp.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python agent_mcp.py \"任务描述\"")
        print("\n示例:")
        print("  python agent_mcp.py \"打开高德地图搜索深圳市民中心，获取地址和驾车公交时间\"")
        print("  python agent_mcp.py \"打开小红书搜索咖啡推荐，告诉我前3个帖子标题\"")
        print("  python agent_mcp.py \"打开设置查看手机存储空间\"")
        sys.exit(0)
    Agent().run(sys.argv[1])
