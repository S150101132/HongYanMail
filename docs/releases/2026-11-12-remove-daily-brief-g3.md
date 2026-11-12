# 2026-11-12 · G3 去掉每日速览

用户用 `/grill-with-docs` 确认：36氪「9点1氪」和 GitHub Trending 都不太需要，这类信息习惯去 GitHub 和微信公众号看；这个位置还没想好怎么改造，先只撤不加。G1、G2 被本项取代。

## 改了什么

- 左栏「每日速览」去掉，位置空着，不放替代内容；设置 → 左侧栏显示里也少了这一项。
- 36氪简报和 GitHub Trending 前后端一起删：`app/news.py`、`ai/trending.py`、后端 `daily_news` / `trending`、桥接的 `dailyNews` / `trending`、错误代码 `NEWS_UNAVAILABLE` / `TRENDING_UNAVAILABLE`、页面和样式、`brief` 图标、演示与截图脚本里的替身；测试 `test_news.py`、`test_trending.py` 删掉。本机数据目录里旧的 `news/`、`trending/` 缓存不再读取，留着无害，可以手动删。
- 设置「启动时打开」整行去掉，启动总是打开收件箱；以前存的 `startPage` 读取时自动丢掉。
- 在等别人、待我确认、本周概括照旧从命令框（Ctrl+K）进入，仍是三栏；顶部返回按钮从「← 每日速览」改为「← 收件箱」。内部名字（`today.js`、`S.local 'today'`）不改。

## 验证

- 全量测试 913 个通过（跳过 2 个）。`tests/test_today.py` 改为检查页面、入口、启动设置都已去掉，子页返回收件箱。
- 演示截图走查（`HONGYAN_WALK_R2=1`）：在等别人、问鸿雁、本周概括、待我确认各子页正常。

## 真机待验收

- 左栏不再有每日速览；Ctrl+K 进「在等别人」，点「← 收件箱」回到收件箱。
