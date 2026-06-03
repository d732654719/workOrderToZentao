"""
工单内容提取器 - 使用Playwright从工单系统提取内容
支持登录保持、验证码自动识别、图片提取、剪贴板复制
"""

import re
import json
import time
import logging
from pathlib import Path
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
    import ddddocr
except ImportError as e:
    raise ImportError(f"请先安装依赖: pip install playwright ddddocr")

# -------------------------- 配置 --------------------------
LOGIN_URL = "https://yun.keytop.cn/kitework/page/login2.html"  # 登录页

# 登录凭证：在 main() 中由 ensure_credentials() 填充（首次运行会逐步提示）
LOGIN_CONFIG = {
    "username": "",
    "password": "",
}

# 状态存储路径
STATE_DIR = Path(__file__).parent
COOKIES_FILE = STATE_DIR / "cookies.json"
LOCALSTORAGE_FILE = STATE_DIR / "localstorage.json"
SCREENSHOTS_DIR = STATE_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# -------------------------- 核心类 --------------------------
class WorkOrderExtractor:
    """工单内容提取器"""

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def launch_browser(self, headless=False):
        """启动浏览器"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=headless)
        self.context = self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        self.page = self.context.new_page()
        logger.info("浏览器启动成功")

    def close(self):
        """关闭浏览器"""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("浏览器已关闭")

    def save_state(self):
        """保存登录状态到本地"""
        # 保存cookies
        cookies = self.context.cookies()
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f)

        # 保存localStorage
        localstorage = self.page.evaluate("() => { let ls = {}; for (let i = 0; i < localStorage.length; i++) { let k = localStorage.key(i); ls[k] = localStorage.getItem(k); } return ls; }")
        with open(LOCALSTORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(localstorage, f)

        logger.info(f"状态已保存到: {STATE_DIR}")

    def load_state(self) -> bool:
        """加载本地登录状态，返回是否成功"""
        if not COOKIES_FILE.exists() or not LOCALSTORAGE_FILE.exists():
            return False

        try:
            # 加载cookies
            with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            self.context.add_cookies(cookies)

            # 加载localStorage
            with open(LOCALSTORAGE_FILE, "r", encoding="utf-8") as f:
                localstorage = json.load(f)
            self.page.goto(LOGIN_URL, wait_until="networkidle")
            self.page.evaluate("""(ls) => {
                for (let k in ls) { localStorage.setItem(k, ls[k]); }
            }""", localstorage)

            logger.info("已加载本地登录状态")
            return True
        except Exception as e:
            logger.warning(f"加载状态失败: {e}")
            return False

    def preprocess_captcha(self, img_path: Path) -> bytes:
        """预处理验证码图片，提高识别率"""
        try:
            from PIL import Image
            img = Image.open(img_path)

            # 转为灰度
            img = img.convert('L')

            # 提高对比度
            from PIL import ImageEnhance
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(2.0)

            # 锐化
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(1.5)

            # 保存预处理后的图片
            processed_path = SCREENSHOTS_DIR / f"captcha_processed_{int(time.time())}.png"
            img.save(processed_path)
            logger.info(f"预处理后的验证码: {processed_path}")

            # 返回bytes
            from io import BytesIO
            buffer = BytesIO()
            img.save(buffer, format='PNG')
            return buffer.getvalue()
        except Exception as e:
            logger.warning(f"预处理失败: {e}")
            # 预处理失败则返回原图
            with open(img_path, 'rb') as f:
                return f.read()

    def ocr_captcha(self, captcha_path: Path) -> tuple:
        """使用ddddocr识别验证码，返回 (识别的文本, 置信度)"""
        try:
            # 先预处理图片
            img_bytes = self.preprocess_captcha(captcha_path)
            result = self.ocr_beta.classification(img_bytes)
            logger.info(f"OCR原始返回: {result}")

            # result 是 dict 或 str
            if isinstance(result, dict):
                text = result.get('text', '')
                confidence = result.get('confidence', 0)
            else:
                text = str(result)
                confidence = 0

            # 过滤非字母数字字符，验证码肯定4位
            text = ''.join(c for c in text if c.isalnum())
            if len(text) == 4:
                logger.info(f"OCR识别结果: {text}, 置信度: {confidence}")
                return text, confidence
            else:
                logger.warning(f"OCR识别结果不是4位: {text}")
                return "", 0
        except Exception as e:
            logger.error(f"OCR识别失败: {e}")
            return "", 0

    def init_ocr(self):
        """初始化ddddocr"""
        if not hasattr(self, 'ocr_beta'):
            self.ocr_beta = ddddocr.DdddOcr(show_ad=False)
            logger.info("OCR模型初始化完成")

    def wait_for_captcha(self) -> str:
        """等待并自动识别验证码"""
        captcha_selector = "img[src*='captcha'], .captcha img, .verify-code img, .login-code img"
        try:
            self.page.wait_for_selector(captcha_selector, timeout=5000)
        except:
            logger.info("未检测到验证码元素，继续...")
            return ""

        # 截图验证码
        captcha_path = SCREENSHOTS_DIR / f"captcha_{int(time.time())}.png"
        self.page.locator(captcha_selector).first.screenshot(path=captcha_path)
        logger.info(f"验证码截图: {captcha_path}")

        # OCR自动识别
        captcha, _ = self.ocr_captcha(captcha_path)

        # 填充验证码
        if captcha and len(captcha) == 4:
            try:
                captcha_field = self.page.locator("input[placeholder*='验证码'], input[name*='captcha'], input[class*='captcha']").first
                captcha_field.fill(captcha)
            except Exception as e:
                logger.warning(f"填充验证码失败: {e}")
                return ""

        return captcha

    def login(self, username, password) -> bool:
        """执行登录流程（带重试机制）"""
        max_retries = 10  # 登录失败重试10次
        max_captcha_retries = 5  # 每次页面加载后最多识别5次验证码

        for attempt in range(max_retries):
            logger.info(f"登录尝试 {attempt + 1}/{max_retries}...")
            self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
            time.sleep(1.5)

            # 初始化OCR
            self.init_ocr()

            # 填写用户名密码（只需填写一次）
            try:
                self.page.locator("input[name='username']").fill(username)
                logger.info("用户名已填写")
            except Exception as e:
                logger.warning(f"填写用户名失败: {e}")

            try:
                self.page.locator("input[name='password']").first.fill(password)
                logger.info("密码已填写")
            except Exception as e:
                logger.warning(f"填写密码失败: {e}")

            # 循环识别验证码，直到成功或超时
            captcha_success = False
            for captcha_attempt in range(max_captcha_retries):
                # 刷新验证码canvas
                try:
                    # 点击验证码图片或刷新按钮来刷新验证码
                    refresh_selectors = ["canvas", ".captcha", "[class*='captcha']"]
                    for sel in refresh_selectors:
                        try:
                            self.page.locator(sel).first.click()
                            time.sleep(0.5)
                            break
                        except:
                            continue

                    # 截取新验证码
                    canvas = self.page.locator("canvas").first
                    captcha_path = SCREENSHOTS_DIR / f"captcha_canvas_{int(time.time())}.png"
                    canvas.screenshot(path=captcha_path)

                    captcha_text, captcha_conf = self.ocr_captcha(captcha_path)
                    if captcha_text and len(captcha_text) == 4:
                        # 填写验证码
                        try:
                            captcha_input = self.page.locator("input.input-val")
                            captcha_input.fill(captcha_text)
                            logger.info(f"验证码已填写: {captcha_text}")
                            captcha_success = True
                            break
                        except Exception as e:
                            logger.warning(f"填写验证码失败: {e}")
                except Exception as e:
                    logger.warning(f"刷新验证码失败: {e}")

            if not captcha_success:
                logger.warning(f"验证码识别失败，继续尝试提交...")
                # 仍然尝试点击登录
                try:
                    self.page.locator("input.input-val").fill("0000")  # 随便填一个
                except:
                    pass

            # 点击登录
            try:
                self.page.locator("button[type='submit']").first.click()
                logger.info("点击登录按钮")
            except Exception as e:
                logger.warning(f"点击登录按钮失败: {e}")

            time.sleep(1.5)

            # 检查登录结果
            current_url = self.page.url
            logger.info(f"登录后URL: {current_url}")

            if "login" not in current_url.lower() and "login2.html" not in current_url:
                logger.info("登录成功")
                self.save_state()
                return True

            # 登录失败，继续下一次循环（重新加载页面）
            logger.warning(f"登录失败，准备重试...")

        logger.error(f"登录失败，已重试{max_retries}次")
        return False

    def ensure_logged_in(self):
        """确保已登录，不行则重新登录"""
        # 尝试加载保存的状态
        if self.load_state():
            # 验证状态是否有效 - 检查页面是否显示登录后的内容
            self.page.reload(wait_until="networkidle")
            time.sleep(0.5)
            # 检查URL是否跳转到登录页，或者页面是否包含登录相关元素
            if "login" in self.page.url.lower():
                logger.info("检测到登录页，需要重新登录")
            elif self.page.locator("text=运维管理").count() > 0:
                logger.info("已自动登录")
                return
            else:
                logger.info("未检测到已登录内容，需要重新登录")
        else:
            logger.info("没有保存的登录状态")

        # 需要重新登录
        logger.info("开始登录...")
        self.login(LOGIN_CONFIG["username"], LOGIN_CONFIG["password"])

    def navigate_to_workorder_list(self):
        """导航到工单列表页（坐席工单）"""
        # 保存当前页面截图
        self.page.screenshot(path=SCREENSHOTS_DIR / f"before_menu_{int(time.time())}.png")

        # 点击"运维管理"展开子菜单
        try:
            ops_selectors = [
                "text=运维管理",
                "[class*='menu-item']:has-text('运维管理')",
            ]
            for selector in ops_selectors:
                try:
                    self.page.locator(selector).first.click()
                    logger.info("点击了运维管理")
                    time.sleep(0.5)
                    break
                except:
                    continue
        except Exception as e:
            logger.warning(f"点击运维管理失败: {e}")

        # 点击子菜单"坐席工单" - 使用 popup 事件监听
        new_page = None
        def handle_popup(page):
            nonlocal new_page
            new_page = page
            logger.info(f"检测到新页面: {page.url}")

        self.context.on("page", handle_popup)

        try:
            menu_selectors = [
                "text=坐席工单",
                "a:text('坐席工单')",
                "[class*='submenu'] a:has-text('坐席工单')",
            ]
            for selector in menu_selectors:
                try:
                    with self.context.expect_page(timeout=15000) as popup_info:
                        self.page.locator(selector).first.click()
                        logger.info("点击了坐席工单")
                    new_page = popup_info.value
                    break
                except:
                    continue
        except Exception as e:
            logger.warning(f"点击坐席工单失败: {e}")

        # 移除本步骤的popup监听，避免干扰后续点击
        self.context.remove_listener("page", handle_popup)

        # 等待新页面加载
        if new_page:
            self.page = new_page
            logger.info(f"切换到新页面: {self.page.url}")
            self.page.wait_for_load_state("networkidle")
            time.sleep(1.5)
        else:
            logger.warning("没有检测到新页面")

        # 再次检查所有页面
        time.sleep(0.5)
        for p in self.context.pages:
            if "MyAgentTicket" in p.url:
                self.page = p
                logger.info(f"从页面列表找到工单列表页: {self.page.url}")
                self.page.wait_for_load_state("networkidle")
                time.sleep(0.5)
                return

        logger.warning("没有找到工单列表页")

    def search_workorder_by_id(self, workorder_id: str) -> bool:
        """在工单列表页通过工单编号输入框搜索工单"""
        logger.info(f"正在搜索工单: {workorder_id}")
        time.sleep(0.5)

        # 1. 找到工单编号输入框并输入
        input_selectors = [
            "input[placeholder*='工单编号']",
            "input[placeholder*='工单']",
            "input[name*='workorder']",
            "input[name*='orderId']",
            ".el-form-item:has-text('工单编号') input",
            ".el-form-item:has-text('工单编号') .el-input__inner",
        ]
        filled = False
        for selector in input_selectors:
            try:
                inp = self.page.locator(selector).first
                if inp.count() > 0 and inp.is_visible():
                    inp.click()
                    inp.fill("")
                    inp.fill(workorder_id)
                    logger.info(f"工单编号已填入输入框 (selector={selector})")
                    filled = True
                    break
            except Exception:
                continue

        if not filled:
            logger.warning("未找到工单编号输入框，尝试模糊匹配")
            try:
                all_inputs = self.page.locator("input.el-input__inner, input[type='text']").all()
                for inp in all_inputs:
                    try:
                        placeholder = inp.get_attribute("placeholder") or ""
                        if "工单" in placeholder or "编号" in placeholder:
                            inp.click()
                            inp.fill("")
                            inp.fill(workorder_id)
                            logger.info(f"工单编号已填入: placeholder='{placeholder}'")
                            filled = True
                            break
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"模糊查找输入框失败: {e}")

        if not filled:
            logger.warning("未能填入工单编号，跳过搜索步骤")
            return False

        time.sleep(0.5)

        # 2. 点击查询按钮
        search_selectors = [
            "button:has-text('查询')",
            "button:has-text('搜索')",
            ".el-button--primary:has-text('查询')",
            "button .el-icon-search",
            "button.el-button span:has-text('查询')",
        ]
        clicked = False
        for selector in search_selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click()
                    logger.info(f"已点击查询按钮 (selector={selector})")
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            # try pressing Enter in the input as fallback
            logger.warning("未找到查询按钮，尝试回车键提交")
            try:
                self.page.keyboard.press("Enter")
                clicked = True
            except Exception:
                pass

        if not clicked:
            logger.warning("未能触发查询")
            return False

        # 3. 等待搜索结果显示
        time.sleep(1.5)
        self.page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        # 截图确认
        self.page.screenshot(path=SCREENSHOTS_DIR / f"search_result_{workorder_id}_{int(time.time())}.png")
        logger.info(f"搜索完成，当前页面: {self.page.url}")
        return True

    def extract_workorder_list(self) -> list:
        """从工单列表页提取所有工单的基本信息（当前页）"""
        logger.info("正在提取当前页工单列表...")
        time.sleep(1.5)


        result_json = self.page.evaluate('''() => {
            const result = {tables: [], rawText: ''};
            const woIdPattern = /\d{8}[A-Z]?\d{4,}/i;

            const tables = document.querySelectorAll('.el-table');
            if (tables.length === 0) {
                result.rawText = document.body.innerText.slice(0, 5000);
                return JSON.stringify(result);
            }

            // Process each table
            tables.forEach((table, tableIdx) => {
                // Check if table is visible
                const rect = table.getBoundingClientRect();
                if (rect.width < 100 || rect.height < 50) return;

                // Get headers
                const headers = [];
                const headerCells = table.querySelectorAll('.el-table__header th .cell');
                headerCells.forEach(th => {
                    const text = th.innerText.trim();
                    if (text) headers.push(text);
                });
                if (headers.length === 0) {
                    table.querySelectorAll('.el-table__header th').forEach(th => {
                        const text = th.innerText.trim();
                        if (text) headers.push(text);
                    });
                }
                if (headers.length === 0) return;

                // Get body rows
                const rows = [];
                const bodyRows = table.querySelectorAll('.el-table__body tr.el-table__row');
                bodyRows.forEach(tr => {
                    const cells = tr.querySelectorAll('td .cell');
                    const targetCells = cells.length > 0 ? cells : tr.querySelectorAll('td');
                    const rowData = {};

                    targetCells.forEach((td, i) => {
                        let text = td.innerText.trim();
                        if (!text) return;
                        const header = headers[i] || 'col_' + i;

                        // Try to find work order ID in this cell
                        const match = text.match(woIdPattern);
                        if (match && !rowData['_workorder_id']) {
                            rowData['_workorder_id'] = match[0];
                            // Remove the ID from stored value to avoid duplication
                            text = text.replace(match[0], '').trim();
                        }
                        if (text) rowData[header] = text;
                    });

                    // Also check buttons
                    const buttons = tr.querySelectorAll('button.el-button--text, a');
                    buttons.forEach(btn => {
                        const text = btn.innerText.trim();
                        const match = text.match(woIdPattern);
                        if (match) {
                            rowData['_workorder_id'] = match[0];
                        }
                    });

                    // Only add if it looks like a work order row (has data, not just action buttons)
                    const allText = Object.values(rowData).join(' ');
                    if (allText.length > 5 && !/^(接单|转单|填写工时)/.test(allText)) {
                        rows.push(rowData);
                    }
                });

                if (rows.length > 0) {
                    result.tables.push({headers: headers, rows: rows, visible: true});
                }
            });

            return JSON.stringify(result);
        }''')

        data = json.loads(result_json)

        if 'rawText' in data and not data.get('tables'):
            logger.warning("未找到Element UI表格，尝试从文本解析")
            return self._extract_workorder_list_from_text(data['rawText'])

        # Merge all tables
        all_workorders = []
        for table in data.get('tables', []):
            rows = table.get('rows', [])
            logger.info(f"表格 [{table.get('headers', [])}] : {len(rows)} 行")
            all_workorders.extend(rows)

        logger.info(f"当前页共提取到 {len(all_workorders)} 个工单")
        return all_workorders

    def _extract_workorder_list_from_text(self, page_text: str) -> list:
        """备用方案：从页面纯文本解析工单编号列表"""

        pattern = re.compile(r'(\d{8}[A-Z]?\d{4,})')
        matches = pattern.findall(page_text)
        seen = set()
        workorders = []
        for m in matches:
            if m not in seen:
                seen.add(m)
                workorders.append({'工单编号': m, '_workorder_id': m})
        logger.info(f"从文本中提取到 {len(workorders)} 个工单编号")
        return workorders

    def get_total_pages(self) -> int:
        """获取工单列表的总页数"""
        try:
            page_items = self.page.locator('.el-pager li').all()
            if page_items:
                last_page = page_items[-1].inner_text().strip()
                if last_page.isdigit():
                    return int(last_page)
            return 1
        except Exception:
            return 1

    def go_to_next_page(self) -> bool:
        """翻到下一页，成功返回True"""
        try:
            next_btn = self.page.locator('button.btn-next:not(.disabled)')
            if next_btn.count() > 0:
                next_btn.first.click()
                time.sleep(1.5)
                return True
            return False
        except Exception:
            return False

    def _get_workorder_ids_from_list(self) -> list:
        """从列表页获取所有工单编号"""
        wo_ids_json = self.page.evaluate('''() => {
            const ids = [];
            const seen = new Set();
            const pattern = /\d{8}[A-Z]?\d{4,}/i;

            const elements = document.querySelectorAll('button, a, span, td');
            elements.forEach(el => {
                const text = (el.innerText || '').trim();
                const match = text.match(pattern);
                if (match && !seen.has(match[0])) {
                    seen.add(match[0]);
                    ids.push(match[0]);
                }
            });

            return JSON.stringify(ids);
        }''')

        wo_ids = json.loads(wo_ids_json)
        # Filter out timestamps (14+ pure digits = timestamp, not work order ID)
        filtered = [i for i in wo_ids if not (len(i) >= 14 and i.isdigit())]
        logger.info(f"找到 {len(filtered)} 个工单编号: {filtered}")
        return filtered

    def _go_back_to_list(self):
        """关闭详情页，返回工单列表页"""
        list_page = None
        for p in self.context.pages:
            if "MyAgentTicket" in p.url and "DetailWorkOrder" not in p.url:
                list_page = p
                break

        if "DetailWorkOrder" in self.page.url:
            self.page.close()
            logger.info("已关闭详情页")

        if list_page:
            self.page = list_page
            self.page.bring_to_front()
            time.sleep(0.5)
            return True

        # Fallback: just navigate back in the same page
        logger.warning("未找到列表页，尝试返回")
        return False

    def extract_batch_workorders(self, wo_ids: list = None, max_count: int = None) -> list:
        """批量提取所有工单的详细信息（逐个点开详情页面）"""
        if wo_ids is None:
            wo_ids = self._get_workorder_ids_from_list()

        if not wo_ids:
            logger.warning("未找到任何工单")
            return []

        if max_count:
            wo_ids = wo_ids[:max_count]

        total = len(wo_ids)
        print(f"\n开始批量提取 {total} 个工单...")

        results = []
        for i, wo_id in enumerate(wo_ids):
            print(f"[{i+1}/{total}] {wo_id} ...", end=" ")
            logger.info(f"===== [{i+1}/{total}] 提取工单 {wo_id} =====")

            # Click into detail
            if not self.click_workorder_by_id(wo_id):
                print("失败(无法打开)")
                logger.warning(f"跳过 {wo_id}: 无法打开详情")
                self._go_back_to_list()
                continue

            # Extract detail info
            self.page.wait_for_load_state("networkidle")
            time.sleep(0.5)
            info = self.extract_workorder_info()

            progress_entries = info.get("progress_entries", [])
            latest_progress = progress_entries[0] if progress_entries else {}

            data = {
                "workorder_id": info.get("workorder_id", ""),
                "parking_id": info.get("parking_id", ""),
                "parking_name": info.get("parking_name", ""),
                "problem": info.get("problem", ""),
                "problem_image_urls": info.get("problem_image_urls", []),
                "latest_progress": latest_progress,
                "progress_attachments": info.get("progress_attachments", []),
            }

            results.append(data)
            self.save_to_file(data)
            print(f"[OK] {data['parking_name']} | {data.get('problem', '')[:30]}")

            # Go back to list for next iteration
            if i < total - 1:
                self._go_back_to_list()

        # Save all results
        all_file = STATE_DIR / "output" / f"all_workorders_{int(time.time())}.json"
        all_file.parent.mkdir(exist_ok=True)
        with open(all_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n全部 {len(results)} 个工单已保存到: {all_file}")

        return results

    def click_workorder_by_id(self, workorder_id: str):
        """根据工单编号找到并点击工单——使用expect_page可靠捕获新标签页"""
        logger.info(f"正在查找工单: {workorder_id}")
        time.sleep(0.5)

        # 清理残留的无用页面（login页、about:blank）
        for p in self.context.pages[:]:
            if "login" in p.url.lower() or p.url == "about:blank":
                try:
                    p.close()
                    logger.debug(f"已关闭残留页面: {p.url}")
                except:
                    pass

        # ---- 策略1: 通过JS在表格行中找到工单编号对应的可点击元素 ----
        clicked_element = self.page.evaluate('''(woId) => {
            const rows = document.querySelectorAll('tr.el-table__row');
            for (const row of rows) {
                if (row.innerText.includes(woId)) {
                    // 优先找 el-button--text 按钮（工单编号本身）
                    const btn = row.querySelector('button.el-button--text');
                    if (btn && btn.innerText.includes(woId)) {
                        btn.click();
                        return 'el-button--text';
                    }
                    // 其次找该行中任意包含编号的button
                    const allBtns = row.querySelectorAll('button');
                    for (const b of allBtns) {
                        if (b.innerText.includes(woId)) {
                            b.click();
                            return 'row-button';
                        }
                    }
                    // 最后点击整行
                    row.click();
                    return 'row-click';
                }
            }
            // 没找到行，尝试全局搜索
            const allBtns = document.querySelectorAll('button');
            for (const b of allBtns) {
                if (b.innerText.includes(woId)) {
                    b.click();
                    return 'global-button';
                }
            }
            return 'not-found';
        }''', workorder_id)
        logger.info(f"JS点击策略: {clicked_element}")

        if clicked_element == 'not-found':
            logger.warning(f"无法找到工单 {workorder_id} 的可点击元素")
            self.page.screenshot(path=SCREENSHOTS_DIR / f"not_found_{workorder_id}_{int(time.time())}.png")
            return False

        # ---- 等待详情页出现（新标签 or SPA导航） ----
        # 先尝试expect_page（新标签页场景）
        try:
            new_page = self.context.wait_for_event("page", timeout=15000)
            self.page = new_page
            logger.info(f"捕获新标签页: {self.page.url}")
            if "DetailWorkOrder" in self.page.url:
                time.sleep(1.5)
                self.page.screenshot(path=SCREENSHOTS_DIR / f"detail_confirm_{int(time.time())}.png")
                return True
        except Exception as e:
            logger.debug(f"未捕获到新标签页: {e}")

        # 再检查所有已打开的页面
        for p in self.context.pages:
            if "DetailWorkOrder" in p.url:
                self.page = p
                logger.info(f"从已有页面找到详情页: {self.page.url}")
                time.sleep(1.5)
                return True

        # SPA导航（当前页面URL变化）
        if "DetailWorkOrder" in self.page.url:
            logger.info(f"SPA导航到详情页: {self.page.url}")
            time.sleep(1.5)
            return True

        # ---- 仍未找到，轮询兜底（最多15秒） ----
        for attempt in range(30):
            for p in self.context.pages:
                if "DetailWorkOrder" in p.url:
                    self.page = p
                    logger.info(f"轮询找到详情页: {self.page.url}")
                    time.sleep(1.5)
                    return True
            if "DetailWorkOrder" in self.page.url:
                logger.info(f"SPA轮询到详情页: {self.page.url}")
                time.sleep(1.5)
                return True
            time.sleep(0.5)

        # ---- 失败诊断 ----
        logger.warning("未能找到详情页，当前打开的页面:")
        for p in self.context.pages:
            logger.info(f"  - {p.url}")
        self.page.screenshot(path=SCREENSHOTS_DIR / f"click_failed_{workorder_id}_{int(time.time())}.png")
        return False

    def extract_workorder_info(self) -> dict:
        """提取工单详情页的关键信息"""
        # 保存详情页截图
        detail_screenshot = SCREENSHOTS_DIR / f"workorder_detail_{int(time.time())}.png"
        self.page.screenshot(path=detail_screenshot)
        logger.info(f"详情页截图: {detail_screenshot}")

        # 等待详情页加载完成 - 增加等待时间
        time.sleep(2.5)

        # ===== 调试：查看页面关键信息 =====
        debug_info = self.page.evaluate('''() => {
            const result = {
                url: window.location.href,
                bodyText: document.body.innerText.slice(0, 5000),
                progressContainers: [],
                progressTabHTML: ''
            };

            // 查找进度描述相关的tab内容
            const tabPanes = document.querySelectorAll('[class*="tab"], [class*="pane"], [class*="progress"], [class*="flow"], [class*="step"], [class*="record"], [class*="history"], [class*="timeline"]');
            tabPanes.forEach(el => {
                const cls = el.className;
                if (typeof cls === 'string' && (cls.includes('progress') || cls.includes('flow') || cls.includes('step') || cls.includes('record') || cls.includes('history') || cls.includes('timeline') || cls.includes('pane'))) {
                    const text = el.innerText?.slice(0, 500) || '';
                    if (text.includes('操作人') || text.includes('描述') || text.includes('处理时长')) {
                        result.progressContainers.push({
                            tag: el.tagName,
                            class: cls.slice(0, 200),
                            text: text,
                            html: el.innerHTML.slice(0, 3000)
                        });
                    }
                }
            });

            // 也查找el-tabs的内容
            const tabsContent = document.querySelector('.el-tabs__content, [class*="tabs"]');
            if (tabsContent) {
                result.progressTabHTML = tabsContent.innerHTML.slice(0, 5000);
            }

            // 查找所有附件文件元素
            const allFileElems = document.querySelectorAll('[class*="file"], [class*="attach"], [class*="upload"], .el-image');
            result.fileElements = [];
            allFileElems.forEach(el => {
                const text = el.innerText?.slice(0, 200) || '';
                const img = el.querySelector('img');
                const src = img ? img.getAttribute('src') : el.getAttribute('href') || '';
                if (src || text) {
                    result.fileElements.push({
                        tag: el.tagName,
                        class: el.className?.slice?.(0, 200) || '',
                        src: src?.slice(0, 300) || '',
                        text: text
                    });
                }
            });

            return JSON.stringify(result);
        }''')

        # 保存调试信息到文件（避免编码问题）
        debug_file = STATE_DIR / "output" / f"debug_{int(time.time())}.json"
        debug_file.parent.mkdir(exist_ok=True)
        with open(debug_file, "w", encoding="utf-8") as f:
            f.write(debug_info)
        logger.info(f"调试信息已保存: {debug_file}")

        result = {}

        result["workorder_id"] = self.extract_label_value("工单编号")
        raw_parking_name = self.extract_label_value("车场名称")
        result["parking_name"] = self._clean_parking_name(raw_parking_name)
        result["parking_id"] = self.extract_label_value("车场ID")
        result["problem"] = self.extract_problem()
        result["problem_image_urls"] = self._extract_problem_image_urls()
        result["progress_entries"] = self.extract_progress_entries()
        result["progress_attachments"] = self.extract_progress_image_urls()

        return result

    def extract_label_value(self, label: str) -> str:
        """根据标签文字找到相邻的值 - 在整个页面文本中搜索"""
        try:
            # 方法1: 获取整个页面的 innerText，然后在其中搜索 "工单编号：值" 格式
            try:
                page_text = self.page.inner_text("body")
                if label + "：" in page_text:
                    idx = page_text.index(label + "：")
                    value = page_text[idx + len(label) + 1:].strip()
                    # 值可能在换行或空格处结束，取第一行
                    lines = value.split('\n')
                    value = lines[0].strip()
                    # 去掉可能的冒号前缀和"更多信息"等后缀
                    value = value.lstrip(":：").strip()
                    # 如果值包含"更多信息"等按钮文字，只取第一个空格前的内容
                    if ' ' in value and ('更多' in value or 'P' in value or '未' in value):
                        value = value.split(' ')[0].strip()
                    if value and len(value) < 50:
                        logger.info(f"提取字段成功 [{label}]: {value}")
                        return value
            except Exception as e:
                logger.warning(f"方法1(inner_text)失败: {e}")

            # 方法2: 查找包含标签的 span 元素，然后获取其父容器的文本
            try:
                elements = self.page.locator(f"span:has-text('{label}')").all()
                for elem in elements:
                    try:
                        # 获取父元素的文本
                        parent = elem.locator("xpath=..").first
                        parent_text = parent.inner_text().strip()
                        if label + "：" in parent_text:
                            idx = parent_text.index(label + "：")
                            value = parent_text[idx + len(label) + 1:].strip()
                            value = value.split('\n')[0].strip()
                            if value and len(value) < 50:
                                logger.info(f"提取字段成功 [{label}]: {value}")
                                return value
                    except:
                        continue
            except Exception as e:
                logger.warning(f"方法2(span)失败: {e}")

            # 方法3: 通用搜索 - 在所有文本中搜索
            try:
                all_elements = self.page.locator("body").inner_text()
                for line in all_elements.split('\n'):
                    if label + "：" in line:
                        value = line.split(label + "：")[1].strip()
                        if value and len(value) < 50:
                            logger.info(f"提取字段成功 [{label}]: {value}")
                            return value
            except Exception as e:
                logger.warning(f"方法3(通用搜索)失败: {e}")

            logger.warning(f"无法提取 {label} 的值")
            return ""
        except Exception as e:
            logger.warning(f"提取 {label} 失败: {e}")
            return ""

    def _clean_parking_name(self, raw: str) -> str:
        """清洗车场名称，去掉末尾的车场ID和备案号"""

        if not raw:
            return raw
        # Remove "车场ID：xxx" and "备案号：xxx" suffixes
        cleaned = re.sub(r'\s*车场ID[：:]\s*\S+', '', raw)
        cleaned = re.sub(r'\s*备案号[：:]\s*\S+', '', cleaned)
        return cleaned.strip()

    def extract_problem(self) -> str:
        """从详情页提取问题描述（使用Quill编辑器内的完整内容）"""
        try:
            # 优先从Quill编辑器提取HTML内容，保留完整文本和格式
            problem_html = self.page.evaluate('''() => {
                const editor = document.querySelector('.ql-editor');
                return editor ? editor.innerHTML : '';
            }''')
            if problem_html:
                # 提取纯文本，保留换行

                text = re.sub(r'<[^>]+>', '\n', problem_html)
                text = re.sub(r'\n{3,}', '\n\n', text).strip()
                if text:
                    logger.info(f"提取问题描述成功({len(text)}字符)")
                    return text

            # 备用：从页面文本提取
            page_text = self.page.inner_text("body")
            if "问题：" not in page_text:
                detail_card = self.page.locator(".detail-info-card").first
                if detail_card:
                    page_text = detail_card.inner_text()

            if "问题：" in page_text:
                idx = page_text.index("问题：")
                remaining = page_text[idx + len("问题："):].strip()
                end_markers = ["附件文件：", "进度描述", "客户资料", "车场基本信息", "知识库"]
                for marker in end_markers:
                    if marker in remaining:
                        remaining = remaining[:remaining.index(marker)]
                problem = remaining.strip()
                if problem:
                    logger.info(f"提取问题描述成功({len(problem)}字符)")
                    return problem

            logger.warning("未找到问题描述")
            return ""
        except Exception as e:
            logger.warning(f"提取问题描述失败: {e}")
            return ""

    def _extract_problem_image_urls(self) -> list:
        """从问题描述（Quill编辑器）中提取在线图片URL"""
        try:
            imgs = self.page.evaluate('''() => {
                const results = [];
                const editor = document.querySelector('.ql-editor');
                if (!editor) return JSON.stringify(results);
                const imgElements = editor.querySelectorAll('img');
                imgElements.forEach(img => {
                    const src = img.getAttribute('src') || '';
                    if (src && src.startsWith('http')) {
                        results.push({src: src});
                    }
                });
                return JSON.stringify(results);
            }''')
            img_list = json.loads(imgs)
            logger.info(f"问题描述中找到 {len(img_list)} 个图片URL")
            return img_list
        except Exception as e:
            logger.warning(f"提取问题图片URL失败: {e}")
            return []

    def _click_progress_tab(self) -> bool:
        """点击进度描述标签页，只执行一次，后续调用复用状态"""
        try:
            tab = self.page.locator("span:has-text('进度描述')").first
            if tab.is_visible():
                tab.click()
                logger.info("已点击进度描述标签页")
                time.sleep(0.5)
                return True
        except Exception as e:
            logger.debug(f"点击进度描述标签页失败: {e}")
        return False

    def extract_progress_entries(self) -> list:
        """从详情页提取所有进度描述条目"""
        try:
            self._click_progress_tab()
            page_text = self.page.inner_text("body")

            # 定位进度描述区域: "进度描述" 到 "客户资料" 之间
            start_markers = ["进度描述回访记录评价支援记录催单记录", "进度描述"]
            end_markers = ["客户资料", "知识库", "车场基本信息"]

            start_idx = -1
            for marker in start_markers:
                idx = page_text.find(marker)
                if idx != -1:
                    start_idx = idx + len(marker)
                    break

            if start_idx == -1:
                logger.warning("未找到进度描述区域")
                return []

            end_idx = len(page_text)
            for marker in end_markers:
                idx = page_text.find(marker, start_idx)
                if idx != -1:
                    end_idx = idx
                    break

            progress_text = page_text[start_idx:end_idx].strip()
            logger.info(f"进度描述文本长度: {len(progress_text)}")

            # 找到所有时间戳位置，提取条目内容（不依赖前后\n）
            ts_pattern = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
            matches = list(ts_pattern.finditer(progress_text))

            entries = []
            for i, match in enumerate(matches):
                ts = match.group(0)
                content_start = match.end()
                content_end = matches[i + 1].start() if i + 1 < len(matches) else len(progress_text)
                content = progress_text[content_start:content_end].strip()

                entry = {"timestamp": ts}

                # 提取各字段
                field_patterns = [
                    ("operator", r'操作人：\s*\n(.+?)(?:\n|$)'),
                    ("duration", r'处理时长：\s*\n(.+?)(?:\n|$)'),
                    ("location_status", r'定位异常.*?：\s*\n?([^\n]+)'),
                    ("description", r'描述：\s*\n(.+?)(?=\n(?:备注|附件文件|操作人|处理时长|$)|\Z)'),
                    ("notes", r'备注：\s*\n?(.+?)(?=\n(?:附件文件|$)|\Z)'),
                ]

                for key, pattern in field_patterns:
                    m = re.search(pattern, content, re.DOTALL)
                    if m:
                        val = m.group(1).strip()
                        if val and val not in ('', 'null', '无'):
                            entry[key] = val

                # 检查是否有附件标记
                entry["has_attachment"] = "附件文件：" in content
                entries.append(entry)

            logger.info(f"提取到 {len(entries)} 条进度描述")
            return entries

        except Exception as e:
            logger.warning(f"提取进度描述失败: {e}")
            return []

    def extract_progress_image_urls(self) -> list:
        """提取进度描述区域的所有附件图片在线URL（不下载到本地）

        注意：Tab已在 extract_progress_entries 中点击过，此处不再重复点击。
        """
        attachments = []
        try:
            time.sleep(0.5)

            imgs = self.page.evaluate('''() => {
                const results = [];
                const allImgs = document.querySelectorAll('img');
                allImgs.forEach(img => {
                    const src = img.getAttribute('src') || '';
                    const alt = img.getAttribute('alt') || '';
                    if (src && src.startsWith('http')
                        && !src.includes('icon') && !src.includes('logo')
                        && !src.includes('svg') && !src.includes('no-handle-order')
                        && (src.includes('saas-obs') || src.includes('upload') || src.includes('temp')
                            || /\.(jpe?g|png|gif|bmp|webp)$/i.test(src))) {
                        const rect = img.getBoundingClientRect();
                        if (rect.width > 50 && rect.height > 30) {
                            results.push({src: src, alt: alt});
                        }
                    }
                });
                return JSON.stringify(results);
            }''')

            img_list = json.loads(imgs)
            logger.info(f"找到 {len(img_list)} 个附件图片在线URL")
            for item in img_list:
                attachments.append({"src": item.get("src", ""), "alt": item.get("alt", "")})

        except Exception as e:
            logger.warning(f"获取进度附件URL失败: {e}")

        return attachments

    def extract_extra_image_urls(self, skip_urls: set = None) -> list:
        """提取页面中不在进度附件中的其他图片URL（不下载到本地）"""
        skip_urls = skip_urls or set()
        urls = []
        img_elements = self.page.locator("img[src], .content img, [class*='image'] img").all()

        for img in img_elements:
            try:
                src = img.get_attribute("src") or ""
                if not src or src.startswith("data:") or src in skip_urls:
                    continue
                if any(k in src.lower() for k in ['icon', 'logo', 'svg', 'no-handle-order']):
                    continue
                urls.append(src)
            except Exception as e:
                logger.warning(f"提取图片URL失败: {e}")

        return urls

    def extract_all(self, workorder_id: str = None) -> dict:
        """提取指定工单的信息"""
        self.ensure_logged_in()

        # 1. 导航到工单列表页
        self.navigate_to_workorder_list()

        # 2. 先尝试直接在当前页查找工单，找不到再输入编号查询
        if workorder_id:
            logger.info(f"先尝试在当前页直接查找工单: {workorder_id}")
            found = self.click_workorder_by_id(workorder_id)
            if not found:
                logger.info(f"当前页未找到工单 {workorder_id}，输入编号查询")
                self.search_workorder_by_id(workorder_id)
                self.click_workorder_by_id(workorder_id)
        else:
            # 如果没有指定工单编号，点击列表中第一个
            self.click_workorder_by_id("20260425K57602")  # 默认第一个

        # 等待页面加载
        self.page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        # 3. 提取关键信息
        info = self.extract_workorder_info()

        # 收集已提取的进度附件URL，避免重复
        progress_urls = {a.get("src", "") for a in info.get("progress_attachments", [])}

        result = {
            "workorder_id": info.get("workorder_id", ""),
            "parking_name": info.get("parking_name", ""),
            "parking_id": info.get("parking_id", ""),
            "problem": info.get("problem", ""),
            "problem_image_urls": info.get("problem_image_urls", []),
            "progress_entries": info.get("progress_entries", []),
            "progress_attachments": info.get("progress_attachments", []),
            "extract_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "url": self.page.url
        }

        logger.info(f"提取完成: 工单{result['workorder_id']} - {result['parking_name']}")
        return result

    def copy_to_clipboard(self, text: str):
        """复制文本到剪贴板"""
        try:
            self.page.keyboard.type(text)
            logger.info("已复制内容（模拟键盘输入）")
        except Exception as e:
            logger.warning(f"复制失败: {e}")

    def save_to_file(self, data: dict, filename: str = None):
        """保存工单数据到文件"""
        if filename is None:
            filename = f"workorder_{data['workorder_id']}_{int(time.time())}.json"

        output_path = STATE_DIR / "output" / filename
        output_path.parent.mkdir(exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"数据已保存到: {output_path}")
        return output_path


# -------------------------- 主流程 --------------------------
def cleanup_old_files():
    """清理旧的截图和调试文件"""
    import shutil
    if SCREENSHOTS_DIR.exists():
        shutil.rmtree(SCREENSHOTS_DIR)
        SCREENSHOTS_DIR.mkdir(exist_ok=True)
        logger.info(f"已清理截图目录: {SCREENSHOTS_DIR}")

    output_dir = STATE_DIR / "output"
    for f in output_dir.glob("debug_*.json"):
        f.unlink()
        logger.debug(f"已删除调试文件: {f.name}")


def main(workorder_id: str = None, batch: bool = False):
    """提取工单信息：先列所有工单，再根据输入提取详情

    Args:
        workorder_id: 指定工单编号（单个提取）
        batch: True=批量提取所有工单详情
    """
    cleanup_old_files()
    extractor = WorkOrderExtractor()

    try:
        extractor.launch_browser(headless=False)

        # 1. 登录并导航到工单列表
        extractor.ensure_logged_in()
        extractor.navigate_to_workorder_list()

        # 2. 获取工单编号列表
        wo_ids = extractor._get_workorder_ids_from_list()
        if not wo_ids:
            print("未能获取到任何工单编号")
            return None

        # --batch 模式：批量提取所有工单详情
        if batch:
            return extractor.extract_batch_workorders(wo_ids)

        # 单个工单模式：直接提取指定工单
        if workorder_id:
            print(f"\n提取工单: {workorder_id}")
            result = _extract_detail(extractor, workorder_id)
            if result is None:
                print(f"未找到工单 {workorder_id}，可用工单: {wo_ids}")
            return result

        # 3. 交互模式：展示列表，等待输入
        print(f"\n工单列表 (共 {len(wo_ids)} 个):")
        print("-" * 50)
        for i, wid in enumerate(wo_ids):
            print(f"  [{i+1:2d}] {wid}")
        print("-" * 50)
        print("使用: python workorder_extractor.py <工单编号>  查看详情")
        print("使用: python workorder_extractor.py --batch       批量提取全部\n")

        try:
            choice = input("请输入要查看详情的工单编号 (直接回车退出): ").strip().upper()
        except (EOFError, OSError):
            choice = ""

        if choice:
            return _extract_detail(extractor, choice)

        return wo_ids

    except Exception as e:
        logger.error(f"提取失败: {e}")
        raise
    finally:
        extractor.close()


def _extract_detail(extractor: WorkOrderExtractor, workorder_id: str) -> dict:
    """提取单个工单的详细信息"""
    print(f"\n正在提取工单 {workorder_id} 的详情...")

    if not extractor.click_workorder_by_id(workorder_id):
        print(f"未找到工单 {workorder_id}")
        return None

    extractor.page.wait_for_load_state("networkidle")
    time.sleep(0.5)
    info = extractor.extract_workorder_info()

    progress_entries = info.get("progress_entries", [])
    latest_progress = progress_entries[0] if progress_entries else {}

    data = {
        "workorder_id": info.get("workorder_id", ""),
        "parking_id": info.get("parking_id", ""),
        "parking_name": info.get("parking_name", ""),
        "problem": info.get("problem", ""),
        "problem_image_urls": info.get("problem_image_urls", []),
        "latest_progress": latest_progress,
        "progress_attachments": info.get("progress_attachments", []),
    }

    # 输出到控制台
    print("\n" + "=" * 50)
    print(f"工单编号: {data['workorder_id']}")
    print(f"车场名称: {data['parking_name']}")
    print(f"车场ID: {data['parking_id']}")
    problem = data.get('problem', '')
    if len(problem) > 80:
        print(f"问题描述: {problem[:80]}...")
    else:
        print(f"问题描述: {problem}")
    lp = data.get('latest_progress', {})
    print(f"最新进度: {lp.get('timestamp','')} | {lp.get('description','')[:50]}")
    print(f"附件数量: {len(data.get('progress_attachments', [])) + len(data.get('problem_image_urls', []))}")
    print("=" * 50)

    extractor.save_to_file(data)

    clipboard_text = (
        f"工单编号: {data['workorder_id']}\n"
        f"车场名称: {data['parking_name']}\n"
        f"车场ID: {data['parking_id']}\n"
        f"问题描述: {data.get('problem', '')}"
    )
    extractor.copy_to_clipboard(clipboard_text)

    return data


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    batch = "--batch" in args
    wo_id = None
    for a in args:
        if not a.startswith("--"):
            wo_id = a.upper()
            break

    if batch:
        print("批量模式：提取所有工单详情\n")
    elif wo_id:
        print(f"目标工单: {wo_id}")
    else:
        print("交互模式：先列出所有工单，再选择查看详情")
    main(workorder_id=wo_id, batch=batch)