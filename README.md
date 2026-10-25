# astrbot_plugin_sb6657

![version](https://img.shields.io/badge/version-v1.2.2-blue)
![license](https://img.shields.io/badge/license-MIT-green)

把 [sb6657.cn](https://sb6657.cn)（斗鱼主播 **玩机器** 的烂梗收集站）接进 AstrBot 的插件。

## 指令

| 指令 | 说明 |
| --- | --- |
| `/烂梗` | 随机来一条 |
| `/烂梗 3` | 随机来 3 条（不超过 `max_count`） |
| `/烂梗 搜 niko` | 关键词搜索（默认 `search_count` 条） |
| `/烂梗 搜 niko 5` | 关键词搜索，只要 5 条 |
| `/烂梗 热门 3` | 24 小时热榜，只要 3 条 |
| `/烂梗 7天` | 七天热榜 |
| `/烂梗 标签` | 查看全部标签及编号 |
| `/烂梗 #07 两条` | 按标签随机（`07` = NiKo），要 2 条 |
| `/烂梗 帮助` | 帮助 |

别名：`sb6657`、`来条烂梗`、`6657烂梗`。

> 注意：本插件使用 `command` 过滤器 + 自行解析参数，所以除空格外的写法（如 `/烂梗#07`）也能识别。
> 指令前的唤醒前缀需要由 AstrBot 的 `wake_prefix` 配置决定（默认 `/`）。

## 关键词自动触发

在插件配置里打开 `auto_trigger_enable`，群友说「来条烂梗」之类的词就会自动回一条，不需要指令。
关键词、冷却时间、是否只在群聊生效都可以在配置面板里改。默认会自动阻止 AI 再回复一遍（`auto_trigger_stop_llm`）。

## 自然语言检索（LLM 搜索）

不用打指令，直接说「**niko 的烂梗**」「**来点詹姆斯的烂梗**」「**有没有 device 烂梗**」，
机器人就会去站里找出对应的梗发出来。

处理分三级，能不花 token 就不花：

1. **规则抽取 + 直搜**：从句子里剥掉「来点 / 有没有 / 给我」这类口水词，拿到主题词直接调站内搜索，秒回；
2. **外号直连**：主题词命中外号表时，先拿对应的正式名去搜（「设备」→ `device`、「大表哥」→ `karrigan`），搜到的结果并进候选里，仍然不花 token；
3. **LLM 扩词**：上面都没结果时，才让当前对话模型补别名、英文名、常见错写，再逐个搜一遍；
4. **标签兜底**：主题词正好是标签名（NiKo、device、京介…）时，按标签随机取。

外号由谁指谁容易搞错，所以留了配置项 **`llm_alias_hints`**（每行一条 `外号=正式名`），默认值：

```
大表哥=karrigan
设备=device
阿汤哥=device
汤黛玉=device
阿杜=dupreeh
森爹=s1mple
九爷=Xyp9x
魔男=Magisk
载物=ZywOo
```

> 注意 device 的外号是「设备 / 阿汤哥」，**「阿杜」是 dupreeh**，别写成 device。
> 这张表两处都用：既写进 LLM 提示词，也被规则层直接拿来搜。发现哪条对不上，
> 在 WebUI 里改一行就行，不用动代码。提示词里还特意要求：拿不准的外号只给原词，不许硬凑。

可选 `llm_rerank` 打开后会再让模型对候选做一次相关性重排（多花一次调用，默认关闭）。

同时注册了一个 LLM 工具 `sb6657_meme`，主对话模型在聊天里也能自己决定来查梗。

> 触发只认「…烂梗」结尾的句式，抽不出干净主题词（「这什么烂梗」「来条烂梗」）就不会乱发，
> 那条会交给下面的关键词自动触发。第一次用前请在 AstrBot 里配好对话模型，
> 没配也能用——只是少了 LLM 扩词那一步。

## 指定条数

句子里想好了要几条就直接写，阿拉伯数字和中文数字都认：

```
niko 的烂梗 3 条
来点詹姆斯烂梗 三个
有没有 device 烂梗，来 5 条
来 3 条烂梗          ← 只给数量不给主题，那就是随机 3 条
```

数量会从句子里剥掉再拿去检索，不会把「3 条」当成关键词；上限由 `max_count` 决定（默认 10）。
不写数量时：搜索用 `search_count`（默认 3），主题检索（`xxx 的烂梗`）和标签随机用 `default_count`（默认 1）。

顺手做了防误判：数量必须跟在「烂梗」后面、或者跟在「来 / 发 / 整 / 给我」这类动词后面才算数，
所以「一个人吃饭的烂梗」不会被当成要 1 条。

## 输出格式

默认**只发梗本身**，不带编号、标签、热度、时间和链接：

```
詹姆斯来咯🏀🦍🏀🦍🏀🦍
```

需要的话可以在配置里逐项打开 `show_title`（标题行）、`show_count_header`（条数/榜单标题行）、
`show_tags`（标签）、`show_meta`（热度与时间）、`append_source`（站点链接），全部默认关闭。

## 安装

1. 把整个 `astrbot_plugin_sb6657` 目录放进 `AstrBot/data/plugins/`；
2. 在 WebUI 的「插件管理」里重载插件，或重启 AstrBot；
3. 依赖只有 `aiohttp`，AstrBot 本体一般已自带；如缺失执行 `pip install -r requirements.txt`。

可以在 WebUI 插件页通过仓库地址安装：`https://github.com/weadee/astrbot_plugin_sb6657`。

不想用 git 的，直接下 [Release](https://github.com/weadee/astrbot_plugin_sb6657/releases/latest) 里的 zip，
在 WebUI「插件管理 → 从文件安装」上传即可。

## 配置项

见插件目录下的 `_conf_schema.json`，在 WebUI 里可视化修改。常用几项：

- `default_count` / `search_count` / `max_count` / `hot_count`：返回条数控制（`search_count` 是搜索的默认条数，用户在句子里写了数量就以用户为准）；
- `max_length`：单条烂梗截断长度，填 `0` 不限制；
- `show_title` / `show_count_header` / `show_tags` / `show_meta` / `append_source`：附加信息开关，默认全关，只发梗本身；
- `dedupe_size`：每个会话最近发过的烂梗记忆条数，用来避免重复刷同一条，填 `0` 关闭；
- `nl_search_enable` / `nl_search_cooldown` / `nl_search_groups_only` / `nl_search_stop_llm` /
  `nl_search_reply_when_empty`：自然语言「xxx 的烂梗」的开关、冷却、仅群聊、是否压掉 AI 续写、没搜到时是否吭声；
- `llm_search_enable` / `llm_search_max_terms` / `llm_rerank` / `llm_timeout` /
  `llm_tool_enable`：LLM 扩词、扩几个词、是否重排、超时秒数、是否允许 AI 主动调工具；
- `base_url`：后端地址，默认走 sb6657.cn 站点前端使用的公开后端。

## 数据来源与声明

- 数据来自 sb6657.cn 站点前端调用的**公开后端接口**（`/machine/getRandOne`、`/machine/Page`、
  `/machine/pageSearch`、`/machine/hotBarrageOf24H`、`/machine/hotBarrageOf7Day`、`/machine/dictList`）。
- 站点 README 明确要求：**第三方工具调用后端接口时不要携带 `dpahjdoiaw` 请求头**，否则会污染站点的来源统计。
  本插件只发送 `User-Agent` 与 `Accept`，不发送该请求头，也不发送 `siteToken`。
- 接口为站点自有服务，可能随时变动或限流。搜索接口不可用时插件会自动退化为「翻页 + 本地关键词过滤」。
- 本插件只读取公开数据，不做投稿、点赞、复制等写操作。梗的内容与版权归 sb6657.cn 及原投稿水友所有。

## 已知限制

- 站点接口为 HTTP 轮询，单次随机多条时会发起多次请求，`max_count` 不宜设得过大。
- 部分烂梗包含直播间特有黑话或攻击性内容，是否适合群内展示请自行判断。
