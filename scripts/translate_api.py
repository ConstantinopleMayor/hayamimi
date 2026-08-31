"""OpenAI-compatible API subtitle translation module.

Translates any detected source language -> target language through an
OpenAI-compatible chat completions endpoint (OpenAI, Ollama's /v1, LM Studio,
vLLM, etc.), instead of the bundled local MT models (which only do ja->*).

Interface notes (mirroring scripts/translate_ja_en.py / translate_m2m.py):
- Exposes the translator contract the worker expects, extended with a
  `src_lang` argument: `translate(text, src_lang)`.
- Any exception, timeout, or empty output falls back to returning the
  original text unchanged -- a subtitle line must never go blank or wrong.
- Uses only the Python standard library (urllib.request), so no new
  dependency is required.

Configuration file (openai_translate.json in the project root):
    {
      "base_url":  "http://localhost:11434/v1",   # OpenAI-compatible base
      "model":     "qwen2.5:7b",                  # model name
      "api_key":   "",                            # leave empty for local servers
      "timeout_sec": 10,                          # per-request timeout
      "targets":   ["zh", "en", "ko"],            # allowed api: targets
      "src_names": { "ja": "Japanese", "zh": "Chinese", "en": "English",
                     "ko": "Korean", "yue": "Cantonese" },
      "prompt":    "optional system-prompt template; {src} and {target} are
                    replaced with source/target language names. Omit to use
                    the built-in default."
    }
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_PROJECT_ROOT, "openai_translate.json")

# Fall back to the language code itself if src_names has no entry.
_DEFAULT_SRC_NAMES = {
    "ja": "Japanese",
    "zh": "Chinese",
    "en": "English",
    "ko": "Korean",
    "yue": "Cantonese",
}

_TARGET_NAMES = {
    "zh": "Chinese (Simplified)",
    "en": "English",
    "ko": "Korean",
}

# Language names for targets not in _TARGET_NAMES.
_EXTRA_TARGET_NAMES = {
    "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian",
    "ja": "Japanese", "yue": "Cantonese",
}

_BUILTIN_PROMPT = (
    "You are a real-time simultaneous interpreter for live subtitles. "
    "Translate the user's {src} text to {target}. Output ONLY the translation, "
    "no commentary, no quotes, no explanation. Keep numbers and proper nouns "
    "intact."
)


def default_config() -> dict:
    """A config dict the API translator can run with (all fields present)."""
    return {
        "base_url": "",
        "model": "",
        "api_key": "",
        "timeout_sec": 10,
        "targets": ["zh", "en", "ko"],
        "src_names": dict(_DEFAULT_SRC_NAMES),
        "prompt": "",
    }


def load_config(path: str = CONFIG_PATH) -> dict | None:
    """Read the openai_translate.json config; None if missing/unusable.

    A config with an empty base_url or model counts as unusable (None).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(cfg, dict):
        return None
    if not str(cfg.get("base_url", "")).strip() or not str(cfg.get("model", "")).strip():
        return None
    out = default_config()
    out.update(cfg)
    if not isinstance(out.get("targets"), list) or not out["targets"]:
        out["targets"] = default_config()["targets"]
    if not isinstance(out.get("src_names"), dict):
        out["src_names"] = dict(_DEFAULT_SRC_NAMES)
    if not isinstance(out.get("prompt"), str):
        out["prompt"] = ""
    return out


def api_targets(cfg: dict | None) -> set[str]:
    """The set of target languages the API translator can serve."""
    if not cfg:
        return set()
    targets = cfg.get("targets") or []
    return {str(t).strip().lower() for t in targets if str(t).strip()}


def target_name(lang: str) -> str:
    return _TARGET_NAMES.get(lang, _EXTRA_TARGET_NAMES.get(lang, lang))


