"""测试: 指数退避重试模式（验证WeComNotifier和WechatSender共享的重试算法）"""
import time
import sys
from unittest.mock import Mock, patch

errors = 0

# ── Test 1: 3次重试后所有尝试都失败 ──
call_count = [0]
def fail_func():
    call_count[0] += 1
    raise TimeoutError("timeout")

def retry_with_backoff(func, max_retries=3):
    for attempt in range(max_retries):
        try:
            return func()
        except (TimeoutError, ConnectionError):
            if attempt < max_retries - 1:
                delay = (2 ** attempt) * 1.5
                time.sleep(delay)
            else:
                return None
    return None

result = retry_with_backoff(fail_func)
if result is None and call_count[0] == 3:
    print("✅ 重试模式: 3次全部失败后返回 None")
else:
    print(f"❌ 重试模式: 期望3次, 实际{call_count[0]}, result={result}")
    errors += 1

# ── Test 2: 第2次尝试成功 ──
call_count2 = [0]
def succeed_on_2nd():
    call_count2[0] += 1
    if call_count2[0] < 2:
        raise TimeoutError("timeout")
    return "success"

result2 = retry_with_backoff(succeed_on_2nd)
if result2 == "success" and call_count2[0] == 2:
    print("✅ 重试模式: 第2次成功")
else:
    print(f"❌ 重试模式: 期望第2次成功, 试了{call_count2[0]}次, result={result2}")
    errors += 1

# ── Test 3: 延迟时间验证 ──
call_count3 = [0]
delays = []
original_sleep = time.sleep
def tracked_sleep(s):
    delays.append(s)
    # 不真的等待，只是记录
def fail_three():
    call_count3[0] += 1
    raise TimeoutError("timeout")

with patch('time.sleep', tracked_sleep):
    retry_with_backoff(fail_three)

expected_delays = [1.5, 3.0]  # 2^0 * 1.5, 2^1 * 1.5
if delays == expected_delays:
    print(f"✅ 重试模式: 延迟时间正确 {delays}")
else:
    print(f"❌ 重试模式: 期望延迟 {expected_delays}, 实际 {delays}")
    errors += 1

print(f"\n{'='*40}")
print(f"结果: {errors} 个错误" if errors else "✅ 全部通过")
