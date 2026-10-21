# 本轮与 DeepSeek 的公开往返正文

日期：2026-10-21；会话：`20261021T125503Z-b1c7e9a00e3f`。仅摘录公开问题和公开答复，不含私有思考、凭据或源文件快照。采纳情况及验证见 [实现记录](DEEPSEEK-IMPLEMENTATION-2026-10-21.md)。

## 请求 1：Codex

请独立审查这份预约代码摘录，给2个最有价值、可落地的新思路或精确diff，回答约800中文字。项目只约四楼，当前分级随机、5轮有限重试、已有订单查重、正常新单详情校验已实现。问题是写请求超时后，服务器可能已成交；当前book_slot只查一次列表，无单后下一轮还能写，verify恢复分支又缺允许池和时段核验。日状态/锁只作用本机，不声称跨仓库全局互斥。
我的初步方案：统一订单详情验证（新单exact；已有单按covers，核uid/status/允许池），以及为每个未确定的POST持久化pending intent，重新启动/后续轮次必须先恢复；查不到不清除pending，不重发，直到人工处理或确认已有目标单。只把明确成功/明确业务拒绝当终态。写前保存会牺牲极小崩溃窗口的自动可用性，但优先避免重复提交。
请质疑：哪些异常必须视为不确定（超时、非JSON、HTTP500、verify失败）？该把pending文件放在哪一层、如何避免已有订单误当本次新建？建议最小状态机与关键回归。若有更好的思路请提出；不建议无证据无限重试或云端准点保证。测试只用离线模拟，不能登录学校或创建订单。

## DeepSeek 回复／请求状态

**结论**：方向正确，但“持久化 pending + 只读恢复”必须落到“提交边界”和“报告语义”两层；否则仍会把旧覆盖单误当成新单，或在 `success+bookingId` 后的 `verify` 失败时丢失已成交证据。以下为静态审查，我未运行测试、未修改文件。

**1. 异常分类：只有可解析的明确业务拒绝才是终态**
必须记 `UNKNOWN`：连接/读超时、连接重置、HTTP 5xx、POST 后跳登录、非 JSON、顶层结构异常、返回 `success` 却无 `bookingId`、`verify` 网络失败或校验失败。HTTP 400/401/403 若不能解析稳定业务码，也保守记 `UNKNOWN`。可记业务拒绝的仅限：座位被占、未开放、参数错误等“未成交”语义明确且可解析；“已有预约/重复预约”不是成功，应触发查单，查不到则 `MANUAL`，不能当作可重发。

**2. pending 层与恢复入口**
不要把 pending 塞进 `daily-state-*.json`：当前 `run_daily` 先检查 `attempted` 并 `continue`，崩溃后恢复会被跳过。建议 `LOCAL/pending/<uid-hash>/<begin>-<duration>.json`，原子写（tmp+`os.replace`，必要时 fsync），保存 `intent_id`、`begin`、`duration`、`seat_id`、`payload_hash`、`baseline_ids`（提交前订单 ID 快照）、`created_at`、`state`、`bookingId`、`last_probe`；不保存 token、密码。登录后、`attempted` 判断前执行 `recover_all_pending()`；存在未决 intent 时，同账号任何新 POST 都应阻断。

**3. 最小状态机**
`UNKNOWN(已持久化) -> SERVER_ACCEPTED(id) -> CONFIRMED(id)`；`UNKNOWN -> REJECTED_TERMINAL` 仅限明确业务拒绝；`UNKNOWN -> RECOVERED_EXACT` 或 `MANUAL`。禁止 `UNKNOWN/RECOVERED_EXACT -> SUBMITTING`。`submission_confirmed=True` 只应由本次收到 `bookingId` 置位；恢复找到 exact 单最多记 `recovered_exact`，不冒充本次新单。若已有单 ID 在 `baseline_ids` 中，标 `already_existed=True`，当前代码恢复分支未设置该标记，需修正。

