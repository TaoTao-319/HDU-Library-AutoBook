# DeepSeek 交流后落实的预约代码改进

日期：2026-10-21。用户在首轮 API 失败之后明确授权改进调用方式、继续交流、实际修改旧版脚本并推送。修改基于 `ac2cc16`；没有改动 DeepSeek 桌面端维护的另一个仓库。

## 真实交流与诊断结果

使用改进后的 `deepseek-collab` helper，模型为 `deepseek-flash`，新会话 `20261021T125503Z-b1c7e9a00e3f`。

| 请求 | 参数 | 结果 |
|---|---|---|
| 1 | 聚焦摘录、stream、low、8,192 token | HTTP 200，`finish_reason=stop`，得到完整设计建议；约 42 秒 |
| 2 | 实现源码回传、stream、low、8,192 token | HTTP 200，但 `finish_reason=length`，8,192 completion token 全为 reasoning token，没有最终正文 |
| 3 | 聚焦最终摘录、stream、none、8,192 token | HTTP 200，`finish_reason=stop`，得到复审回复；约 5 秒 |

第二次失败现在有明确的诊断依据；这不能反推首次旧会话两次失败也必然是同一原因。没有读取、记录或公开模型的私有思考文本。

## 从 DeepSeek 学到并采纳的思路

1. **把未知提交放在独立于每日轮次的记录中。** 如果只存在 `daily-state`，重启可能先跳过已记轮次，漏掉恢复。现在 pending 在账号级独立保存，恢复先于 `finished` / `attempted` 的跳过判断。
2. **把“收到订单号”和“核验通过”分开。** 收到 `success + bookingId` 后立即持久化编号，后续核验超时仍保留它；下次直接只读验证这个编号，不用依赖可能滞后的列表。
3. **恢复成功不等于证明本次新建成功。** 恢复结果标记 `outcome=recovered`、`submission_confirmed=false`；提交前订单基线仅用于判断已知旧订单，不因“不在基线”就断言本次创建。
4. **无法证明未成交时，不把异常当作可重试失败。** HTTP/网络异常、非 JSON、无订单号的成功响应、未知结构、验证失败都保持 pending；明确拒绝的响应才允许原来的有限换座或退避。

DeepSeek 的第一条公开回复还明确指出：“若运行在 GitHub Actions 新 job，本地 pending 无法跨 job 持久化”。本次保留了这个限制，没有宣称实现跨 runner 或跨仓库的严格一次提交。

## Codex 的实现与补充判断

| 文件 | 实际改动 |
|---|---|
| `submission_state.py` | 账号隔离的 `SubmissionJournal`；提交前原子写入并 fsync；`unknown` / `server_accepted`；完成后先保存 verified 提示，再移除 pending；损坏记录不自动清除 |
| `main.py` | 新增 `SubmissionUncertain` 和只读恢复；统一精确/覆盖时段与允许座位池校验；在每日跳过判断之前恢复；不确定结果立即停止新增提交 |
| `main.py` | 使用已核验订单提示弥补列表滞后，但仍 GET 详情；服务端列表同 ID 的当前状态优先；恢复单不计本轮新单 |
| `test_submission_safety.py` | 12 项新增故障注入；原有测试调整为隔离的临时状态目录 |
| `scripts/deepseek_collab.py` 与对应测试 | 空回复也保存结束原因/用量；网络错误记录异常类别与耗时；支持 `--stream`；修复 Windows 应用目录重定向的会话校验 |

补充修复来自 Codex 自查：若每日入口先恢复并清除 pending，而列表仍然滞后，后续预约准备可能忘记该订单。因此在清除 pending 前先持久化 verified 提示，重启也能再次 GET 详情确认。归档失败则保留 pending、停止提交。

本次保持一个账号一个未决提交，避免多 slot pending 增加复杂度。单独出现“稍后”“服务异常”等模糊消息不会被当作拒绝成交的证明。允许池来自本次有效配置，四楼配置下恢复也不能接受其他楼层订单。

## 复审意见的取舍

DeepSeek 的最终静态复审认可补丁方向，同时提出两点需要核对：

- 订单号字段是否一致：已核对 `accepted()` 写入、schema 验证及恢复读取，三处都是 `booking_id`；重启后直接按该 ID GET 的测试通过。该疑问不构成已证实漏洞，没有照搬其条件性的改名建议。
- 订单未来可能被取消：恢复会重新 GET 当前详情；正常业务在确认后仍可能变化，无法通过本轮客户端代码保证未来永不取消。本轮没有额外加入确认后的复查轮次，保留正常业务查单与状态变化的边界。

DeepSeek 仅做静态审查，未执行本机测试、未修改文件。Codex 执行检查并顺序落盘，未声称与 DeepSeek 桌面维护进程达成共识。

## 验证与实际边界

- 预约测试：`python -m unittest test_main test_seat_plan test_submission_safety`，**49 项通过**。
- 工具测试：`python -m unittest discover -s scripts -p test_deepseek_collab.py`，**12 项通过**。
- 覆盖：超时首查无单、进程重启不重发、收到编号后核验失败、错误楼层/用户/状态/时段、合法更长覆盖单、写前记录失败、损坏文件、异常响应、finished 日优先恢复、归档失败及列表滞后。
- 全部预约测试使用临时目录和模拟响应，没有真实登录学校或创建订单。
- 原有定时点、五轮上限、任务开关和工作流文件保持原配置。
- pending 与 verified 都是当前文件系统的本地证据。全新 GitHub runner 或其他仓库仍无法读取，跨端互斥、云端持久化和真实高峰时序留待独立实现与验收。
- 持续未知时保守停写，可能减少自动补跑机会；需网页核对后人工处理记录，不能删除未知状态来宣称“安全重试”。

完整会话记录（含源文件快照，未整份公开）在本机：

```text
%LOCALAPPDATA%\CodexDeepSeek\sessions\20261021T125503Z-b1c7e9a00e3f\discussion.md
```

公开往返正文另见 [本轮公开讨论记录](DEEPSEEK-DIALOGUE-2026-10-21.md)。
