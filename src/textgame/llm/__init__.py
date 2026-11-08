"""LLM 层（第 12 章）：LLMClient 唯一封装点。

M1 先提供凭据配置与 ``python -m textgame.llm.smoke`` 连通性自检；
完整 LLMClient（超时 / 重试 / 限流 / 校验修复 / 成本归集 / 供应商回退）在 M5
切片 019-llmclient 实现。``providers.py`` 是全仓唯一 import LiteLLM 的位置。
"""