**精确修改建议（伪 diff）**
```diff
+ intent = self.pending.save_atomic(begin, duration, seat['id'],
+                                   baseline_ids=self.existing_ids, data_hash=...)
  try:
      payload = self.request('POST', BOOK_PATH, ...)
- except requests.RequestException:
-     existing = self.covered_booking(...)
-     if existing: return self.verify(existing['id'])
-     raise BookingError(...)
+ except (requests.RequestException, BookingError) as e:
+     raise SubmissionUncertain(intent) from e
  if success:
+     self.pending.mark_accepted(intent, result['bookingId'])
-     verified = self.verify(result['bookingId'], begin, duration, seat['id'])
+     verified = self.verify(result['bookingId'], begin, duration, seat['id'],
+                            allowed_seat_ids=allowed_ids)
+     self.pending.mark_confirmed(...)
```
`verify` 需区分 `exact` 与 `covers`：新单要求 uid/status/begin/duration/seat_id 且座位在允许池；已有单按覆盖时段核验，但不要求 seat_id 相同，且只标 `already_existed`。恢复时 `baseline_ids` 仅作强证据；列表可能延迟，不能仅凭“不在基线”断言新单。

**关键回归**：①POST 超时但服务端成交、首查无单，第二轮仍不得 POST；后续只读查到 exact 只记 `recovered_exact`。②`success+bookingId` 后 verify 超时，重启只核验该 ID。③提交前已有覆盖单，报告不得标新单。④pending 写失败则不 POST，损坏不自动删除。⑤`attempted` 已记录且存在 pending 时，恢复先于跳过。⑥恢复座位在允许池外则 `MANUAL`。

**不确定性**：若运行在 GitHub Actions 新 job，本地 pending 无法跨 job 持久化，只能防同进程/同工作区重启，不能承诺跨仓库全局去重；且无服务端幂等键时无法根除“响应丢失但已成交”。另需把 `book_slot` 的本地 deadline 检查放在 `save_atomic` 之前，避免“尚未发送却留下假 UNKNOWN”。

- 模型：`deepseek-flash`；effort：`low`；finish_reason：`stop`。

## 请求 2：Codex

已采纳你的pending独立于daily-state、保留server accepted ID、只读恢复不归因为新单的建议。现在做第二轮静态复审，请只列真正阻塞发布的最多3项漏洞（约800字），给最小修改，不重复一般建议。
当前实现：submission_state.SubmissionJournal账号隔离，写前原子写+fsync；unknown/server_accepted状态。任何不确定响应保留pending；识别的明确拒绝才clear；POST超时先查单，暂时查不到则抛SubmissionUncertain，后续轮/重启被阻止。success+id先持久化ID再verify，失败不丢ID。recover_pending只读验证，报告submission_confirmed=false，只有baseline已知单才already_existed=true；标outcome=recovered。恢复成功当前同slot立即返回，不再POST；reserve_day把恢复单加入列表快照防列表滞后。run_daily登录恢复先于finished/attempted跳过；未决异常立即退出。verify新单exact/已有covers并检查本次配置允许池。
Codex取舍：为保持改动可审查，只允许一个本地账号级pending，而非每slot目录；存在一个未知提交就阻止同账号新写入。main.single_instance覆盖全流程，pending非跨runner租约。新job、不同仓库没有共享pending；本轮不改工作流/开关，文档将明确这个限制，不承诺全局exactly-once。
已执行37项原有测试+10项新增故障注入=47项通过；协作helper的12项测试通过，流式/诊断已真实成功。新增测试覆盖POST超时首查无单和重启不重发、accepted后verify超时恢复只读、错楼层/用户/状态/时段、合法更长covers、写ahead失败无POST、损坏文件保留、success无id/异常data保持pending、finished日也优先恢复。
请检查还有哪些路径会误删pending、重复写入或把恢复单误当新单；尤其反馈状态分类、deadline与日状态的交互。不要声称实际运行或修改了源码。

## DeepSeek 回复／请求状态

DeepSeek returned no public answer (finish_reason=length).

- 模型：`deepseek-flash`；effort：`low`；finish_reason：`length`。

## 请求 3：Codex

