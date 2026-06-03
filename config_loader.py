"""
凭证配置加载器
- load_config()                静默加载 config.json，不存在返回空 dict
- prompt_and_save_config(cfg)  逐步提示填写缺失字段，保存到 config.json
- ensure_credentials(cfg)      凭证齐全则直接返回；缺啥就逐步补问，再保存

输入体验：所有字段一律明文 input() 显示（不隐藏输入），便于用户当场确认。
如需重置凭证：删除 config.json 后重新运行即可。
"""

import json
import sys
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"

# 固定值：禅道系统地址 / 执行ID 是部署级配置，不参与凭证输入
ZENTAO_URL = "http://zentao.hlong.cc/zentao"
EXECUTION_ID = 162  # 分部非标

# 各字段：(key, 标签, 默认值) —— 仅账号 / 密码
_WORKORDER_FIELDS = [
    ("username", "账号", ""),
    ("password", "密码", ""),
]
_ZENTAO_FIELDS = [
    ("account",  "账号", "dengchang"),
    ("password", "密码", ""),
]


def load_config() -> dict:
    """静默加载 config.json；不存在/为空/格式损坏时返回空 dict。"""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            # 文件存在但为空（0 字节 / 全空白），按"未配置"处理
            return {}
        return json.loads(content)
    except (json.JSONDecodeError, OSError) as e:
        # 文件存在但内容损坏，提示并按空配置处理（main() 会引导用户首次配置）
        print(f"[WARN] {CONFIG_FILE.name} 格式错误（{e.__class__.__name__}: {e}），将按空配置处理",
              file=sys.stderr)
        return {}


def _ask(prompt: str, current: str, default: str = "") -> str:
    """单步输入：按 Enter 接受已保存值，输入新值则覆盖。明文显示。

    只在 config.json 已有保存值时显示占位符；代码级默认值不展示，
    但仍作为最后兜底（用户直接回车时静默使用）。
    """
    suffix = f" [{current}]" if current else ""
    return input(f"  {prompt}{suffix}: ").strip() or current or default


def _summarize_and_confirm(config: dict) -> bool:
    """打印本次填写的内容摘要，等待用户确认。返回 True 表示已确认。"""
    print()
    print("-" * 60)
    print("  本次填写的内容确认：")
    print("-" * 60)
    print("  [工单系统]")
    print(f"    账号: {config['workorder'].get('username', '')}")
    print(f"    密码: {config['workorder'].get('password', '')}")
    print("  [禅道]（系统地址与执行ID 为固定值，无需填写）")
    print(f"    系统地址: {ZENTAO_URL}")
    print(f"    执行ID:   {EXECUTION_ID}")
    print(f"    账号:     {config['zentao'].get('account', '')}")
    print(f"    密码:     {config['zentao'].get('password', '')}")
    print("-" * 60)
    while True:
        ans = input("  以上信息是否正确？[Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("  请输入 Y 或 n")


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
    for key, label, default in _WORKORDER_FIELDS:
        config["workorder"][key] = _ask(
            label, config["workorder"].get(key, ""), default
        )

    # 第 2 步：禅道
    print("\n[第 2 步 / 共 2 步] 禅道登录")
    for key, label, default in _ZENTAO_FIELDS:
        config["zentao"][key] = _ask(
            label, config["zentao"].get(key, ""), default
        )

    # 确认
    if not _summarize_and_confirm(config):
        print("\n[INFO] 已取消，请重新填写。\n")
        return prompt_and_save_config(config)

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
    config.setdefault("workorder", {})
    config.setdefault("zentao", {})

    required = [
        ("工单系统", "workorder", "username"),
        ("工单系统", "workorder", "password"),
        ("禅道",     "zentao",    "account"),
        ("禅道",     "zentao",    "password"),
    ]
    missing_labels = [
        f"{sys_name}.{key}"
        for sys_name, section, key in required
        if not (config.get(section) or {}).get(key)
    ]

    if missing_labels:
        print(f"\n[INFO] 检测到缺失凭证：{', '.join(missing_labels)}")
        return prompt_and_save_config(config)

    return config
