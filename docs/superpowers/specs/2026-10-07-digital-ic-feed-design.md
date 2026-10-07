# 数字 IC 设计与验证文献订阅

## 目标与范围

在 Llxyyds666/digital-ic-paper-feed 建立独立公开仓库和 GitHub Pages 订阅。覆盖数字 RTL/HDL、微架构、RISC-V/SoC/FPGA、逻辑与高层次综合、布局布线和 PPA、STA/CDC/RDC、UVM/受约束随机仿真、断言/形式验证、等价检查、覆盖率/验证自动化及数字 DFT。排除纯软件验证、数学定理、模拟/RF 器件和制造材料等没有数字设计/验证贡献的文献。

用户补充：只收顶刊顶会。最终白名单为期刊 TCAD、TVLSI、TODAES、IEEE TC、JSSC，以及主会 DAC、ICCAD、FMCAD、CAV、ISCA、MICRO、HPCA、ASPLOS、ISSCC、VLSI Symposium。2026-10-07 用户确认新增 JSSC、ISSCC、VLSI 的数字电路论文，其他来源暂不扩入。仅以出版来源元数据识别，不接受标题/摘要自称顶会，不接受未确认发表场所的 arXiv 预印本；排除 Workshop/Companion/Poster/Demo/Tutorial/Doctoral 辅助轨道。会议名称仅允许年份、届数、IEEE/ACM 等已知包装的完整名称，不误收 ASP-DAC 或 CAD/Graphics。DAC 区分 ACM/IEEE 与同名 ASME 机械会议，CAV 保留 Crossref 全部容器名，不将泛 LNCS 当作 CAV。

## 方案

采用现有文献项目的已验证抓取、状态、重试、摘要补全和事务发布模块，重建领域配置与 AI 提示词。相较从零重写，能够直接保留 DOI/arXiv 去重、失败队列恢复和密钥保护；相较单一 RSS 关键词方案，DeepSeek 能甄别泛软件验证和非数字器件噪声。

## 数据流程

官方期刊 RSS 与 Crossref 期刊/主会多查询抓取 → 来源白名单校验 → 规则预筛与仓储附件排除 → 持久队列 → 摘要补全 → 每天至多 100 篇 DeepSeek 筛选 → 中文摘要、设计/验证标签 → 当日推荐一篇 → 累计 RSS 与 HTML 原子发布 → GitHub 推送成功后可选 Bark 两条通知。

首次数据库回溯 30 天，后续独立查询水位/游标增量续抓。RSS 窗口由来源决定。每个外部逻辑请求最多重试三次，不设置每日 AI 请求总次数上限。历史已处理文献不重复请求 AI。各条查询单独保留进度，部分来源失败不能推进失败来源水位。

## 输出

- filtered_feed.xml：规则候选 RSS，最多 2000 条。
- ai_summary_feed.xml：全领域 AI 精选累计中文 RSS。
- design_verification_feed.xml：带数字设计/验证标签的累计 RSS。
- design_feed.xml、verification_feed.xml：独立专题订阅，可交叉收录。
- ai_summary.html：首页与精选摘要；缺密钥时明确标识尚未 AI 筛选。
- ai_usage.json：候选、请求、tokens 消耗记录；不估算未经证实的价格。

## 凭证与自动化

密钥只通过环境变量与 Actions Secrets 提供。DEEPSEEK_API_KEY 为 AI 必需；IEEE_API_KEY、SEMANTIC_SCHOLAR_API_KEY、BARK_TOKEN 为可选。不从旧仓库导出密钥，不将密钥写入代码、提交或日志。缺少 DeepSeek 密钥时采集继续，摘要工作流提示待配置并跳过 AI。IEEE 元数据按文章号或 DOI 精确匹配，只补缺失字段；补 DOI 后原始 RSS 与全部精选、状态同批刷新，失败筛选不丢已完成的补全数据。

采集每 6 小时；每日摘要北京时间 09:17（与金刚石任务错峰）；部署 GitHub Pages。发布前测试，生成后校验 RSS GUID 唯一性、专题子集、仓储排除以及状态一致性。

## 验证与完成条件

真实官方来源抓取结果；规则正/负样例；严格 AI 决策和推荐验证；重试/原子性/去重回归；本地测试通过；仓库成功创建与推送；远端 CI/采集和 Pages 成功。没有密钥时不得将未处理候选伪装成 AI 精选。
