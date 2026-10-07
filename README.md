# Digital IC Paper Feed

面向数字 IC 设计与验证的文献订阅：先核验顶刊顶会来源，再筛选实际数字硬件贡献，由 DeepSeek 提供证据支撑的中文摘要、设计/验证标签和每日阅读推荐。

入口：[数字 IC 文献订阅](https://llxyyds666.github.io/digital-ic-paper-feed/) · [中文精选](https://llxyyds666.github.io/digital-ic-paper-feed/ai_summary.html)

## 订阅

| 内容 | RSS |
| --- | --- |
| 综合精选，覆盖所有数字 IC 分类 | [ai_summary_feed.xml](https://llxyyds666.github.io/digital-ic-paper-feed/ai_summary_feed.xml) |
| 数字设计与验证合并专题 | [design_verification_feed.xml](https://llxyyds666.github.io/digital-ic-paper-feed/design_verification_feed.xml) |
| 数字设计：RTL、微架构、SoC、综合、PPA 等 | [design_feed.xml](https://llxyyds666.github.io/digital-ic-paper-feed/design_feed.xml) |
| 数字验证：仿真、UVM、形式、断言、DFT 等 | [verification_feed.xml](https://llxyyds666.github.io/digital-ic-paper-feed/verification_feed.xml) |
| 来源核验候选，含主会待补摘要记录，尚未经过 AI 精选 | [filtered_feed.xml](https://llxyyds666.github.io/digital-ic-paper-feed/filtered_feed.xml) |

在 Zotero 的“订阅”中新建订阅，粘贴所需 RSS 链接即可。设计和验证专题允许交叉收录；同时订阅合并专题及其子专题，也会在不同订阅中看到同一篇论文。每份 RSS 内使用稳定文献标识和累计去重，更新摘要不会生成新条目。

## 范围与质量标准

只接受以下 15 个出版来源中的相关研究。来源白名单以出版元数据核验，论文标题自称会议名称不构成来源证明。

- 期刊：IEEE TCAD、IEEE TVLSI、ACM TODAES、IEEE Transactions on Computers、IEEE JSSC。
- 会议：DAC、ICCAD、FMCAD、CAV、ISCA、MICRO、HPCA、ASPLOS、ISSCC、VLSI Symposium 主会论文。

JSSC、ISSCC、VLSI Symposium 也出版模拟及混合信号论文，其中只有具有明确数字设计或数字验证贡献的论文进入精选。会议 Workshop、Companion、Poster、Demo、Tutorial、Doctoral 等辅助轨道不在当前范围；普通期刊、DATE、ASPDAC、新闻和未确认发表来源的 arXiv 预印本不入选。

技术范围包括 RTL/Verilog/SystemVerilog、微架构、RISC-V/SoC/NoC/FPGA、逻辑综合/HLS、实现与 PPA、STA/CDC/RDC、仿真/UVM/受约束随机测试、功能覆盖、形式验证/SVA/等价检查、DFT/ATPG/BIST、数字硬件安全与相关 EDA 方法。纯软件程序证明、通用软件算法以及没有数字设计/验证贡献的模拟电路、器件和材料研究排除。

已核验当届主会身份、但缺少原始摘要的论文会先保留为未判断候选，避免只因标题没出现关键词而漏收；后续按每日队列补摘要并由 DeepSeek 保守判断。候选不等于入选，原始候选流可能包含最后被排除的纯软件研究，精选与设计/验证专题仍遵守上述范围。

顶级来源是准入条件。精选还考察实际研究对象、方法、验证证据和学习价值；启用 Bark 后，AI 每天从本次入选论文中挑选一篇适合阅读的论文。没有合适的新论文时不强行推荐。

## 更新节奏与成本

- 期刊数据库采集覆盖近 30 天，IEEE API 按入库窗口增量、Crossref 重查滚动出版窗口以补回延迟登记；未完成分页会保留游标继续抓取。期刊 RSS 的卷期窗口由出版方决定。
- 会议另行回补 **2026 届**主会论文，不限近 30 天。ASPLOS 2026 第一卷在 2025 年 12 月出版，也在范围内。论文集按当届 DOI/正式会名核验，FMCAD 从 DataCite 官方 DOI 元数据获取；CAV 和 ASPLOS 从核实的出版元数据枚举，排除邀请报告、教程、学生论坛等。
- 每 6 小时采集一次；每天北京时间 09:17 进行 AI 筛选。GitHub 定时任务可能有排队延迟。
- 每天最多处理 100 篇待筛候选，入选数量取决于论文质量和实际新增数量。100 是候选处理上限，积压留在队列中后续处理。
- 回补产生积压时仍按每天 100 篇候选分批处理，不会一次把全年会议全部交给 AI。旧摘要保留、不重复付费；期刊目录中早期在线日期较早的论文也不等于当日新增。
- 网络及不合格 AI 响应采用失败重试；每次逻辑请求的暂时性失败最多尝试 3 次，不设置每天 API 请求总次数硬上限。无效凭证等不可重试错误直接报告。
- 历史已处理文献及既有摘要不重复请求 AI。全部 RSS 累计保存入选历史，原始候选 RSS 最多保留 2,000 条。
- [ai_usage.json](https://llxyyds666.github.io/digital-ic-paper-feed/ai_usage.json) 记录候选数、已处理数、入选数、请求尝试数和 tokens。实际金额以服务商账单为准。

采集从出版源 RSS、IEEE API、Crossref 和 DataCite 元数据获取原始摘要。配置 IEEE 密钥后，官方 API 直接发现期刊及会议论文，受阻的 IEEE RSS 保留为无密钥时的备用来源；再按文章号或 DOI 精确补齐 IEEE 文献的 DOI、作者和缺失摘要，并用 Semantic Scholar 和 OpenAIRE 补全。期刊卷期页码和字面 `null` 不视为原始摘要。缺少原始摘要时会显示“仅据标题，缺少摘要”，采用保守判断，不编造方法、实验结果或性能提升。每日推荐请求携带候选的完整原始摘要；缺摘要时推荐理由同样明确标识。

## 配置与密钥

到 [Actions Secrets 设置](https://github.com/Llxyyds666/digital-ic-paper-feed/settings/secrets/actions) 添加环境凭证：

| Secret | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 启用 DeepSeek 筛选与中文摘要；每日推荐还需启用 Bark |
| `SEMANTIC_SCHOLAR_API_KEY` | 可选，补全文献原始摘要 |
| `IEEE_API_KEY` | IEEE 期刊/会议发现与官方元数据补全；缺少时只走不需密钥的备用源 |
| `BARK_TOKEN` | 可选，通过官方 Bark 服务接收通知 |

配置 `BARK_TOKEN` 后默认启用 Bark，可将仓库 Actions 变量 `BARK_ENABLED` 设为 `false` 暂停。完整筛选成功且发布校验通过后发送两条通知：第一条包含候选、已处理和入选数量；第二条包含当日推荐论文、设计/验证方向、简短原因和论文链接。无新推荐时发送“今日无数字 IC 设计与验证方向推荐”。图标使用本仓库公开的 IC 图标，设备 token 只从环境读取。部分批次失败时保留校验通过的已完成结果，任务仍报告失败，以便及时排查。

默认模型保留为 `deepseek-v4-flash-vision-exp`，可通过 `DEEPSEEK_MODEL` 环境变量覆盖。密钥不写入代码、配置、RSS、提交或日志。旧项目的 Secrets 不会自动继承到本仓库。未配置 DeepSeek 时可以采集候选，AI 精选需配置后生成。

领域查询见 [config/queries.json](config/queries.json)，来源白名单见 [config/venues.json](config/venues.json)，手动专题修正见 [config/focus_overrides.json](config/focus_overrides.json)。来源失败保留续抓进度；摘要、全部专题 RSS、消耗记录和状态共同完成事务发布。发布前执行测试，生成后校验来源白名单、稳定标识唯一性、专题子集及仓储附件排除。

2026 届会源目录见 [config/conference_editions.json](config/conference_editions.json)。ASPLOS 两卷与 CAV 正式章节数分别核验为 152、81，不代表全部属于数字 IC；DAC 当届元数据尚未能完整枚举，MICRO/ICCAD 尚未到会期，公开库与 IEEE 会继续检查，并在采集日志分别报告失败、续抓和查询完成。源查询返回空不证明该会议全年没有论文。

## 本地运行

Python 3.11+：

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ic_feed.collect
```

AI 凭证通过当前进程环境或安全凭证管理工具提供，再执行：

```powershell
python -m ic_feed.summarize
```

无摘要、来源元数据延迟和出版源暂时故障可能导致漏收或晚收。入口展示的是已发布内容，来源白名单与 AI 筛选共同决定是否入选。
