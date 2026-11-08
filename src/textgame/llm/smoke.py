"""LLM API 连通性自检（开发 / 运维工具，不进入业务调用链）。

用法（仓库根目录）::

    uv run python -m textgame.llm.smoke

从 ``TEXTGAME_LLM_PROVIDER`` / ``TEXTGAME_LLM_MODEL`` / ``TEXTGAME_LLM_BASE_URL`` /
``TEXTGAME_LLM_API_KEY``（``.env`` 或环境变量）读取配置，发一条最小 chat 请求，
验证「凭据 + 网络 + 模型名」是否可用；不打印密钥，失败返回非 0 退出码。
完整 LLMClient（重试 / 计量 / 校验修复 / 回退）见第 12 章，M5 切片 019 实现。
"""

from __future__ import annotations

import re
import sys
import time

from textgame.config.settings import get_settings
from textgame.llm.providers import chat_completion

# 只匹配 DeepSeek/OpenAI 风格的真实密钥形态；说明文字与占位符不命中
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9]{20,}")
_MISSING_KEY_HINT = (
    "未配置 TEXTGAME_LLM_API_KEY：请复制 .env.example 为 .env 并填入密钥（.env 不入库）"
)
_PROMPT = "只回复两个字：可用"


def _mask(text: str) -> str:
    """脱敏输出，确保错误信息里不会出现完整密钥。"""
    return _SECRET_PATTERN.sub("sk-***", text)


def main() -> int:
    """执行一次连通性自检；返回进程退出码（0 成功 / 1 API 失败 / 2 未配置密钥）。"""
    settings = get_settings()
    api_key = settings.llm_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        print(f"[FAIL] {_MISSING_KEY_HINT}")
        return 2

    started = time.perf_counter()
    try:
        result = chat_completion(
            provider=settings.llm_provider,
            model=settings.llm_model,
            api_key=api_key.get_secret_value(),
            api_base=settings.llm_base_url,
            messages=[{"role": "user", "content": _PROMPT}],
            timeout_s=20.0,
            max_tokens=32,
        )
    except Exception as exc:  # 自检工具：所有失败都转成退出码，不向上抛裸异常
        print(f"[FAIL] {type(exc).__name__}: {_mask(str(exc))}")
        return 1

    latency_s = time.perf_counter() - started
    print("[OK] LLM API 配置可用")
    print(
        f"  provider={settings.llm_provider} model={settings.llm_model} "
        f"base_url={settings.llm_base_url}"
    )
    print(
        f"  latency={latency_s:.2f}s prompt_tokens={result['prompt_tokens']} "
        f"completion_tokens={result['completion_tokens']}"
    )
    print(f"  content={_mask(result['content'])[:60]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
