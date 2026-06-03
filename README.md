# 工单 → 禅道任务 同步工具

将科拓工单系统中的工单数据自动提取并创建为禅道（ZenTao）任务，支持截图/附件嵌入、非标业务归类自动识别。

## 解决的问题

手动将工单信息录入禅道需要：打开工单系统 → 复制问题描述 → 下载截图 → 打开禅道 → 填写各类字段 → 上传附件 → 创建任务。整个过程繁琐且容易遗漏。

本工具一键完成以下三步：

1. **提取工单** — Playwright 浏览器自动化登录工单系统，提取问题描述、处理进度、截图/附件
2. **匹配模板** — 自动识别非标业务归类（BusCategory），按"非标售后运维模板"填充描述
3. **创建禅道任务** — 调用禅道 REST API 创建任务，并补设指派/模块等标准字段

## 依赖

| 依赖 | 用途 |
|------|------|
| Python 3.11+ | 运行环境 |
| [playwright](https://playwright.dev/python/) | 浏览器自动化，登录工单系统并提取数据 |
| [ddddocr](https://github.com/sml2h3/ddddocr) | 验证码 OCR 自动识别 |
| [requests](https://requests.readthedocs.io/) | HTTP 请求（禅道 API 调用） |

安装依赖：

```bash
pip install playwright ddddocr requests
playwright install chromium
```

## 项目结构

```
workOrderToZentao/
├── workOrderToZentao.py   # 主入口，流程编排
├── workorder_extractor.py # Playwright 工单提取器
├── zantao.py              # 禅道 API 客户端
├── config_loader.py       # 凭证加载器（首次运行逐步提示填写）
├── jiami_3des.py          # 3DES 加密工具（备用）
├── debug_changepwd.py     # 调试脚本
├── config.json            # 本地凭证（首次运行自动生成，已 gitignore）
├── cookies.json           # 浏览器登录状态缓存
├── localstorage.json      # 浏览器本地存储缓存
├── images/                # 提取的截图/附件缓存
└── workorder2zentao.log   # 运行日志
```

## 使用方法

### 分两个阶段

| 阶段 | 触发条件 | 运行方式 | 交互内容 |
|---|---|---|---|
| **首次配置** | `config.json` 缺失或不完整 | ⚠️ **必须用命令行** | 工单号 + 逐步填 4 项凭证 |
| **日常使用** | `config.json` 已完整 | CLI 或 IDE F5 都可 | 仅工单号（凭证自动加载） |

> 首次必须用命令行的原因：凭证填写包含 4 次以上 `input()`，IDE 的运行面板无 stdin 交互，提示会卡住无法响应。**后续**凭证已在 `config.json` 中，每次只问一个工单号，IDE F5 即可。

### 首次配置（命令行）

```bash
python workOrderToZentao.py 20260525Y489423
```

脚本会分 2 步：

```
[步骤 1 / 2] 工单编号: 20260525Y489423
[步骤 2 / 2] 凭证检查
[INFO] 检测到缺失凭证：工单系统.username, 工单系统.password, 禅道.account, 禅道.password

============================================================
  凭证填写（按 Enter 接受默认值或已保存值，输入新值则覆盖）
============================================================

[第 1 步 / 共 2 步] 工单系统登录
  账号: ___
  密码 [***]: ___

[第 2 步 / 共 2 步] 禅道登录
  账号:     ___
  密码 [***]: ___

------------------------------------------------------------
  本次填写的内容确认：
  ...
------------------------------------------------------------
  以上信息是否正确？[Y/n]: ___

[OK] 凭证已保存到 config.json，下次运行将自动加载。
```

> 按 **Enter** 接受已保存值，输入新值则覆盖。如需重置凭证：删除 `config.json` 后重新运行。

### 日常使用

**命令行**（推荐）：

```bash
python workOrderToZentao.py 20260525Y489423                          # 工单号
python workOrderToZentao.py 20260525Y489423 你的禅道密码              # 工单号 + 临时覆盖禅道密码
```
```
** IDE直接运行 workOrderToZentao.py**（凭证已存于 `config.json` 时可用）
需要写入工单编号


```

> ⚠️ 若 `config.json` 不完整，直接运行 / IDE F5 会提示"必须先在命令行完成首次配置"并退出，不会卡住。

## 配置说明

- **凭证**（工单系统账号/密码、禅道账号/密码）— 由 `config.json` 提供，首次运行通过 `config_loader.ensure_credentials()` 逐步提示
- **固定配置**（禅道系统地址、禅道执行ID）— 存于 `config_loader.py` 的 `ZENTAO_URL` / `EXECUTION_ID`，不参与凭证输入
- `HARDCODED_FIELDS` — 任务默认字段（指派给、模块、产品线、优先级等）
- `DESCRIPTION_TEMPLATE` — 任务描述 HTML 模板
- `BUS_CATEGORY_KEYWORDS` — 非标业务归类关键词匹配规则

凭证仅保存在本地 `config.json`（已加入 `.gitignore`），不会推送到 Git。
