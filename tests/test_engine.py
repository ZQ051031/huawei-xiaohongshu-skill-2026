import copy
import json
import unittest

import _path
from saver.data import DIET_RESTRICTIONS, InputError, load_catalog, load_recipes, recipe_tags
from saver.engine import run

CAT = load_catalog()
RECIPES = {r["id"]: r for r in load_recipes()}
FAMILY = json.loads((_path.SKILL / "examples" / "family_of_three.json").read_text(encoding="utf-8"))


def base(**kw):
    req = {"household": {"adults": 2}, "budget": {"weekly": 300}, "meals": {"dinner": 7},
           "start_date": "2026-11-23"}
    req.update(kw)
    return req


def dish_ids(res):
    return [d["recipe_id"] for m in res["meal_plan"] for d in m["dishes"]]


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.family = run(copy.deepcopy(FAMILY))

    def test_output_sections(self):
        for k in ("summary", "meal_plan", "shopping_list", "savings", "coupons", "outputs", "data_notice"):
            self.assertIn(k, self.family)
        self.assertTrue(self.family["data_notice"]["is_sample"])
        self.assertEqual(len(self.family["meal_plan"]), 16)

    def test_savings_add_up(self):
        s, steps = self.family["summary"], self.family["savings"]["steps"]
        self.assertAlmostEqual(s["baseline_total"] - sum(x["amount"] for x in steps), s["total"], delta=0.02)
        self.assertGreater(s["savings_total"], 0)
        self.assertLessEqual(s["total"], s["baseline_total"])

    def test_store_totals_add_up(self):
        stores = self.family["shopping_list"]["stores"]
        self.assertAlmostEqual(sum(x["total"] for x in stores), self.family["summary"]["total"], delta=0.02)
        for st in stores:
            items = sum(i["cost"] for i in st["items"])
            self.assertAlmostEqual(items, st["subtotal"], delta=0.02)
            self.assertAlmostEqual(st["subtotal"] - sum(d["amount"] for d in st["order_discounts"]) + st["delivery_fee"],
                                   st["total"], delta=0.02)

    def test_shopping_covers_needs(self):
        for st in self.family["shopping_list"]["stores"]:
            for it in st["items"]:
                bought = sum(p["pack_qty"] * p["count"] for p in it["packs"])
                self.assertGreaterEqual(bought + 1e-6, it["need"], it["name"])

    def test_max_stores_respected(self):
        self.assertLessEqual(len(self.family["shopping_list"]["stores"]), 2)
        one = run(base(stores={"max_stores": 1}))
        self.assertEqual(len(one["shopping_list"]["stores"]), 1)

    def test_allergy_excluded(self):
        for rid in dish_ids(self.family):
            self.assertNotIn("peanut", recipe_tags(RECIPES[rid], CAT))

    def test_vegetarian(self):
        res = run(base(diet={"restrictions": ["vegetarian"]}))
        bad = set(DIET_RESTRICTIONS["vegetarian"])
        for rid in dish_ids(res):
            self.assertFalse(bad & set(recipe_tags(RECIPES[rid], CAT)), rid)

    def test_pantry_reduces_cost(self):
        without = run(base())["summary"]["total"]
        with_p = run(base(pantry=["大米:5000", "鸡蛋:20", "番茄:2000", "北豆腐:2000"]))
        self.assertLess(with_p["summary"]["total"], without)
        self.assertTrue(with_p["pantry_used"])

    def test_coupon_never_increases_cost(self):
        plain = run(base())["summary"]["total"]
        cpn = run(base(coupons=[{"type": "percent_off", "store": "*", "categories": ["vegetable"], "percent": 30}]))
        self.assertLessEqual(cpn["summary"]["total"], plain + 1e-9)
        self.assertTrue(cpn["coupons"]["used"])

    def test_threshold_coupon_applied(self):
        res = run(base(household={"adults": 4}, stores={"allowed": ["market_a"], "max_stores": 1},
                       coupon_texts=["示例超市A：满50减10"]))
        st = res["shopping_list"]["stores"][0]
        self.assertGreaterEqual(st["subtotal"], 50)
        self.assertEqual([d["amount"] for d in st["order_discounts"]], [10])

    def test_delivery_fee(self):
        res = run(base(household={"adults": 1}, meals={"dinner": 1}, dishes_per_meal=1,
                       stores={"allowed": ["app_b"], "max_stores": 1}))
        st = res["shopping_list"]["stores"][0]
        self.assertLess(st["subtotal"], 59)
        self.assertEqual(st["delivery_fee"], 6)

    def test_price_override_used(self):
        res = run(base(stores={"allowed": ["market_a"], "max_stores": 1},
                       price_overrides=[{"store": "market_a", "ingredient": "egg", "pack_qty": 30, "price": 1.0}]))
        eggs = [i for i in res["shopping_list"]["stores"][0]["items"] if i["ingredient"] == "egg"]
        if eggs:
            self.assertEqual(eggs[0]["packs"][0]["pack_qty"], 30)
            self.assertAlmostEqual(eggs[0]["packs"][0]["unit_price"], 1.0)

    def test_over_budget(self):
        res = run(base(budget={"weekly": 10}))
        self.assertEqual(res["summary"]["status"], "over_budget")
        self.assertTrue(res["budget_tips"])

    def test_deterministic(self):
        self.assertEqual(run(copy.deepcopy(FAMILY)), run(copy.deepcopy(FAMILY)))

    def test_note_limits_and_tags(self):
        n = self.family["outputs"]["xiaohongshu_note"]
        self.assertLessEqual(len(n["title"]), 20)
        self.assertLessEqual(len(n["body"]), 1000)
        self.assertIn("#小红书Skill创新赛", n["body"])
        self.assertIn("示例数据", n["body"])

    def test_english_output(self):
        res = run(base(output={"lang": "en"}))
        self.assertIn("Weekly grocery savings plan", res["outputs"]["markdown"])

    def test_restrictive_diet_degrades_gracefully(self):
        res = run(base(diet={"restrictions": ["vegan", "soy_free", "gluten_free"]}, meals={"dinner": 7, "breakfast": 7}))
        self.assertTrue(res["meal_plan"])
        for rid in dish_ids(res):
            tags = set(recipe_tags(RECIPES[rid], CAT))
            self.assertFalse(tags & (set(DIET_RESTRICTIONS["vegan"]) | {"soy", "gluten"}), rid)

    def test_invalid_input_reports_all_errors(self):
        with self.assertRaises(InputError) as cm:
            run({"household": {"adults": -1}, "budget": {"weekly": "x"}, "unknown": 1})
        self.assertGreaterEqual(len(cm.exception.errors), 3)

    def test_zero_meals_rejected(self):
        with self.assertRaises(InputError):
            run(base(meals={"dinner": 0}))


if __name__ == "__main__":
    unittest.main()
