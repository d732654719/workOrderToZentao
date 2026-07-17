# workOrderToZentao 设计文档（逆向整理）

> 本文档由 Claude 根据当前代码逆向整理而成（2026-06-10），用于补回"项目原始 plan"缺失的部分。
> 数据来源：`workOrderToZentao.py` / `zantao.py` / `workorder_extractor.py` / `config_loader.py` / `README.md` / `git log`。
> 与 README 配套阅读：README 讲"怎么用"，本文讲"为什么这么设计"。

---

## 1. 项目目标

### 1.1 解决的问题

将**科拓工单系统（keytop.cn）**的工单数据自动同步为**禅道（ZenTao）任务**。

**手动流程的痛点**：

1. 打开工单系统 → 复制问题描述
2. 下载截图/附件
3. 打开禅道 → 填写执行、模块、指派人、产品线、类型、优先级等十几个字段
4. 上传附件 → 创建任务

**自动化后**：

- 一次输入工单号 → 浏览器自动登录 → 提取工单（含截图下载到本地）→ 识别业务归类 → 上传图片到禅道文件库 → 创建任务 → 回填指派/模块

### 1.2 目标用户

- 工单处理岗（科拓非标售后运维），每人每天处理若干工单
- 典型任务量：每天 1~10 单
- 单次操作省时 ≈ 80%（实测估算）

---

## 2. 整体架构

### 2.1 模块划分

```
workOrderToZentao/
├── workOrderToZentao.py   # 主入口 + 流程编排（671 行）
├── workorder_extractor.py # Playwright 工单提取器（1507 行）
├── zantao.py              # 禅道 API 客户端（752 行）
├── config_loader.py       # 凭证加载器（148 行）
├── jiami_3des.py          # 备用：3DES 加密工具
├── debug_changepwd.py     # 调试脚本：登录重定向问题排查
├── config.json            # 本地凭证（gitignored）
├── cookies.json           # 浏览器登录状态（gitignored）
├── localstorage.json      # 浏览器 localStorage（gitignored）
├── images/                # 提取的工单截图（gitignored）
├── screenshots/           # 调试截图（gitignored）
├── output/                # 提取数据落盘（gitignored）
└── workorder2zentao.log   # 运行日志
```

### 2.2 三层架构

```
┌────────────────────────────────────────────────────────────┐
│ Layer 1: 编排层 (workOrderToZentao.py)                      │
│   - WorkOrderFetcher       包装 PlaywrightExtractor         │
│   - BusCategoryDetector    关键词匹配业务归类                │
│   - ZentaoTaskCreator      包装 ZenTaoClient + 图片上传     │
│   - run() / main()         三步流程入口                      │
├────────────────────────────────────────────────────────────┤
│ Layer 2: 适配层                                             │
│   - workorder_extractor.py 适配"科拓工单系统"（无 API）      │
│   - zantao.py              适配"禅道"（双认证）              │
├────────────────────────────────────────────────────────────┤
│ Layer 3: 基础设施层 (config_loader.py)                       │
│   - 凭证首次配置引导                                        │
│   - config.json 加载/保存                                   │
└────────────────────────────────────────────────────────────┘
```

### 2.3 关键设计原则

| 原则 | 体现 |
|---|---|
| **职责单一** | 三个核心模块互不依赖：extractor 只懂"取工单"，zentao 只懂"写禅道"，主入口只懂"编排" |
| **双认证兜底** | 禅道客户端同时实现 REST v1 + Session，本环境 REST PUT 不可用时回退 Session |
| **配置可覆盖** | `HARDCODED_FIELDS` 留有兜底；`assignedTo` 优先级链：CLI > IDE 变量 > config.json > 默认 account |
| **凭证外置** | 所有账号/密码/指派给均不入 git；`config.json` 自动生成并 gitignore |
| **图片永久化** | 不用 base64 或临时链接，本地下载 → 上传禅道文件库 → 引用 `file-read-{id}.ext` 永久 URL |

---

## 3. 模块详细设计

