"""CI 许可证门禁：扫描 ``pip-licenses --format=json`` 输出，阻断 GPL/AGPL/未知许可证。

对应开发文档第 17 章 §17.2 切片 004-ci-license-gate（护栏：GPL/AGPL 出现即阻断）、
第 18 章 §18.6（deps job）、第 19 章 §19.5（pip-licenses 白名单规则）、
第 20 章 R-L1（许可证污染）。只依赖标准库，不引入新依赖。

判定规则（刻意保守、可辩护的 allowlist 规则）：
1. 出现 ``GPL`` / ``AGPL`` / ``LGPL`` 或 ``GNU * General Public License`` 字样即阻断，
   优先级高于任何宽松关键词：双许可（如 ``MIT OR GPL-3.0``）同样不放行。
   ``GPL`` 子串一并覆盖 ``AGPL`` / ``LGPL`` / ``GPLv2`` / ``GPL-3.0`` 等写法；
   仅含 ``GPL`` 全文的许可证（``GNU GENERAL PUBLIC LICENSE`` 正文）用整段短语兜底。
2. 未命中阻断词时，命中任一宽松许可证关键词（MIT / Apache / BSD / ISC / PSF / MPL /
   Zlib / Unlicense / CC0 等）即放行。关键词按词边界匹配，兼容 pip-licenses 的多种
   写法：``MIT``、``MIT License``、``Apache Software License``、``BSD License``、
   ``Python Software Foundation License``、``Mozilla Public License 2.0 (MPL 2.0)``、
   复合表达式 ``Apache-2.0 AND MIT`` / ``MPL-2.0 AND (Apache-2.0 OR MIT)``，以及个别
   包把整段 MIT 正文塞进 ``License`` 字段的情形（正文含独立单词 ``MIT``）。
3. 既无阻断词也无宽松关键词（空值、``UNKNOWN``、``NOASSERTION`` 等）一律阻断，
   交人工在第 19 章 §19.5 allowlist 登记理由与到期日后才能放行。

用法::

    uv run pip-licenses --format=json --with-urls > artifacts/licenses.json
    uv run python scripts/check_licenses.py artifacts/licenses.json   # 或省略参数读 stdin

退出码：0 全部放行；1 存在阻断项；2 输入文件缺失或 JSON 非法。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# 传染/强互惠许可证标记：License 字段规范化（大写）后按子串匹配。
# 顺序无关，任一命中即阻断；GPL 子串同时覆盖 AGPL/LGPL/GPLv2/GPL-3.0 等写法。
_COPYLEFT_MARKERS = (
    "GPL",
    "GNU GENERAL PUBLIC LICENSE",
    "GNU LESSER GENERAL PUBLIC LICENSE",
    "GNU LIBRARY GENERAL PUBLIC LICENSE",
    "GNU AFFERO GENERAL PUBLIC LICENSE",
)
# 宽松许可证关键词：词边界匹配，避免 "LIMIT"/"PERMITTED" 等正文单词误命中 MIT。
_PERMISSIVE_PATTERN = re.compile(r"\b(?:MIT|APACHE|BSD|ISC|PSF|MPL|ZLIB|UNLICENSE|CC0)\b")
# 不含上面单词但可判定的宽松许可证写法。
_PERMISSIVE_PHRASES = ("PYTHON SOFTWARE FOUNDATION", "PUBLIC DOMAIN")
# 缺少许可证信息（pip-licenses 对元数据缺失的包输出这些值）。
_EMPTY_LICENSE_MARKERS = frozenset({"", "UNKNOWN", "NOASSERTION", "NONE"})
# 项目自身已声明 MIT（pyproject `license = "MIT"` + LICENSE 文件；R2 于 2026-11-09 关闭），
# 因此默认**不再**豁免任何包：门禁会同样校验 `textgame` 自身的许可证元数据。
_IGNORE_PACKAGES_DEFAULT = ""
_PREVIEW_LENGTH = 80
_EXIT_OK = 0
_EXIT_BLOCKED = 1
_EXIT_INPUT_ERROR = 2


class LicenseInputError(Exception):
    """许可证清单无法读取或格式非法（对应退出码 2）。"""


def classify_license(license_text: str) -> str:
    """把 License 字段判定为 ``"allowed"`` / ``"blocked"`` / ``"unknown"`` 三态。"""
    normalized = " ".join(license_text.upper().split())
    if normalized in _EMPTY_LICENSE_MARKERS:
        return "unknown"
    if any(marker in normalized for marker in _COPYLEFT_MARKERS):
        return "blocked"
    if _PERMISSIVE_PATTERN.search(normalized):
        return "allowed"
    if any(phrase in normalized for phrase in _PERMISSIVE_PHRASES):
        return "allowed"
    return "unknown"


def load_entries(source: str | None) -> list[dict[str, Any]]:
    """读取 pip-licenses JSON；``source`` 为 None 时读 stdin。格式问题抛 LicenseInputError。"""
    if source is None:
        origin = "<stdin>"
        try:
            raw = sys.stdin.read()
        except OSError as exc:  # pragma: no cover - 极少数 stdin 异常
            raise LicenseInputError(f"读取 stdin 失败: {exc}") from exc
    else:
        path = Path(source)
        origin = str(path)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LicenseInputError(f"无法读取 {path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise LicenseInputError(f"{path} 不是 UTF-8 文本: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LicenseInputError(f"{origin} 不是合法 JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise LicenseInputError(
            f"{origin} 顶层必须是 JSON 数组（pip-licenses --format=json 的输出）"
        )

    entries: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("Name"), str):
            raise LicenseInputError(f"{origin} 第 {index} 条记录缺少字符串字段 Name")
        entries.append(item)
    return entries


def _parse_ignored_packages(raw: str) -> set[str]:
    """支持 ``--ignore-packages a,b c`` 三种分隔写法，包名大小写不敏感。"""
    return {part.casefold() for chunk in raw.split(",") for part in chunk.split() if part}


def _preview(text: str) -> str:
    """单行化并截断过长 License（整段 MIT 正文可能上千字符）。"""
    collapsed = " ".join(text.split())
    if not collapsed:
        return "<empty>"
    if len(collapsed) <= _PREVIEW_LENGTH:
        return collapsed
    return collapsed[:_PREVIEW_LENGTH] + "..."


def main(argv: list[str] | None = None) -> int:
    """执行许可证门禁；返回退出码（0 放行 / 1 阻断 / 2 输入错误）。"""
    parser = argparse.ArgumentParser(
        description="pip-licenses JSON 许可证门禁：GPL/AGPL/LGPL 与未知许可证即阻断",
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="pip-licenses --format=json 输出文件；省略时读取 stdin",
    )
    parser.add_argument(
        "--ignore-packages",
        default=_IGNORE_PACKAGES_DEFAULT,
        metavar="NAMES",
        help=(
            "逗号/空格分隔的包名，不参与门禁（默认空：项目自身已声明 MIT，见第 19 章 §19.5；"
            "仅用于人工登记过的例外）"
        ),
    )
    args = parser.parse_args(argv)

    try:
        entries = load_entries(args.input)
    except LicenseInputError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return _EXIT_INPUT_ERROR

    ignored = _parse_ignored_packages(args.ignore_packages)
    counts: Counter[str] = Counter()
    blocked: list[tuple[str, str, str]] = []
    ignored_count = 0
    for item in entries:
        name = item["Name"]
        license_text = str(item.get("License") or "").strip()
        if name.casefold() in ignored:
            ignored_count += 1
            continue
        counts[license_text or "<empty>"] += 1
        if classify_license(license_text) != "allowed":
            blocked.append((name, str(item.get("Version") or ""), license_text or "<empty>"))

    checked = len(entries) - ignored_count
    print(f"许可证扫描：共 {len(entries)} 个包，检查 {checked} 个，忽略 {ignored_count} 个")
    if counts:
        print("许可证分布：")
        for license_text, count in counts.most_common():
            print(f"  {count:>4}  {_preview(license_text)}")

    if blocked:
        print(
            f"[BLOCKED] {len(blocked)} 个包命中 GPL/AGPL/LGPL 或无法识别许可证"
            "（第 19 章 §19.5、第 20 章 R-L1）："
        )
        for name, version, license_text in blocked:
            suffix = f" {version}" if version else ""
            print(f"  - {name}{suffix}: {_preview(license_text)}")
        print("处置：移除/替换依赖，或在第 19 章 §19.5 allowlist 登记理由与到期日（人工 review）")
        return _EXIT_BLOCKED

    print("[OK] 未发现 GPL/AGPL/LGPL 或无法识别的许可证")
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
