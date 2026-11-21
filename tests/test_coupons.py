import unittest

import _path  # noqa: F401
from saver.coupons import best_order_discount, normalize_coupon, parse_coupon_text, product_cost, threshold_discount
from saver.data import InputError, load_catalog

CAT = load_catalog()


def item(**kw):
    c = {"type": "percent_off", "store": "*", "ingredients": ["egg"]}
    c.update(kw)
    return normalize_coupon(c, CAT, 0)


class ParseTests(unittest.TestCase):
    def test_threshold_with_store(self):
        c = parse_coupon_text("示例超市A：满100减15", CAT)
        self.assertEqual((c["type"], c["store"], c["threshold"], c["amount"]), ("threshold_off", "market_a", 100, 15))

    def test_percent_discount_zhe(self):
        c = parse_coupon_text("鸡蛋8折", CAT)
        self.assertEqual(c["type"], "percent_off")
        self.assertAlmostEqual(c["percent"], 20)
        self.assertEqual(c["ingredients"], ["egg"])

    def test_buy_x_get_y(self):
        c = parse_coupon_text("app_b: 牛奶 买2送1", CAT)
        self.assertEqual((c["type"], c["store"], c["buy"], c["get"]), ("buy_x_get_y", "app_b", 2, 1))

    def test_second_half_price(self):
        c = parse_coupon_text("第二件半价 牛奶", CAT)
        self.assertEqual((c["type"], c["n"]), ("nth_percent", 2))
        self.assertAlmostEqual(c["percent"], 50)

    def test_fixed_off(self):
        c = parse_coupon_text("豆腐立减1元", CAT)
        self.assertEqual((c["type"], c["amount"]), ("fixed_off_item", 1))

    def test_english(self):
        c = parse_coupon_text("spend 80 get 10 off at app_b", CAT)
        self.assertEqual((c["type"], c["store"], c["threshold"], c["amount"]), ("threshold_off", "app_b", 80, 10))

    def test_unparseable(self):
        with self.assertRaises(InputError):
            parse_coupon_text("今天天气不错", CAT)


class MathTests(unittest.TestCase):
    def test_percent(self):
        self.assertAlmostEqual(product_cost(10.0, 3, [item(percent=20)])[0], 24.0)

    def test_buy2_get1(self):
        cost, free = product_cost(10.0, 3, [item(type="buy_x_get_y", buy=2, get=1)])[:2]
        self.assertAlmostEqual(cost, 20.0)
        self.assertEqual(free, 1)

    def test_second_half(self):
        self.assertAlmostEqual(product_cost(10.0, 2, [item(type="nth_percent", n=2, percent=50)])[0], 15.0)

    def test_fixed_off_never_negative(self):
        self.assertAlmostEqual(product_cost(1.0, 2, [item(type="fixed_off_item", amount=5)])[0], 0.0)

    def test_threshold_repeatable_and_cap(self):
        c = normalize_coupon({"type": "threshold_off", "threshold": 50, "amount": 10, "repeatable": True,
                              "max_discount": 25}, CAT, 0)
        self.assertAlmostEqual(threshold_discount(c, 120), 20)
        self.assertAlmostEqual(threshold_discount(c, 200), 25)
        self.assertAlmostEqual(threshold_discount(c, 49.99), 0)

    def test_non_stackable_threshold_picks_best(self):
        a = normalize_coupon({"type": "threshold_off", "threshold": 50, "amount": 5, "stackable": False}, CAT, 0)
        b = normalize_coupon({"type": "threshold_off", "threshold": 100, "amount": 15, "stackable": False}, CAT, 1)
        disc = best_order_discount(120, 120, [a, b])[0]
        self.assertAlmostEqual(disc, 15)

    def test_invalid_coupon(self):
        with self.assertRaises(InputError):
            normalize_coupon({"type": "percent_off", "percent": 150}, CAT, 0)


if __name__ == "__main__":
    unittest.main()