### 3.1 `workorder_extractor.py` — 工单提取器

#### 角色
把**没有开放 API 的科拓工单系统**（`yun.keytop.cn`）当黑盒，用 Playwright 模拟浏览器操作提取数据。

#### 核心类 `WorkOrderExtractor`

**生命周期**：

```
launch_browser() → ensure_logged_in() → navigate_to_workorder_list()
  → click_workorder_by_id(id) → extract_workorder_info() → close()
```

**关键方法**（按调用顺序）：

| 方法 | 行号范围 | 职责 |
|---|---|---|
| `launch_browser(headless=False)` | 55 | 启动 Chromium（headless=False 便于人工介入验证码） |
| `load_state()` / `save_state()` | 74~111 | cookies + localStorage 持久化，**避免每次重新登录** |
| `ensure_logged_in()` | (中间) | 优先 load_state，失败再走登录页 |
| `preprocess_captcha()` | 113 | PIL 灰度 + 对比度增强 + 锐化，提高 OCR 准确率 |
| `ocr_captcha()` | 147 | ddddocr 识别验证码，返回 (text, confidence) |
| `navigate_to_workorder_list()` | (中间) | 跳到工单列表 |
| `_get_workorder_ids_from_list()` | (中间) | 抓取所有工单号 |
| `click_workorder_by_id()` | (中间) | 按工单号定位点击 |
| `extract_workorder_info()` | (中间) | 抓取详情：车场名/ID、问题描述、进度列表、附件 |
| `extract_all()` | (中间) | **一键封装**：登录+导航+点击+提取 |
| `save_to_file()` | (中间) | 落盘到 `output/` |
| `copy_to_clipboard()` | (中间) | 复制摘要到剪贴板 |
| `close()` | 66 | 关闭浏览器，释放资源 |

**登录态持久化是核心优化**：

- 第一次运行：登录 → 保存 cookies + localStorage
- 之后运行：直接加载 → 跳过登录 → 节省 ~10s + 避免验证码失败

#### 验证码策略

科拓登录需要图形验证码，方案：

1. **截图**到 `screenshots/captcha_*.png`
2. **PIL 预处理**：灰度 → 对比度 ×2 → 锐度 ×1.5
3. **ddddocr 识别**：返回 (text, confidence)
4. 置信度低时**人工兜底**：headless=False 让人在浏览器里看

### 3.2 `zantao.py` — 禅道 API 客户端

#### 角色
封装禅道 API 调用，**屏蔽 REST v1 / Session 两套接口的差异**，对上层提供统一动作（登录、创建任务、补设字段）。

#### 核心类 `ZenTaoClient`

##### 双认证机制

```
┌──────────────────────────────────────────────────┐
│ 认证方式 1: REST v1 Token (推荐)                  │
│   POST /api.php/v1/tokens                        │
│   后续请求 Header: Token: <token>                │
│   用途: REST 创建任务、查询、文件上传              │
├──────────────────────────────────────────────────┤
│ 认证方式 2: Session (HTML 表单登录)               │
│   GET  /user-refreshRandom.html → verifyRand     │
│   POST /user-login.html (md5(md5(pwd)+rand) 哈希)│
│   用途: 编辑页 form POST（assignedTo/module 必走） │
└──────────────────────────────────────────────────┘
```

