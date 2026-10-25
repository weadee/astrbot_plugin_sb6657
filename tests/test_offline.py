"""离线自测：用 stub 顶掉 astrbot，验证 main.py 的解析 / 分发 / 格式化逻辑。"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

# ---------------------------------------------------------------- stub astrbot


class _Filter:
    class EventMessageType:
        ALL = "all"

    @staticmethod
    def command(name, alias=None):
        def deco(fn):
            fn.__dsh_command__ = (name, alias)
            return fn
        return deco

    @staticmethod
    def event_message_type(kind):
        def deco(fn):
            fn.__dsh_listener__ = kind
            return fn
        return deco

    @staticmethod
    def llm_tool(name=None, **kwargs):
        def deco(fn):
            fn.__dsh_llm_tool__ = name
            return fn
        return deco


class _Logger:
    def _p(self, level, msg, *a, **kw):
        print(f"[{level}] {msg}")

    def info(self, msg, *a, **kw):
        self._p("info", msg)

    def warning(self, msg, *a, **kw):
        self._p("warn", msg)

    def error(self, msg, *a, **kw):
        self._p("error", msg)


api_mod = types.ModuleType("astrbot.api")
api_mod.logger = _Logger()
api_mod.AstrBotConfig = dict
event_mod = types.ModuleType("astrbot.api.event")
event_mod.filter = _Filter


class AstrMessageEvent:
    pass


event_mod.AstrMessageEvent = AstrMessageEvent
star_mod = types.ModuleType("astrbot.api.star")


class Star:
    def __init__(self, context):
        self.context = context


class Context:
    pass


star_mod.Star = Star
star_mod.Context = Context
for name, mod in (
    ("astrbot", types.ModuleType("astrbot")),
    ("astrbot.api", api_mod),
    ("astrbot.api.event", event_mod),
    ("astrbot.api.star", star_mod),
):
    sys.modules[name] = mod
sys.modules["astrbot"].api = api_mod
api_mod.event = event_mod
api_mod.star = star_mod

# ---------------------------------------------------------------- load plugin

PLUGIN = Path(__file__).resolve().parents[1] / "main.py"
spec = importlib.util.spec_from_file_location("sb6657_main", PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# ---------------------------------------------------------------- fake backend

RANDOM = {
    "id": 23866,
    "barrage": "一个战士，辛苦劳作踩缝纫机16个月，获得三次表扬还余五分。",
    "cnt": "7",
    "tags": "06",
    "submitTime": "2026-09-14T21:14:21",
}
PAGE = {
    "code": 200,
    "msg": "请求成功",
    "data": {
        "list": [
            {
                "id": 9001,
                "barrage": "niko 你人呢？niko！",
                "cnt": "12",
                "tags": "07",
                "submitTime": "2026-09-01T10:00:00",
            }
        ],
        "total": 1,
        "lastPage": True,
    },
}
SEARCH = {
    "code": 200,
    "msg": "ok",
    "data": {
        "total": 4,
        "list": [
            {
                "id": 9002 + i,
                "barrage": f"搜索命中的 niko 烂梗 {i + 1}",
                "cnt": "3",
                "tags": "07,13",
                "submitTime": "2026-09-02T11:00:00",
            }
            for i in range(4)
        ],
        "isLastPage": True,
    },
}
DEVICE_SEARCH = {
    "code": 200,
    "msg": "ok",
    "data": {
        "total": 2,
        "list": [
            {
                "id": 8001 + i,
                "barrage": f"搜索命中的 device 烂梗 {i + 1}",
                "cnt": "5",
                "tags": "07",
                "submitTime": "2026-09-03T11:00:00",
            }
            for i in range(2)
        ],
        "isLastPage": True,
    },
}
HOT24 = {
    "code": 200,
    "msg": "ok",
    "data": [
        {
            "id": None,
            "barrageId": "23826",
            "barrage": "热榜第一条",
            "cnt": "46",
            "tags": "00,02",
            "hotDateTime": "2026-09-14T21:52:24",
        }
    ],
}
TAGS = {
    "code": 200,
    "msg": "ok",
    "data": [
        {"dictValue": "06", "dictLabel": "群魔乱舞"},
        {"dictValue": "07", "dictLabel": "NiKo"},
    ],
}
BOOM = {"code": 500, "msg": "Cannot invoke String.split"}


class FakePlugin(mod.Sb6657Plugin):
    async def _request(self, method, path, *, params=None, json_body=None):
        if path == "/machine/getRandOne":
            SEQ[0] += 1
            item = dict(RANDOM)
            item["id"] = 23866 + SEQ[0]
            item["barrage"] = f"{RANDOM['barrage']}（第 {SEQ[0]} 条）"
            return item
        if path == "/machine/Page":
            return PAGE["data"]
        if path == "/machine/pageSearch":
            if FAIL_SEARCH[0]:
                return BOOM
            keyword = str((json_body or {}).get("barrage") or "")
            if keyword.lower() == "device":
                return DEVICE_SEARCH["data"]
            if "niko" in keyword.lower() or keyword in ("s1mple", "karrigan"):
                return SEARCH["data"]
            return {"total": 0, "list": [], "isLastPage": True}
        if path in ("/machine/hotBarrageOf24H", "/machine/hotBarrageOf7Day"):
            return HOT24["data"]
        if path == "/machine/dictList":
            return TAGS["data"]
        raise AssertionError(f"unexpected path {path}")

    async def _llm_text(self, prompt, system_prompt=None, umo=None):
        LLM_CALLS.append(prompt)
        LLM_SYSTEMS.append(system_prompt or "")
        return LLM_REPLY[0]


FAIL_SEARCH = [False]
SEQ = [0]
LLM_CALLS = []
LLM_SYSTEMS = []
LLM_REPLY = ['{"terms": ["大表哥", "karrigan"]}']


class FakeEvent:
    def __init__(self, text, origin="test:GroupMessage:1", wake=False):
        self.message_str = text
        self.unified_msg_origin = origin
        self.stopped = False
        self.llm = None
        self.is_at_or_wake_command = wake

    def get_message_str(self):
        return self.message_str

    def get_group_id(self):
        return "1"

    def plain_result(self, text):
        return f"RESULT<{text}>"

    def stop_event(self):
        self.stopped = True

    def should_call_llm(self, value):
        self.llm = value


# ---------------------------------------------------------------- tests

FAILURES = []


def check(label, cond, extra=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label} {extra if not cond else ''}")
    if not cond:
        FAILURES.append(label)


async def main():
    cfg = {
        "default_count": 1,
        "max_count": 5,
        "hot_count": 2,
        "max_length": 200,
        "show_title": True,
        "show_count_header": True,
        "show_tags": True,
        "show_meta": True,
        "append_source": True,
        "dedupe_size": 30,
        "auto_trigger_enable": True,
        "auto_trigger_keywords": ["来条烂梗"],
        "auto_trigger_cooldown": 0,
        "auto_trigger_stop_llm": True,
    }
    plugin = FakePlugin(Context(), cfg)

    # 1. 随机一条
    out = await plugin.dispatch("", "s1")
    check("随机一条包含内容与标题", "踩缝纫机" in out and "🎲 6657 烂梗 #" in out, out)
    check("标签被翻译成中文", "群魔乱舞" in out, out)
    check("不带站点来源之外的原始标签码", "06" not in out, out)

    # 2. 指令参数解析
    ev = FakeEvent("/烂梗 搜 niko")
    check("_extract_args 解析指令参数", plugin._extract_args(ev) == "搜 niko", plugin._extract_args(ev))
    ev2 = FakeEvent("/来条烂梗")
    check("别名参数解析为空", plugin._extract_args(ev2) == "", plugin._extract_args(ev2))
    ev3 = FakeEvent("/sb6657 热门")
    check("英文别名参数解析", plugin._extract_args(ev3) == "热门", plugin._extract_args(ev3))

    # 3. 搜索
    out = await plugin.dispatch("搜 niko", "s1")
    check("搜索返回命中结果", "niko" in out, out)

    # 4. 搜索接口挂了时走本地兜底
    FAIL_SEARCH[0] = True
    out = await plugin.dispatch("搜 niko", "s1")
    check("搜索接口异常时本地兜底仍能命中", "niko" in out, out)
    FAIL_SEARCH[0] = False

    # 5. 热榜
    out = await plugin.dispatch("热门", "s1")
    check("热榜标题与内容", "24 小时热榜" in out and "热榜第一条" in out, out)
    out = await plugin.dispatch("7天", "s1")
    check("七天热榜调用同一渲染路径", "七天热榜" in out, out)

    # 6. 标签列表 / 标签随机
    out = await plugin.dispatch("标签", "s1")
    check("标签列表含编号与名称", "07 · NiKo" in out, out)
    out = await plugin.dispatch("#07", "s1")
    check("按编号取标签", "NiKo" in out, out)
    out = await plugin.dispatch("NiKo", "s1")
    check("按标签名取标签", "NiKo" in out, out)
    out = await plugin.dispatch("#99", "s1")
    check("未知标签给出提示", "没有找到标签" in out, out)

    # 7. 条数解析与上限
    out = await plugin.dispatch("99", "s1")
    check("条数被 max_count 截断", "× 5" in out, out)
    out = await plugin.dispatch("2", "s2")
    check("多条时带有 × N 标题", "× 2" in out, out)

    # 8. 帮助
    out = await plugin.dispatch("帮助", "s1")
    check("帮助文本含指令", "/烂梗 标签" in out, out)

    # 9. 自动触发
    ev = FakeEvent("群友说：来条烂梗")
    results = [r async for r in plugin.on_any_message(ev)]
    check("自动触发回复一条", len(results) == 1 and "RESULT<" in results[0], results)
    check("自动触发默认阻止 LLM 续写", ev.stopped and ev.llm is False, (ev.stopped, ev.llm))

    ev = FakeEvent("来条烂梗")
    results = [r async for r in plugin.on_any_message(ev)]
    check("再次触发被冷却/去重约束后仍有回复", len(results) == 1, results)

    ev = FakeEvent("烂梗", wake=True)
    results = [r async for r in plugin.on_any_message(ev)]
    check("@机器人+指令名时自动触发让位给指令", results == [], results)

    ev = FakeEvent("/烂梗 帮助")
    results = [r async for r in plugin.on_any_message(ev)]
    check("带唤醒前缀的指令不被自动触发", results == [], results)

    ev = FakeEvent("普通聊天")
    results = [r async for r in plugin.on_any_message(ev)]
    check("无关消息不触发", results == [], results)

    ev = FakeEvent("/烂梗")
    results = [r async for r in plugin.on_any_message(ev)]
    check("指令消息不会被自动触发重复处理", results == [], results)

    # 9.5 自然语言「xxx 的烂梗」检索
    check("话题抽取：niko的烂梗", mod._match_nl_topic("niko的烂梗") == "niko")
    check("话题抽取：来点詹姆斯的烂梗", mod._match_nl_topic("来点詹姆斯的烂梗") == "詹姆斯")
    check("话题抽取：有没有device烂梗", mod._match_nl_topic("有没有device烂梗") == "device")
    check("话题抽取：来条烂梗不算话题", mod._match_nl_topic("来条烂梗") is None)
    check("话题抽取：吐槽不算话题", mod._match_nl_topic("这什么烂梗") is None)
    check("话题抽取：夹带口水词不算", mod._match_nl_topic("群友说：来条烂梗") is None)
    check("话题抽取：无烂梗字样不触发", mod._match_nl_topic("今天天气不错") is None)
    check("话题抽取：@提及会被剥掉", mod._match_nl_topic("@机器人 niko的烂梗") == "niko")

    check("LLM 检索词解析", mod._parse_terms('{"terms": ["a", "b", "a"]}', 3) == ["a", "b"])
    check("LLM 检索词解析兜底引号", mod._parse_terms('应该是 "device" 吧', 2) == ["device"])
    check("LLM 检索词解析无词可抠", mod._parse_terms("我觉得是 niko 和 device", 2) == [])

    # 9.6 数量解析
    check("数量：句尾 3 条", mod._split_count("来点 niko 的烂梗 3 条") == ("来点 niko 的烂梗", 3), mod._split_count("来点 niko 的烂梗 3 条"))
    check("数量：紧贴烂梗", mod._split_count("niko的烂梗3条") == ("niko的烂梗", 3), mod._split_count("niko的烂梗3条"))
    check("数量：烂梗后面带动词", mod._split_count("niko的烂梗来3条") == ("niko的烂梗", 3), mod._split_count("niko的烂梗来3条"))
    check("数量：写在前面", mod._split_count("来3条niko的烂梗") == ("来niko的烂梗", 3), mod._split_count("来3条niko的烂梗"))
    check("数量：中文数字", mod._split_count("来点 device 的烂梗 两 个") == ("来点 device 的烂梗", 2), mod._split_count("来点 device 的烂梗 两 个"))
    check("数量：「一个人」不算数量", mod._split_count("一个人吃饭的烂梗") == ("一个人吃饭的烂梗", None), mod._split_count("一个人吃饭的烂梗"))
    check("数量：没写就返回 None", mod._split_count("niko的烂梗") == ("niko的烂梗", None), mod._split_count("niko的烂梗"))
    check("数量：十二能解析", mod._count_from_token("十二") == 12, mod._count_from_token("十二"))

    out = await plugin.dispatch("搜 niko 2", "cnt-cmd")
    check("指令搜索指定 2 条", out.count("搜索命中的 niko") == 2, out)

    out = await plugin.dispatch("搜 niko", "cnt-cmd2")
    check("指令搜索默认按 search_count 发 3 条", out.count("搜索命中的 niko") == 3, out)

    check("数量上限被 max_count 收住", plugin._clamp_count(9) == 5, plugin._clamp_count(9))
    out = await plugin.dispatch("搜 九条 niko", "cnt-cmd3")
    check("指令搜索能解析中文数量并受上限约束", out.count("搜索命中的 niko") == 4, out)

    out = await plugin.dispatch("niko的烂梗 两条", "cnt-cmd4")
    check("指令主题检索按数量返回", out.count("🎲") == 2, out)

    ev = FakeEvent("来点niko的烂梗 4条", origin="cnt:nl:1")
    results = [r async for r in plugin.on_any_message(ev)]
    check("自然语言带数量返回对应条数", len(results) == 1 and results[0].count("搜索命中的 niko") == 4, results)

    ev = FakeEvent("来 3 条烂梗", origin="cnt:nl:2")
    results = [r async for r in plugin.on_any_message(ev)]
    check("只说「来 3 条烂梗」按数量随机", len(results) == 1 and results[0].count("踩缝纫机") == 3, results)

    out = await plugin.dispatch("niko的烂梗", "nl-cmd")
    check("指令参数也能按主题搜", "niko" in out, out)

    ev = FakeEvent("niko的烂梗", origin="nl:group:1")
    results = [r async for r in plugin.on_any_message(ev)]
    check("自然语言检索直接命中", len(results) == 1 and "niko" in results[0], results)
    check("自然语言检索后阻止 LLM 续写", ev.stopped and ev.llm is False, (ev.stopped, ev.llm))

    ev = FakeEvent("niko的烂梗", origin="nl:group:1")
    results = [r async for r in plugin.on_any_message(ev)]
    check("自然语言检索有冷却", results == [], results)

    LLM_CALLS.clear()
    LLM_SYSTEMS.clear()
    ev = FakeEvent("表哥的烂梗", origin="nl:group:2")
    results = [r async for r in plugin.on_any_message(ev)]
    check("直搜无果时走 LLM 扩词", len(results) == 1 and "niko" in results[0], results)
    check("LLM 扩词提示词带原话", any("表哥" in p for p in LLM_CALLS), LLM_CALLS)
    check("别名对照进入系统提示词", any("大表哥=karrigan" in s for s in LLM_SYSTEMS), LLM_SYSTEMS)
    check("系统提示词带上设备的映射", any("设备=device" in s for s in LLM_SYSTEMS), LLM_SYSTEMS)
    check("系统提示词不再把大表哥当 device", not any("大表哥」→ device" in s for s in LLM_SYSTEMS), LLM_SYSTEMS)

    hints = plugin._alias_hints()
    check("别名对照：默认值含大表哥", "大表哥=karrigan" in hints.splitlines(), hints)
    check("别名对照：默认值含设备", "设备=device" in hints.splitlines(), hints)
    check("别名对照：设备映射到 device", plugin._alias_map().get("设备") == "device", plugin._alias_map())
    check("别名对照：阿杜是 dupreeh", plugin._alias_map().get("阿杜") == "dupreeh", plugin._alias_map())
    plugin.config["llm_alias_hints"] = "大表哥=karrigan; 设备=device\n\n"
    check("别名对照：分号与空行都能拆", plugin._alias_hints().splitlines() == ["大表哥=karrigan", "设备=device"], plugin._alias_hints())

    # 外号直连：规则层就能把「设备」翻成 device，不花 token
    LLM_CALLS.clear()
    plugin.config["llm_alias_hints"] = "设备=device"
    ev = FakeEvent("设备的烂梗", origin="nl:device:1")
    results = [r async for r in plugin.on_any_message(ev)]
    check("外号直连：设备的烂梗能搜到 device 的梗", len(results) == 1 and "device" in results[0], results)
    check("外号直连不消耗 LLM", LLM_CALLS == [], LLM_CALLS)
    plugin.config["llm_alias_hints"] = "大表哥=karrigan"

    plugin.config["llm_search_enable"] = False
    ev = FakeEvent("完全查不到的东西的烂梗", origin="nl:group:3")
    results = [r async for r in plugin.on_any_message(ev)]
    check("查不到时默认沉默", results == [], results)

    plugin.config["nl_search_reply_when_empty"] = True
    ev = FakeEvent("完全查不到的东西的烂梗", origin="nl:group:4")
    results = [r async for r in plugin.on_any_message(ev)]
    check("开启后查不到会提示", len(results) == 1 and "没翻到" in results[0], results)
    plugin.config["nl_search_reply_when_empty"] = False
    plugin.config["llm_search_enable"] = True

    tool_text = await plugin.llm_find_meme(FakeEvent("x"), "niko")
    check("LLM 工具返回梗原文", "niko" in tool_text, tool_text)
    tool_text = await plugin.llm_find_meme(FakeEvent("x"), "查不到")
    check("LLM 工具查不到时明确说明", "没有找到" in tool_text, tool_text)

    # 10. 关闭自动触发
    plugin.config["auto_trigger_enable"] = False
    ev = FakeEvent("来条烂梗")
    results = [r async for r in plugin.on_any_message(ev)]
    check("自动触发开关关闭后不回复", results == [], results)

    # 11. 指令 handler 端到端
    plugin.config["auto_trigger_enable"] = True
    ev = FakeEvent("/烂梗 热门")
    results = [r async for r in plugin.sb6657_cmd(ev)]
    check("指令 handler 正常产出", len(results) == 1 and "热榜" in results[0], results)

    # 12. 默认配置（纯梗模式）：不应出现编号、标签、热度、链接、条数头
    pure = FakePlugin(Context(), {"default_count": 1, "max_count": 5})
    out = await pure.dispatch("", "pure")
    check("纯梗模式只输出梗本身", out.startswith(RANDOM["barrage"]) and "\n" not in out, repr(out))
    for token in ("6657", "#", "🏷️", "🔥", "🕒", "🔗", "×"):
        check(f"纯梗模式不含 {token}", token not in out, repr(out))

    out = await pure.dispatch("3", "pure2")
    check("纯梗模式多条也没有条数头", "×" not in out and out.count("\n\n") >= 1, repr(out))

    out = await pure.dispatch("热门", "pure")
    check("纯梗模式热榜没有榜单标题", "6657" not in out and "Top" not in out, repr(out))
    check("纯梗模式热榜仍有内容", "热榜第一条" in out, repr(out))

    out = await pure.dispatch("搜 niko", "pure")
    check("纯梗模式搜索没有搜索标题", "相关烂梗" not in out, repr(out))
    check("纯梗模式搜索仍有内容", "niko" in out, repr(out))

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} 项失败: {FAILURES}")
        return 1
    print("✅ 全部通过")
    return 0


raise SystemExit(asyncio.run(main()))
