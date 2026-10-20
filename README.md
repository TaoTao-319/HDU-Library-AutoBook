# HDU Library AutoBook · 杭电图书馆自动预约

来源：[ZRui-C/HDU_AUTO_BOOK-public](https://github.com/ZRui-C/HDU_AUTO_BOOK-public)，基于 V2 分支，原始提交 `093b967ff0812602723c16c4c27a595664fede9a`。

2026-10-19 已在当前 Windows 电脑上完成真实登录、查询空位、创建预约、查询订单、重复运行不新增预约、取消测试订单验证。现按后天对应的星期预约四楼 35–58 号座位中的一个（自定义后台 ID 62599–62622，按列表顺序尝试可用座位）：周一 15:00–22:00、周二 16:00–22:00、周三 07:00–08:00、周四 15:00–22:00、周五 13:00–22:00；周六、周日关闭。

## 定时行为

2026-10-19 已完成一次**手动触发的云端预约验证**：[GitHub Actions 测试记录](https://github.com/TaoTao-319/HDU-Library-AutoBook/actions/runs/39262208380) 的事件为 `workflow_dispatch`，登录、预约提交、订单详情核验全部成功；测试时本机任务和本机入口均已禁用。首次云端测试曾在学校登录阶段超时，后续测试成功；保留登录阶段的脱敏诊断以便排查偶发超时。

**原生定时自动预约尚未验证成功。** 截至 2026-10-20 上午，预约仓库的 `schedule` 运行记录仍为零，独立的无账号定时探针也未触发。当前正在进行跨仓库对照测试。手动运行、提交触发的测试通过、以及仅核验已有订单，都不能作为新建自动预约成功的证明；验收需要实际 `schedule` 事件运行、提交新预约并查单确认。

北京时间每天 **21:40** 定时触发，**21:45** 补跑，默认预约后天；实际提交还需要等待 GitHub 调度、环境准备及学校登录，不能保证在设定分钟准时提交。已存在覆盖目标时段的有效预约时跳过；与已有预约部分重叠时明确报错。按预约日期对应的星期读取配置。

当前为**仅云端测试模式**：`config/basic_config.yml` 的 `cloud-only: true` 会阻止本机登录和提交预约；即使直接运行 `main.py` 或误启本机任务，也只会记录“本机执行已停用”后退出。只有 GitHub Actions 环境可以继续执行。本机任务 `HDU-Library-2140`、`HDU-Library-2145` 已更新为 21:39 / 21:44 提前启动、21:40 / 21:45 提交，但均保持 **Disabled**，旧任务已移除。重新运行安装脚本也会保持禁用。

云端运行无需本机开机，结果在 GitHub Actions 页面查看。需要恢复本机运行时，应先将 `cloud-only` 改为 `false`，再手动启用对应的 Windows 任务；本机运行需要电脑开机、联网且当前 Windows 用户已登录。遇到人工验证码时退出并明确提示。

GitHub Actions 配置了两条每日定时：UTC `40 13 * * *` 和 `45 13 * * *`，对应北京时间 21:40 / 21:45；GitHub 调度存在排队延迟，当前暂停本机任务以单独验证云端。GitHub Actions 需要在仓库 Settings → Secrets and variables → Actions 中设置 SCHOOL_ID 和 PASSWORD。

## 本机运行

当前项目已配置 `.venv`。云端测试期间，以下本机运行命令会被 `cloud-only` 开关拦截；离线回归测试仍可运行：

```powershell
# 只读：登录、查单、查询后天 07:00–08:00 的二楼西空位
.\.venv\Scripts\python.exe .\main.py --check

# 按 user_config.yml 立即运行；对应星期未启用时直接退出，不登录
.\.venv\Scripts\python.exe .\main.py

# 真实提交单次预约，忽略星期开关；测试使用后请在图书馆页面取消不需要的订单
.\.venv\Scripts\python.exe .\main.py --test-booking --date 2026-10-21 --start 7 --hours 1 --region 二楼西

# 复现离线回归，不访问学校，也不使用账号密码
.\.venv\Scripts\python.exe -m unittest -v test_main
```

`--check`、`--test-booking` 支持 `--date YYYY-MM-DD`、`--start`、`--hours` 和 `--region`。区域支持原配置中的短名/全称、`自定义`、`不限`；普通运行由配置决定这些值。默认使用后天、07:00–08:00、二楼西。

重新准备环境（Python 3.10 或更新版本、Chrome）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Save-Credential.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-Schedule.ps1
```

`Save-Credential.ps1` 使用 Windows DPAPI 加密本机凭据，仅当前 Windows 用户在当前电脑可解密。密码未写入源代码或配置；`.local/`、`logs/`、虚拟环境均被 Git 忽略。也可通过 `SCHOOL_ID`、`PASSWORD` 环境变量提供凭据，这两个变量优先于本机凭据。

需要查看登录页面时设置 `$env:HDU_SHOW_BROWSER='1'`，检查后用 `Remove-Item Env:HDU_SHOW_BROWSER` 恢复无窗口模式。ChromeDriver 由 Selenium 自动匹配，可用 `CHROMEDRIVER_PATH` 指定已安装的驱动、`CHROME_BINARY` 指定浏览器。

卸载两项本机任务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-Schedule.ps1 -Remove
```

## 启用需要预约的日期

编辑 `user_config.yml`。例如，若希望预约后天落在周三的二楼西 09:00–12:00：

```yaml
周三:
  启用: true
  name: 二楼西
  开始时间: 9
  持续小时数: 3
```

这代表周一晚提交周三的预约；周三晚读取的是周五配置。自动提交时间始终是 21:40 / 21:45。

多段时段请用以下写法，删除同一级的 `开始时间` / `持续小时数`：

```yaml
周三:
  启用: true
  name: 四楼
  时间段:
    - 开始时间: 9
      持续小时数: 3
    - 开始时间: 14
      持续小时数: 4
```

时段不能重叠。区域别名和座位 ID 区间保留在 `config/seat_config.yml`；正常流程以学校接口实时返回的座位和状态为准。自定义座位仍在 `user_config.yml` 文末设置，填的是后台座位 ID，不是页面显示的座位号。

## 修复及验证

已复现原脚本在这台 Windows 上读取 UTF-8 中文配置时发生 `UnicodeDecodeError`。原项目七天均为关闭，因此直接运行也不会自动预约。现统一指定 UTF-8，配置路径相对脚本定位，登录使用无窗口 Chrome 并等待实际页面就绪。

接口支持动态读取分类，分类参数放入 POST 表单，实时筛选空位；预约请求沿用学校网页的 `api_time` 和 `Api-Token` 计算方式。实测学校目前也接受旧版无签名请求，签名缺失不能认定为本次故障原因。

HTTP 会话复用连接，去掉固定 5 秒登录等待，设置连接/读取超时。成功必须同时满足 `CODE=ok`、`DATA.result=success`、返回订单编号，再查询详情核对所有者、日期、时长和座位。预约失败返回非零退出码；服务限流时停止本轮，等待补跑；座位被占时有限次顺序换座；提交网络异常时先查单，避免盲目重复提交。重叠运行由本机文件锁阻止。

本机日志位于 `logs/YYYY-MM-DD.log`；云端运行日志在 GitHub Actions 对应运行记录的 Auto Book 步骤查看。每个已核实的时段写入 `.local/booking-*.json`；这些文件记录的是当时订单状态，后续取消不会回写历史记录，云端临时文件不会自动回写仓库。`.local/test-result.json` 仅记录早先本机测试订单已经取消，不代表后续云端预约状态。

2026-10-19 修复版完成真实预约与查单验证，提交请求约 **70.8 ms**；再次运行识别已有订单、未新增预约。测试订单均已取消并核实状态，没有改动账号原有预约。

本机等待函数 12 次短时测量平均触发偏差 0.253 ms、最大 0.507 ms，仅代表本机计时测量，不包含网络、操作系统高负载、时钟误差或服务器排队，不能保证放号时同样表现。今天实际测试发生在 20:00 之后，尚未验证下一次真实 20:00 放号场景。

请只启用实际需要使用的日期和时段，并按学校要求签到使用。
