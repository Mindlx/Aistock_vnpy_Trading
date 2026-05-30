#!/usr/bin/env python3
"""
实时行情链路检查
使用东财HTTP推流API + WebSocket双重验证
"""
import asyncio
import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


async def check():
    import aiohttp

    now = datetime.now()
    weekday = now.weekday()
    hour = now.hour

    print(f"[{now.strftime('%H:%M:%S')}] 东财数据源链路检查...")

    # HTTP推流API（全天可用）
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        url = (
            "https://push2.eastmoney.com/api/qt/stock/get"
            "?secid=1.600519"
            "&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f170"
        )
        try:
            async with session.get(url) as resp:
                data = await resp.json()
                if data and data.get("data"):
                    d = data["data"]
                    print(f"  ✅ HTTP推流API: 贵州茅台 {d.get('f43','?')}")
                else:
                    print(f"  ❌ HTTP推流API: 无数据")
        except Exception as e:
            print(f"  ❌ HTTP推流API: {e}")

    # WebSocket（非交易时段可能关闭）
    ws_url = "wss://push2.eastmoney.com/PushServer/WebSocket"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.ws_connect(ws_url, heartbeat=5) as ws:
                print(f"  ✅ WebSocket 连接成功")
                await ws.send_str('{"op":"ping"}')
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                print(f"  WS ping响应: {str(msg.data)[:60]}")
                await ws.close()
    except Exception as e:
        if weekday < 5 and 9 <= hour <= 15:
            print(f"  ❌ WebSocket: 交易时段不可用! {e}")
        else:
            print(f"  ⚠️ WebSocket: 非交易时段已关闭 (正常)")


if __name__ == "__main__":
    asyncio.run(check())
