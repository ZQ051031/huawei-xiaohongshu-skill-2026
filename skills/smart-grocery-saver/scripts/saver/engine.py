"""End-to-end orchestration: input -> meal plan -> optimised basket -> savings -> outputs."""

import copy
import datetime as _dt

from . import SKILL_NAME, __version__
from .data import load_catalog, load_recipes, normalize_input, Catalog
from .planner import Planner
from .report import render_markdown, render_note
from .shopping import Shopper, _r2

WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
WEEKDAYS_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MEAL_ZH = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
STEP_LABELS = {
    "pantry": ("先用家里存货", "Use pantry first"),
    "stores": ("多店比价分单", "Smart store split"),
    "item_coupons": ("单品券/促销叠加", "Item coupons & promos"),
    "order_coupons": ("满减/包邮", "Order coupons & delivery"),
}


def run(raw_input, catalog=None, recipes=None):
    """Run the Skill. ``raw_input`` is the user request dict (see schemas/input.schema.json)."""
    catalog = Catalog(copy.deepcopy(catalog.raw)) if isinstance(catalog, Catalog) else (
        Catalog(copy.deepcopy(catalog)) if isinstance(catalog, dict) else load_catalog())
    recipes = recipes if recipes is not None else load_recipes()
    req = normalize_input(raw_input, catalog, recipes)
    lang = req["output"]["lang"]
    allowed = req["stores"]["allowed"]
    max_stores = req["stores"]["max_stores"]
    shopper = Shopper(catalog, req["coupon_list"], allowed, req["stores"].get("extra_store_cost", 0))
    planner = Planner(req, catalog, recipes, shopper)
    warnings = list(req.get("input_warnings", [])) + list(planner.warnings)

    plan = planner.solve()
    basket, steps, baseline = _price(plan, planner, shopper, req, catalog, allowed, max_stores)
    budget = float(req["budget"]["weekly"])
    budget_mode = False
    if basket["total"] > budget:
        alt_plan = planner.solve(variety_weight=0.0, max_repeats=req["variety"].get("max_repeats", 2) + 1)
        alt = _price(alt_plan, planner, shopper, req, catalog, allowed, max_stores)
        if alt[0]["total"] < basket["total"] - 0.005:
            plan, (basket, steps, baseline), budget_mode = alt_plan, alt, True
            warnings.append("Over budget with the balanced plan, so budget mode was used (fewer protein-variety rules)."
                            if lang == "en" else "均衡菜单超预算，已自动切换为省钱模式（放宽荤素多样性要求）。")
    for w in planner.warnings:
        if w not in warnings:
            warnings.append(w)

    consumption = planner.consumption(plan)
    _, staples = planner.purchase_needs(consumption)
    bought = {}
    for s in basket["stores"]:
        for ln in s["lines"]:
            bought[ln["ingredient"]] = ln["bought_qty"]
    unit_cost = {}
    for s in basket["stores"]:
        for ln in s["lines"]:
            if ln["bought_qty"]:
                unit_cost[ln["ingredient"]] = ln["cost"] / ln["bought_qty"]

    meal_plan = []
    for slot, rid in zip(planner.slots, plan):
        r = planner.recipes[rid]
        date = req["start_date"].toordinal() + slot["day"] - 1
        d = _dt.date.fromordinal(date)
        dish_cost = sum(unit_cost.get(ing, 0.0) * q * planner.portions for ing, q in r["ingredients"].items())
        entry = next((m for m in meal_plan if m["day"] == slot["day"] and m["meal"] == slot["meal"]), None)
        if entry is None:
            entry = {"day": slot["day"], "date": d.isoformat(),
                     "weekday": (WEEKDAYS_ZH if lang == "zh" else WEEKDAYS_EN)[d.weekday()],
                     "meal": slot["meal"], "meal_label": MEAL_ZH[slot["meal"]] if lang == "zh" else slot["meal"].title(),
                     "dishes": []}
            meal_plan.append(entry)
        entry["dishes"].append({"recipe_id": rid, "name": r["name_" + lang], "role": slot["role"],
                                "minutes": r.get("minutes"), "est_cost": _r2(dish_cost)})
    if planner.staple:
        for m in meal_plan:
            if m["meal"] in ("lunch", "dinner"):
                st = planner.staple
                c = sum(unit_cost.get(ing, 0.0) * q * planner.portions for ing, q in st["ingredients"].items())
                m["dishes"].append({"recipe_id": st["id"], "name": st["name_" + lang], "role": "staple",
                                    "minutes": st.get("minutes"), "est_cost": _r2(c)})
    for m in meal_plan:
        m["est_cost"] = _r2(sum(x["est_cost"] for x in m["dishes"]))

    pantry_used, leftovers = [], []
    for ing in sorted(set(consumption) | set(req["pantry_map"])):
        if planner._is_assumed_staple(ing):
            continue
        have = req["pantry_map"].get(ing, 0.0)
        use = consumption.get(ing, 0.0)
        if have > 0 and use > 0:
            pantry_used.append({"ingredient": ing, "name": catalog.name(ing, lang), "qty": round(min(have, use), 1),
                                "unit": catalog.unit(ing)})
        left = have + bought.get(ing, 0.0) - use
        if left > 0.5 and (bought.get(ing) or use > 0):
            leftovers.append({"ingredient": ing, "name": catalog.name(ing, lang), "qty": round(left, 1),
                              "unit": catalog.unit(ing), "perishable": bool(catalog.ingredients[ing].get("perishable")),
                              "est_value": _r2(unit_cost.get(ing, 0.0) * left)})

    leftovers.sort(key=lambda l: (not l["perishable"], -l["est_value"]))
    unavailable = sorted(set(basket.get("unavailable", [])) | set(basket.get("not_in_chosen_stores", [])))
    if unavailable:
        names = ", ".join(catalog.name(i, lang) for i in unavailable)
        warnings.append(("Not available in your chosen stores (buy elsewhere): " if lang == "en" else
                         "所选商店买不到，需另行购买：") + names)

    total = basket["total"]
    n_meals = len(meal_plan)
    portions_served = sum(planner.portions for m in meal_plan)
    coupons_used = []
    for s in basket["stores"]:
        for ln in s["lines"]:
            for c in ln["coupons"]:
                if c["id"] not in [x["id"] for x in coupons_used]:
                    coupons_used.append(dict(c, store=s["store"]))
        for c in s["order_discounts"]:
            coupons_used.append({"id": c["id"], "label": c["label"], "store": s["store"]})
    used_ids = {c["id"] for c in coupons_used}
    unused = [{"id": c["id"], "label": c["label"], "reason": _unused_reason(c, basket, catalog, lang)}
              for c in req["coupon_list"] if c["id"] not in used_ids]
    unused += [{"id": c["id"], "label": c["label"], "reason": c.get("_reason", "expired")} for c in req["inactive_coupons"]]

    stores_out = []
    for s in basket["stores"]:
        items = []
        for ln in s["lines"]:
            ing = ln["ingredient"]
            items.append({"ingredient": ing, "name": catalog.name(ing, lang), "unit": catalog.unit(ing),
                          "need": ln["need"], "bought_qty": ln["bought_qty"], "packs": ln["packs"],
                          "list_cost": ln["list_cost"], "cost": ln["cost"], "coupons": ln["coupons"],
                          "category": catalog.ingredients[ing]["category"]})
        items.sort(key=lambda x: (x["category"], x["ingredient"]))
        stores_out.append({"store": s["store"], "name": catalog.store_name(s["store"], lang), "items": items,
                           "list_subtotal": s["list_subtotal"], "subtotal": s["subtotal"],
                           "order_discounts": s["order_discounts"], "delivery_fee": s["delivery_fee"],
                           "total": s["total"]})

    savings_total = _r2(baseline - total)
    result = {
        "skill": SKILL_NAME, "version": __version__,
        "data_notice": {"is_sample": catalog.is_sample, "en": catalog.notice, "zh": catalog.notice_zh},
        "request": {"portions_per_meal": planner.portions, "household": req["household"],
                    "meals": req["meal_days"], "dishes_per_meal": req["dishes_per_meal"],
                    "forbidden_tags": req["forbidden_tags"], "stores_allowed": allowed,
                    "max_stores": max_stores, "start_date": req["start_date"].isoformat(), "lang": lang},
        "summary": {
            "currency": catalog.currency, "total": total, "budget": budget,
            "remaining": _r2(budget - total), "status": "within_budget" if total <= budget + 1e-9 else "over_budget",
            "baseline_total": baseline, "savings_total": savings_total,
            "savings_pct": round(100.0 * savings_total / baseline, 1) if baseline > 0 else 0.0,
            "meals": n_meals, "cost_per_portion": _r2(total / portions_served) if portions_served else 0.0,
            "stores_used": [s["store"] for s in stores_out], "budget_mode": budget_mode,
        },
        "meal_plan": meal_plan,
        "shopping_list": {"stores": stores_out, "unavailable": unavailable,
                          "check_pantry_staples": [{"ingredient": i, "name": catalog.name(i, lang)} for i in staples]},
        "pantry_used": pantry_used,
        "leftovers": leftovers,
        "savings": {"baseline_total": baseline, "final_total": total, "steps": steps,
                    "baseline_definition": ("Same meal plan bought at your default store (cheapest pack combination), "
                                            "ignoring your pantry and all coupons." if lang == "en" else
                                            "同一份菜单在平时常去的商店按最划算包装购买，不用家中存货、不用任何优惠券。")},
        "coupons": {"used": coupons_used, "unused": unused},
        "excluded_recipes": planner.excluded,
        "warnings": warnings,
    }
    if result["summary"]["status"] == "over_budget":
        result["budget_tips"] = _budget_tips(result, lang)
    result["outputs"] = {"markdown": render_markdown(result, catalog), "xiaohongshu_note": render_note(result, req, catalog)}
    return result


