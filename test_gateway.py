"""LLM Gateway 并发压力测试脚本

2 线程并发循环请求，自动获取第一个可用模型。
用法: python test_gateway.py [--threads N] [--url URL] [--key KEY]
"""

import argparse
import json
import sys
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── 统计信息 ──────────────────────────────────────────────────────────
stats = {
    "sent": 0,
    "ok": 0,
    "fail": 0,
    "total_tokens": 0,
}
stats_lock = threading.Lock()
start_time = time.time()


def get_models(base_url: str, api_key: str = "") -> list:
    url = f"{base_url}/v1/models"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
        return [m["id"] for m in data.get("data", []) if m.get("id")]


def worker(thread_id: int, base_url: str, api_key: str, model: str):
    """单个工作线程：循环发送请求。"""
    url = f"{base_url}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "详细介绍一下你自己"}],
        "stream": False,
        "max_tokens": 256,
    }).encode("utf-8")

    while not stop_event.is_set():
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", 0) or 0

            with stats_lock:
                stats["sent"] += 1
                stats["ok"] += 1
                stats["total_tokens"] += tokens
        except urllib.error.HTTPError as e:
            with stats_lock:
                stats["sent"] += 1
                stats["fail"] += 1
            status = e.code
            if status == 429:
                pass  # 队列满，正常重试
            else:
                body_text = e.read().decode()[:100]
                print(f"\n[T{thread_id}] HTTP {status}: {body_text}")
        except Exception as e:
            with stats_lock:
                stats["sent"] += 1
                stats["fail"] += 1
            print(f"\n[T{thread_id}] 错误: {e}")

        # 短暂等待避免 CPU 空转
        time.sleep(0.5)


def print_stats():
    """定时打印统计信息。"""
    last_sent = 0
    while not stop_event.is_set():
        time.sleep(5)
        elapsed = time.time() - start_time
        with stats_lock:
            s = stats["sent"]
            ok = stats["ok"]
            fail = stats["fail"]
            tokens = stats["total_tokens"]
        delta = s - last_sent
        last_sent = s
        rps = delta / 5
        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
              f"已发送 {s} | 成功 {ok} | 失败 {fail} | "
              f"token {tokens} | {rps:.1f} req/s")


def main():
    parser = argparse.ArgumentParser(description="LLM Gateway 并发测试")
    parser.add_argument("--url", default="http://127.0.0.1:8001", help="网关地址")
    parser.add_argument("--key", default="", help="API Key")
    parser.add_argument("--threads", type=int, default=2, help="并发线程数")
    args = parser.parse_args()

    global stop_event
    stop_event = threading.Event()

    print(f"连接网关: {args.url}")
    models = get_models(args.url, args.key)
    if not models:
        print("没有可用模型")
        sys.exit(1)
    model = models[0]
    print(f"模型: {model} | 线程: {args.threads} | 按 Ctrl+C 停止\n")

    # 启动统计线程
    t_stat = threading.Thread(target=print_stats, daemon=True)
    t_stat.start()

    # 启动工作线程
    threads = []
    for i in range(args.threads):
        t = threading.Thread(target=worker, args=(i + 1, args.url, args.key, model), daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n正在停止...")
        stop_event.set()
        # 等待线程退出
        for t in threads:
            t.join(timeout=2)

    elapsed = time.time() - start_time
    with stats_lock:
        print(f"\n{'='*50}")
        print(f"测试结束")
        print(f"耗时: {elapsed:.0f}s")
        print(f"发送: {stats['sent']} | 成功: {stats['ok']} | 失败: {stats['fail']}")
        if stats["sent"]:
            print(f"成功率: {stats['ok']/stats['sent']*100:.1f}%")
        print(f"总 token: {stats['total_tokens']}")
        print(f"{'='*50}")


if __name__ == "__main__":
    main()