**两套并存的原因**：本环境禅道实例 `tasks/{id}` 的 REST PUT/POST 不可用（注释 [zantao.py:414](zantao.py#L414) 明确说明），所以**创建走 REST，字段补设走 Session**。

##### 关键方法

| 方法 | 职责 |
|---|---|
| `get_token(account, pwd)` | REST v1 登录拿 token |
| `login(account, pwd)` | Session 登录（verifyRand + MD5 哈希密码） |
| `create_task_rest(...)` | multipart/form-data 创建任务，**支持 files[] 附件** |
| `update_task_rest(...)` | **本环境禁用**（taskEntry::post Fatal error） |
| `assign_task_session(task_id, who)` | 走 `task-assignTo-{id}.html` 专用指派页，**字段少、成功率高** |
| `update_task_session(...)` | 走 `task-edit-{id}.html` 编辑页，**必须传 desc 防覆盖** |
| `upload_file_rest(path)` | 上传图片到文件库，**REST 失败回退 Session** |
| `build_file_read_url(result)` | 构造 `file-read-{id}.{ext}` 永久 URL |
| `verify_task_assigned(id, who)` | REST 查询校验指派是否生效 |

##### 关键设计决策

**1. 创建 + 补设两阶段**：

```python
# 阶段 1: REST 创建（带 desc、busCategory、productL 等富文本/多值字段）
result = client.create_task_rest(...)

# 阶段 2: Session 补设 assignedTo/module（REST PUT 在本环境不可用）
if assigned_to:
    client.assign_task_session(task_id, assigned_to)  # 专用指派页（更稳）
client.update_task_session(task_id, ..., desc=desc)   # 必传 desc 防清空
```

**2. 上传文件 REST + Session 双路径**：

```
upload_file_rest(path)
  ├─ _upload_image_rest()      # POST /api.php/v1/files (字段 imgFile)
  └─ _upload_image_session()   # POST /file-ajaxUpload.html (字段 imgFile)
```

**3. Session 响应校验** ([zantao.py:420](zantao.py#L420))：

```python
def _session_response_ok(text, url):
    if "不能为空" in text or "Fatal error" in text:
        return False
    if "task-view" in url or "task-history" in url:
        return True
    # 解析 JSON 或 HTML 找 "success" / "保存成功"
```

### 3.3 `workOrderToZentao.py` — 主流程编排

#### 角色
**唯一一个"懂业务"的模块**——知道"工单"怎么映射成"禅道任务"。

#### 核心类与函数

##### `WorkOrderFetcher`
包装 `WorkOrderExtractor`，加一层**数据清洗**：
- 排除"接收工单" / "开始处理工单"等无意义进度
- 合并进度文本为 `full_progress`
- 挑出最新进度为 `latest_progress`

##### `BusCategoryDetector` — 非标业务归类识别

**业务背景**：禅道任务的 `BusCategory[]` 字段是 29 个固定选项的 checkbox，**多选但本场景下只勾一个**。

**识别策略**：关键词字典匹配

```python
BUS_CATEGORY_KEYWORDS = {
    "支付": "计费", "扫码": "计费", "白屏": "计费", ...
    "道闸": "出入逻辑", "栏杆": "出入逻辑", "抬杆": "出入逻辑", ...
    "报表": "报表", "月租": "内部车管理", ...
    "会员": "平台对接-会员对接", "城市平台": "平台对接-城市平台", ...
    "终端-PC岗亭": ...,
}
DEFAULT_BUS_CATEGORY = "出入逻辑"  # 兜底
```

匹配源：`problem` + `full_progress` + `parking_name` 拼成一段文本，**按字典序遍历**（先命中先返回）。

##### `extract_non_number()` — 非标编号识别

4 个正则按优先级匹配（`[workOrderToZentao.py:262](workOrderToZentao.py#L262)`）：

| 优先级 | 模式 | 例子 | 说明 |
|---|---|---|---|
| 1 | `cn\.keytop\.ns\.\w+\.(\d+)` | `cn.keytop.ns.tc/1061434` | 项目代码 |
| 2 | `FB_(\d+)` | `对接FB_1061434` | FB 下划线 |
| 3 | `_FB(\d+)` | `支付_FB79167` | 后缀 _FB |
| 4 | `(?<![A-Za-z0-9_])FB(\d+)` | `FB79167 出口` | FB 前缀 |

匹配源：合并 `problem` + `full_progress` + 每条 `progress_entry.description` + `progress_entry.notes`。

##### `ZentaoTaskCreator` — 任务创建器

**两段式创建**（[workOrderToZentao.py:444](workOrderToZentao.py#L444)）：

```python
def create_task(self, fields):
    # 弹出三个不走 REST 的字段
    desc = fields.pop("desc", "")
    module_id = fields.pop("module_id", None)
    assigned_to = fields.pop("assigned_to", None)

    # 阶段 1: REST 创建（desc 走 HTML 富文本）
    result = self.client.create_task_rest(
        desc=desc, assigned_to=assigned_to, module_id=module_id, **fields
    )

    # 阶段 2: Session 补设（指派优先用专用页，模块走编辑页且必带 desc）
    if result.get("id"):
        if assigned_to:
            self.client.assign_task_session(task_id, assigned_to)
        self.client.update_task_session(
            task_id, ..., desc=desc,  # 必传 desc！否则会被清空
            type_of=..., belong_no=..., non_number=..., product_l=..., bus_category=...
        )
```

**图片永久化**（[workOrderToZentao.py:381](workOrderToZentao.py#L381)）：

```python
def upload_task_images(self, task_info):
    # 1. 收集本地图片路径
    local_paths = self._collect_local_image_paths(task_info)

    # 2. 逐个上传到禅道文件库
    for path in local_paths:
        result = self.client.upload_file_rest(str(path))
        url = self.client.build_file_read_url(result, fallback_ext=ext)
        zentao_urls.append(url)

    # 3. 返回永久 file-read URL 列表
    return zentao_urls
```

正文 HTML 中用 `<img src="{url}" style="max-width:100%;margin:4px 0;" />` 内联嵌入。

##### `run()` — 三步流程

```python
def run(workorder_id, password, assigned_to=None, template_name=None):
    # Step 1: 提取
    fetcher = WorkOrderFetcher()
    task_info = fetcher.fetch(workorder_id)  # Playwright 提取+清洗

    # Step 2: 识别业务归类
    detector = BusCategoryDetector()
    bus_category = detector.detect(task_info)

    # Step 3: 创建
    creator = ZentaoTaskCreator()
    creator.login(password)                                 # REST + Session 双登
    task_info["zentao_image_urls"] = creator.upload_task_images(task_info)
    fields = creator.build_task_data(task_info, bus_category)
    result = creator.create_task(fields)

    # 清理临时文件
    shutil.rmtree(SCREENSHOTS_DIR); SCREENSHOTS_DIR.mkdir()
    shutil.rmtree(IMAGES_DIR / workorder_id)
```

### 3.4 `config_loader.py` — 凭证管理

#### 角色
**首次运行引导 + 后续静默加载**。

#### 三个核心函数

| 函数 | 职责 |
|---|---|
| `load_config()` | 静默加载 `config.json`，损坏/不存在返回 `{}` |
| `prompt_and_save_config(cfg)` | 逐步提示填写，**带确认环节**（Y/n），写回文件 |
| `ensure_credentials(cfg)` | 入口函数：齐则直接返回，缺则 `prompt_and_save_config` |

#### 设计要点

- **凭证不存代码**：`ZENTAO_URL` / `EXECUTION_ID` 是固定配置，**不参与凭证输入**（部署级 vs 用户级）
- **明文 input()**：注释 [config_loader.py:8](config_loader.py#L8) 说明"明文显示便于用户当场确认"
- **支持覆盖**：第 N 次运行时 `input(f"  账号 [old_value]: ")`，按 Enter 接受已保存值
- **确认环节**：填写完先打印摘要，再问 Y/n，避免静默写入

#### 数据结构

```json
{
  "workorder": {
    "username": "...",
    "password": "..."
  },
  "zentao": {
    "account": "dengchang",
    "password": "...",
    "assignedTo": "yangfangfang"  // 可选，默认 = account
  }
}
```

---

## 4. 核心数据流

### 4.1 数据转换链

```
工单系统 (DOM)
  │  Playwright 抓取
  ▼
{
  workorder_id, parking_id, parking_name,
  problem, latest_progress, full_progress,
  progress_entries[], progress_attachments[],
  problem_image_urls[]
}
  │  关键词匹配 / 正则提取
  ▼
{
  ...上面所有字段,
  bus_category,  # "计费" / "出入逻辑" / ...
  non_number     # "1061434" / ""
}
  │  build_task_data()
  ▼
{
  execution_id, name, desc, assigned_to, priority,
  task_type, type_of, belong_no, non_number,
  product_l, bus_category, module_id, estimate
}
  │  create_task() 拆分
  ├─→ REST POST   → desc, bus_category, product_l, non_number, belong_no, ...
  └─→ Session POST → assigned_to, module_id, desc(防清空), type_of, ...
  ▼
禅道任务 (id=xxx)
```

### 4.2 字段映射表

| 禅道字段 | 来源 | 备注 |
|---|---|---|
| `execution` | 固定 `EXECUTION_ID=162` | 硬编码，"分部非标" |
| `name` | `"工单编号：{id} 项目名称：({parking_id}){parking_name}"` | 拼接 |
| `desc` | `DESCRIPTION_TEMPLATE.format(problem=...)` | HTML 富文本 |
| `assignedTo` | CLI > IDE 变量 > config.json > account | 多级覆盖 |
| `priority` | `HARDCODED_FIELDS.priority=3` | 中等 |
| `type` (任务类型) | `HARDCODED_FIELDS.task_type=6` | 运维支持 |
| `TypeOf` (归属类型) | `HARDCODED_FIELDS.type_of=8` | 硬编码 |
| `BelongNO` | `workorder_id` | 工单号 |
| `NonNumber` | `extract_non_number()` 4 个正则 | 自动识别 |
| `ProductL` | `HARDCODED_FIELDS.ProductL="6X"` | 非标产品线 |
| `BusCategory[]` | `BusCategoryDetector.detect()` | 关键词匹配 |
| `module` | `HARDCODED_FIELDS.module_id=3907` | "售后运维/工单处理" |
| `estimate` | `0.5` | 半天工时 |
| `estStarted` / `deadline` | 当天 | time.strftime |

---

## 5. 入口与运行模式

### 5.1 三种入口

| 入口 | 触发 | 适用场景 |
|---|---|---|
| **CLI 无参** | `python workOrderToZentao.py` | tty 交互式 |
| **CLI 带工单号** | `python workOrderToZentao.py 20260525Y489423` | 日常使用 |
| **CLI 带工单号 + 指派** | `python workOrderToZentao.py 20260525Y489423 linyusen` | 临时转派 |
| **IDE F5** | 改 `_WORKORDER_ID` / `_ASSIGNED_TO` 后运行 | 调试 |

### 5.2 stdin 检测分流

`workOrderToZentao.py:643-675` 的分支逻辑是项目的一个巧妙点：

```
if len(sys.argv) >= 3:           # CLI 显式 2 个参数
    main(argv[1], assigned_to=argv[2])
elif len(sys.argv) == 2:         # CLI 显式 1 个参数
    main(argv[1])
elif sys.stdin.isatty():         # 交互式终端
    main()                        # 走 input() 提示工单号
else:                            # IDE F5 / Code Runner / 管道
    if config.json 不完整:
        print("⚠️ 必须先在命令行完成首次配置") + sys.exit(1)
    else:
        使用 _WORKORDER_ID / _ASSIGNED_TO 直接走 main()
```

**为什么这样分？** IDE 的运行面板没有 stdin，`input()` 会卡住。`sys.stdin.isatty()` 准确识别"非交互"环境，给出明确提示并退出。

### 5.3 凭证状态机

```
                        首次运行（缺 config.json）
                                ↓
                config.json 不完整 / 不存在
                                ↓
                  必须用 CLI（stdin.isatty() == True）
                                ↓
                逐步填写 4 项 + Y/n 确认 + 保存
                                ↓
                ─────────────────────────────────
                                ↓
                  日常使用（config.json 已完整）
                                ↓
        ┌──────────┬──────────┬──────────────┐
        │ CLI      │ IDE F5   │ stdin 无 tty  │
        │ 直接用   │ 读变量   │ 静默用        │
        └──────────┴──────────┴──────────────┘
```

---

## 6. 关键设计决策与权衡

### 6.1 为什么用 Playwright 而不是 requests？

科拓工单系统**没有公开 API**，且登录有图形验证码 + JS 加密。Playwright 模拟真实浏览器是**唯一可靠**的方案。

代价：
- 启动慢（~3s）
- 占用内存（~200MB）
- 但省下的手动操作时间（~2min/单）远超成本

### 6.2 为什么双认证（REST + Session）并存？

**实测发现**：本环境禅道实例的 `tasks/{id}` REST PUT/POST 返回 Fatal error（[zantao.py:414](zantao.py#L414)）。

解决：
- **创建**走 REST（带富文本 desc、多值 BusCategory、文件上传）
- **字段补设**走 Session 编辑页（assignedTo、module）
- **指派**优先用 `task-assignTo-{id}.html` 专用页（字段少、成功率高）

### 6.3 为什么图片要"下载→上传→file-read 永久 URL"？

旧方案（已废弃）把图片转 base64 嵌入 HTML，问题：
- HTML 体积爆炸（>1MB）
- 复制粘贴到禅道富文本会丢失
- **图片丢失**——这是 commit `7ad89b9` 修复的 bug

新方案：
1. Playwright 下载到 `images/{workorder_id}/`
2. 调用 `upload_file_rest()` 上传到禅道文件库
3. 正文用 `<img src="http://zentao.hlong.cc/zentao/file-read-{id}.png">`
4. 永久有效，不受本地清理影响

### 6.4 为什么"凭证外置 + 逐步提示"？

- 工单系统/禅道账号**不在代码里**——避免泄露、避免改密码要改代码
- **逐步提示**而非一次性粘贴——避免长密码输错
- **Y/n 确认**——避免静默写入错值
- **支持覆盖**——后续运行按 Enter 接受已保存值

### 6.5 为什么用 `HARDCODED_FIELDS` 而不是全 config 化？

- `module_id=3907`、`ProductL="6X"`、`priority=3` 是**业务级固定值**（禅道里建好就不变）
- 放代码里**减少配置项**、**避免误改**
- 真要改：直接改 `workOrderToZentao.py` 的常量

### 6.6 `assignedTo` 优先级链

```
CLI 参数 > _ASSIGNED_TO 变量 > config.json.zentao.assignedTo > config.json.zentao.account
```

- **CLI 临时转派**最灵活
- **IDE 调试**改 _ASSIGNED_TO 即可
- **日常固定指派**写进 config.json
- **兜底**指给自己

---

## 7. 演化历史（git log 总结）

| Commit | Date | 变更 |
|---|---|---|
| `a021823` | 2026-06-03 | 🎉 首次提交：三大模块 + 凭证加载 + 2930 行 |
| `60e86f5` | 2026-06-03 | refactor: 凭证填写移入 main() 流程，按步提示 |
| `cb40429` | 2026-06-03 | refactor: 凭证外置 + 逐步提示填写 |
| `e52c9d3` | 2026-06-04 | feat: 从工单进度自动提取非标编号并同步到禅道 NonNumber |
| `16c30b0` | 2026-06-05 | feat: 同上功能增强（多模式正则） |
| `ca05ae8` | 2026-06-10 | feat: 支持自定义指派给（CLI参数/IDE变量/config.json） |
| `7ad89b9` | 2026-06-10 | fix: 图片上传到禅道服务器生成永久链接 |
| `c5ff31b` | 2026-06-10 | docs: 更新说明书-指派给用法 & 图片永久链接说明 |

---

## 8. 已知约束与 TODO

### 8.1 已知约束

| 约束 | 位置 | 影响 |
|---|---|---|
| 禅道 REST `tasks/{id}` PUT 不可用 | [zantao.py:414](zantao.py#L414) | 创建后必须走 Session 补设 |
| 工单系统无 API，必须用 Playwright | 整体架构 | 启动慢、依赖浏览器 |
| 验证码识别率不是 100% | [workorder_extractor.py:147](workorder_extractor.py#L147) | headless=False 让人工兜底 |
| module_id=3907 硬编码 | [workOrderToZentao.py:42](workOrderToZentao.py#L42) | 禅道里改模块树要同步改代码 |
| BusCategory 关键词字典需手工维护 | [workOrderToZentao.py:111](workOrderToZentao.py#L111) | 新业务类型要加关键词 |

### 8.2 未实现 / TODO

- [ ] `ZentaoTaskCreator.find_module_id()` — 模块路径→ID 自动查询（[workOrderToZentao.py:323](workOrderToZentao.py#L323)，暂返回 None）
- [ ] 批量工单处理（`workorder_extractor.py` 有 `--batch`，但 `workOrderToZentao.py` 主流程只支持单工单）
- [ ] 进度回报（任务创建后回写到工单系统）
- [ ] 失败重试与断点续传

---

## 9. 改进方向（仅供参考）

### 9.1 短期
1. **抽取 `task_info` 数据结构** — 现在在三个模块间以 dict 传递，建议用 `dataclass`
2. **统一日志格式** — 现在 `logging` 写到 `workorder2zentao.log`，`print` 输出到 stdout，混用
3. **环境变量兜底** — `ZENTAO_URL` / `EXECUTION_ID` 可改为 env var，便于多环境部署

### 9.2 中期
1. **配置文件化 HARDCODED_FIELDS** — 当字段增多时放 `config.json`，但要保留代码默认值
2. **测试覆盖** — 给 `BusCategoryDetector.detect()` / `extract_non_number()` / `_format_description()` 加单元测试
3. **任务创建结果结构化输出** — 落盘 JSON 供后续回写工单系统用

### 9.3 长期
1. **GUI/CLI 框架化** — `click` / `typer` 替代手写 `sys.argv` 分支
2. **REST 失败时自动重试 + 退避**
3. **多工单系统适配层** — 现在只支持科拓，可抽象 `WorkOrderSource` 接口

---

## 10. 附录

### 10.1 关键代码位置速查

| 功能 | 文件:行 |
|---|---|
| 三步主流程 | [workOrderToZentao.py:500](workOrderToZentao.py#L500) |
| 业务归类关键词字典 | [workOrderToZentao.py:111](workOrderToZentao.py#L111) |
| 非标编号 4 个正则 | [workOrderToZentao.py:263](workOrderToZentao.py#L263) |
| 描述 HTML 模板 | [workOrderToZentao.py:50](workOrderToZentao.py#L50) |
| 两段式任务创建 | [workOrderToZentao.py:444](workOrderToZentao.py#L444) |
| 图片永久 URL 构造 | [zantao.py:249](zantao.py#L249) |
| Session 专用指派页 | [zantao.py:458](zantao.py#L458) |
| Session 编辑页补设 | [zantao.py:480](zantao.py#L480) |
| 凭证首次引导 | [config_loader.py:84](config_loader.py#L84) |
| stdin 分流 | [workOrderToZentao.py:643](workOrderToZentao.py#L643) |

### 10.2 术语表

| 术语 | 含义 |
|---|---|
| 工单 | 科拓工单系统里的一条售后/运维任务 |
| 任务 | 禅道里的一个 task |
| 工单号 / workorder_id | 形如 `20260610P10697` |
| 禅道任务 ID | 数字，如 `98901` |
| BusCategory | 禅道任务字段，**非标业务归类**（29 个 checkbox） |
| NonNumber | 禅道任务字段，**非标编号**（关联工单的项目代码） |
| TypeOf | 禅道任务字段，**归属类型**（8 = 运维支持） |
| ProductL | 禅道任务字段，**非标产品线**（"6X"） |
| BelongNO | 禅道任务字段，**归属编号**（= 工单号） |
| 指派给 / assignedTo | 禅道任务字段，**处理人** |
| execution_id | 禅道"执行"（项目下的迭代），本项目固定 162（分部非标） |

---

**文档维护**：本文档由 Claude 在 2026-06-10 逆向整理。代码变更后请同步更新对应章节。
