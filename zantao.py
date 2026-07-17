"""
禅道 API 客户端 — 支持两种认证方式：
  1. RESTful API v1 (Token 认证) — 推荐
  2. 模拟浏览器 HTML 表单登录 — 兼容旧版本
"""

import json
import hashlib
import re
import time
from urllib.parse import unquote, urlparse

import requests
from config_loader import ZENTAO_URL, ACCOUNT

# 图片扩展名 → MIME 类型映射（供文件上传和附件预览使用）
MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


class ZenTaoClient:
    """禅道客户端"""

    def __init__(self, base_url: str = ZENTAO_URL):
        self.base_url = base_url
        self.session = requests.Session()
        self.token: str = ""
        self.logged_in = False

    # ======================== RESTful API v1 (Token 认证) ========================

    def get_token(self, account: str, password: str) -> str:
        """通过 RESTful API v1 获取 Token"""
        print(f"[REST] 获取 Token (account={account})...")
        r = self.session.post(
            f"{self.base_url}/api.php/v1/tokens",
            json={"account": account, "password": password},
        )
        print(f"    响应: {r.status_code} | {r.text[:300]}")
        try:
            data = r.json()
            # v1 直接返回 {"token":"xxx"}，不带 status 字段
            token = data.get("token", "")
            if token:
                self.token = token
                self.logged_in = True
                print(f"    Token 获取成功: {token[:16]}...")
                return self.token
            else:
                reason = data.get("error", data.get("message", str(data)))
                print(f"    Token获取失败: {reason}")
                return ""
        except json.JSONDecodeError:
            print(f"    响应非JSON: {r.text[:300]}")
            return ""

    def _rest_get(self, path: str, params: dict = None) -> dict:
        """RESTful API v1 GET 请求"""
        url = f"{self.base_url}/api.php/v1/{path}"
        r = self.session.get(url, params=params, headers={"Token": self.token})
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"_raw": r.text[:500]}

    def _rest_post(self, path: str, data: dict) -> dict:
        """RESTful API v1 POST 请求 (JSON)"""
        url = f"{self.base_url}/api.php/v1/{path}"
        r = self.session.post(url, json=data, headers={"Token": self.token})
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"_raw": r.text[:500]}

    def _rest_post_form(self, path: str, data: dict) -> dict:
        """RESTful API v1 POST 请求 (form-encoded, 用于某些自定义 entry)"""
        url = f"{self.base_url}/api.php/v1/{path}"
        r = self.session.post(url, data=data, headers={"Token": self.token})
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"_raw": r.text[:500]}

    def _rest_post_multipart(self, path: str, data: dict, files: list = None) -> dict:
        """RESTful API v1 POST 请求 (multipart/form-data, 用于带文件上传的请求)

        Args:
            data: 普通表单字段
            files: 文件列表，格式 [("files[]", (filename, bytes_or_fileobj, mime_type)), ...]
        """
        url = f"{self.base_url}/api.php/v1/{path}"
        r = self.session.post(url, data=data, files=files or [],
                              headers={"Token": self.token})
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"_raw": r.text[:500]}

    def _rest_put(self, path: str, data: dict) -> dict:
        """RESTful API v1 PUT 请求 (JSON)"""
        url = f"{self.base_url}/api.php/v1/{path}"
        r = self.session.put(url, json=data, headers={"Token": self.token})
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"_raw": r.text[:500]}

    def _rest_put_form(self, path: str, data: dict) -> dict:
        """RESTful API v1 PUT 请求 (form-urlencoded)"""
        url = f"{self.base_url}/api.php/v1/{path}"
        form = {k: ("" if v is None else str(v)) for k, v in data.items()}
        r = self.session.put(url, data=form, headers={"Token": self.token})
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"_raw": r.text[:500]}

    def _rest_put_multipart(self, path: str, data: dict) -> dict:
        """RESTful API v1 PUT 请求 (multipart/form-data，与创建任务相同编码)"""
        url = f"{self.base_url}/api.php/v1/{path}"
        files = [(k, (None, "" if v is None else str(v))) for k, v in data.items()]
        r = self.session.put(url, files=files, headers={"Token": self.token})
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"_raw": r.text[:500]}

    @staticmethod
    def _rest_failed(result: dict) -> bool:
        if not isinstance(result, dict):
            return True
        return bool(result.get("error") or result.get("_raw"))

    def get_task_rest(self, task_id: int) -> dict:
        return self._rest_get(f"tasks/{task_id}")

    def verify_task_assigned(self, task_id: int, assigned_to: str) -> bool:
        if not assigned_to:
            return False
        task = self.get_task_rest(task_id)
        if not isinstance(task, dict) or task.get("error"):
            return False
        at = task.get("assignedTo")
        if isinstance(at, dict):
            return at.get("account") == assigned_to
        return at == assigned_to

    def upload_file_rest(self, filepath: str) -> dict:
        """上传图片到禅道文件库，供正文 <img> 内联引用。

        REST v1 POST /files 底层调用 file::ajaxUpload，文件字段必须为 imgFile。
        失败时回退 Session 接口 file-ajaxUpload.html。
        """
        import os

        if not os.path.isfile(filepath):
            return {"error": "file_not_found", "path": filepath}

        filename = os.path.basename(filepath)
        ext = os.path.splitext(filepath)[1].lower()
        mime = MIME_MAP.get(ext, "image/png")

        print(f"    File: {filename} ({os.path.getsize(filepath)}B, mime={mime})")

        result = self._upload_image_rest(filepath, filename, mime)
        if isinstance(result, dict) and result.get("id"):
            return result

        print(f"    [REST imgFile] 失败: {result}")
        result = self._upload_image_session(filepath, filename, mime)
        if isinstance(result, dict) and result.get("id"):
            return result

        print(f"    [Session ajaxUpload] 失败: {result}")
        return result if isinstance(result, dict) else {"error": "all_attempts_failed"}

    def _upload_image_rest(self, filepath: str, filename: str, mime: str) -> dict:
        """REST v1: POST /api.php/v1/files，字段 imgFile"""
        url = f"{self.base_url}/api.php/v1/files"
        print(f"    URL: {url}")
        print(f"    Token: {self.token[:16]}...")
        try:
            with open(filepath, "rb") as fh:
                files = [("imgFile", (filename, fh, mime))]
                result = self._rest_post_multipart("files", {}, files)
            print(f"    [REST imgFile] {json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)[:200]}")
            return self._normalize_upload_result(result)
        except Exception as e:
            print(f"    [REST imgFile] 异常: {e}")
            return {"error": str(e)}

    def _upload_image_session(self, filepath: str, filename: str, mime: str) -> dict:
        """Session: POST /file-ajaxUpload.html，字段 imgFile（富文本编辑器同款）"""
        url = f"{self.base_url}/file-ajaxUpload.html"
        print(f"    URL: {url} (Session)")
        try:
            with open(filepath, "rb") as fh:
                r = self.session.post(
                    url,
                    files={"imgFile": (filename, fh, mime)},
                    headers={"Referer": url},
                )
            print(f"    [Session ajaxUpload] status={r.status_code} body={r.text[:200]}")
            try:
                data = r.json()
            except json.JSONDecodeError:
                return {"error": "invalid_json", "_raw": r.text[:300]}
            return self._normalize_upload_result(data)
        except Exception as e:
            print(f"    [Session ajaxUpload] 异常: {e}")
            return {"error": str(e)}

    @staticmethod
    def _normalize_upload_result(data: dict) -> dict:
        """统一 REST / Session 上传响应为 {id, url, extension}"""
        if not isinstance(data, dict):
            return {"error": "invalid_response"}
        if data.get("error") and not data.get("id"):
            return data
        # REST 直接返回 {id, url}
        file_id = data.get("id")
        url = data.get("url", "")
        # Session 旧格式 {error: 0, url: ...} 或嵌套 data
        if not file_id and data.get("error") == 0 and url:
            m = re.search(r"file-read-(\d+)", url)
            if m:
                file_id = int(m.group(1))
        nested = data.get("data")
        if not file_id and isinstance(nested, dict):
            file_id = nested.get("id")
            url = url or nested.get("url", "")
        if file_id:
            ext = ""
            if url:
                m = re.search(r"file-read-\d+\.(\w+)", url)
                if m:
                    ext = f".{m.group(1)}"
            return {"id": file_id, "url": url, "extension": ext}
        return data

    def build_file_read_url(self, upload_result: dict, fallback_ext: str = ".png") -> str:
        """根据文件上传结果构造正文内联引用的 file-read URL"""
        url = upload_result.get("url", "")
        if url:
            if url.startswith("/"):
                parsed_base = urlparse(self.base_url)
                return f"{parsed_base.scheme}://{parsed_base.netloc}{url}"
            if url.startswith("http"):
                return url

        file_id = upload_result.get("id")
        if not file_id:
            return ""
        ext = upload_result.get("extension") or fallback_ext
        if ext and not str(ext).startswith("."):
            ext = f".{ext}"
        return f"{self.base_url}/file-read-{file_id}{ext}"

    def get_products_rest(self) -> list:
        """通过 REST API v1 获取产品列表"""
        data = self._rest_get("products")
        return self._parse_list(data, "products")

    def get_projects_rest(self) -> list:
        """通过 REST API v1 获取项目列表"""
        data = self._rest_get("projects")
        return self._parse_list(data, "projects")

    def get_executions_rest(self, project_id: int = None) -> list:
        """通过 REST API v1 获取执行列表"""
        params = {"projectID": project_id} if project_id else None
        data = self._rest_get("executions", params=params)
        return self._parse_list(data, "executions")

    def _parse_list(self, data: dict, key: str) -> list:
        """解析 REST API 返回的列表数据"""
        if not isinstance(data, dict):
            return []
        items = data.get(key, [])
        if isinstance(items, list):
            return [{"id": it.get("id"), "name": it.get("name")}
                    for it in items if isinstance(it, dict)]
        return []

    # TypeOf 映射: task_type 字符串 → 数字代码

    def create_task_rest(self, execution_id: int, name: str, desc: str = "",
                         assigned_to: str = None, priority: int = 3,
                         task_type: int = 8, estimate: float = 0,
                         est_started: str = "", deadline: str = "",
                         module_id: str = None, story_id: int = None,
                         belong_no: str = "", product_l: str = "",
                         bus_category: str = "", non_number: str = "",
                         iteration: str = "", mode: str = "linear",
                         image_paths: list = None,
                         type_of: int = None) -> dict:
        """通过 REST API v1 创建任务（multipart/form-data，支持图片附件上传）

        v1 接口: POST /api.php/v1/executions/{id}/tasks (multipart)
        支持文件上传: image_paths 参数传入本地图片路径列表，以 files[] 字段上传
        """
        import time
        import os

        data = {
            "mode": mode,
            "execution": str(execution_id),
            "type": task_type,
            "assignedTo": assigned_to or "",
            "name": name,
            "estStarted": est_started or time.strftime("%Y-%m-%d"),
            "deadline": deadline or time.strftime("%Y-%m-%d"),
            "pri": str(priority),
            "estimate": str(estimate) if estimate else "",
            "desc": desc,
            "uid": f"{int(time.time() * 1000):013d}",
            "after": "toTaskList",
            "TypeOf": str(type_of),
            "BelongNO": belong_no or f"API-{int(time.time())}",
            "estimateHour": "0",
            "ProductL": product_l,
            "BusCategory[]": bus_category,
            "NonNumber": non_number or "",
            "iteration": iteration or "",
            "keywords": "",
            "status": "wait",
            "consumed": "0",
            "left": str(estimate) if estimate else "0",
            "color": "",
            "mailto[]": "",
            "contactList": "",
        }
        if module_id:
            data["module"] = str(module_id)
        if story_id:
            data["story"] = str(story_id)

        # 构建文件上传列表
        files = []
        if image_paths:
            for img_path in image_paths:
                if os.path.isfile(img_path):
                    ext = os.path.splitext(img_path)[1].lower()
                    mime = MIME_MAP.get(ext, "image/png")
                    filename = os.path.basename(img_path)
                    files.append(
                        ("files[]", (filename, open(img_path, "rb"), mime))
                    )

        print(f"[create_task_rest] 请求参数: {json.dumps(data, ensure_ascii=False, indent=2)}")
        try:
            result = self._rest_post_multipart(
                f"executions/{execution_id}/tasks", data, files
            )
            print(f"[create_task_rest] 响应: {json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)[:500]}")
            return result
        finally:
            # 关闭所有打开的文件句柄
            for _, (_, fh, _) in files:
                fh.close()

    def update_task_rest(self, task_id: int, execution_id: int, name: str,
                         desc: str = "", assigned_to: str = None, priority: int = 3,
                         task_type: int = 8, estimate: float = 0,
                         est_started: str = "", deadline: str = "",
                         module_id: int = None, story_id: int = None,
                         belong_no: str = "", product_l: str = "",
                         bus_category: str = "", type_of: int = None,
                         status: str = "wait") -> dict:
        """通过 REST API v1 修改任务 — 使用与 create_task_rest 相同的 multipart 字段集"""
        type_of_val = type_of if type_of is not None else 8
        data = {
            "mode": "linear",
            "execution": str(execution_id),
            "type": str(task_type),
            "assignedTo": assigned_to or "",
            "name": name,
            "estStarted": est_started or time.strftime("%Y-%m-%d"),
            "deadline": deadline or time.strftime("%Y-%m-%d"),
            "pri": str(priority),
            "estimate": str(estimate) if estimate else "",
            "desc": desc,
            "status": status,
            "consumed": "0",
            "left": str(estimate) if estimate else "0",
            "TypeOf": str(type_of_val),
            "BelongNO": belong_no or "",
            "estimateHour": "0",
            "ProductL": product_l or "",
            "BusCategory[]": bus_category or "",
            "keywords": "",
            "mailto[]": "",
            "contactList": "",
        }
        if module_id is not None:
            data["module"] = str(module_id)
        if story_id:
            data["story"] = str(story_id)

        path = f"tasks/{task_id}"
        print(f"[update_task_rest] multipart PUT {self.base_url}/api.php/v1/{path}")
        print(f"  assignedTo={data.get('assignedTo')}, module={data.get('module')}, "
              f"TypeOf={data.get('TypeOf')}, BelongNO={data.get('BelongNO')}")

        result = self._rest_put_multipart(path, data)
        # 本环境 tasks/{id} 不支持 POST，勿回退 POST（会触发 taskEntry::post Fatal error）

        print(f"[update_task_rest] 响应: "
              f"{json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else str(result)[:500]}")
        return result

    @staticmethod
    def _session_response_ok(text: str, url: str) -> bool:
        if "不能为空" in text or "Fatal error" in text:
            return False
        if "task-view" in (url or "") or "task-history" in (url or ""):
            return True
        try:
            parts = json.loads(text)
            blob = json.dumps(parts, ensure_ascii=False)
            return "success" in blob or "保存成功" in blob
        except (json.JSONDecodeError, TypeError):
            return "保存成功" in text or "result\":\"success" in text

    def _load_task_edit_form(self, task_id: int) -> dict:
        """从任务编辑页加载完整表单字段"""
        edit_url = f"{self.base_url}/task-edit-{task_id}.html"
        form_data = {}
        r_get = self.session.get(edit_url)
        if r_get.status_code == 200:
            self._parse_form_fields(r_get.text, form_data)
        if len(form_data) < 10:
            try:
                r_zin = self.session.get(f"{edit_url}?zin=1")
                zin_parts = json.loads(r_zin.text)
                for part in zin_parts:
                    if part.get("name") == "data" and part.get("data"):
                        for pair in part["data"].split("&"):
                            if "=" in pair:
                                key, value = pair.split("=", 1)
                                form_data[key] = unquote(value)
                        break
                    if part.get("name") == "main" and part.get("data"):
                        self._parse_form_fields(part["data"], form_data)
            except (json.JSONDecodeError, TypeError):
                pass
        form_data.setdefault("id", str(task_id))
        return form_data

    def assign_task_session(self, task_id: int, assigned_to: str) -> dict:
        """仅指派任务（专用指派页，字段少、成功率高）"""
        if not assigned_to:
            return {"success": False, "task_id": task_id}
        url = f"{self.base_url}/task-assignTo-{task_id}.html"
        print(f"[assign_task_session] GET: {url}")
        form_data = {"assignedTo": assigned_to, "id": str(task_id)}
        r_get = self.session.get(url)
        if r_get.status_code == 200:
            self._parse_form_fields(r_get.text, form_data)
        form_data["assignedTo"] = assigned_to
        print(f"[assign_task_session] POST assignedTo={assigned_to}")
        r = self.session.post(
            url,
            data=form_data,
            headers={"Referer": url},
            allow_redirects=True,
        )
        ok = self._session_response_ok(r.text, r.url) or self.verify_task_assigned(task_id, assigned_to)
        print(f"[assign_task_session] status={r.status_code}, url={r.url}, ok={ok}")
        return {"status_code": r.status_code, "task_id": task_id, "success": ok}

    def update_task_session(self, task_id: int, execution_id: int,
                            module_id: int = None, assigned_to: str = None,
                            type_of: int = 8, belong_no: str = "",
                            non_number: str = "", product_l: str = "",
                            bus_category: str = "", task_type: int = 6,
                            desc: str = "") -> dict:
        """通过 Session 编辑页 POST 补设 module/assignedTo（须传入 desc 避免覆盖为空）"""
        edit_url = f"{self.base_url}/task-edit-{task_id}.html"
        form_data = self._load_task_edit_form(task_id)

        form_data["execution"] = str(execution_id)
        form_data["type"] = str(task_type)
        form_data.setdefault("status", "wait")
        form_data.setdefault("pri", "3")
        form_data["TypeOf"] = str(type_of)
        form_data["BelongNO"] = belong_no or form_data.get("BelongNO", "")
        if non_number:
            form_data["NonNumber"] = non_number
        if product_l:
            form_data["ProductL"] = product_l
        if bus_category:
            form_data["BusCategory[]"] = bus_category
        if module_id is not None:
            form_data["module"] = str(module_id)
        if assigned_to:
            form_data["assignedTo"] = assigned_to
        # 富文本编辑器在 HTML 中常为空，必须用创建时的 desc 回填，否则会把描述清空
        if desc:
            form_data["desc"] = desc
        # 预计开始 / 截止日期：表单解析常为空，默认当天
        today = time.strftime("%Y-%m-%d")
        form_data["estStarted"] = today
        form_data["deadline"] = today

        print(f"[update_task_session] POST: module={form_data.get('module')}, "
              f"estStarted={today}, deadline={today}, "
              f"assignedTo={form_data.get('assignedTo')}, NonNumber={form_data.get('NonNumber', '')}, "
              f"TypeOf={form_data.get('TypeOf')}, BelongNO={form_data.get('BelongNO')}, "
              f"字段数={len(form_data)}")

        r = self.session.post(
            edit_url,
            data=form_data,
            headers={"Referer": edit_url},
            allow_redirects=True,
        )
        ok = self._session_response_ok(r.text, r.url)
        if not ok and assigned_to:
            print("[update_task_session] 编辑页保存未确认，尝试指派页...")
            return self.assign_task_session(task_id, assigned_to)
        if assigned_to and not self.verify_task_assigned(task_id, assigned_to):
            print("[update_task_session] REST 校验指派未生效，尝试指派页...")
            return self.assign_task_session(task_id, assigned_to)
        print(f"[update_task_session] status={r.status_code}, url={r.url}, ok={ok}")
        return {"status_code": r.status_code, "task_id": task_id, "success": ok}

    @staticmethod
    def _parse_form_fields(html: str, form_data: dict):
        """从 HTML 中提取表单字段（input/select/textarea）"""
        for m in re.finditer(r'<input[^>]+>', html):
            tag = m.group(0)
            name_m = re.search(r'\bname="([^"]+)"', tag)
            if not name_m:
                continue
            name = name_m.group(1)
            type_m = re.search(r'\btype="([^"]+)"', tag)
            if type_m and type_m.group(1) in ("submit", "button", "checkbox", "radio"):
                continue
            value_m = re.search(r'\bvalue="([^"]*)"', tag)
            if name not in form_data:
                form_data[name] = value_m.group(1) if value_m else ""

        for m in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.DOTALL):
            sel_name = m.group(1)
            sel_content = m.group(2)
            opt_m = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', sel_content)
            if not opt_m:
                opt_m = re.search(r'<option[^>]*value="([^"]*)"', sel_content)
            if opt_m and sel_name not in form_data:
                form_data[sel_name] = opt_m.group(1)

        for m in re.finditer(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', html, re.DOTALL):
            if m.group(1) not in form_data:
                form_data[m.group(1)] = m.group(2)

    # ======================== Session 认证 (HTML 表单登录) ========================

    def _get_verify_rand(self) -> str:
        r = self.session.get(f"{self.base_url}/user-refreshRandom.html")
        rand = r.text.strip()
        return rand if rand.isdigit() else ""

    def login(self, account: str, password: str) -> bool:
        """模拟浏览器 HTML 表单登录"""
        print("[Session] 获取登录页面和 verifyRand...")
        self.session.get(f"{self.base_url}/user-login.html")
        verify_rand = self._get_verify_rand()
        print(f"    verifyRand: {verify_rand}")

        pwd_md5 = hashlib.md5(password.encode()).hexdigest()
        pwd_hash = hashlib.md5((pwd_md5 + verify_rand).encode()).hexdigest()
        print(f"[Session] 提交登录表单...")

        r = self.session.post(
            f"{self.base_url}/user-login.html",
            data={
                "account": account,
                "password": pwd_hash,
                "passwordStrength": "2",
                "referer": "/zentao/",
                "verifyRand": verify_rand,
                "keepLogin": "1",
                "captcha": "",
            },
            headers={"Referer": f"{self.base_url}/user-login.html"},
            allow_redirects=False,
        )

        if r.status_code in (302, 301, 303):
            redirect_url = r.headers.get("Location", "")
            if redirect_url:
                full_url = f"{self.base_url}{redirect_url}" if redirect_url.startswith("/") else redirect_url
                r = self.session.get(full_url)

        r = self.session.get(f"{self.base_url}/product-index.html")
        is_changepwd = ('rawModule":"my"' in r.text and 'rawMethod":"changepassword"' in r.text)
        self.logged_in = True
        print(f"[Session] 登录验证: {'失败(被重定向到改密页)' if is_changepwd else '成功'}")
        return self.logged_in

    def _get_json(self, url: str) -> dict:
        r = self.session.get(url)
        try:
            outer = json.loads(r.text)
            if isinstance(outer, dict) and "data" in outer:
                try:
                    return json.loads(outer["data"])
                except (json.JSONDecodeError, TypeError):
                    return outer
            return outer
        except json.JSONDecodeError:
            return {"_raw_html": r.text[:500]}

    def _extract_list(self, data: dict, key: str) -> list:
        items = []
        source = data.get(key, data) if isinstance(data, dict) else data
        if isinstance(source, dict):
            for pid, info in source.items():
                if isinstance(info, dict) and "name" in info:
                    items.append({"id": pid, "name": info["name"]})
        elif isinstance(source, list):
            items = source
        return items

    def get_products(self) -> list:
        data = self._get_json(f"{self.base_url}/product-index.json?onlybody=yes")
        return self._extract_list(data, "products")

    def get_projects(self) -> list:
        data = self._get_json(f"{self.base_url}/project-index.json?onlybody=yes")
        return self._extract_list(data, "projects")

    def get_executions(self, project_id: int = None) -> list:
        url = f"{self.base_url}/execution-all.json?onlybody=yes"
        if project_id:
            url += f"&projectID={project_id}"
        data = self._get_json(url)
        return self._extract_list(data, "executions")

    def create_task(self, execution_id: int, name: str, desc: str = "",
                    assigned_to: str = None, priority: int = 3,
                    task_type: str = "devel", estimate: float = 0) -> dict:
        """通过旧版 JSON 接口创建任务 (Session 方式)"""
        data = {
            "name": name,
            "desc": desc,
            "assignedTo": assigned_to or ACCOUNT,
            "type": task_type,
            "pri": priority,
            "estimate": estimate,
        }
        r = self.session.post(
            f"{self.base_url}/task-create-{execution_id}-0-0.json",
            data=data,
        )
        try:
            return json.loads(r.text)
        except json.JSONDecodeError:
            return {"error": "NOT_JSON", "raw": r.text[:500]}


# ======================== 主程序 ========================
if __name__ == "__main__":
    import sys
    import os
    from getpass import getpass

    os.makedirs("output", exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "diagnose"
    client = ZenTaoClient()

    if cmd == "diagnose":
        print("=" * 50)
        print("诊断禅道 API 端点")
        print("=" * 50)

        print("\n--- RESTful API v1 ---")
        for label, url in [
            ("tokens",  f"{ZENTAO_URL}/api.php/v1/tokens"),
            ("products", f"{ZENTAO_URL}/api.php/v1/products"),
            ("projects", f"{ZENTAO_URL}/api.php/v1/projects"),
        ]:
            try:
                r = client.session.get(url, timeout=5) if "tokens" not in label else \
                     client.session.post(url, json={"account": "test", "password": "test"}, timeout=5)
                print(f"  [{r.status_code}] {label} | {r.text[:200]}")
            except Exception as e:
                print(f"  [ERR] {label}: {e}")

        print("\n--- 旧版 JSON 接口 ---")
        r = client.session.get(f"{ZENTAO_URL}/api-getsessionid.json")
        print(f"  [{r.status_code}] api-getsessionid.json")

    elif cmd == "rest-login":
        print("=" * 50)
        print("RESTful API v1 Token 登录")
        print("=" * 50)
        password = sys.argv[2] if len(sys.argv) > 2 else getpass("密码: ")
        token = client.get_token(ACCOUNT, password)
        if not token:
            sys.exit(1)

        print("\n--- 产品列表 (REST v1) ---")
        prods = client.get_products_rest()
        for p in prods:
            print(f"  [{p['id']}] {p['name']}")

        print("\n--- 项目列表 (REST v1) ---")
        projs = client.get_projects_rest()
        for p in projs:
            print(f"  [{p['id']}] {p['name']}")

        print("\n--- 执行列表 (REST v1) ---")
        execs = client.get_executions_rest()
        for e in execs[:10]:
            print(f"  [{e['id']}] {e['name']}")

    elif cmd == "rest-create":
        print("=" * 50)
        print("RESTful API v1 创建任务")
        print("=" * 50)
        password = sys.argv[2] if len(sys.argv) > 2 else getpass("密码: ")
        token = client.get_token(ACCOUNT, password)
        if not token:
            sys.exit(1)

        # 科拓非标固定用执行ID=162
        execution_id = 162
        print(f"\n执行: 分部非标 (id={execution_id})")

        result = client.create_task_rest(
            execution_id=execution_id,
            name="[REST测试] API创建任务验证",
            desc="<p>通过 RESTful API v1 创建的测试任务</p>",
            belong_no=f"API-{int(time.time())}",
        )
        print(f"\n创建结果: {json.dumps(result, ensure_ascii=False, indent=2)}")

    else:
        print(f"用法: python zantao.py [diagnose|rest-login|rest-create]")
        print(f"  diagnose    - 诊断可用API端点")
        print(f"  rest-login  - RESTful API v1 Token登录 + 列出产品/项目/执行")
        print(f"  rest-create - RESTful API v1 创建测试任务")
