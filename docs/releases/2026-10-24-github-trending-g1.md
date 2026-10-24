# 2026-10-24 · G1「今天」页换成 GitHub Trending

用户反馈今天页对自己作用不大，想每天看 GitHub Trending 和每个项目的中文简介。按「加一个撤一个」整页替换，不另加入口。

## 改了什么

- 左栏「今天」改为「Trending」（设置 → 启动时打开 里同名）。列表是 github.com/trending 日榜：排名、项目名、今日新增星标、中文简介（没有时为英文原描述）、语言和总星标。
- 点一项在阅读栏看卡片：中文简介、星标 / 今日新增 / Fork、英文原描述，「在浏览器打开」。
- 中文简介：抓到榜单后取每个项目 README 开头（≤1500 字），连同描述一次交给已配置的 AI，每个项目一两句（≤80 字）。资料放在随机分隔符之间，提示词说明其中的话不执行。
- 按天缓存在 `HongyanData/trending/<日期>.json`（只有公开数据，保留 7 天）；当天再打开不再联网、不再调 AI；列表头「刷新」重新抓取并重写简介。
- 没配 AI：显示英文原描述和「去设置」提示，不取 README；配好后下次打开补上简介。AI 失败：显示原因和「重试」，列表照常。打不开 GitHub：`TRENDING_UNAVAILABLE`。
- 原首页的要我做 / 快到期任务清单去掉（`backend.today_overview` 删除）；在等别人、待我确认、本周概括仍从命令框（Ctrl+K）进入，子页顶部「← Trending」。

代码：`coremail_desktop/ai/trending.py`、`backend.trending`、`web/today.js`；演示模式和截图走 `trending.demo_get`，不联网。

## 验证

- `tests/test_trending.py`（按 2026-10-24 真实页面裁剪的一行 HTML 解析、提示词分隔、AI 结果校验）、`tests/test_today.py`（无 AI → 有 AI 补简介 → 当天缓存不再调用 → 刷新与坏结果 → 网络失败）。全量 753 个测试通过。
- 本机真实网络：`backend.trending()` 取到当天日榜 10 个项目（GitHub 当天只给了 10 个），10 个 README 都取到，提示约 1.6 万字。
- 截图走查（`HONGYAN_WALK_R2=1 tools/screenshot_app.py`）：`g1-trending`、`g1-trending-card`、`r2-wait`。
- 没有验证：用真实 AI Key 生成中文简介（需要用户在本机打开 Trending 页看一次，约半分钟）。
