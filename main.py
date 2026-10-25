"""astrbot_plugin_sb6657 -- 把 sb6657.cn（玩机器烂梗收集）接进 AstrBot。

数据来源
    sb6657.cn 站点前端使用的公开后端接口（https://hguofichp.cn:10086）。
    站点 README 明确说明：第三方工具调用后端接口时 **不要** 携带 `dpahjdoiaw`
    请求头，否则会污染站点的来源统计。本插件因此只发送最基础的请求头。

功能
    /烂梗            随机来一条
    /烂梗 3          随机来 3 条
    /烂梗 搜 niko    关键词搜索（可加数量：/烂梗 搜 niko 5）
    /烂梗 热门       24 小时热榜
    /烂梗 7天        七天热榜
    /烂梗 标签       查看全部标签
    /烂梗 #07        按标签随机
    另外支持关键词自动触发（默认关闭，见插件配置）。

    LLM 搜索（v1.1）
        群里直接说「niko 的烂梗」「来点詹姆斯的烂梗」这类话，插件会自己
        抽出主题词去站内检索；规则抽不出 / 搜不到时，再让 LLM 扩同义词、
        别名（例如 大表哥 → karrigan，对照表可在配置里维护），并可让 LLM
        对候选做相关性重排。
        同时注册了一个 LLM 工具 sb6657_meme，让主对话模型也能主动调用。

    数量（v1.2）
        「niko 的烂梗 3 条」「来点詹姆斯烂梗 3个」「来 5 条烂梗」都能识别
        （阿拉伯数字或中文数字都行，上限由 max_count 决定）。
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections import deque
from typing import Any

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

# --------------------------------------------------------------------------- 常量

DEFAULT_BASE_URL = "https://hguofichp.cn:10086"
SITE_URL = "https://sb6657.cn"

# 外号对照的默认值，可在插件配置 llm_alias_hints 里覆盖/追加，每行「外号=正式名」
DEFAULT_ALIAS_HINTS = "大表哥=karrigan"

# 指令名与别名。改动时请同步修改 _extract_args 与 _looks_like_command。
COMMAND_NAMES = ("烂梗", "sb6657", "来条烂梗")
COMMAND_ALIASES = {"sb6657", "来条烂梗", "6657烂梗"}

HELP_TEXT = """🀄 6657 玩机器烂梗 · 数据来自 sb6657.cn

/烂梗              随机来一条
/烂梗 3            随机来 3 条
/烂梗 搜 niko      关键词搜索（默认 3 条）
/烂梗 搜 niko 5    搜 5 条
/烂梗 热门 5       24 小时热榜（默认 5 条）
/烂梗 7天          七天热榜
/烂梗 标签         查看全部标签
/烂梗 #07 两条     按标签随机（07 = NiKo）
/烂梗 帮助         显示这份帮助

