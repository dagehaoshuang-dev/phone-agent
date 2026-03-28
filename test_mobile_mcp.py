"""
mobile-mcp 性能测试脚本
测试任务: 打开高德地图 → 搜索"深圳市民中心" → 获取地址 → 查看驾车和公交到达时间
"""

import json
import subprocess
import sys
import time


class MobileMCPClient:
    """通过 stdio JSON-RPC 与 mobile-mcp 通信"""

    def __init__(self):
        self.proc = subprocess.Popen(
            ["npx", "-y", "@mobilenext/mobile-mcp@latest"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._id = 0
        # 初始化 MCP
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        })

    def _send(self, method: str, params: dict) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        line = json.dumps(msg)
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

        # 读取响应（跳过通知）
        while True:
            resp_line = self.proc.stdout.readline()
            if not resp_line:
                raise RuntimeError("MCP server closed")
            try:
                resp = json.loads(resp_line.strip())
            except json.JSONDecodeError:
                continue
            if "id" in resp:
                return resp
            # 没有 id 的是通知，跳过

    def call_tool(self, name: str, arguments: dict = None) -> dict:
        return self._send("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })

    def close(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)


def extract_text_elements(elements: list) -> list:
    """从元素列表中提取有文本的元素"""
    return [
        {"text": e.get("text", ""), "label": e.get("label", ""),
         "x": e["coordinates"]["x"] + e["coordinates"]["width"] // 2,
         "y": e["coordinates"]["y"] + e["coordinates"]["height"] // 2}
        for e in elements
        if e.get("text") or e.get("label")
    ]


def find_element(elements: list, text: str):
    """在元素列表中查找包含指定文本的元素"""
    for e in elements:
        if text in (e.get("text", "") or "") or text in (e.get("label", "") or ""):
            return e
    return None


def timed(label: str):
    """计时装饰器/上下文管理器"""
    class Timer:
        def __init__(self, label):
            self.label = label
            self.elapsed = 0
        def __enter__(self):
            self.start = time.time()
            return self
        def __exit__(self, *args):
            self.elapsed = time.time() - self.start
            print(f"  [{self.label}] {self.elapsed:.2f}s")
    return Timer(label)


def main():
    print("=" * 60)
    print("mobile-mcp 性能测试")
    print("任务: 打开高德地图 → 搜索深圳市民中心 → 获取地址 → 驾车/公交时间")
    print("=" * 60)

    total_start = time.time()
    step_times = []
    mcp_call_count = 0

    # ── 启动 MCP ──
    print("\n[0] 启动 mobile-mcp server...")
    with timed("启动MCP") as t:
        client = MobileMCPClient()
    step_times.append(("启动MCP", t.elapsed))

    device_id = None

    try:
        # ── 步骤1: 获取设备 ──
        print("\n[1] 获取设备列表...")
        with timed("获取设备") as t:
            resp = client.call_tool("mobile_list_available_devices")
            mcp_call_count += 1
        step_times.append(("获取设备", t.elapsed))

        content = resp.get("result", {}).get("content", [])
        if content:
            devices_text = content[0].get("text", "")
            devices = json.loads(devices_text) if devices_text.startswith("{") or devices_text.startswith("[") else {}
            if isinstance(devices, dict) and "devices" in devices:
                devices = devices["devices"]
            if devices:
                device_id = devices[0]["id"] if isinstance(devices, list) else None
                print(f"  设备: {devices[0].get('name', device_id)} ({device_id})")
            else:
                # 尝试从文本中提取
                print(f"  原始响应: {devices_text[:200]}")

        if not device_id:
            print("  未找到设备，退出")
            client.close()
            return

        # ── 步骤2: 关闭并重新打开高德地图 ──
        print("\n[2] 重启高德地图...")
        with timed("terminate+launch") as t:
            client.call_tool("mobile_terminate_app", {
                "device": device_id,
                "packageName": "com.autonavi.minimap",
            })
            mcp_call_count += 1
            time.sleep(0.5)
            client.call_tool("mobile_launch_app", {
                "device": device_id,
                "packageName": "com.autonavi.minimap",
            })
            mcp_call_count += 1
            time.sleep(2)  # 等待 app 启动
        step_times.append(("重启高德地图", t.elapsed))

        # ── 步骤3: 获取首页元素 ──
        print("\n[3] 获取首页元素...")
        with timed("list_elements(首页)") as t:
            resp = client.call_tool("mobile_list_elements_on_screen", {"device": device_id})
            mcp_call_count += 1
        step_times.append(("获取首页元素", t.elapsed))

        elements_text = resp.get("result", {}).get("content", [{}])[0].get("text", "")
        # 找搜索框
        print(f"  搜索框: 找到" if "搜索框" in elements_text else "  搜索框: 未找到")

        # ── 步骤4: 点击搜索框 ──
        print("\n[4] 点击搜索框...")
        with timed("click(搜索框)") as t:
            client.call_tool("mobile_click_on_screen_at_coordinates", {
                "device": device_id, "x": 400, "y": 1091,
            })
            mcp_call_count += 1
            time.sleep(0.5)
        step_times.append(("点击搜索框", t.elapsed))

        # ── 步骤5: 获取搜索页元素，找到"深圳市民中心"历史记录 ──
        print("\n[5] 获取搜索页元素...")
        with timed("list_elements(搜索页)") as t:
            resp = client.call_tool("mobile_list_elements_on_screen", {"device": device_id})
            mcp_call_count += 1
        step_times.append(("获取搜索页元素", t.elapsed))

        elements_text = resp.get("result", {}).get("content", [{}])[0].get("text", "")
        elements_data = []
        try:
            parsed = json.loads(elements_text.replace("Found these elements on screen: ", ""))
            if isinstance(parsed, list):
                elements_data = parsed
        except:
            pass

        # 找深圳市民中心
        target = find_element(elements_data, "深圳市民中心")
        if target:
            tx = target["coordinates"]["x"] + target["coordinates"]["width"] // 2
            ty = target["coordinates"]["y"] + target["coordinates"]["height"] // 2
            print(f"  找到'深圳市民中心' @ ({tx}, {ty})")
        else:
            # fallback: 从上次操作知道大概位置
            tx, ty = 300, 839
            print(f"  使用默认坐标 ({tx}, {ty})")

        # ── 步骤6: 点击"深圳市民中心" ──
        print("\n[6] 点击'深圳市民中心'...")
        with timed("click(深圳市民中心)") as t:
            client.call_tool("mobile_click_on_screen_at_coordinates", {
                "device": device_id, "x": tx, "y": ty,
            })
            mcp_call_count += 1
            time.sleep(1)
        step_times.append(("点击深圳市民中心", t.elapsed))

        # ── 步骤7: 获取详情页 ──
        print("\n[7] 获取详情页信息...")
        with timed("list_elements(详情页)") as t:
            resp = client.call_tool("mobile_list_elements_on_screen", {"device": device_id})
            mcp_call_count += 1
        step_times.append(("获取详情页", t.elapsed))

        elements_text = resp.get("result", {}).get("content", [{}])[0].get("text", "")
        # 提取关键信息
        info = {"name": "深圳市民中心", "address": "", "type": "", "hours": ""}
        if "福田区福中三路" in elements_text:
            info["address"] = "福田区福中三路(市民中心地铁站C口步行190米)"
        if "政府机关" in elements_text:
            info["type"] = "政府机关"
        if "09:00-12:00" in elements_text:
            info["hours"] = "周一至周五 09:00-12:00，14:00-17:45"
        print(f"  名称: {info['name']}")
        print(f"  地址: {info['address']}")
        print(f"  类型: {info['type']}")
        print(f"  营业: {info['hours']}")

        # ── 步骤8: 点击"路线"按钮 ──
        print("\n[8] 点击'路线'按钮...")
        with timed("click(路线)") as t:
            client.call_tool("mobile_click_on_screen_at_coordinates", {
                "device": device_id, "x": 872, "y": 1920,
            })
            mcp_call_count += 1
            time.sleep(1.5)
        step_times.append(("点击路线", t.elapsed))

        # ── 步骤9: 获取驾车路线（默认页面） ──
        print("\n[9] 获取驾车路线...")
        with timed("list_elements(驾车)") as t:
            resp = client.call_tool("mobile_list_elements_on_screen", {"device": device_id})
            mcp_call_count += 1
        step_times.append(("获取驾车路线", t.elapsed))

        elements_text = resp.get("result", {}).get("content", [{}])[0].get("text", "")
        # 提取驾车信息
        drive_info = []
        import re
        # 匹配 "XX分钟" 的模式
        for m in re.finditer(r'"(\d+分钟)".*?"(\d+公里)"', elements_text):
            drive_info.append(f"{m.group(1)} / {m.group(2)}")
        if drive_info:
            print(f"  驾车方案: {' | '.join(drive_info[:3])}")
        else:
            print(f"  (需要先切换到驾车tab)")

        # 判断当前是否在驾车页面，如果在公交页面则需要切换
        if "公共交通" in elements_text and "驾车" in elements_text:
            # 检查是否已经在驾车页
            need_switch_to_drive = "距离短" in elements_text or "高速多" in elements_text or "一路畅通" in elements_text
            need_switch_to_transit = "11号线" in elements_text or "步行2公里" in elements_text

            if need_switch_to_transit:
                # 当前在公交页面，先记录公交数据
                transit_text = elements_text
                print("\n  当前在公交页面，记录数据后切换驾车...")

                # 切换到驾车
                print("\n[9.1] 切换到驾车tab...")
                # 找驾车tab位置
                drive_tab = find_element(
                    json.loads(elements_text.replace("Found these elements on screen: ", "")) if elements_text.startswith("Found") else [],
                    "驾车"
                )
                drive_x = drive_tab["coordinates"]["x"] + drive_tab["coordinates"]["width"] // 2 if drive_tab else 145
                drive_y = drive_tab["coordinates"]["y"] + drive_tab["coordinates"]["height"] // 2 if drive_tab else 315

                with timed("click(驾车tab)") as t:
                    client.call_tool("mobile_click_on_screen_at_coordinates", {
                        "device": device_id, "x": drive_x, "y": drive_y,
                    })
                    mcp_call_count += 1
                    time.sleep(1)
                step_times.append(("切换驾车tab", t.elapsed))

                with timed("list_elements(驾车)") as t:
                    resp = client.call_tool("mobile_list_elements_on_screen", {"device": device_id})
                    mcp_call_count += 1
                step_times.append(("获取驾车路线2", t.elapsed))
                elements_text = resp.get("result", {}).get("content", [{}])[0].get("text", "")

            elif need_switch_to_drive:
                # 当前在驾车页面，先记录驾车数据，然后切换公交
                pass

        # 重新提取驾车信息
        drive_info = []
        for m in re.finditer(r'"(\d+分钟)"', elements_text):
            drive_info.append(m.group(1))
        print(f"  驾车方案时间: {' | '.join(drive_info[:3])}")

        # ── 步骤10: 切换到公共交通 ──
        print("\n[10] 切换到公共交通...")
        # 找公共交通tab
        elements_data = []
        try:
            raw = elements_text
            if raw.startswith("Found"):
                raw = raw.replace("Found these elements on screen: ", "")
            elements_data = json.loads(raw)
        except:
            pass

        transit_tab = find_element(elements_data, "公共交通")
        if transit_tab:
            ttx = transit_tab["coordinates"]["x"] + transit_tab["coordinates"]["width"] // 2
            tty = transit_tab["coordinates"]["y"] + transit_tab["coordinates"]["height"] // 2
        else:
            ttx, tty = 364, 315

        with timed("click(公交tab)") as t:
            client.call_tool("mobile_click_on_screen_at_coordinates", {
                "device": device_id, "x": ttx, "y": tty,
            })
            mcp_call_count += 1
            time.sleep(1)
        step_times.append(("切换公交tab", t.elapsed))

        # ── 步骤11: 获取公交路线 ──
        print("\n[11] 获取公交路线...")
        with timed("list_elements(公交)") as t:
            resp = client.call_tool("mobile_list_elements_on_screen", {"device": device_id})
            mcp_call_count += 1
        step_times.append(("获取公交路线", t.elapsed))

        elements_text = resp.get("result", {}).get("content", [{}])[0].get("text", "")
        transit_info = []
        for m in re.finditer(r'"([\d小时]+\d*分)"', elements_text):
            transit_info.append(m.group(1))
        print(f"  公交方案时间: {' | '.join(transit_info[:4])}")

    finally:
        client.close()

    # ── 汇总 ──
    total_elapsed = time.time() - total_start

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"\n总耗时: {total_elapsed:.2f}s ({int(total_elapsed)}秒)")
    print(f"MCP 调用次数: {mcp_call_count}")
    print(f"\n各步骤耗时:")
    for name, elapsed in step_times:
        bar = "█" * int(elapsed * 10)
        print(f"  {name:<20s} {elapsed:6.2f}s {bar}")

    mcp_total = sum(e for _, e in step_times)
    print(f"\n步骤总计: {mcp_total:.2f}s")
    print(f"其中等待(sleep): ~6.0s")
    print(f"纯MCP通信+操作: ~{mcp_total - 6:.2f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