第二轮由于finish_reason=length未得到正文，诊断已保留。本轮关闭思考，仅需公开回答500中文字：检查最新版源码是否有发布阻塞点，最多2项具体路径，不要重述通用建议。
已按第一次实际回复实现：账号级pending写前落盘、保留已接受ID、所有恢复路径校验uid/status/精确或覆盖时间/允许池、未知结果不再写、恢复不冒充新单。补充Codex自查发现：run_daily恢复成功若清pending但列表滞后，稍后reserve_day可能再写。因此现在完成核验后先持久化verified订单提示再清pending，reserve_day合并该账号提示（以服务端当前列表的同ID状态优先），随后仍GET详情验证；进程重启也不会忘掉已经核验但列表还没出现的订单。归档写失败保留pending。提示不是服务端真相，需再验证。
已49项预约离线测试通过（37旧+12新增）和12项helper测试通过。本轮不改工作流、五轮时间、任务开关或另一个仓库，不声称跨runner/跨仓库互斥；新runner没有本地journal，仍需后续共享租约/主端方案。仅识别到明确拒绝响应才清pending；unknown与verify失败立即退出。deadline写前检查与落盘后再检查均有，未发出POST时可以安全撤销。请指出尚存真实漏洞，若无阻塞则说明静态审查的局限，不要虚构已执行测试。

## DeepSeek 回复／请求状态

**结论**：未见“必然重复下单/静默丢单”的一级发布阻塞点，上述补丁方向成立；但静态审查只能发现可推演缺陷，无法验证真实服务端时序，我本轮未运行任何测试或修改文件。下面只列两处真实可触发的路径。

**问题1（中高）：恢复分支的 `intent.get('booking_id')` 判断与 journal schema 不一致。**
`submission_state.py` 只在 `state=='server_accepted'` 时持久化 `booking_id`，但摘录的 `recover_pending` 用键名 `booking_id` 分派——若 journal 实际字段确为该名则 OK；否则 `server_accepted` 记录会落入 `else` 覆盖单路径，在列表滞后时先抛 `SubmissionUncertain`，虽不重发但不必要地卡住。请核对 `accepted()` 写入的键名与 `recover_pending` 读取的键名是否逐字一致；不一致则改 `recover_pending` 读同一键或读 `state`。

**问题2（中）：`reserve_day` 的 `cached_bookings` 合并会把提示当“已有覆盖”跳过核验细节。**
合并后仅填 `id/status/time/duration`，进入 `covered_booking` 命中后走 `verify(duplicate, covers=True)`，这步没问题；但后续 `custom_candidates` 只对 `verified.seat_id` 做池校验，而 `verify` 内 `allowed_seat_ids` 对覆盖单也会校验——两者重复不算漏洞。真正风险：如果提示里的订单在 `client.bookings()` 中被同 ID 覆盖成 `status` 为已取消，合并逻辑保留的是列表版（正确）；但若列表尚未返回该 ID，则用提示链 `covers` 核验时 `status` 在 `ACTIVE_STATUSES` 内就通过，若服务端最终把它取消，下一轮 `run_daily` 因 `finished` 已置位不会重试。这是设计取舍（优先不重复提交），不算阻塞，但请确认 `covered_booking` 使用的是 `verify` 并会在后续轮次重新拉列表——目前 `finished` 置位后不再轮询。

**修改建议（问题1）**：确认 `accepted()` 键名；若要与摘录一致，把 `submission_state.py` 中 `current.update(..., booking_id=int(booking_id))` 改为 `accepted_id` 并同步 `recover_pending` 读取，或反之，确保唯一键名。

**问题2 修改（可选）**：`finished` 置位前若来自 `cached_bookings`（非本次提交），可改为“仅在后续轮次再次 GET 确认后 finished”，但会拖慢 open 时机，属产品权衡，不阻塞发布。

**不确定性**：无 `submission_state.py` 完整调用方与 `verify` 的 `covers` 参数在 `book_fourth_floor` 中是否同样传入 `allowed_seat_ids`，我未见到该路径，建议在同一次静态通读时确认。静态审查无法证明 POST 后崩溃与订单落库的相对顺序，需真实回放或 mock 服务端时序测试补充。

- 模型：`deepseek-flash`；effort：`none`；finish_reason：`stop`。
