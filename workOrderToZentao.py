"""
工单 → 禅道任务 同步工具
三步流程：
  1. 从工单系统提取工单数据
  2. 匹配模板 "非标售后运维模板" 预填字段
  3. 将工单内容填入禅道任务并创建
"""

import json
import logging
import shutil
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

from zantao import ZenTaoClient, ACCOUNT
from workorder_extractor import WorkOrderExtractor as PlaywrightExtractor, SCREENSHOTS_DIR
import workorder_extractor
from config_loader import load_config, ensure_credentials

# -------------------------- 配置参数 --------------------------

# 禅道系统配置（默认值，凭证会在 main() 中由 ensure_credentials 填充）
ZENTAO_CONFIG = {
    "url": "http://zentao.hlong.cc/zentao",
    "account": ACCOUNT,        # 默认 dengchang
    "password": "",
    "execution_id": 162,       # 分部非标
}

# 3. 硬编码字段参数（用户指定）
HARDCODED_FIELDS = {
    "assignedTo": "dengchang",       # 指派给：邓畅
    "module_id": 3907,               # 所属模块ID：售后运维/工单处理
    "ProductL": "6X",                # 非标产品线：6X
    "task_type": 6,             # 任务类型：运维支持 (type=6)
    "type_of": 8,               # 归属类型 (TypeOf=8)
    "priority": 3,                   # 优先级：3（中等）
}

# 4. 模板 "非标售后运维模板" — 描述结构模板
DESCRIPTION_TEMPLATE = """<h1><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">一、问题场景描述</span></h1>
{problem}

<h1><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">二、故障原因</span></h1>
<p></p>

<h1><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">三、解决方案</span></h1>
<h2><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">1、临时方案</span></h2>
<p></p>
<h2><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">2、最终方案</span></h2>
<p></p>

<h1><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">四、此类问题以什么方式规避以后再出现？</span></h1>
<h2><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">1、开发人员如何规避？</span></h2>
<p></p>
<h2><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">2、测试人员如何规避？</span></h2>
<p></p>

<h1><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">五、待完善内容及预计完善时间？</span></h1>
<p></p>

<h1><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">六、需要其他团队做哪些支持？</span></h1>
<h2><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">1、售前技术</span></h2>
<p></p>
<h2><span style="font-family: Source\ Han\ Sans\ CN, PingFangSC, Microsoft\ YaHei, HiraginoSansGB, Roboto, Helvetica, Tahoma, sans-serif">2、坐席技术</span></h2>
<p></p>"""

# 5. BusCategory[] 可选值（从页面HTML checkbox提取）
BUS_CATEGORY_OPTIONS = [
    "出入逻辑",
    "计费",
    "报表",
    "内部车管理",
    "预约管理",
    "平台对接-会员对接",
    "平台对接-城市平台",
    "平台对接-集团平台",
    "平台对接-交管平台",
    "平台对接-银联对接",
    "平台对接-电子发票",
    "平台对接-ETC",
    "平台对接-充电桩",
    "平台对接-菜鸟模式",
    "平台对接-其他平台",
    "硬件对接",
    "终端-PC岗亭",
    "终端 - 出口缴费机",
    "终端 - 场内缴费机",
    "终端 - LCD屏",
    "终端 - 速停车",
    "终端 - 商户助手",
    "终端 - 云助手APP",
    "终端 - 移动岗亭",
    "终端 - 抵扣券发放机",
    "终端 - 公众号/小程序",
    "终端 - LED",
    "镜像数据处理",
    "硬件定制",
]

