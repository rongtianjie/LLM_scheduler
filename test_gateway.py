"""LLM Gateway 测试脚本

自动获取第一个可用模型并发送聊天请求。
用法: python test_gateway.py [--stream] [--url URL] [--key KEY]
"""

import argparse
import json
import sys
import urllib.request
import urllib.error


def get_models(base_url: str, api_key: str = "") -> list:
    """获取模型列表，返回第一个可用模型 ID。"""
    url = f"{base_url}/v1/models"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            models = [m["id"] for m in data.get("data", []) if m.get("id")]
            return models
    except Exception as e:
        print(f"获取模型列表失败: {e}")
        sys.exit(1)


def send_request(base_url: str, api_key: str, model: str, stream: bool):
    """发送聊天请求。"""
    url = f"{base_url}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "详细介绍一下你自己"}],
        "stream": stream,
        "max_tokens": 1024,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as e:
        print(f"请求失败 (HTTP {e.code}): {e.read().decode()}")
        sys.exit(1)
    except Exception as e:
        print(f"请求失败: {e}")
        sys.exit(1)

    if stream:
        print(f"\n[{model}] 流式响应:\n{'='*50}")
        for line in resp.read().decode().split("\n"):
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    chunk = json.loads(line[6:])
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        print(content, end="", flush=True)
                except json.JSONDecodeError:
                    pass
        print(f"\n{'='*50}")
    else:
        data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        print(f"\n[{model}] 非流式响应:\n{'='*50}")
        print(content)
        print(f"{'='*50}")
        if usage:
            print(f"\nToken 用量: 输入={usage.get('prompt_tokens','?')}  "
                  f"输出={usage.get('completion_tokens','?')}  "
                  f"总计={usage.get('total_tokens','?')}")


def main():
    parser = argparse.ArgumentParser(description="LLM Gateway 测试工具")
    parser.add_argument("--url", default="http://127.0.0.1:8001",
                        help="网关地址 (默认: http://127.0.0.1:8001)")
    parser.add_argument("--key", default="", help="API Key (可选)")
    parser.add_argument("--stream", action="store_true",
                        help="启用流式输出")
    args = parser.parse_args()

    print(f"连接网关: {args.url}")
    models = get_models(args.url, args.key)
    if not models:
        print("没有可用模型")
        sys.exit(1)

    model = models[0]
    print(f"可用模型: {', '.join(models[:5])}"
          f"{'...' if len(models) > 5 else ''}")
    print(f"使用模型: {model}")
    print(f"流式模式: {'开启' if args.stream else '关闭'}")
    print()

    send_request(args.url, args.key, model, args.stream)


if __name__ == "__main__":
    main()
