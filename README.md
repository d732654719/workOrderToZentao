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
├── jiami_3des.py          # 3DES 加密工具（备用）
├── debug_changepwd.py     # 调试脚本
├── cookies.json           # 浏览器登录状态缓存
├── localstorage.json      # 浏览器本地存储缓存
├── images/                # 提取的截图/附件缓存
└── workorder2zentao.log   # 运行日志
```

## 使用方法

### 方式一：代码内填写参数（推荐）

在 [workOrderToZentao.py](workOrderToZentao.py) 底部修改变量后直接运行：

```python
_WORKORDER_ID = "20260525Y489423"  # 工单编号
_ACCOUNT = ""                       # 禅道账号，留空默认 dengchang
_PASSWORD = ""                      # 禅道密码，留空取默认值
```

### 方式二：命令行参数

```bash
python workOrderToZentao.py 20260525Y489423              # 仅传工单编号
python workOrderToZentao.py 20260525Y489423 你的密码      # 传工单编号+密码
```

### 方式三：交互式输入

```bash
python workOrderToZentao.py
# 提示后输入工单编号，回车继续
```

## 配置说明

核心配置在 [workOrderToZentao.py](workOrderToZentao.py) 顶部：

- `ZENTAO_CONFIG` — 禅道系统地址、账号、密码、执行ID
- `HARDCODED_FIELDS` — 任务默认字段（指派给、模块、产品线、优先级等）
- `DESCRIPTION_TEMPLATE` — 任务描述 HTML 模板
- `BUS_CATEGORY_KEYWORDS` — 非标业务归类关键词匹配规则

工单系统登录凭证在 [workorder_extractor.py](workorder_extractor.py) 的 `LOGIN_CONFIG` 中配置。
