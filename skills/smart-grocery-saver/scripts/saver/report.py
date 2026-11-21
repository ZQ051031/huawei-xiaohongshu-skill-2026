"""Human-readable outputs: Markdown report and a Xiaohongshu-style shareable note."""

NOTE_TITLE_MAX = 20     # Xiaohongshu note title limit (characters)
NOTE_BODY_MAX = 1000    # Xiaohongshu note body limit (characters)
CONTEST_TAGS = ["#小红书Skill创新赛", "#2026华为开发者大赛"]


def _money(x):
    return "¥%.2f" % x


def _qty(q, unit):
    if unit == "pcs":
        return "%d枚" % round(q)
    if q >= 1000:
        return "%gkg" % round(q / 1000.0, 2) if unit == "g" else "%gL" % round(q / 1000.0, 2)
    return "%d%s" % (round(q), unit)


def render_markdown(res, catalog):
    zh = res["request"]["lang"] == "zh"
    s, out = res["summary"], []
    T = (lambda a, b: a) if zh else (lambda a, b: b)
    out.append("# " + T("一周省钱买菜计划", "Weekly grocery savings plan"))
    if res["data_notice"]["is_sample"]:
        out.append("> " + T("⚠️ " + res["data_notice"]["zh"], "⚠️ " + res["data_notice"]["en"]))
    out.append("")
    out.append("| %s | %s |" % (T("项目", "Item"), T("数值", "Value")))
    out.append("|---|---|")
    rows = [
        (T("每餐份量（成人当量）", "Portions per meal (adult equiv.)"), "%g" % res["request"]["portions_per_meal"]),
        (T("在家用餐", "Meals at home"), str(s["meals"])),
        (T("周预算", "Weekly budget"), _money(s["budget"])),
        (T("优化后花费", "Optimised spend"), _money(s["total"])),
        (T("预算状态", "Budget status"), T("✅ 预算内，剩余 ", "✅ within budget, ") + _money(s["remaining"]) + T("", " left")
         if s["status"] == "within_budget" else T("❌ 超预算 ", "❌ over by ") + _money(-s["remaining"])),
        (T("未优化基准", "Unoptimised baseline"), _money(s["baseline_total"])),
        (T("共节省", "Total saved"), "%s (%.1f%%)" % (_money(s["savings_total"]), s["savings_pct"])),
        (T("人均每餐", "Per portion"), _money(s["cost_per_portion"])),
    ]
    out += ["| %s | %s |" % r for r in rows]
    out.append("")
    out.append("## " + T("省钱明细", "Where the savings come from"))
    for st in res["savings"]["steps"]:
        out.append("- %s: %s" % (st["label"], _money(st["amount"])))
    out.append("- " + T("基准定义：", "Baseline: ") + res["savings"]["baseline_definition"])
    out.append("")
    out.append("## " + T("一周菜单", "Meal plan"))
    out.append("| %s | %s | %s | %s |" % (T("日期", "Day"), T("餐次", "Meal"), T("菜品", "Dishes"), T("估算成本", "Est. cost")))
    out.append("|---|---|---|---|")
    for m in res["meal_plan"]:
        out.append("| %s %s | %s | %s | %s |" % (m["weekday"], m["date"][5:], m["meal_label"],
                                               "、".join(d["name"] for d in m["dishes"]) if zh else ", ".join(d["name"] for d in m["dishes"]),
                                               _money(m["est_cost"])))
    out.append("")
    out.append("## " + T("购物清单", "Shopping list"))
    for st in res["shopping_list"]["stores"]:
        out.append("### %s — %s" % (st["name"], _money(st["total"])))
        out.append("| %s | %s | %s | %s | %s |" % (T("食材", "Item"), T("需要", "Need"), T("购买", "Buy"),
                                                 T("原价→到手", "List → paid"), T("优惠", "Deals")))
        out.append("|---|---|---|---|---|")
        for it in st["items"]:
            packs = " + ".join("%s ×%d%s" % (p["label"], p["count"], T("（含赠%d）" % p["free"], " (%d free)" % p["free"]) if p["free"] else "")
                               for p in it["packs"])
            deals = "; ".join(c["label"] for c in it["coupons"]) or "—"
            out.append("| %s | %s | %s | %s → %s | %s |" % (it["name"], _qty(it["need"], it["unit"]), packs,
                                                         _money(it["list_cost"]), _money(it["cost"]), deals))
        extra = []
        for d in st["order_discounts"]:
            extra.append("%s −%s" % (d["label"], _money(d["amount"])))
        if st["delivery_fee"]:
            extra.append(T("配送费 +", "Delivery +") + _money(st["delivery_fee"]))
        out.append("")
        out.append(T("小计 ", "Subtotal ") + _money(st["subtotal"]) + ("；" if zh else "; ") + ("；" if zh else "; ").join(extra) if extra
                   else T("小计 ", "Subtotal ") + _money(st["subtotal"]))
        out.append("")
    sl = res["shopping_list"]
    if sl["check_pantry_staples"]:
        out.append(T("**请确认家里有这些调味品（未计入花费）：** ", "**Check you have these staples (not costed):** ")
                   + ("、" if zh else ", ").join(x["name"] for x in sl["check_pantry_staples"]))
        out.append("")
    if res["pantry_used"]:
        out.append("## " + T("用掉的家中存货", "Pantry used"))
        out += ["- %s %s" % (p["name"], _qty(p["qty"], p["unit"])) for p in res["pantry_used"]]
        out.append("")
    if res["leftovers"]:
        out.append("## " + T("本周剩余（下周优先用）", "Leftovers (use first next week)"))
        out += ["- %s %s%s" % (l["name"], _qty(l["qty"], l["unit"]), T("（易坏）", " (perishable)") if l["perishable"] else "")
                for l in res["leftovers"]]
        out.append("")
    if res["coupons"]["unused"]:
        out.append("## " + T("未用上的优惠", "Coupons not used"))
        out += ["- %s%s" % (c["label"], " (%s)" % c["reason"] if c.get("reason") else "") for c in res["coupons"]["unused"]]
        out.append("")
    if res.get("budget_tips"):
        out.append("## " + T("预算建议", "Budget tips"))
        out += ["- " + t for t in res["budget_tips"]]
        out.append("")
    if res["warnings"]:
        out.append("## " + T("提示", "Notes"))
        out += ["- " + w for w in res["warnings"]]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _fit_title(cands):
    for t in cands:
        if len(t) <= NOTE_TITLE_MAX:
            return t
    return cands[-1][:NOTE_TITLE_MAX]