class TranslatorApi:
    """Translates any source language -> target via an OpenAI-compatible chat API.

    The worker calls `translate(text, src_lang)`. Like the local translators this
    class never raises out of translate() and never returns an empty line: any
    failure returns the source text unchanged.
    """

    # Marked so the worker knows this translator can handle ANY source
    # language (local MT modules only handle ja -> target).
    IS_API = True

    def __init__(self, cfg: dict, target_lang: str):
        if not cfg or not target_lang:
            raise ValueError("TranslatorApi requires a usable config and target_lang")
        self.cfg = cfg
        self.target_lang = target_lang
        self._base_url = str(cfg.get("base_url", "")).rstrip("/")
        self._model = str(cfg.get("model", ""))
        self._api_key = str(cfg.get("api_key", "")).strip()
        timeout = cfg.get("timeout_sec", 10)
        try:
            self._timeout = float(timeout) if timeout is not None else 10.0
        except (TypeError, ValueError):
            self._timeout = 10.0

    # -- internals ----------------------------------------------------------

    def _src_name(self, src_lang: str) -> str:
        if not src_lang:
            return "the source language"
        names = self.cfg.get("src_names") or {}
        return names.get(src_lang, _DEFAULT_SRC_NAMES.get(src_lang, src_lang))

    def _system_prompt(self, src_lang: str) -> str:
        template = self.cfg.get("prompt") or _BUILTIN_PROMPT
        return template.replace("{src}", self._src_name(src_lang)).replace(
            "{target}", target_name(self.target_lang)
        )

    def _request(self, text: str, src_lang: str) -> str:
        """Return the translated text; raise on any HTTP-level failure."""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt(src_lang)},
                {"role": "user", "content": text},
            ],
            "temperature": 0.0,
            # keep summaries short; live subtitle rhythm
            "max_tokens": 500,
        }
        # Reasoning-model support (OpenAI-compatible endpoints like
        # SiliconFlow): `enable_thinking` switches thinking mode on/off for
        # MOST reasoning models (DeepSeek-V4, GLM, Kimi, ...). NOT all models
        # accept the field — e.g. SiliconFlow's tencent/Hunyuan-MT-7B returns
        # 400 code 20015 "current model does not support parameter
        # enable_thinking". So we only send it when the config EXPLICITLY sets
        # it: `"enable_thinking": true/false`. Omitted (default) = don't send
        # the field at all, which keeps strict MT endpoints happy.
        et = self.cfg.get("enable_thinking")
        if et is not None:
            payload["enable_thinking"] = bool(et) if not isinstance(et, str) else et
        body = json.dumps(payload).encode("utf-8")
        url = self._base_url + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("API response has no choices")
        msg = choices[0].get("message") or {}
        out = msg.get("content")
        if not isinstance(out, str) or not out.strip():
            raise ValueError("API response has empty content")
        return out.strip()

    # -- public interface ---------------------------------------------------

    def translate(self, text: str, src_lang: str = "ja") -> str:
        """Translate one line from src_lang to self.target_lang.

        Returns the source text unchanged (never raises, never blanks the
        line) if input is empty/whitespace-only, or on any API failure.
        """
        if text is None:
            return text
        stripped = text.strip()
        if not stripped:
            return text
        try:
            out = self._request(stripped, src_lang or "ja")
            if not out:
                return text
            return out
        except Exception:
            # urllib.error.HTTPError/URLError, TimeoutError, JSON errors, ...:
            # a subtitle line must never go blank.
            return text


def _smoke_test() -> None:
    """Minimal connectivity test against the configured endpoint."""
    sys.stdout.reconfigure(encoding="utf-8")

    cfg = load_config()
    if cfg is None:
        print("no usable openai_translate.json (missing or empty base_url/model) "
              "-- nothing to test")
        return
    print(f"config: base_url={cfg['base_url']} model={cfg['model']} "
          f"timeout={cfg.get('timeout_sec')}")

    tr = TranslatorApi(cfg, target_lang=str(cfg["targets"][0]))
    for src, line in [("ja", "今日はお集まりいただきありがとうございます。"),
                      ("en", "The meeting starts at 3pm."),
                      ("zh", "会议下午三点开始。"),
                      ("ko", "회의는 오후 3시에 시작합니다.")]:
        t0 = __import__("time").perf_counter()
        out = tr.translate(line, src)
        ms = (__import__("time").perf_counter() - t0) * 1000
        print(f"[{src} -> {tr.target_lang}] [{ms:6.0f}ms] {line!r} -> {out!r}")


if __name__ == "__main__":
    _smoke_test()