"""
凭证配置加载器
- load_config()                静默加载 config.json，不存在返回空 dict
- prompt_and_save_config(cfg)  逐步提示填写缺失字段，保存到 config.json
- ensure_credentials(cfg)      凭证齐全则直接返回；缺啥就逐步补问，再保存

config.json 已加入 .gitignore，不会被推送到 Git。
如需重置凭证：删除 config.json 后重新运行即可。
"""

import getpass
import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"

# 各字段的默认值与提示语
_WORKORDER_FIELDS = [
    ("username", "账号", False),
    ("password", "密码", True),
]
_ZENTAO_FIELDS = [
    ("url",        "系统地址", False, "http://zentao.hlong.cc/zentao"),
    ("account",    "账号",     False, "dengchang"),
    ("password",   "密码",     True,  None),
    ("execution_id", "执行ID", False, "162"),
]


def load_config() -> dict:
    """静默加载 config.json；不存在返回空 dict。"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _ask(prompt: str, current: str, default: str = "", secret: bool = False) -> str:
    """单步输入：按 Enter 接受已保存值/默认值，输入新值则覆盖。"""
    if current:
        suffix = f" [***]" if secret else f" [{current}]"
    elif default:
        suffix = f" [{default}]"
    else:
        suffix = ""

    if secret:
        value = getpass.getpass(f"  {prompt}{suffix}: ").strip()
    else:
        value = input(f"  {prompt}{suffix}: ").strip()

    return value or current or default


def prompt_and_save_config(current: dict = None) -> dict:
    """逐步提示填写工单系统 + 禅道凭证，保存到 config.json。"""
    config = dict(current or {})
    config.setdefault("workorder", {})
    config.setdefault("zentao", {})

    print()
    print("=" * 60)
    print("  凭证填写（按 Enter 接受默认值或已保存值，输入新值则覆盖）")
    print("=" * 60)

    # 第 1 步：工单系统
    print("\n[第 1 步 / 共 2 步] 工单系统登录")
    for key, label, secret in _WORKORDER_FIELDS:
        config["workorder"][key] = _ask(
            label, config["workorder"].get(key, ""), secret=secret
        )

    # 第 2 步：禅道
    print("\n[第 2 步 / 共 2 步] 禅道登录")
    for key, label, secret, default in _ZENTAO_FIELDS:
        value = _ask(
            label,
            str(config["zentao"].get(key, "")),
            default=default,
            secret=secret,
        )
        if key == "execution_id":
            value = int(value) if value else (default or 162)
        config["zentao"][key] = value

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] 凭证已保存到 {CONFIG_FILE}，下次运行将自动加载。\n")
    return config


def ensure_credentials(current: dict = None) -> dict:
    """
    检查凭证是否齐全：
    - 齐全 → 直接返回
    - 缺失 → 调用 prompt_and_save_config 补齐后保存返回
    """
    config = current or load_config()
    wk = config.setdefault("workorder", {})
    zt = config.setdefault("zentao", {})

    required = [
        ("工单系统", "workorder", "username"),
        ("工单系统", "workorder", "password"),
        ("禅道",     "zentao",    "account"),
        ("禅道",     "zentao",    "password"),
    ]
    missing_labels = []
    for sys_name, section, key in required:
        if not (config.get(section) or {}).get(key):
            missing_labels.append(f"{sys_name}.{key}")

    if missing_labels:
        print(f"\n[INFO] 检测到缺失凭证：{', '.join(missing_labels)}")
        return prompt_and_save_config(config)

    return config
