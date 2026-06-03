"""
凭证配置加载器
- 首次运行：交互式提示逐项填写，保存到 config.json
- 后续运行：从 config.json 静默加载
- config.json 已加入 .gitignore，不会被推送到 Git

若需重置凭证，删除 config.json 后重新运行即可。
"""

import getpass
import json
import sys
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"


def load_or_create_config() -> dict:
    """
    加载 config.json；不存在则交互式逐项提示填写并保存。
    返回 dict: {"workorder": {...}, "zentao": {...}}
    """
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    print("=" * 60, file=sys.stderr)
    print("  首次运行，请逐项填写登录凭证", file=sys.stderr)
    print(f"  填写后将保存到: {CONFIG_FILE}", file=sys.stderr)
    print("  （已加入 .gitignore，不会推送到 Git）", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
        suffix = f" [{default}]" if default else ""
        if secret:
            value = getpass.getpass(f"{prompt}{suffix}: ").strip()
        else:
            value = input(f"{prompt}{suffix}: ").strip()
        return value or default

    config = {
        "workorder": {
            "username": _ask("工单系统账号"),
            "password": _ask("工单系统密码", secret=True),
        },
        "zentao": {
            "url": _ask("禅道系统地址", default="http://zentao.hlong.cc/zentao"),
            "account": _ask("禅道账号", default="dengchang"),
            "password": _ask("禅道密码", secret=True),
            "execution_id": int(_ask("禅道执行ID", default="162")),
        },
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] 凭证已保存到 {CONFIG_FILE}，下次运行将自动加载。\n", file=sys.stderr)
    return config
