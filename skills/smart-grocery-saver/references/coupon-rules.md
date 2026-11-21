# Coupon types & stacking rules / 优惠券类型与叠加规则

| `type` | 中文示例 (粘贴到 `coupon_texts`) | Fields | Effect |
|---|---|---|---|
| `percent_off` | `鸡蛋8折`, `蔬菜9折`, `豆腐 8折` | `percent` (20 = 20% off), `ingredients`/`categories` | price × (1 − percent/100) |
| `fixed_off_item` | `豆腐立减1元` | `amount`, `max_uses` | −amount per pack (never below 0) |
| `buy_x_get_y` | `牛奶买2送1`, `buy 2 get 1 free` | `buy`, `get` | every buy+get packs, `get` are free; the optimiser may take the free pack |
| `nth_percent` | `第二件半价` | `n`, `percent` | every n-th paid pack gets percent off |
| `threshold_off` | `满100减15`, `每满50减5`, `满59减8 最多减20`, `spend 80 get 10 off` | `threshold`, `amount` or `percent`, `repeatable`, `max_discount`, `excludes_discounted` | order-level discount per store |

Common fields: `store` (`"*"` = any store; a store id/name prefix like `示例超市A：` sets it), `stackable`
(default true; text `不可叠加` / `不与其他优惠同享` sets false), `valid_until` (`YYYY-MM-DD`, expired coupons are
listed as unused), `label`.

## Stacking
1. Item level, per product line: all *stackable* item coupons for that store+ingredient combine in the order
   buy-x-get-y → n-th item → percent → fixed. A *non-stackable* item coupon is evaluated alone; the optimiser
   keeps whichever option is cheaper.
2. Order level, per store: stackable threshold coupons combine; non-stackable ones are used alone; the best
   combination wins. Eligibility uses the subtotal after item discounts, or the list-price subtotal before item
   discounts when `excludes_discounted` is true (text `特价除外`).
3. Item and order discounts combine (typical for Chinese grocery apps). If a real shop forbids this, mark the
   coupon `stackable: false`.
4. Delivery fee is added per store unless the post-discount subtotal reaches `free_delivery_threshold`.

These are modelling rules, not the terms of any real shop — the user is responsible for real coupon terms.
