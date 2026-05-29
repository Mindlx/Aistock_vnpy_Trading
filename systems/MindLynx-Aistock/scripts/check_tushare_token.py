#!/usr/bin/env python3
"""
Tushare Token 健康检查脚本
每周一9:00运行，检查 Tushare API 是否正常响应
"""
import os
import sys
import json
from datetime import datetime

import requests

TOKEN = os.environ.get("TUSHARE_TOKEN", "")
API_URL = "http://api.tushare.pro"

if not TOKEN:
    print("TUSHARE ERROR: TUSHARE_TOKEN not configured")
    sys.exit(1)

# 最小可用性检查：查询最新交易日
payload = {
    "api_name": "daily",
    "token": TOKEN,
    "params": {"ts_code": "000001.SZ", "start_date": "20260101", "end_date": datetime.now().strftime("%Y%m%d")},
    "fields": "trade_date,close",
}

try:
    resp = requests.post(API_URL, json=payload, timeout=15)
    if resp.status_code != 200:
        print(f"TUSHARE ERROR: HTTP {resp.status_code}")
        sys.exit(1)

    result = resp.json()
    if result.get("code") != 0:
        msg = result.get("msg", "unknown error")
        print(f"TUSHARE ERROR: {msg}")
        sys.exit(1)

    data = result.get("data", {})
    items = data.get("items", [])
    if items:
        latest = items[0][0]  # trade_date
        close = items[0][1]   # close
        print(f"TUSHARE OK: 000001.SZ latest={latest} close={close}")
    else:
        print("TUSHARE WARN: empty response, but API responded")
except requests.exceptions.Timeout:
    print("TUSHARE ERROR: timeout after 15s")
    sys.exit(1)
except Exception as e:
    print(f"TUSHARE ERROR: {e}")
    sys.exit(1)