# BusCategory[] 关键词 → 选项映射（根据工单内容关键词匹配）
BUS_CATEGORY_KEYWORDS = {
    "支付": "计费",
    "缴费": "计费",
    "扫码": "计费",
    "计费": "计费",
    "白屏": "计费",
    "扣费": "计费",
    "收费": "计费",
    "道闸": "出入逻辑",
    "栏杆": "出入逻辑",
    "抬杆": "出入逻辑",
    "出入口": "出入逻辑",
    "通道": "出入逻辑",
    "车牌": "出入逻辑",
    "识别": "出入逻辑",
    "相机": "出入逻辑",
    "摄像头": "出入逻辑",
    "报表": "报表",
    "内部车": "内部车管理",
    "月租": "内部车管理",
    "预约": "预约管理",
    "会员": "平台对接-会员对接",
    "城市平台": "平台对接-城市平台",
    "集团": "平台对接-集团平台",
    "交管": "平台对接-交管平台",
    "银联": "平台对接-银联对接",
    "发票": "平台对接-电子发票",
    "ETC": "平台对接-ETC",
    "充电桩": "平台对接-充电桩",
    "菜鸟": "平台对接-菜鸟模式",
    "硬件": "硬件对接",
    "岗亭": "终端-PC岗亭",
    "场内缴费机": "终端 - 场内缴费机",
    "出口缴费机": "终端 - 出口缴费机",
    "缴费机": "终端 - 出口缴费机",
    "LCD": "终端 - LCD屏",
    "速停车": "终端 - 速停车",
    "商户助手": "终端 - 商户助手",
    "云助手": "终端 - 云助手APP",
    "移动岗亭": "终端 - 移动岗亭",
    "抵扣券": "终端 - 抵扣券发放机",
    "公众号": "终端 - 公众号/小程序",
    "小程序": "终端 - 公众号/小程序",
    "LED": "终端 - LED",
    "镜像": "镜像数据处理",
    "定制": "硬件定制",
}

DEFAULT_BUS_CATEGORY = "出入逻辑"  # 默认选第一个

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    filename="workorder2zentao.log",
)
logger = logging.getLogger(__name__)


# -------------------------- 工单提取（Playwright UI自动化）--------------------------

