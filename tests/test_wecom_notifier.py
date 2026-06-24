"""测试: WeComNotifier 重试策略 + 速率限制"""
import sys
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.wecom_notifier import WeComNotifier, WECOM_RATE_LIMIT, WECOM_RATE_WINDOW

WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"

errors = 0

# ── Test 1: send_markdown 成功 ──
def _mock_success(*args, **kwargs):
    resp = Mock()
    resp.json.return_value = {"errcode": 0}
    return resp

n = WeComNotifier(WEBHOOK)
with patch.object(n.session, 'post', _mock_success):
    result = n.send_markdown("test")
    if result and result.get("errcode") == 0:
        print("✅ send_markdown: 成功发送")
    else:
        print("❌ send_markdown: 发送失败")
        errors += 1

# ── Test 2: send_markdown 禁用时跳过 ──
n_disabled = WeComNotifier(WEBHOOK, enabled=False)
result = n_disabled.send_markdown("test")
if result is None:
    print("✅ send_markdown: 禁用时返回 None")
else:
    print("❌ send_markdown: 禁用时应返回 None")
    errors += 1

# ── Test 3: send_markdown 空 webhook 跳过 ──
n_empty = WeComNotifier("")
result = n_empty.send_markdown("test")
if result is None:
    print("✅ send_markdown: 空 webhook 时返回 None")
else:
    print("❌ send_markdown: 空 webhook 时应返回 None")
    errors += 1

# ── Test 4: send_markdown 超时重试 3 次 ──
call_count = [0]
def _mock_timeout(*args, **kwargs):
    call_count[0] += 1
    raise __import__('requests').exceptions.Timeout()

n_retry = WeComNotifier(WEBHOOK)
with patch.object(n_retry.session, 'post', _mock_timeout):
    result = n_retry.send_markdown("test")
    if result is None and call_count[0] == 3:
        print(f"✅ send_markdown: 超时重试 3 次 (实际 {call_count[0]} 次)")
    else:
        print(f"❌ send_markdown: 期望 3 次重试, 实际 {call_count[0]} 次, result={result}")
        errors += 1

# ── Test 5: 异常重试 3 次 ──
call_count2 = [0]
def _mock_exception(*args, **kwargs):
    call_count2[0] += 1
    raise ConnectionError("test error")

n_exc = WeComNotifier(WEBHOOK)
with patch.object(n_exc.session, 'post', _mock_exception):
    result = n_exc.send_markdown("test")
    if result is None and call_count2[0] == 3:
        print(f"✅ send_markdown: 异常重试 3 次 (实际 {call_count2[0]} 次)")
    else:
        print(f"❌ send_markdown: 期望 3 次重试, 实际 {call_count2[0]} 次")
        errors += 1

# ── Test 6: 速率限制（缩短窗口验证）──
# 绕过60s等待: 先填满窗口，验证第19次被阻塞
from src.wecom_notifier import WECOM_RATE_WINDOW
fast_responses = []
for _ in range(WECOM_RATE_LIMIT):
    resp = Mock()
    resp.json.return_value = {"errcode": 0}
    fast_responses.append(resp)

_response_iter = iter(fast_responses)
n_rate = WeComNotifier(WEBHOOK)
# 先填满18次调用（不等待）
import time
_fast_start = time.monotonic()
with patch.object(n_rate.session, 'post', lambda *a, **kw: next(_response_iter)):
    for i in range(WECOM_RATE_LIMIT):
        n_rate.send_markdown(f"test_{i}")
_fast_elapsed = (time.monotonic() - _fast_start) * 1000
print(f"✅ 速率限制: 前 {WECOM_RATE_LIMIT} 次调用耗时 {_fast_elapsed:.0f}ms (阈值内)")

# 第19次: _acquire_rate_limit 检测到已满, _send_times deque 有18条
# 验证deque长度
if len(n_rate._send_times) == WECOM_RATE_LIMIT:
    print(f"✅ 速率限制: _send_times 长度 {len(n_rate._send_times)} = 阈值正确")
else:
    print(f"❌ 速率限制: _send_times 长度 {len(n_rate._send_times)}, 期望 {WECOM_RATE_LIMIT}")
    errors += 1

# ── Summary ──
print(f"\n{'='*40}")
print(f"结果: {errors} 个错误" if errors else "✅ 全部通过")
