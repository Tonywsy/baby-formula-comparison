#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
婴儿配方奶粉对比评测 —— 自动校验 harness
=========================================
用途：每次修改 国产奶粉对比评测.html 后必跑，防止公开页面出现可挑刺的错误。

四道检查（均为硬性，任一失败则退出码=1）：
  1) 能量自洽：17*蛋白 + 37*脂肪 + 17*碳水 (kJ/100g) 与标注能量偏差须 ≤5%
  2) GB 10765-2021 国标反推：每项营养换算为每100kJ，对照 gb_10765_2021.json
     （范围只从官方JSON读取，绝不硬编码在脚本里）
  3) 注册号新鲜度：网页内注册号须包含 reg_numbers.json 中的当前有效号
  4) 基础完整性：无负值/NaN、字段数=28、产品数=4

软检查（仅警告，不影响退出码）：
  5) 来源原值比对：计算出的每100kJ 与 source_per_100kJ.json 锚点偏差 ≤10%

用法：
  python audit_harness.py [html_path]
  html_path 缺省为同目录下的 国产奶粉对比评测.html
"""
import re
import sys
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HTML = os.path.join(HERE, "国产奶粉对比评测.html")

PRODUCT_ORDER = ["飞鹤臻爱倍护", "金领冠育护", "中特美庐臻铂之星", "君乐宝乐铂"]
EXPECTED_FIELDS = 28
EXPECTED_PRODUCTS = 4
ENERGY_TOL = 0.05   # 能量自洽允许偏差


def load_json(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def parse_html(html_path):
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    # --- 解析 N 对象 ---
    m = re.search(r"const N=\{(.*?)\n\};", html, re.DOTALL)
    if not m:
        raise RuntimeError("未在 HTML 中找到 const N={...}")
    n_block = m.group(1)
    fields = {}
    for fm in re.finditer(r"(\w+):\s*\[([^\]]*)\]", n_block):
        name = fm.group(1)
        raw = [x.strip() for x in fm.group(2).split(",") if x.strip() != ""]
        vals = [float(x) for x in raw]
        fields[name] = vals

    # --- 解析 PRODS 的 name + regNum ---
    prods = []
    for pm in re.finditer(r"name:'([^']*)'[^}]*?regNum:'([^']*)'", html):
        prods.append({"name": pm.group(1), "regNum": pm.group(2)})

    return fields, prods


def check_energy(fields, report):
    fails = []
    energy = fields.get("energy")
    for i, pname in enumerate(PRODUCT_ORDER):
        if i >= len(energy):
            continue
        stated = energy[i]
        atwater = 17 * fields["protein"][i] + 37 * fields["fat"][i] + 17 * fields["carb"][i]
        diff = (stated - atwater) / stated if stated else 0
        status = "OK" if abs(diff) <= ENERGY_TOL else "FAIL"
        report.append((f"能量自洽[{pname}]", status,
                       f"标注 {stated} vs Atwater {atwater:.1f} kJ/100g (偏差 {diff*100:+.1f}%)"))
        if abs(diff) > ENERGY_TOL:
            fails.append((pname, diff))
    return fails


def check_gb(fields, gb, report):
    fails = []
    ranges = gb["ranges"]
    energy = fields["energy"]
    for field, rng in ranges.items():
        if field not in fields:
            report.append((f"国标[{field}]", "SKIP", "HTML 无此字段"))
            continue
        mn, mx = rng.get("min"), rng.get("max")
        for i, pname in enumerate(PRODUCT_ORDER):
            if i >= len(fields[field]):
                continue
            coef = energy[i] / 100.0
            per100kJ = fields[field][i] / coef
            # 范围判定
            lo_ok = (mn is None) or (per100kJ >= mn)
            hi_ok = (mx is None) or (per100kJ <= mx)
            note = f"{per100kJ:.3f} {rng['unit']}/100kJ (国标 {mn}~{mx})"
            # ARA 特殊比例规则
            ratio_note = ""
            if field == "ARA" and "DHA" in fields:
                dha = fields["DHA"][i] / coef
                if per100kJ < dha - 1e-9:
                    hi_ok = False
                    ratio_note = f" | ARA({per100kJ:.2f})<DHA({dha:.2f}) 违反ARA≥DHA"
                if per100kJ > 2 * dha + 1e-9:
                    hi_ok = False
                    ratio_note = f" | ARA({per100kJ:.2f})>2×DHA({2*dha:.2f}) 违反ARA≤2×DHA"
            status = "OK" if (lo_ok and hi_ok) else "FAIL"
            report.append((f"国标[{field}][{pname}]", status, note + ratio_note))
            if not (lo_ok and hi_ok):
                fails.append((field, pname, per100kJ, mn, mx))
    # 未被国标管控的字段（仅提示，不判失败）
    for fld, why in gb.get("not_gb_controlled", {}).items():
        if fld in fields:
            report.append((f"国标[{fld}]", "N/A", f"GB 10765-2021 无强制范围：{why}"))
    return fails


def check_reg(prods, reg_data, report):
    fails = []
    for p in prods:
        name = p["name"]
        displayed = p["regNum"]
        entry = reg_data["products"].get(name)
        if not entry:
            report.append((f"注册号[{name}]", "WARN", f"reg_numbers.json 无此产品记录：{displayed}"))
            continue
        expected = entry["current"]
        tokens = re.findall(r"YP\d+", displayed)
        if expected in tokens:
            report.append((f"注册号[{name}]", "OK",
                           f"显示 '{displayed}' 含当前有效号 {expected} ({entry.get('status')})"))
        else:
            report.append((f"注册号[{name}]", "FAIL",
                           f"显示 '{displayed}' 不含当前有效号 {expected} —— 注册号可能已过期！"))
            fails.append((name, displayed, expected))
    return fails


def check_source(fields, src, report):
    warns = []
    tol = src.get("tolerance", 0.10)
    energy = fields["energy"]
    for key, anchors in src["by_product_index"].items():
        idx = int(key.split("_")[0])
        pname = PRODUCT_ORDER[idx] if idx < len(PRODUCT_ORDER) else key
        for field, srcval in anchors.items():
            if field not in fields:
                continue
            coef = energy[idx] / 100.0
            computed = fields[field][idx] / coef
            dev = abs(computed - srcval) / srcval if srcval else 0
            status = "OK" if dev <= tol else "WARN"
            report.append((f"来源比对[{field}][{pname}]", status,
                           f"计算 {computed:.3f} vs 来源 {srcval} (偏差 {dev*100:+.1f}%)"))
            if dev > tol:
                warns.append((field, pname, computed, srcval, dev))
    return warns


def check_integrity(fields, prods, report):
    fails = []
    # 字段数
    if len(fields) != EXPECTED_FIELDS:
        report.append(("完整性[字段数]", "FAIL", f"实际 {len(fields)} ≠ 期望 {EXPECTED_FIELDS}"))
        fails.append("field_count")
    else:
        report.append(("完整性[字段数]", "OK", f"{len(fields)} 个营养字段"))
    # 产品数
    if len(prods) != EXPECTED_PRODUCTS:
        report.append(("完整性[产品数]", "FAIL", f"实际 {len(prods)} ≠ 期望 {EXPECTED_PRODUCTS}"))
        fails.append("product_count")
    else:
        report.append(("完整性[产品数]", "OK", f"{len(prods)} 款产品：{', '.join(p['name'] for p in prods)}"))
    # 负值 / NaN
    for field, vals in fields.items():
        for i, v in enumerate(vals):
            if v is None or (isinstance(v, float) and (v != v or v < 0)):
                report.append((f"完整性[{field}]", "FAIL", f"产品#{i} 值异常：{v}"))
                fails.append((field, i))
    return fails


def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HTML
    if not os.path.exists(html_path):
        print(f"[ERROR] HTML 不存在: {html_path}")
        sys.exit(2)

    gb = load_json("gb_10765_2021.json")
    reg_data = load_json("reg_numbers.json")
    src = load_json("source_per_100kJ.json")

    try:
        fields, prods = parse_html(html_path)
    except Exception as e:
        print(f"[ERROR] 解析 HTML 失败: {e}")
        sys.exit(1)

    report = []
    f_energy = check_energy(fields, report)
    f_gb = check_gb(fields, gb, report)
    f_reg = check_reg(prods, reg_data, report)
    f_int = check_integrity(fields, prods, report)
    w_src = check_source(fields, src, report)

    # 输出报告
    print("=" * 78)
    print("婴儿配方奶粉对比评测 —— 自动校验报告")
    print(f"HTML: {os.path.basename(html_path)}")
    print(f"国标库: {gb['standard']} ({gb['unit']})")
    print("=" * 78)
    for label, status, detail in report:
        mark = {"OK": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭️", "N/A": "➖"}.get(status, status)
        print(f"[{mark}] {label:<26} {detail}")
    print("=" * 78)

    hard_fails = len(f_energy) + len(f_gb) + len(f_reg) + len(f_int)
    print(f"硬性失败：{hard_fails} 项 | 软性警告：{len(w_src)} 项")
    if hard_fails == 0:
        print("结论：✅ 全部硬性检查通过，数据可发布。")
        sys.exit(0)
    else:
        print("结论：❌ 存在硬性失败，必须修复后再发布！")
        sys.exit(1)


if __name__ == "__main__":
    main()