def _price(plan, planner, shopper, req, catalog, allowed, max_stores):
    consumption = planner.consumption(plan)
    gross, _ = planner.purchase_needs(consumption, use_pantry=False)
    net, _ = planner.purchase_needs(consumption, use_pantry=True)
    default = req["stores"]["default_store"]
    assign0 = {}
    for ing in gross:
        if shopper.line(ing, default, gross[ing], False) is not None:
            assign0[ing] = default
        else:
            opts = [(shopper.line(ing, s, gross[ing], False), s) for s in allowed]
            opts = [(l["cost"], allowed.index(s), s) for l, s in opts if l is not None]
            if opts:
                assign0[ing] = min(opts)[2]
    base = shopper.evaluate(gross, assign0, False, False)
    assign1 = {i: s for i, s in assign0.items() if i in net}
    step_pantry = shopper.evaluate({i: q for i, q in net.items() if i in assign1}, assign1, False, False)
    s2 = shopper.optimize(net, max_stores, False, False, initial=assign1)
    s3 = shopper.optimize(net, max_stores, True, False, initial=s2["assignment"])
    s4 = shopper.optimize(net, max_stores, True, True, initial=s3["assignment"])
    lang = req["output"]["lang"]
    seq = [("pantry", base, step_pantry), ("stores", step_pantry, s2), ("item_coupons", s2, s3), ("order_coupons", s3, s4)]
    steps = []
    for key, prev, cur in seq:
        zh, en = STEP_LABELS[key]
        steps.append({"key": key, "label": zh if lang == "zh" else en, "amount": _r2(prev["total"] - cur["total"])})
    return s4, steps, base["total"]