def render_note(res, req, catalog):
    zh = res["request"]["lang"] == "zh"
    s = res["summary"]
    hh = req["household"]
    people = int(hh.get("adults", 0)) + int(hh.get("children", 0))
    total, saved = s["total"], s["savings_total"]
    if zh:
        title = _fit_title([
            "%d口之家一周买菜¥%d 省¥%d💰" % (people, round(total), round(saved)),
            "一周买菜¥%d 省了¥%d💰" % (round(total), round(saved)),
            "一周买菜只花¥%d" % round(total),
        ])
    else:
        title = _fit_title(["Week of meals ¥%d, saved ¥%d" % (round(total), round(saved)),
                            "¥%d week, saved ¥%d" % (round(total), round(saved)),
                            "Groceries ¥%d/week" % round(total), "My grocery week"])
    tags = ["#省钱攻略", "#一周菜谱", "#买菜省钱", "#优惠券叠加", "#居家过日子"] if zh else \
        ["#grocerysavings", "#mealplan", "#budgetcooking", "#coupons"]
    tags += [t if t.startswith("#") else "#" + t for t in req["output"].get("extra_hashtags", [])]
    if req["output"].get("include_contest_tags"):
        tags += CONTEST_TAGS
    top_steps = sorted([st for st in res["savings"]["steps"] if st["amount"] > 0.004], key=lambda x: -x["amount"])
    dinners = [m for m in res["meal_plan"] if m["meal"] == "dinner"] or res["meal_plan"]
    lines = []
    if zh:
        lines.append("💰本周战绩：%d口人 · %d顿在家吃 · 实付¥%.2f（预算¥%g）" % (people, s["meals"], total, s["budget"]))
        lines.append("📉比不优化少花¥%.2f（%.1f%%），人均每餐¥%.2f" % (saved, s["savings_pct"], s["cost_per_portion"]))
        if top_steps:
            lines.append("")
            lines.append("✨省钱秘诀")
            for i, st in enumerate(top_steps[:4], 1):
                lines.append("%d️⃣ %s 省¥%.2f" % (i, st["label"], st["amount"]))
        used = res["coupons"]["used"]
        if used:
            lines.append("🎟️用上的券：" + "、".join(c["label"] for c in used[:5]))
        lines.append("")
        lines.append("🍳一周晚餐")
        for m in dinners[:7]:
            lines.append("%s：%s" % (m["weekday"], "+".join(d["name"] for d in m["dishes"] if d["role"] != "staple")))
        if res["leftovers"]:
            lines.append("")
            lines.append("♻️剩余食材下周先用：" + "、".join(l["name"] for l in res["leftovers"][:5]))
        lines.append("")
        lines.append("💬你家一周买菜花多少？评论区说说你的省钱招，我更新进下周清单～")
        if res["data_notice"]["is_sample"]:
            lines.append("（注：价格为示例数据，实际以你所在商店为准）")
    else:
        lines.append("💰This week: %d people · %d home meals · paid ¥%.2f (budget ¥%g)" % (people, s["meals"], total, s["budget"]))
        lines.append("📉Saved ¥%.2f (%.1f%%) vs. unoptimised shopping, ¥%.2f per portion" % (saved, s["savings_pct"], s["cost_per_portion"]))
        if top_steps:
            lines.append("")
            lines.append("✨How I saved")
            for i, st in enumerate(top_steps[:4], 1):
                lines.append("%d. %s: ¥%.2f" % (i, st["label"], st["amount"]))
        lines.append("")
        lines.append("🍳Dinners")
        for m in dinners[:7]:
            lines.append("%s: %s" % (m["weekday"], " + ".join(d["name"] for d in m["dishes"] if d["role"] != "staple")))
        lines.append("")
        lines.append("💬How much does your weekly shop cost? Share your best trick in the comments!")
        if res["data_notice"]["is_sample"]:
            lines.append("(Prices are sample data - check your local store.)")
    tag_line = " ".join(tags)
    body = "\n".join(lines)
    while len(body) + 2 + len(tag_line) > NOTE_BODY_MAX and len(lines) > 4:
        drop = next((i for i in range(len(lines) - 1, -1, -1) if lines[i] and lines[i][0] in "周MTWFS" and "：" in lines[i] or ": " in lines[i] and lines[i][:3] in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")), None)
        if drop is None:
            break
        lines.pop(drop)
        body = "\n".join(lines)
    full = (body + "\n\n" + tag_line)[:NOTE_BODY_MAX]
    return {"title": title, "body": full, "hashtags": tags, "title_chars": len(title), "body_chars": len(full),
            "limits": {"title": NOTE_TITLE_MAX, "body": NOTE_BODY_MAX}}
