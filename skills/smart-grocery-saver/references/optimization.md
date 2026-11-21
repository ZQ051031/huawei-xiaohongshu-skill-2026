# Optimisation details

**1. Normalisation** (`saver/data.py`). The request is validated against `schemas/input.schema.json` (all
errors reported at once), merged with defaults, and resolved: Chinese/English ingredient aliases, store names,
pantry strings (`鸡蛋:6`), coupon texts, price overrides, diet restrictions → forbidden tags.
Portions per meal = adults + children × `child_portion` (default 0.6).

**2. Meal plan** (`saver/planner.py`). Slots: breakfast / lunch / dinner per selected day; lunch & dinner get
`dishes_per_meal` dishes (main, + vegetable side, + soup) plus rice. Recipes containing forbidden tags or
disliked ingredients are excluded (listed in `excluded_recipes`). A greedy start plus local search minimises

    marginal ingredient cost (after pantry) + variety_weight × (dish repeats beyond max_repeats,
    same main protein too often, too few meat/fish mains vs min_meat_ratio) − favourite bonus

Reusing an ingredient bought in a pack lowers marginal cost, which naturally reduces waste. If safe recipes are
too few, repeats are relaxed and a warning is emitted. If the balanced plan is over budget, a second plan with
`variety_weight = 0` is tried ("budget mode").

**3. Purchase needs.** Consumption is summed per ingredient; pantry stock is subtracted; staples (oil, salt,
soy sauce, vinegar, sugar, starch) are assumed at home when `assume_staples` is true and listed to check.

**4. Shopping** (`saver/shopping.py`). For each ingredient × store, every pack-count vector covering the need
(plus extra packs to unlock buy-x-get-y) is priced with every allowed item-coupon set; the cheapest wins.
For each store subset of size ≤ `max_stores`, ingredients start at their cheapest store and a local search
moves items between stores when that lowers
`Σ(item cost) − order discounts + delivery fees + extra_store_cost × (stores − 1)`
— e.g. consolidating items to reach a 满减 threshold or free delivery.

**5. Savings breakdown** (`saver/engine.py`). Baseline = same meals bought at the default store with the
cheapest packs, no pantry, no coupons. Then sequentially: pantry → store comparison (best stores, no coupons)
→ item coupons → order-level coupons/delivery. Steps sum exactly to `baseline − total`.

**6. Outputs** (`saver/report.py`). Markdown report and a Xiaohongshu note (title ≤ 20 chars, body ≤ 1000
chars incl. hashtags, sample-data disclaimer when the bundled catalog is used).

The search is heuristic (local search, not an exact MILP) but deterministic; it runs in well under a second.