def _budget_tips(result, lang):
    over = _r2(result["summary"]["total"] - result["summary"]["budget"])
    if lang == "zh":
        return ["当前方案超出预算 ¥%.2f。" % over,
                "可把 1-2 顿荤菜换成鸡蛋/豆腐类主菜，或减少一顿在家用餐。",
                "把 max_stores 调大（允许多去一家店）或补充你手上的优惠券，通常还能再省一些。",
                "在 pantry 中填写家里已有的食材，可直接抵扣采购量。"]
    return ["The plan is ¥%.2f over budget." % over,
            "Swap 1-2 meat mains for egg/tofu dishes, or plan one fewer meal at home.",
            "Allow one more store (max_stores) or add the coupons you have - both usually lower the total.",
            "List what is already in your pantry; it is deducted from the shopping list."]


def _unused_reason(c, basket, catalog, lang):
    zh = lang != "en"
    bought = {ln["ingredient"]: s["store"] for s in basket["stores"] for ln in s["lines"]}
    if c["type"] == "threshold_off":
        stores = [s for s in basket["stores"] if c["store"] in ("*", s["store"])]
        if not stores:
            return "没有在该店下单" if zh else "no order placed at this store"
        best = max(s["subtotal"] for s in stores)
        return ("订单小计¥%.2f未达门槛¥%g，凑单不划算" % (best, c["threshold"]) if zh else
                "order subtotal ¥%.2f is below the ¥%g threshold and padding the order did not pay off"
                % (best, c["threshold"]))
    targets = [i for i in bought if (not c.get("ingredients") or i in c["ingredients"])
               and (not c.get("categories") or catalog.ingredients[i].get("category") in c["categories"])]
    if not targets:
        return "本周菜单不需要这些商品" if zh else "the meal plan does not need these items"
    if c["store"] != "*" and all(bought[i] != c["store"] for i in targets):
        return "其他商店更便宜" if zh else "another store is cheaper even after this deal"
    return "用券后的包装组合不比其他方案便宜" if zh else "no cheaper pack combination with this deal"
