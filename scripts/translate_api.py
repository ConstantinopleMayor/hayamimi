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
                    the built-in default.",
      "context_n": 6,       # recent (src, translation) pairs sent as context
                            # (0 disables); partial drafts never enter it
      "context_timeout_s": 60  # no translation for this long = new topic:
                            # the context is dropped
    }
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import deque


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

# Full-width / half-width parenthetical, innermost (no nesting support).
_GLOSS_RE = re.compile(r"[（(]([^（()）]*?)[）)]")

# Characters allowed to stay inside a parenthetical WITHOUT marking it as a
# gloss: CJK, digits, CJK punctuation, a few ASCII time/ratio glyphs.
_KEEP_INNER = re.compile(
    r"[^\u4e00-\u9fff0-9，。、！？；：“”‘’《》—…·：:\s年月日点分号%\\/\\-]"
)


def _strip_gloss(text: str) -> str:
    """Drop parenthetical glosses that are not pure Chinese.

    Hunyuan-MT-7B (and some other chat models) kindly append the original
    word as a gloss: 亡灵节（Día de los Muertos）. In a subtitle line that
    gloss is noise -- drop the whole parenthetical whenever its content
    contains ANY character outside the keep-set (latin, kana, hangul,
    ...). Pure-Chinese parentheticals (（北京）, （预计8:30）) are legitimate
    and kept.
    """
    if not text:
        return text
    return _GLOSS_RE.sub(lambda m: "" if _KEEP_INNER.search(m.group(1)) else m.group(0), text)


_BUILTIN_PROMPT = (
    "You are a real-time simultaneous interpreter for live subtitles. "
    "Translate the user's {src} text to {target}. Output ONLY the translation "
    "of that text -- no commentary, no quotes, no explanation, no labels or "
    "prefaces (no '翻译:', 'Translation:', quotation marks, or echoed source), "
    "no extra sentences. Keep numbers and proper nouns intact. Do NOT add "
    "parenthetical glosses like 亡灵节（Día de los Muertos）: translate names "
    "and terms directly and omit the original form."
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
        "context_n": 6,
        "context_timeout_s": 60,
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
    for key, fallback in (("context_n", 6), ("context_timeout_s", 60)):
        try:
            out[key] = max(0, int(out.get(key, fallback)))
        except (TypeError, ValueError):
            out[key] = fallback
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
        # Rolling conversation context: per source language, the last N
        # confirmed (source, translation) pairs, replayed into the request
        # as few-shot examples so names/terms/pronouns stay consistent.
        # Partial drafts never enter it (record=False); a gap longer than
        # context_timeout_s drops it (topic change). config context_n=0
        # disables the whole mechanism.
        self._context_n = int(cfg.get("context_n", 6))
        try:
            self._context_timeout = float(cfg.get("context_timeout_s", 60))
        except (TypeError, ValueError):
            self._context_timeout = 60.0
        self._hist: dict[str, deque] = {}       # src_lang -> deque[(src, tr)]
        self._hist_ts: dict[str, float] = {}    # src_lang -> last record time

    def _push_history(self, src_lang: str, src: str, tr: str) -> None:
        """Record one confirmed pair; caps itself at context_n entries."""
        if self._context_n <= 0 or not src or not tr:
            return
        h = self._hist.setdefault(src_lang, deque(maxlen=self._context_n))
        h.append((src, tr))
        self._hist_ts[src_lang] = time.monotonic()

    def _history(self, src_lang: str) -> list:
        """The pairs to replay, or [] once the topic gap elapsed."""
        if self._context_n <= 0 or src_lang not in self._hist:
            return []
        if self._context_timeout > 0:
            last = self._hist_ts.get(src_lang, 0.0)
            if time.monotonic() - last > self._context_timeout:
                self._hist.pop(src_lang, None)
                self._hist_ts.pop(src_lang, None)
                return []
        return list(self._hist.get(src_lang, []))

    # -- internals ----------------------------------------------------------

    def _src_name(self, src_lang: str) -> str:
        if not src_lang:
            return "the source language"
        names = self.cfg.get("src_names") or {}
        return names.get(src_lang, _DEFAULT_SRC_NAMES.get(src_lang, src_lang))

    _CONTEXT_NOTE = (
        " Recent lines (the earlier user/assistant pairs) are reference "
        "context: keep names, terminology and pronouns consistent with them. "
        "Translate ONLY the last user message and output ONLY its "
        "translation -- no commentary, no quotes, no explanation, no labels "
        "or prefaces (no '翻译:', 'Translation:', quotation marks, or echoed "
        "source). Do NOT add parenthetical glosses like 亡灵节（Día de los "
        "Muertos）: translate names and terms directly."
    )

    def _system_prompt(self, src_lang: str) -> str:
        template = self.cfg.get("prompt") or _BUILTIN_PROMPT
        base = template.replace("{src}", self._src_name(src_lang)).replace(
            "{target}", target_name(self.target_lang)
        )
        if self._context_n > 0 and self._history(src_lang):
            return base + self._CONTEXT_NOTE
        return base

    def _request(self, text: str, src_lang: str) -> str:
        """Return the translated text; raise on any HTTP-level failure.

        Recent confirmed (source, translation) pairs are replayed as
        few-shot context (oldest first) -- system, then the pairs, then the
        current line as the only thing to translate.
        """
        messages = [{"role": "system", "content": self._system_prompt(src_lang)}]
        for src, tr in self._history(src_lang):
            messages.append({"role": "user", "content": src})
            messages.append({"role": "assistant", "content": tr})
        messages.append({"role": "user", "content": text})
        payload = {
            "model": self._model,
            "messages": messages,
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

    def translate(self, text: str, src_lang: str = "ja", record: bool = True) -> str:
        """Translate one line from src_lang to self.target_lang.

        Returns the source text unchanged (never raises, never blanks the
        line) if input is empty/whitespace-only, or on any API failure.

        record=True (confirmed lines / refined groups) stores the pair in
        the rolling context; record=False (in-progress partial drafts) does
        not -- a draft is not finished text. A fallback (out == text) is
        never stored either.

        The API output is post-processed by _strip_gloss() before both the
        return value and history recording: non-Chinese parentheticals
        (Hunyuan-MT-7B's 亡灵节（Día de los Muertos） style glosses) are
        dropped, so the rendered subtitle AND the context examples stay
        clean.
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
            out = _strip_gloss(out).strip()
            if not out:  # the whole line was one gloss: keep the source
                return text
            if record and out != stripped:
                self._push_history(src_lang or "ja", stripped, out)
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