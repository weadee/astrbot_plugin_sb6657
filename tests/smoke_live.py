"""联网冒烟测试：用真实 sb6657 后端跑一遍插件的主要分支。"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

# ---- stub astrbot（与自测脚本同款） ----


class _Filter:
    class EventMessageType:
        ALL = "all"

    @staticmethod
    def command(name, alias=None):
        return lambda fn: fn

    @staticmethod
    def event_message_type(kind):
        return lambda fn: fn

    @staticmethod
    def llm_tool(name=None, **kwargs):
        return lambda fn: fn


class _Logger:
    def info(self, m, *a, **k):
        print("[info]", m)

    def warning(self, m, *a, **k):
        print("[warn]", m)

    def error(self, m, *a, **k):
        print("[error]", m)


api_mod = types.ModuleType("astrbot.api")
api_mod.logger = _Logger()
api_mod.AstrBotConfig = dict
event_mod = types.ModuleType("astrbot.api.event")
event_mod.filter = _Filter
event_mod.AstrMessageEvent = type("AstrMessageEvent", (), {})
star_mod = types.ModuleType("astrbot.api.star")


class Star:
    def __init__(self, context):
        self.context = context


star_mod.Star = Star
star_mod.Context = type("Context", (), {})
for name, m in (
    ("astrbot", types.ModuleType("astrbot")),
    ("astrbot.api", api_mod),
    ("astrbot.api.event", event_mod),
    ("astrbot.api.star", star_mod),
):
    sys.modules[name] = m
sys.modules["astrbot"].api = api_mod

spec = importlib.util.spec_from_file_location(
    "sb6657_main", Path(__file__).resolve().parents[1] / "main.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


async def main():
    plugin = mod.Sb6657Plugin(
        star_mod.Context(),
        {"default_count": 2, "max_count": 5, "hot_count": 3, "timeout": 15},
    )
    ok = True
    print("=== 句式抽取 ===")
    for sentence in ("niko的烂梗", "来点詹姆斯的烂梗", "有没有device烂梗", "来条烂梗", "这什么烂梗"):
        print(f"  {sentence!r} -> {mod._match_nl_topic(sentence)!r}")

    for label, coro in (
        ("随机 2 条", plugin.dispatch("2", "live")),
        ("关键词搜索 niko", plugin.dispatch("搜 niko", "live")),
        ("主题检索 niko", plugin.reply_topic("niko", "niko的烂梗", "live")),
        ("主题检索 device", plugin.reply_topic("device", "device的烂梗", "live")),
        ("24h 热榜", plugin.dispatch("热门", "live")),
        ("七天热榜", plugin.dispatch("7天", "live")),
        ("标签列表", plugin.dispatch("标签", "live")),
        ("标签 #07", plugin.dispatch("#07", "live")),
        ("默认（空参）", plugin.dispatch("", "live")),
    ):
        try:
            out = await coro
            assert out and out.strip(), "返回为空"
            print(f"\n=== {label} ===\n{out[:600]}")
        except Exception as exc:
            ok = False
            print(f"\n=== {label} === ❌ {type(exc).__name__}: {exc}")
    await plugin.terminate()
    print("\n" + ("✅ 联网冒烟测试通过" if ok else "❌ 有分支失败"))
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
