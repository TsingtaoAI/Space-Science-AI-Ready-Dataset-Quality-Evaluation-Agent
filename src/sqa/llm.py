"""模型服务客户端（OpenAI 兼容，可接评测平台提供的 Qwen、DeepSeek 等）。

三个变量全部从环境变量读取，缺失即报错，不使用默认值；密钥不写入镜像。
默认判定路径不依赖模型；只有 --use-llm 时才要求这些变量。模型只负责「读」与「改写表述」，
计量、判定、计分与排序都由确定性程序完成（见规格 llm 段与 sqa/assist.py）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field

ENV_VARS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


class LLMConfigError(Exception):
    pass


class LLMCallError(Exception):
    pass


@dataclass
class LLMClient:
    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0
    usage: dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})

    @classmethod
    def from_env(cls) -> "LLMClient":
        missing = [v for v in ENV_VARS if not os.environ.get(v)]
        if missing:
            raise LLMConfigError(f"缺少环境变量: {', '.join(missing)}（由评测平台注入）")
        return cls(os.environ["LLM_BASE_URL"].rstrip("/"), os.environ["LLM_API_KEY"], os.environ["LLM_MODEL"])

    @property
    def tokens(self) -> int:
        return self.usage["prompt_tokens"] + self.usage["completion_tokens"]

    def chat(self, messages: list[dict], retry: int = 1, **params) -> str:
        """调用一次对话接口；网络或服务错误按 retry 重试，仍失败抛 LLMCallError。"""
        body = {"model": self.model, "messages": messages, "temperature": 0, **params}
        err = None
        for _ in range(retry + 1):
            try:
                req = urllib.request.Request(
                    f"{self.base_url}/chat/completions",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.load(resp)
                u = data.get("usage") or {}
                self.usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                self.usage["completion_tokens"] += u.get("completion_tokens", 0)
                self.usage["calls"] += 1
                return data["choices"][0]["message"]["content"]
            except Exception as e:  # noqa: BLE001  网络、超时、返回格式错误统一按失败处理
                err = e
        raise LLMCallError(f"{type(err).__name__}: {err}")

    def chat_json(self, system: str, user: str, retry: int = 1, **params) -> dict:
        """要求模型输出 JSON；容忍 ```json 包裹，解析失败抛 LLMCallError。"""
        text = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], retry, **params)
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(m.group(0) if m else text)
        except (ValueError, AttributeError) as e:
            raise LLMCallError(f"模型输出不是 JSON：{text[:200]}") from e