也可以直接说「NiKo 的烂梗」「来点詹姆斯的烂梗 3 条」，
机器人会自己去站里找相关的，条数写在句尾就行。"""

# 自然语言触发：「xxx 的烂梗」这类句式
NL_SUFFIX_RE = re.compile(r"(?:的)?烂梗(?:呢|啊|呀|吗|吧|嘛)?[\s?？!！。.~～…，,、]*$")
# 中文数字，用于「三条」「十二个」这类数量词
_CN_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
# 句式前缀里的口水词，长词在前，避免「给我来点」只吃掉「给我」
NL_FILLERS = (
    "我想看看",
    "我想看",
    "我想听",
    "给我来点",
    "给我来条",
    "给我来一个",
    "给我来",
    "帮我找找",
    "帮我找",
    "帮我搜",
    "来一个",
    "来一条",
    "来一点",
    "来点",
    "来条",
    "来个",
    "整两个",
    "整点",
    "搞点",
    "搞个",
    "有没有",
    "有木有",
    "查一下",
    "查查",
    "搜一下",
    "搜搜",
    "找一下",
    "找找",
    "发一个",
    "发一条",
    "发点",
    "发个",
    "讲个",
    "说个",
    "想看",
    "看看",
    "求个",
    "求",
    "要个",
    "来",
)
# 抽出来的主题词如果只是这些，说明不是在问某一个具体的梗
NL_TOPIC_STOPWORDS = {
    "这",
    "这个",
    "那个",
    "这种",
    "那种",
    "这些",
    "那些",
    "什么",
    "啥",
    "啥子",
    "你",
    "我",
    "他",
    "她",
    "它",
    "你们",
    "我们",
    "他们",
    "大家",
    "谁",
    "哪",
    "哪个",
    "sb",
    "666",
}


class Sb6657Error(Exception):
    """调用 sb6657 接口过程中的可预期错误。"""


# --------------------------------------------------------------------------- 工具函数


def _strip_wake_prefix(text: str) -> str:
    """去掉唤醒前缀（/ ! ！ . 。 、 等），得到指令正文。"""
    return re.sub(r"^[\s/!！.。、，,]+", "", text or "")


def _normalize_meme(item: Any) -> dict | None:
    """把接口返回的原始条目整理成统一结构，内容为空时返回 None。"""
    if not isinstance(item, dict):
        return None

    content = str(item.get("barrage") or item.get("content") or "").strip()
    if not content:
        return None

    raw_id = item.get("id")
    if raw_id is None:
        raw_id = item.get("barrageId")
    meme_id = str(raw_id) if raw_id is not None else None

    raw_tags = item.get("tags") or ""
    tags = [t.strip() for t in str(raw_tags).split(",") if t.strip()]

    try:
        copy_count = int(item.get("cnt") or 0)
    except (TypeError, ValueError):
        copy_count = 0

    try:
        likes = int(item.get("likes") or 0)
    except (TypeError, ValueError):
        likes = 0

    return {
        "id": meme_id,
        "content": content,
        "tags": tags,
        "copy_count": copy_count,
        "likes": likes,
        "submit_time": item.get("submitTime") or None,
        "hot_time": item.get("hotDateTime") or None,
    }


def _short_time(value: Any) -> str:
    """把 2026-09-14T21:14:21 之类的时间裁剪成 2026-09-14 21:14。"""
    text = str(value or "").replace("T", " ")
    return text[:16]


def _join_result(items: list[str]) -> str:
    """把多条结果拼成一条消息，并去掉首尾空白（QQ 适配器会 strip）。"""
    if len(items) == 1:
        return items[0].strip()
    return ("\n\n".join(items)).strip()


def _clean_topic(raw: str) -> str:
    """去掉主题词前后的标点、@提及和收尾的「的」。"""
    text = re.sub(r"@\S+", " ", raw or "")
    text = text.strip(" \t\r\n，,。.、！!？?：:；;“”\"'（）()【】[]~～…·")
    while text.endswith("的"):
        text = text[:-1].strip()
    return text.strip()


# 数量表达式：「3 条」「三条」「来 5 个」——只有在「烂梗」后面或动词后面才算数
_COUNT_NUM = r"(\d{1,2}|[一二两三四五六七八九十]{1,3})"
_COUNT_UNIT = r"(?:条|个|句|段)"
_COUNT_TOKEN_RE = re.compile(rf"{_COUNT_NUM}\s*{_COUNT_UNIT}")
_COUNT_VERB_CHARS = "来发整搞求要给点弄取"
# 「个」最容易把「一个人」误判成 1 条，所以只认带动词的写法
_COUNT_LOOSE_UNITS = "条句段"
_COUNT_TAIL_RE = re.compile(r"[，,、\s]*(?:来|发|整|搞|要|给我|来点|再加|多来)?\s*")


def _count_from_token(token: str) -> int | None:
    """把「3」「三」「十二」这类 token 转成整数。"""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        value = int(token)
    elif token == "十":
        value = 10
    elif "十" in token:
        head, _, tail = token.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        value = tens * 10 + ones
    else:
        value = _CN_DIGITS.get(token)
    if not value or value < 1:
        return None
    return value


def _split_count(text: str) -> tuple[str, int | None]:
    """把句子里的数量词剥出来，返回 (去掉数量词的句子, 数量)。

    只认两种位置：紧跟在「烂梗」后面（niko 的烂梗来 3 条），或者跟在一个
    动词后面（来 3 条 niko 的烂梗 / 3 条 niko 的烂梗）。否则「一个人吃饭
    的烂梗」这种也会被当成 1 条。
    """
    sentence = (text or "").strip()
    if not sentence:
        return sentence, None

    for matched in _COUNT_TOKEN_RE.finditer(sentence):
        count = _count_from_token(matched.group(1))
        if not count or count > 30:
            continue
        start, end = matched.start(1), matched.end()
        unit = matched.group(0).strip()[-1]
        before = sentence[:start].rstrip()

        if before and before[-1] in _COUNT_VERB_CHARS:
            ok = True
        elif not before:
            ok = unit in _COUNT_LOOSE_UNITS
        else:
            pos = sentence.rfind("烂梗", 0, start)
            ok = pos >= 0 and bool(_COUNT_TAIL_RE.fullmatch(sentence[pos + 2: start]))
        if not ok:
            continue

        rest = (sentence[:start] + sentence[end:]).strip()
        # 「niko 的烂梗来 3 条」剥完会剩一个「来」，顺手擦掉
        rest = rest.rstrip(" \t" + _COUNT_VERB_CHARS)
        return rest, count

    # 没带量词、只用空格隔开的尾随数字：「搜 niko 2」
    tail = re.search(r"[\s，,、](\d{1,2})\s*$", sentence)
    if tail:
        count = _count_from_token(tail.group(1))
        if count and count <= 30:
            return sentence[: tail.start()].strip(), count
    return sentence, None


def _match_nl_topic(text: str) -> str | None:
    """从「来点 xxx 的烂梗」这类句子里抽出主题词，抽不出来返回 None。

    只认「…烂梗」结尾的句式，其他句子一律不管，避免群里随便一句话就被
    当成检索请求。
    """
    if not text:
        return None
    sentence = re.sub(r"@\S+", " ", text).strip()
    if "烂梗" not in sentence:
        return None
    matched = NL_SUFFIX_RE.search(sentence)
    if not matched:
        return None

    topic = _clean_topic(sentence[: matched.start()])
    fillers = sorted(NL_FILLERS, key=len, reverse=True)
    for _ in range(3):  # 「给我来点 xxx」可能要剥两层
        for filler in fillers:
            if topic.startswith(filler):
                topic = _clean_topic(topic[len(filler):])
                break
        else:
            break

    if not (1 <= len(topic) <= 16):
        return None
    if topic.lower() in NL_TOPIC_STOPWORDS:
        return None
    # 残句里还留着口水词、标点或空格，说明这不是「xxx 的烂梗」这种干净句式
    if any(filler in topic for filler in fillers if len(filler) >= 2):
        return None
    # 「这什么烂梗」「哪来的烂梗」这类是吐槽，不是检索请求
    if any(word in topic for word in ("什么", "啥", "哪", "怎么", "这是")):
        return None
    if re.search(r"[\s，,。.！!？?：:；;、~～…]", topic):
        return None
    if not re.search(r"[\w\u4e00-\u9fff]", topic):
        return None
    return topic


def _parse_terms(text: str, limit: int = 3) -> list[str]:
    """从 LLM 的回复里尽量稳地抠出检索词列表。"""
    candidates: list[str] = []
    raw = (text or "").strip()

    payload = None
    matched = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", raw)
    if matched:
        try:
            payload = json.loads(matched.group(0))
        except (ValueError, TypeError):
            payload = None

    if isinstance(payload, dict):
        for key in ("terms", "keywords", "words", "queries"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = [str(v) for v in value]
                break
        else:
            candidates = [str(v) for v in payload.values() if isinstance(v, str)]
    elif isinstance(payload, list):
        candidates = [str(v) for v in payload]
    else:
        candidates = re.findall(r"[\"“]([^\"”]{1,20})[\"”]", raw)

    terms: list[str] = []
    for item in candidates:
        term = _clean_topic(str(item))
        term = term.strip("\"'“”")
        if not term or len(term) > 16:
            continue
        if term.lower() in NL_TOPIC_STOPWORDS:
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _llm_tool_decorator(name: str):
    """老版本 AstrBot 没有 filter.llm_tool，降级成空装饰器，别让插件加载失败。"""
    register = getattr(filter, "llm_tool", None)
    if callable(register):
        try:
            return register(name=name)
        except Exception:  # 不同版本签名不一致时也别崩
            return lambda fn: fn
    return lambda fn: fn


def _parse_indices(text: str, upper: int) -> list[int]:
    """从 LLM 的重排回复里取出一串下标（0 ~ upper-1）。"""
    order: list[int] = []
    for raw in re.findall(r"\d+", text or ""):
        try:
            idx = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < upper and idx not in order:
            order.append(idx)
    return order


# --------------------------------------------------------------------------- 插件


class Sb6657Plugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        # 标签字典缓存：dictValue -> dictLabel
        self._tag_map: dict[str, str] = {}
        self._tag_map_ts: float = 0.0
        # 每个会话最近发过的烂梗 id，用于尽量不重复
        self._recent: dict[str, deque] = {}
        # 每个会话上次自动触发的时间戳，用于限流
        self._last_auto: dict[str, float] = {}
        # 每个会话上次「xxx 的烂梗」检索的时间戳，单独算冷却
        self._last_nl: dict[str, float] = {}

    # ---------------------------------------------------------------- 配置读取

    def _cfg(self, key: str, default: Any = None) -> Any:
        """读取插件配置，任何异常都回退到默认值。"""
        try:
            value = self.config.get(key, default)
        except Exception:  # 配置对象异常时不能拖垮插件
            return default
        return default if value is None else value

    def _base_url(self) -> str:
        return str(self._cfg("base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")

    # ---------------------------------------------------------------- HTTP 层

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is None or self._session.closed:
                try:
                    timeout_sec = float(self._cfg("timeout", 10) or 10)
                except (TypeError, ValueError):
                    timeout_sec = 10.0
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=max(1.0, timeout_sec)),
                    headers={
                        "User-Agent": "AstrBot-sb6657-plugin/1.0 (+https://sb6657.cn)",
                        "Accept": "application/json",
                        # 注意：按站点 README 要求，第三方调用不携带
                        # dpahjdoiaw / siteToken，避免污染站点统计。
                    },
                )
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> Any:
        """请求后端接口并返回 payload["data"]，失败时抛 Sb6657Error。"""
        url = f"{self._base_url()}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            session = await self._get_session()
            async with session.request(
                method, url, params=clean_params or None, json=json_body
            ) as resp:
                if resp.status != 200:
                    raise Sb6657Error(f"接口返回 HTTP {resp.status}")
                payload = await resp.json(content_type=None)
        except asyncio.TimeoutError as exc:
            raise Sb6657Error("请求 sb6657 接口超时") from exc
        except aiohttp.ClientError as exc:
            raise Sb6657Error(f"网络请求失败：{exc}") from exc
        except Sb6657Error:
            raise
        except Exception as exc:  # 包括 JSON 解析失败
            raise Sb6657Error(f"解析接口响应失败：{exc}") from exc

        if not isinstance(payload, dict):
            raise Sb6657Error("接口返回格式异常")
        if payload.get("code") != 200:
            raise Sb6657Error(f"接口报错：{payload.get('msg') or payload.get('code')}")
        return payload.get("data")

    # ---------------------------------------------------------------- 业务接口

    async def api_random(self) -> dict | None:
        """随机一条烂梗。"""
        data = await self._request("GET", "/machine/getRandOne")
        return _normalize_meme(data)

    async def api_page(self, page_num: int = 1, page_size: int = 20, tags: str | None = None) -> list[dict]:
        """分页拉取烂梗列表（不传 tags 即全部）。"""
        data = await self._request(
            "GET",
            "/machine/Page",
            params={"pageNum": page_num, "pageSize": page_size, "tags": tags},
        )
        rows = (data or {}).get("list") if isinstance(data, dict) else None
        return [m for m in (_normalize_meme(r) for r in (rows or [])) if m]

    async def api_search(self, keyword: str, page_size: int = 10) -> list[dict]:
        """关键词搜索（POST）。"""
        data = await self._request(
            "POST",
            "/machine/pageSearch",
            json_body={
                "barrage": keyword,
                "sort": 0,
                "pageSize": page_size,
                "pageNum": 1,
            },
        )
        rows = (data or {}).get("list") if isinstance(data, dict) else None
        return [m for m in (_normalize_meme(r) for r in (rows or [])) if m]

    async def api_search_fallback(self, keyword: str, limit: int = 10) -> list[dict]:
        """搜索接口不可用时的兜底：翻几页全部烂梗，本地做关键词过滤。"""
        needle = keyword.lower()
        found: list[dict] = []
        for page in range(1, 4):
            try:
                rows = await self.api_page(page_num=page, page_size=50)
            except Sb6657Error:
                break
            for meme in rows:
                if needle in meme["content"].lower():
                    found.append(meme)
                    if len(found) >= limit:
                        return found
            if not rows:
                break
        return found

    async def api_hot(self, days: int, limit: int = 5) -> list[dict]:
        """热榜。days == 7 取七天热榜，否则取 24 小时热榜。"""
        path = "/machine/hotBarrageOf7Day" if days == 7 else "/machine/hotBarrageOf24H"
        data = await self._request("GET", path)
        rows = data if isinstance(data, list) else []
        return [m for m in (_normalize_meme(r) for r in rows[:limit]) if m]

    async def api_tags(self) -> list[dict]:
        data = await self._request("GET", "/machine/dictList")
        return data if isinstance(data, list) else []

    async def get_tag_map(self) -> dict[str, str]:
        """获取 dictValue -> dictLabel 映射，带 30 分钟缓存。"""
        ttl = float(self._cfg("tag_cache_ttl", 1800) or 1800)
        if self._tag_map and (time.time() - self._tag_map_ts) < ttl:
            return self._tag_map
        try:
            entries = await self.api_tags()
        except Sb6657Error as exc:
            logger.warning(f"sb6657 获取标签字典失败，沿用旧缓存：{exc}")
            return self._tag_map

        mapping: dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            value = str(entry.get("dictValue") or "").strip()
            label = str(entry.get("dictLabel") or "").strip()
            if value and label:
                mapping[value] = label
        if mapping:
            self._tag_map = mapping
            self._tag_map_ts = time.time()
        return self._tag_map

    # ---------------------------------------------------------------- 去重与格式化

    def _recent_deque(self, key: str) -> deque:
        try:
            size = int(self._cfg("dedupe_size", 30) or 0)
        except (TypeError, ValueError):
            size = 30
        size = max(0, size)
        dq = self._recent.get(key)
        if dq is None or dq.maxlen != size:
            dq = deque(dq or [], maxlen=size or 1)
            self._recent[key] = dq
        return dq

    def format_meme(self, meme: dict, tag_map: dict[str, str]) -> str:
        """把一条烂梗渲染成文本。

        默认只输出梗本身，不带编号、标签、热度、链接等附加信息；
        需要时可在插件配置里逐项打开。
        """
        lines: list[str] = []

        if bool(self._cfg("show_title", False)):
            title = "🎲 6657 烂梗"
            if meme.get("id"):
                title += f" #{meme['id']}"
            lines.append(title)

        content = meme["content"]
        try:
            max_len = int(self._cfg("max_length", 200) or 0)
        except (TypeError, ValueError):
            max_len = 200
        if max_len > 0 and len(content) > max_len:
            content = content[:max_len] + "…"
        lines.append(content)

        meta: list[str] = []
        if bool(self._cfg("show_tags", False)) and meme.get("tags"):
            labels = "、".join(tag_map.get(t, t) for t in meme["tags"])
            meta.append(f"🏷️ {labels}")
        if meme.get("copy_count") and bool(self._cfg("show_meta", False)):
            meta.append(f"🔥 复制 {meme['copy_count']}")
        if bool(self._cfg("show_meta", False)):
            stamp = meme.get("hot_time") or meme.get("submit_time")
            if stamp:
                meta.append(f"🕒 {_short_time(stamp)}")
        if meta:
            lines.append(" · ".join(meta))

        if bool(self._cfg("append_source", False)):
            lines.append(f"🔗 {SITE_URL}")

        return "\n".join(lines)

    def _help_text(self) -> str:
        name = COMMAND_NAMES[0]
        # 帮助文本按实际主指令名渲染
        return HELP_TEXT.replace("/烂梗", f"/{name}")

    # ---------------------------------------------------------------- 各分支实现

    def _max_count(self) -> int:
        try:
            value = int(self._cfg("max_count", 10) or 10)
        except (TypeError, ValueError):
            value = 10
        return max(1, value)

    def _default_count(self) -> int:
        try:
            value = int(self._cfg("default_count", 1) or 1)
        except (TypeError, ValueError):
            value = 1
        return max(1, value)

    def _clamp_count(self, count: Any) -> int | None:
        """把用户写的数量收进 1 ~ max_count，没写或写错返回 None。"""
        if count is None:
            return None
        try:
            value = int(count)
        except (TypeError, ValueError):
            return None
        if value < 1:
            return None
        return min(value, self._max_count())

    async def reply_random(self, count: int, dedupe_key: str) -> str:
        count = max(1, min(count, self._max_count()))
        recent = self._recent_deque(dedupe_key)

        picked: list[dict] = []
        seen: set[str] = set()
        attempts = 0
        max_attempts = count * 6

        while len(picked) < count and attempts < max_attempts:
            attempts += 1
            try:
                meme = await self.api_random()
            except Sb6657Error:
                if not picked:
                    raise
                break
            if not meme:
                continue
            key = str(meme.get("id") or meme["content"])
            if key in seen:
                continue
            # 前 2/3 的尝试里避开最近发过的，避免群里一直重复
            if key in recent and attempts <= count * 4:
                continue
            seen.add(key)
            picked.append(meme)

        if not picked:
            raise Sb6657Error("没有取到烂梗，稍后再试")

        for meme in picked:
            recent.append(str(meme.get("id") or meme["content"]))

        tag_map = await self.get_tag_map()
        blocks = [self.format_meme(m, tag_map) for m in picked]
        if len(blocks) > 1 and bool(self._cfg("show_count_header", False)):
            blocks.insert(0, f"🎲 6657 烂梗 × {len(blocks)}")
        return _join_result(blocks)

    def _search_count(self) -> int:
        """没写数量时，搜索默认发几条。"""
        try:
            value = int(self._cfg("search_count", 3) or 3)
        except (TypeError, ValueError):
            value = 3
        return max(1, min(value, self._max_count()))

    async def reply_search(self, keyword: str, count: int | None = None) -> str:
        keyword = keyword.strip()
        if not keyword:
            return self._help_text()
        count = self._clamp_count(count) or self._search_count()

        try:
            memes = await self.api_search(keyword, page_size=max(count, 3))
        except Sb6657Error as exc:
            logger.warning(f"sb6657 搜索接口不可用，尝试本地过滤：{exc}")
            memes = []
        if not memes:
            try:
                memes = await self.api_search_fallback(keyword, limit=max(count, 3))
            except Sb6657Error as exc:
                logger.warning(f"sb6657 兜底搜索失败：{exc}")
                memes = []

        if not memes:
            return f"🔍 没搜到含「{keyword}」的烂梗，换几个字试试？"

        memes = memes[:count]
        tag_map = await self.get_tag_map()
        blocks: list[str] = []
        if bool(self._cfg("show_count_header", False)):
            blocks.append(f"🔍 「{keyword}」相关烂梗 {len(memes)} 条")
        blocks += [self.format_meme(m, tag_map) for m in memes]
        return _join_result(blocks)

    # ---------------------------------------------------------------- LLM 检索

    async def _llm_text(self, prompt: str, system_prompt: str | None = None, umo: str | None = None) -> str:
        """调一次当前会话使用的对话模型，失败一律降级成空串。"""
        provider = None
        try:
            getter = getattr(self.context, "get_using_provider_async", None)
            if callable(getter):
                provider = await getter(umo)
            else:
                provider = self.context.get_using_provider(umo)
        except Exception as exc:
            logger.warning(f"sb6657 获取对话模型失败：{exc}")
            return ""
        if provider is None:
            logger.warning("sb6657 没有可用的对话模型，跳过 LLM 检索增强。")
            return ""

        try:
            timeout = float(self._cfg("llm_timeout", 15) or 15)
        except (TypeError, ValueError):
            timeout = 15.0
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, system_prompt=system_prompt, contexts=[]),
                timeout=max(3.0, timeout),
            )
        except asyncio.TimeoutError:
            logger.warning("sb6657 调用对话模型超时")
            return ""
        except Exception as exc:
            logger.warning(f"sb6657 调用对话模型失败：{exc}")
            return ""
        return str(getattr(resp, "completion_text", "") or "")

    def _llm_term_limit(self) -> int:
        try:
            value = int(self._cfg("llm_search_max_terms", 3) or 3)
        except (TypeError, ValueError):
            value = 3
        return max(1, min(value, 5))

    def _alias_hints(self) -> str:
        """外号对照表（来自配置），每行「外号=正式名」，也接受分号分隔。"""
        raw = str(self._cfg("llm_alias_hints", DEFAULT_ALIAS_HINTS) or "").strip()
        if not raw:
            return ""
        lines = [ln.strip() for ln in re.split(r"[\r\n;；]+", raw) if ln.strip()]
        return "\n".join(lines[:30])

    async def llm_terms(self, raw_text: str, umo: str | None = None, limit: int | None = None) -> list[str]:
        """让 LLM 把一句话拆成若干个站内能命中的检索词（含别名/外号）。"""
        limit = limit or self._llm_term_limit()
        hints = self._alias_hints()
        system_prompt = (
            "你在为「6657 玩机器烂梗库」抽取检索词。"
            "用户说了一句话，里面提到某个主播、选手、解说、事件或口头禅，"
            "你要给出能在这个站内搜到相关烂梗的关键词。"
            "优先用原词；再补同义外号、英文名、拼音、常见错写、可以拆开检索的片段。"
            "外号必须是你能确定指同一个人的，拿不准就只给原词，不要硬凑。"
            + (f"\n已知别名对照（以此为准，不要改判给其他人）：\n{hints}\n" if hints else "")
            + "不要给整句，不要解释，不要加标点。"
            f'只输出 JSON：{{"terms": ["词1", "词2"]}}，最多 {limit} 个。'
        )
        prompt = f"用户原话：{raw_text}\n请给出检索词。"
        text = await self._llm_text(prompt, system_prompt, umo)
        return _parse_terms(text, limit)

    async def _llm_rerank(self, topic: str, memes: list[dict], umo: str | None, limit: int) -> list[dict]:
        """候选多于要发的条数时，让 LLM 按相关性排个序（默认关闭）。"""
        cap = 8
        subset = memes[:cap]
        listing = "\n".join(f"{i}. {m['content'][:60]}" for i, m in enumerate(subset))
        system_prompt = (
            "你要判断烂梗条目和主题的相关性，把最相关的排在前面，"
            '完全无关的不要选。只输出 JSON：{"order": [0, 2]}。'
        )
        prompt = f"主题：{topic}\n候选：\n{listing}\n请给出最相关的前 {limit} 条下标。"
        text = await self._llm_text(prompt, system_prompt, umo)
        order = _parse_indices(text, len(subset))[:limit]
        if not order:
            return memes
        ranked = [subset[i] for i in order]
        ranked += memes[len(subset):]
        return ranked

    # ---------------------------------------------------------------- 主题检索

    async def _search_quiet(self, keyword: str, limit: int = 6) -> list[dict]:
        """搜索但不上抛错误，接口挂了就本地翻页过滤。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        try:
            rows = await self.api_search(keyword, page_size=limit)
        except Sb6657Error as exc:
            logger.warning(f"sb6657 搜索接口不可用（{keyword}）：{exc}")
            rows = []
        if not rows:
            try:
                rows = await self.api_search_fallback(keyword, limit=limit)
            except Sb6657Error as exc:
                logger.warning(f"sb6657 兜底搜索失败（{keyword}）：{exc}")
                rows = []
        return rows

    async def _search_by_tag_label(self, topic: str, limit: int) -> list[dict]:
        """主题词正好是标签名时，按标签随机取。"""
        tag_map = await self.get_tag_map()
        lowered = topic.lower()
        for value, label in tag_map.items():
            if label.lower() != lowered:
                continue
            try:
                rows = await self.api_page(page_num=1, page_size=max(limit * 5, 20), tags=value)
            except Sb6657Error:
                return []
            if not rows:
                return []
            return random.sample(rows, k=min(limit, len(rows)))
        return []

    def _pick_memes(self, memes: list[dict], count: int, dedupe_key: str) -> list[dict]:
        """从候选里挑几条，尽量避开这个会话最近发过的。"""
        recent = self._recent_deque(dedupe_key or "__global__")
        fresh = [m for m in memes if str(m.get("id") or m["content"]) not in recent]
        pool = fresh or memes
        if not pool:
            return []
        size = min(count, len(pool))
        picked = random.sample(pool, k=size) if size < len(pool) else list(pool)
        for meme in picked:
            recent.append(str(meme.get("id") or meme["content"]))
        return picked

    async def reply_topic(
        self,
        topic: str,
        raw_text: str = "",
        dedupe_key: str = "",
        allow_llm: bool = True,
        count: int | None = None,
    ) -> str:
        """按主题检索：先直搜，搜不到再让 LLM 扩词，最后按标签兜底。"""
        topic = _clean_topic(topic)
        if not topic:
            return ""
        limit = self._clamp_count(count) or self._default_count()
        limit = max(1, min(limit, self._max_count()))
        memes = await self._search_quiet(topic, limit=max(limit * 3, 6))

        if not memes and allow_llm and bool(self._cfg("llm_search_enable", True)):
            terms = await self.llm_terms(raw_text or topic, dedupe_key)
            seen: set[str] = set()
            for term in terms:
                if term.lower() == topic.lower():
                    continue
                for meme in await self._search_quiet(term, limit=max(limit * 2, 4)):
                    key = str(meme.get("id") or meme["content"])
                    if key in seen:
                        continue
                    seen.add(key)
                    memes.append(meme)
                if len(memes) >= limit:
                    break
            if terms:
                logger.info(f"sb6657 主题「{topic}」直搜无果，LLM 扩词：{terms}")

        if not memes:
            memes = await self._search_by_tag_label(topic, limit)
        if not memes:
            return ""

        if len(memes) > limit and bool(self._cfg("llm_rerank", False)):
            memes = await self._llm_rerank(topic, memes, dedupe_key, limit)

        picked = self._pick_memes(memes, limit, dedupe_key)
        if not picked:
            return ""

        tag_map = await self.get_tag_map()
        blocks: list[str] = []
        if len(picked) > 1 and bool(self._cfg("show_count_header", False)):
            blocks.append(f"🔍 「{topic}」相关烂梗 {len(picked)} 条")
        blocks += [self.format_meme(m, tag_map) for m in picked]
        return _join_result(blocks)

    async def reply_hot(self, days: int, count: int | None = None) -> str:
        try:
            limit = int(self._cfg("hot_count", 5) or 5)
        except (TypeError, ValueError):
            limit = 5
        limit = self._clamp_count(count) or max(1, min(limit, self._max_count()))

        memes = await self.api_hot(days, limit=limit)
        if not memes:
            return "🔥 现在还没有热榜数据，稍后再试。"

        tag_map = await self.get_tag_map()
        blocks: list[str] = []
        if bool(self._cfg("show_count_header", False)):
            title = "🔥 6657 七天热榜" if days == 7 else "🔥 6657 24 小时热榜"
            blocks.append(f"{title}（Top {len(memes)}）")
        blocks += [self.format_meme(m, tag_map) for m in memes]
        return _join_result(blocks)

    async def reply_tag_list(self) -> str:
        tag_map = await self.get_tag_map()
        if not tag_map:
            return "🏷️ 暂时拿不到标签列表，稍后再试。"
        lines = ["🏷️ 6657 烂梗标签（用法：/烂梗 #编号）"]
        for value, label in sorted(tag_map.items(), key=lambda kv: kv[0]):
            lines.append(f"  {value} · {label}")
        return "\n".join(lines)

    async def reply_by_tag(self, raw_tag: str, count: int | None = None) -> str:
        raw_tag = raw_tag.strip()
        if not raw_tag:
            return await self.reply_tag_list()

        tag_map = await self.get_tag_map()
        value = raw_tag if raw_tag in tag_map else None
        if value is None:  # 允许用标签名代替编号
            lowered = raw_tag.lower()
            for val, label in tag_map.items():
                if label.lower() == lowered:
                    value = val
                    break
        if value is None:
            return (
                f"🏷️ 没有找到标签「{raw_tag}」。\n"
                "用 /烂梗 标签 查看全部标签，或直接用关键词搜索。"
            )

        count = self._clamp_count(count) or min(self._default_count(), self._max_count())
        rows = await self.api_page(page_num=1, page_size=50, tags=value)
        if not rows:
            return f"🏷️「{raw_tag}」标签下暂时没有烂梗。"

        picked = random.sample(rows, k=min(count, len(rows)))
        label = tag_map.get(value, raw_tag)
        blocks: list[str] = []
        if bool(self._cfg("show_count_header", False)):
            blocks.append(f"🏷️ {label} · 随机 {len(picked)} 条")
        blocks += [self.format_meme(m, tag_map) for m in picked]
        return _join_result(blocks)

    # ---------------------------------------------------------------- 参数解析

    def _extract_args(self, event: AstrMessageEvent) -> str:
        """取出指令后面的参数文本（不依赖框架的参数解析，兼容性更好）。"""
        try:
            raw = event.get_message_str() or ""
        except Exception:
            raw = getattr(event, "message_str", "") or ""
        text = _strip_wake_prefix(raw)
        for name in COMMAND_NAMES:
            if text.startswith(name):
                return text[len(name):].strip()
        for alias in COMMAND_ALIASES:
            if text.startswith(alias):
                return text[len(alias):].strip()
        return text.strip()

    @staticmethod
    def _looks_like_command(text: str, wake: bool = False) -> bool:
        """判断这条消息是不是在调用本插件的指令。

        裸喊「来条烂梗」这种词（没有唤醒前缀、也没 @ 机器人）不算指令，
        应该交给关键词自动触发处理，否则自动触发永远轮不到自己。
        """
        stripped = _strip_wake_prefix(text)
        has_prefix = wake or stripped != text
        if not has_prefix:
            return False
        return any(stripped.startswith(name) for name in COMMAND_NAMES) or any(
            stripped.startswith(alias) for alias in COMMAND_ALIASES
        )

    async def dispatch(self, args: str, dedupe_key: str) -> str:
        """根据参数文本决定走哪个分支。"""
        args = (args or "").strip()
        lowered = args.lower()

        if not args:
            return await self.reply_random(self._default_count(), dedupe_key)

        if lowered in {"帮助", "help", "?", "？", "菜单", "用法", "说明"}:
            return self._help_text()

        # 纯数字就是随机条数，必须在剥数量之前判断
        if re.fullmatch(r"\d{1,3}", args):
            return await self.reply_random(int(args), dedupe_key)

        # 「搜 niko 3」「搜 三条 niko」：先把数量剥出来，剩下的才是关键词
        matched = re.match(r"^搜[索]?\s*(.+)$", args)
        if matched:
            keyword, count = _split_count(matched.group(1))
            return await self.reply_search(keyword, count)

        # 其余分支都允许在参数里带数量：「热门 5」「#07 两条」「niko 的烂梗 3 条」
        plain, count = _split_count(args)
        plain = plain.strip() or args
        plain_lowered = plain.lower()

        if plain_lowered in {"热门", "热榜", "hot", "24h", "24小时", "24"}:
            return await self.reply_hot(1, count)

        if plain_lowered in {"7d", "7天", "七天", "7日", "周榜", "七日榜"}:
            return await self.reply_hot(7, count)

        if plain_lowered in {"标签", "tag", "tags", "分类"}:
            return await self.reply_tag_list()

        if plain.startswith("#"):
            return await self.reply_by_tag(plain[1:], count)

        tag_map = await self.get_tag_map()
        if plain in tag_map.values() or plain_lowered in {v.lower() for v in tag_map.values()}:
            return await self.reply_by_tag(plain, count)

        # 兜底当主题检索：兼容「niko」「niko 的烂梗」「点 niko」几种写法
        topic = _match_nl_topic(plain)
        if topic is None:
            topic = re.sub(r"(?:的)?烂梗(?:呢|啊|呀|吗|吧)?$", "", plain).strip(" 的") or plain
        text = await self.reply_topic(topic, plain, dedupe_key, count=count)
        return text or f"🔍 没搜到含「{topic}」的烂梗，换几个字试试？"

    # ---------------------------------------------------------------- 指令入口

    @filter.command("烂梗", alias=COMMAND_ALIASES)
    async def sb6657_cmd(self, event: AstrMessageEvent):
        """随机来一条 6657 玩机器烂梗，也支持搜索、热榜和标签筛选。"""
        args = self._extract_args(event)
        try:
            text = await self.dispatch(args, event.unified_msg_origin)
        except Sb6657Error as exc:
            logger.warning(f"sb6657 指令执行失败：{exc}")
            yield event.plain_result(f"😥 获取烂梗失败：{exc}")
            return
        except Exception as exc:  # 兜底，避免拖垮其他插件
            logger.error(f"sb6657 指令出现未预期错误：{exc}", exc_info=True)
            yield event.plain_result("😥 插件出错了，请查看 AstrBot 日志。")
            return
        if text:
            yield event.plain_result(text)

    # ---------------------------------------------------------------- LLM 工具

    @_llm_tool_decorator("sb6657_meme")
    async def llm_find_meme(self, event: AstrMessageEvent, keyword: str) -> str:
        """从 6657 玩机器烂梗库检索与关键词相关的烂梗原文。

        当用户提到玩机器、6657 相关的主播、选手、解说、事件或口头禅，
        并想听对应的烂梗、梗文、弹幕梗时，调用这个工具拿原文。

        Args:
            keyword(string): 检索关键词，例如 niko、device、詹姆斯
        """
        if not bool(self._cfg("llm_tool_enable", True)):
            return "管理员关闭了烂梗检索工具。"
        try:
            text = await self.reply_topic(
                keyword, keyword, event.unified_msg_origin, allow_llm=False
            )
        except Sb6657Error as exc:
            logger.warning(f"sb6657 LLM 工具检索失败：{exc}")
            return f"检索失败：{exc}"
        except Exception as exc:
            logger.warning(f"sb6657 LLM 工具执行出错：{exc}")
            return "检索失败，稍后再试。"
        return text or f"烂梗库里没有找到与「{keyword}」相关的内容。"

    # ---------------------------------------------------------------- 自然语言触发

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        """「xxx 的烂梗」自动检索；命中关键词时随机来一条。"""
        try:
            text = (event.message_str or "").strip()
        except Exception:
            return
        try:
            wake = bool(event.is_at_or_wake_command)
        except Exception:
            wake = False
        if not text or self._looks_like_command(text, wake):
            return

        key = event.unified_msg_origin

        # 1) 自然语言主题检索（默认开启）
        if bool(self._cfg("nl_search_enable", True)):
            plain, count = _split_count(text)
            topic = _match_nl_topic(plain)
            # 「来 3 条烂梗」只给了数量没给主题，直接按数量随机
            wants_random = not topic and bool(count) and bool(NL_SUFFIX_RE.search(plain))
            if topic or wants_random:
                if bool(self._cfg("nl_search_groups_only", False)) and not event.get_group_id():
                    return
                try:
                    cooldown = float(self._cfg("nl_search_cooldown", 20) or 0)
                except (TypeError, ValueError):
                    cooldown = 20.0
                now = time.time()
                if cooldown > 0 and (now - self._last_nl.get(key, 0.0)) < cooldown:
                    return
                self._last_nl[key] = now

                try:
                    if wants_random:
                        result = await self.reply_random(int(count), key)
                    else:
                        result = await self.reply_topic(topic, plain, key, count=count)
                except Sb6657Error as exc:
                    logger.warning(f"sb6657 主题检索失败（{topic or count}）：{exc}")
                    return
                if not result:
                    if bool(self._cfg("nl_search_reply_when_empty", False)):
                        yield event.plain_result(f"🔍 站里没翻到「{topic}」的烂梗，换个说法试试？")
                    return
                if bool(self._cfg("nl_search_stop_llm", True)):
                    self._stop_event(event)
                yield event.plain_result(result)
                return

        # 2) 老的关键词随机触发（默认关闭）
        if not bool(self._cfg("auto_trigger_enable", False)):
            return

        if bool(self._cfg("auto_trigger_groups_only", False)) and not event.get_group_id():
            return

        keywords = self._cfg("auto_trigger_keywords", []) or []
        if not any(str(k) and str(k) in text for k in keywords):
            return

        try:
            cooldown = float(self._cfg("auto_trigger_cooldown", 15) or 0)
        except (TypeError, ValueError):
            cooldown = 15.0
        now = time.time()
        if cooldown > 0 and (now - self._last_auto.get(key, 0.0)) < cooldown:
            return
        self._last_auto[key] = now

        try:
            result = await self.reply_random(1, key)
        except Sb6657Error as exc:
            logger.warning(f"sb6657 自动烂梗失败：{exc}")
            return

        if bool(self._cfg("auto_trigger_stop_llm", True)):
            self._stop_event(event)
        yield event.plain_result(result)

    @staticmethod
    def _stop_event(event: AstrMessageEvent) -> None:
        """阻止后续流程（例如 LLM）再回复一遍。"""
        try:
            event.should_call_llm(False)
        except Exception:
            pass
        try:
            event.stop_event()
        except Exception:
            pass

    # ---------------------------------------------------------------- 生命周期

    async def terminate(self):
        """插件被停用/卸载时释放连接。"""
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception as exc:
                logger.warning(f"关闭 sb6657 HTTP 会话失败：{exc}")
        self._session = None
