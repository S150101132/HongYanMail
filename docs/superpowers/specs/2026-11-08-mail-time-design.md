# Z1 邮件时间统一 — 设计

2026-11-08，由 `/improve-codebase-architecture`（候选 1「把服务器时间收进一个深模块」，并入候选 2 的大部分）拷问定下。起因是同日修的 bug：搜索结果按北京时间写进缓存，打开邮件时右上角晚 5 小时（[记录](../../releases/2026-11-08-search-date-zone.md)）。

## 要解决的问题

邮件时间在程序里以不带时区的文字 `2026-11-08 11:50:12` 流动，每个读它的地方自己猜时区，有三种猜法：

- 利雅得：JSON-RPC 请求带 `tz="Asia/Riyadh"`，`_schedule_at`、`_sent_date`，`tasks/rules.py` 的第二份 `RIYADH`；
- 账号时区（Asia/Shanghai）：表单搜索、定时发送、自动回复；
- 电脑本地时区：`app/cache.py` 的 `date_ts`（排序、筛选、到期判断）、`archive/catalog.py`、`archive/pst.py`、界面 `new Date(文字)`。

后两种今天没出错，只因为电脑设在利雅得；换到别的时区就会整体偏移。

## 定下来的规则

1. **范围**：只管邮件时间（服务器返回的时间、邮件头 `Date`、写给服务器的时间、本地存档里邮件的时间）。任务中心、AI 结果、导出文件名等本机记录用的「现在」不管，照旧按电脑时区。
2. **显示**：邮件时间按电脑所在时区显示，随所在地变化（见 `GLOSSARY.md`「邮件时间」）。任务的「今天」、截止日期、「3 天内到期」也按电脑时区算。
3. **存储约定**：服务器、缓存、本地存档、adapter 之间一律用**利雅得时间文字**（`YYYY-MM-DD HH:MM:SS`），与所在地无关；只在显示和按「今天」判断时换成电脑时区。缓存不迁移。
4. **adapter interface 不变**：仍交出利雅得文字；各来源的换算在 adapter 里做。改成带时区的值（报告候选 3）这次不做。

> 以后读代码的人可能困惑「为什么存利雅得、显示本机」：利雅得是向服务器请求时已经验证过的约定（`tz` 标记），存它就不用迁移缓存；显示跟着人走。

## 接口

**Python：`coremail_desktop/protocol/mailtime.py`**（Qt 无关）

| 名称 | 作用 |
| --- | --- |
| `STORE_ZONE` | 存储约定的时区（利雅得），全程序唯一一份；请求的 `tz="Asia/Riyadh"` 从它来 |
| `stored(text, zone)` | 某时区写的服务器时间文字 → 存储用的利雅得文字；读不懂的原样返回 |
| `moment(text)` | 存储文字 → 带时区的时刻；读不懂返回 None |
| `account_zone(name)` | 账号 `time_zone` → 固定偏移时区（从 `coremail.py` 搬来，行为不变） |

调用方改动：

- `protocol/coremail.py`：`RESPONSE_ZONE`、`_in_response_zone`、`account_zone`、`FIXED_ZONES` 换成 `mailtime`；`_schedule_at` 用 `moment`。
- `app/cache.py`：`date_ts` 用 `moment`（按利雅得解析，与电脑时区无关）。
- `app/backend.py`：`_sent_date` 和 3779 行一带改用 `moment` / `stored`，不再从 `tasks.rules` 借 `RIYADH`。
- `archive/catalog.py`、`archive/pst.py`：邮件时间写成存储文字，不再用 `astimezone()`。
- `tasks/rules.py`：删掉 `RIYADH`；「今天」按电脑时区。

**界面：`web/app.js` 的 `mailTime(text)`**：把存储文字按 `+03:00` 读成 `Date`；阅读栏右上角（928、931 行）等直接解析邮件时间文字的地方都改用它。列表已用 `ts`，不用改。

## 测试

- 打在 `mailtime` 的接口上：给定文字和时区，断言输出文字 / 时刻；不依赖电脑当前时区（Windows 下单个测试里不能安全切换系统时区）。
- `tests/test_desktop_search_move.py` 的搜索时区回归测试留在 adapter 那层不动。
- 「今天」按电脑时区：用注入的 `now` 测 `tasks/rules.today`。

## 不做

- 显示时区做成设置项（跟电脑走即可）。
- 缓存改存 UTC、adapter 交出带时区的值（候选 3）。
- 验证表单搜索接口能否直接带 `tz`（服务器要求人工验证登录，未测；换算不依赖它）。