class WorkOrderFetcher:
    """使用 Playwright 从工单系统提取数据，封装浏览器生命周期"""

    def __init__(self):
        self.extractor: PlaywrightExtractor | None = None

    def fetch(self, workorder_id: str) -> dict | None:
        """启动浏览器 → 登录 → 导航 → 提取 → 关闭，返回结构化工单数据"""
        ext = PlaywrightExtractor()
        self.extractor = ext
        try:
            ext.launch_browser(headless=False)

            # extract_all 内部已包含: ensure_logged_in → navigate → click → extract
            raw = ext.extract_all(workorder_id)
            if not raw or not raw.get("workorder_id"):
                logger.error(f"工单 {workorder_id} 提取失败：未获取到有效数据")
                return None

            progress_entries = raw.get("progress_entries", [])
            # 排除"接收工单"和"开始处理工单"这类无实际内容的进度
            skip_descs = {"接收工单", "开始处理工单"}
            meaningful_entries = [
                e for e in progress_entries
                if e.get("description", "") not in skip_descs
            ]
            progress_lines: list[str] = []
            for entry in meaningful_entries:
                ts = entry.get("timestamp", "")
                operator = entry.get("operator", "")
                desc = entry.get("description", "")
                notes = entry.get("notes", "")
                duration = entry.get("duration", "")
                progress_lines.append(f"[{ts}] {operator}（耗时{duration}）: {desc}")
                if notes:
                    progress_lines.append(f"  备注: {notes}")
            full_progress = "\n".join(progress_lines)

            latest_entry = meaningful_entries[0] if meaningful_entries else {}
            latest_progress = latest_entry.get("description", "")

            return {
                "workorder_id": raw.get("workorder_id", ""),
                "parking_name": raw.get("parking_name", ""),
                "parking_id": raw.get("parking_id", ""),
                "problem": raw.get("problem", ""),
                "latest_progress": latest_progress,
                "full_progress": full_progress,
                "progress_entries": progress_entries,
                "progress_attachments": raw.get("progress_attachments", []),
                "problem_image_urls": raw.get("problem_image_urls", []),
                "extract_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        finally:
            ext.close()


# -------------------------- 非标业务归类识别 --------------------------

class BusCategoryDetector:
    """根据工单内容自动识别 BusCategory[] 勾选项

    从页面 HTML checkbox 中提取的全部29个选项，根据工单内容关键词匹配。
    若无法判断，默认选择第一个选项（出入逻辑）。
    """

    def __init__(self, options=None, keyword_map=None, default=None):
        self.options = options or BUS_CATEGORY_OPTIONS
        self.keyword_map = keyword_map or BUS_CATEGORY_KEYWORDS
        self.default = default or DEFAULT_BUS_CATEGORY

    def detect(self, task_info: dict) -> str:
        """对工单内容做关键词匹配，返回应勾选的 BusCategory 值"""
        text = " ".join([
            task_info.get("problem", ""),
            task_info.get("full_progress", ""),
            task_info.get("parking_name", ""),
        ])

        for keyword, category in self.keyword_map.items():
            if keyword in text:
                logger.info(f"BusCategory 自动识别: 关键词'{keyword}' → '{category}'")
                return category

        logger.info(f"BusCategory 未匹配到关键词，使用默认: {self.default}")
        return self.default


# -------------------------- 禅道任务创建器 --------------------------

class ZentaoTaskCreator:
    """向禅道创建任务（封装 ZenTaoClient）"""

    def __init__(self, config=None):
        self.config = config or ZENTAO_CONFIG
        self.client = ZenTaoClient(self.config["url"])
        self.execution_id = self.config["execution_id"]

    def login(self, password: str) -> bool:
        """登录禅道（REST Token + Session 双认证）"""
        token = self.client.get_token(self.config["account"], password)
        if not token:
            logger.error("禅道 REST API 登录失败")
            return False
        # 同时做 Session 登录，用于后续 task-edit 表单提交
        self.client.login(self.config["account"], password)
        return True

    def find_module_id(self, module_path: str) -> int | None:
        """通过模块路径（如 '售后运维/工单处理'）查找模块ID

        TODO: 需要确认禅道 REST API 是否支持模块树查询。
        当前返回 None 表示需要手动填写 module_id。
        """
        # 尝试通过 REST API 获取产品模块列表
        # GET /api.php/v1/products/{id}/modules
        # 由于不确定产品ID和执行/项目ID的关系，这里暂不实现自动查询
        logger.warning(f"模块ID自动查询未实现，请手动配置 module_path='{module_path}' 对应的ID")
        return None

    def build_task_data(self, task_info: dict, bus_category: str) -> dict:
        """根据工单信息和 BusCategory 识别结果，构造创建任务所需的完整参数字典"""
        workorder_id = task_info["workorder_id"]
        parking_id = task_info.get("parking_id", "")
        parking_name = task_info.get("parking_name", "")

        # 任务名称：工单编号 + 项目名称（车场ID）车场名称
        task_name = f"工单编号：{workorder_id} 项目名称：({parking_id}){parking_name}"

        # 任务描述：按模板填充工单内容，嵌入在线图片URL（<img>标签）
        task_desc = self._format_description(task_info)

        fields = {
            "execution_id": self.execution_id,
            "name": task_name,
            "desc": task_desc,
            "assigned_to": HARDCODED_FIELDS["assignedTo"],
            "priority": HARDCODED_FIELDS["priority"],
            "task_type": HARDCODED_FIELDS["task_type"],
            "type_of": HARDCODED_FIELDS["type_of"],
            "belong_no": workorder_id,
            "product_l": HARDCODED_FIELDS["ProductL"],
            "bus_category": bus_category,
            "module_id": HARDCODED_FIELDS["module_id"],
            "estimate": 2.0,
        }

        return fields

    @staticmethod
    def _collect_image_urls(task_info: dict) -> list[str]:
        """从 problem_image_urls 和 progress_attachments 中提取去重在线图片 URL"""
        seen = set()
        urls = []
        for key in ("problem_image_urls", "progress_attachments"):
            for att in task_info.get(key, []):
                if isinstance(att, dict):
                    src = att.get("src", "")
                    if src and src.startswith("http") and src not in seen:
                        seen.add(src)
                        urls.append(src)
        print(f"[format] 收集到 {len(urls)} 个在线图片URL")
        return urls

    def _format_description(self, task_info: dict) -> str:
        """格式化工单内容为禅道任务描述HTML

        优先使用在线图片 URL 嵌入 <img> 标签；若无在线 URL 则降级为 files[] 附件上传。
        """
        problem = task_info.get("problem", "")
        extract_time = task_info.get("extract_time", "")

        # 最新进度
        raw_progress = task_info.get("latest_progress", "")
        if isinstance(raw_progress, dict):
            desc = raw_progress.get("description", "")
            ts = raw_progress.get("timestamp", "")
            latest_text = f"[{ts}] {desc}" if ts else desc
        else:
            latest_text = raw_progress if raw_progress else ""

        # 构建问题场景描述：问题文本 + 最新进度
        problem_html = f"<p>{problem}</p>"

        if latest_text:
            problem_html += f"<p><strong>最新处理进度：</strong>{latest_text}</p>"

        # 优先嵌入在线图片 URL（从工单系统 HTML 中提取的 src 属性）
        image_urls = self._collect_image_urls(task_info)
        if image_urls:
            problem_html += "<p><strong>相关截图：</strong></p>"
            for url in image_urls:
                problem_html += f'<img src="{url}" style="max-width:100%;margin:4px 0;" />'

        print(f"[format] problem={len(problem)}chars, latest={len(latest_text)}chars, "
              f"online_images={len(image_urls)}")

        body = DESCRIPTION_TEMPLATE.format(problem=problem_html)

        desc = body + f"<hr><p><em>数据提取时间: {extract_time}</em></p>"
        print(f"[format] 描述大小: {len(desc)} chars")
        return desc

    def create_task(self, fields: dict) -> dict | None:
        """创建禅道任务：REST 创建 + Session 表单补设 assignedTo/module

        本环境 REST PUT/POST 更新 tasks/{id} 不可用，指派/模块统一走 Session。
        """
        desc = fields.pop("desc", "")
        module_id = fields.pop("module_id", None)
        assigned_to = fields.pop("assigned_to", None)
        task_type = fields.get("task_type", 8)
        product_l = fields.get("product_l", "")
        bus_category = fields.get("bus_category", "")
        execution_id = fields.get("execution_id", self.execution_id)

        try:
            result = self.client.create_task_rest(
                desc=desc,
                assigned_to=assigned_to,
                module_id=module_id,
                **fields,
            )
            logger.info(f"禅道任务创建结果: {json.dumps(result, ensure_ascii=False)}")

            if result and isinstance(result, dict) and result.get("id"):
                task_id = result["id"]
                # 先仅指派，减少对编辑页的依赖；模块等仍走编辑页并带上 desc
                if assigned_to:
                    self.client.assign_task_session(task_id, assigned_to)
                session_result = self.client.update_task_session(
                    task_id=task_id,
                    execution_id=execution_id,
                    module_id=module_id,
                    assigned_to=assigned_to,
                    desc=desc,
                    type_of=fields.get("type_of", 8),
                    belong_no=fields.get("belong_no", ""),
                    product_l=product_l,
                    bus_category=bus_category,
                    task_type=task_type,
                )
                logger.info(f"任务 {task_id} Session补设结果: {session_result}")
                if session_result.get("success") or self.client.verify_task_assigned(
                    task_id, assigned_to
                ):
                    print("  [OK] 指派给/模块已写入")
                else:
                    print("  [WARN] 指派给/模块可能未写入，请在禅道任务页手动确认")

            return result
        except Exception as e:
            logger.error(f"禅道任务创建失败: {e}")
            return None


# -------------------------- 主流程 --------------------------

def run(workorder_id: str, password: str, template_name: str = None):
    """完整流程：提取工单 → 匹配模板 → 创建禅道任务

    Args:
        workorder_id: 工单编号
        password: 禅道登录密码
        template_name: 模板名称，默认 "非标售后运维模板"
    """
    print(f"\n{'='*50}")
    print(f"工单 → 禅道任务 同步开始")
    print(f"工单编号: {workorder_id}")
    print(f"模板: {template_name or '非标售后运维模板'}")
    print(f"{'='*50}\n")

    # Step 1: 提取工单信息（Playwright UI自动化）
    print("[Step 1/3] 提取工单信息（Playwright浏览器自动化）...")
    fetcher = WorkOrderFetcher()
    task_info = fetcher.fetch(workorder_id)
    if not task_info:
        print("[FAIL] 提取工单信息失败，终止流程")
        return None
    print(f"  [OK] 工单提取成功: {task_info['parking_name']}")
    print(f"    问题: {task_info['problem'][:60]}...")

    # Step 2: 自动识别非标业务归类(BusCategory) + 匹配描述模板
    print("\n[Step 2/3] 自动识别非标业务归类 & 匹配模板...")
    detector = BusCategoryDetector()
    bus_category = detector.detect(task_info)
    print(f"  [OK] 非标业务归类: {bus_category}")
    print(f"  [OK] 描述模板: 非标售后运维模板")

    # Step 3: 创建禅道任务
    print("\n[Step 3/3] 创建禅道任务...")
    creator = ZentaoTaskCreator()

    # 登录
    if not creator.login(password):
        print("[FAIL] 禅道登录失败，终止流程")
        return None

    # 构造并创建
    fields = creator.build_task_data(task_info, bus_category)
    print(f"  任务名称: {fields['name']}")
    print(f"  指派给: {fields['assigned_to']}")
    print(f"  归属编号: {fields['belong_no']}")
    print(f"  非标产品线: {fields['product_l']}")
    print(f"  非标业务归类: {fields['bus_category']}")
    print(f"  模块ID: {fields['module_id']}")
    print(f"  任务类型: {fields['task_type']} (TypeOf=8)")

    result = creator.create_task(fields)
    if result and isinstance(result, dict) and result.get("id"):
        task_id = result["id"]
        print(f"\n  [OK] 禅道任务创建成功!")
        print(f"    任务ID: {task_id}")

        screenshots_dir = SCREENSHOTS_DIR if fetcher.extractor else None
        if screenshots_dir and screenshots_dir.exists():
            shutil.rmtree(screenshots_dir)
            screenshots_dir.mkdir(exist_ok=True)
            print(f"    [清理] screenshots 目录已清空")
    else:
        print("\n  [FAIL] 禅道任务创建失败，请查看日志")

    return result


def _setup_credentials(account: str = None, password: str = None):
    """
    加载/补全凭证（缺失则逐步提示填写），并把结果同步到：
      - ZENTAO_CONFIG（本模块）
      - workorder_extractor.LOGIN_CONFIG
    命令行显式传入的 account/password 优先于 config.json。
    """
    cfg = ensure_credentials(load_config())

    zt = cfg.get("zentao", {})
    ZENTAO_CONFIG["url"]          = zt.get("url", ZENTAO_CONFIG["url"])
    ZENTAO_CONFIG["account"]      = account or zt.get("account", ZENTAO_CONFIG["account"])
    ZENTAO_CONFIG["password"]     = password or zt.get("password", ZENTAO_CONFIG["password"])
    ZENTAO_CONFIG["execution_id"] = zt.get("execution_id", ZENTAO_CONFIG["execution_id"])

    wk = cfg.get("workorder", {})
    workorder_extractor.LOGIN_CONFIG["username"] = wk.get("username", "")
    workorder_extractor.LOGIN_CONFIG["password"] = wk.get("password", "")


def main(workorder_id: str = None, password: str = None, account: str = None):
    """入口：可通过参数或交互式输入"""
    if not workorder_id:
        workorder_id = input("请输入工单编号: ").strip()
    if not workorder_id:
        print("[FAIL] 工单编号不能为空")
        return None

    # 第 1 步：工单编号
    print(f"\n[步骤 1 / 2] 工单编号: {workorder_id}")

    # 第 2 步：凭证（如有缺失，逐步提示填写并自动保存）
    print("[步骤 2 / 2] 凭证检查")
    _setup_credentials(account=account, password=password)

    return run(workorder_id, password or ZENTAO_CONFIG["password"])


if __name__ == "__main__":
    # ↓↓↓ 可直接修改以下参数后按 F5 运行（留空则交互式询问）↓↓↓
    _WORKORDER_ID = "20260603J6789"
    _ACCOUNT = ""        # 留空则取 config.json / 交互输入
    _PASSWORD = ""       # 留空则取 config.json / 交互输入

    if len(sys.argv) >= 3:
        result = main(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        result = main(sys.argv[1])
    elif _WORKORDER_ID:
        result = main(_WORKORDER_ID, _PASSWORD or None, _ACCOUNT or None)
    else:
        print("用法: python workOrderToZentao.py <工单编号> [密码]")
        print("示例: python workOrderToZentao.py 20260425K57602")
        print("说明: 首次运行会逐步提示填写凭证，已保存到 config.json\n")
        result = main()
