---
name: smart-grocery-saver
description: 家庭一周买菜省钱助手 / Household weekly grocery & coupon-saving planner. Use when a user wants a weekly meal plan and shopping list for their household that fits a budget, respects dietary needs/allergies, uses up pantry items, compares stores, stacks coupons (满减, 折扣, 买赠, 第二件半价, 立减) and produces a shareable Xiaohongshu (小红书) style savings note. Triggers - "一周买菜", "省钱菜单", "买菜预算", "优惠券怎么叠加", "weekly grocery budget", "meal plan with coupons".
license: MIT
metadata:
  version: "1.0.0"
  author: ZQ051031
  language: zh-CN, en
  runtime: python>=3.9 (standard library only, no network)
  entrypoint: scripts/grocery_saver.py
  manifest: skill.json
---

# Smart Grocery Saver · 省钱买菜助手

把“家里几口人、一周预算、忌口/过敏、冰箱里还有什么、手上有哪些券”变成：
**一周菜单 + 按商店分好的最省钱购物清单 + 优惠券叠加明细 + 一篇可直接发的小红书省钱笔记**。

All numbers come from a deterministic optimiser in `scripts/saver/` — never invent prices or savings yourself.

> ⚠️ `data/sample_catalog.json` contains **fictional sample stores and illustrative prices** (示例数据), not real
> or current prices of any real shop. Always tell the user this and offer to use their own prices.

## When to use
- 用户想控制每周买菜开销、想要一周菜单、想知道券怎么用最划算、想把省钱成果发小红书。
- The user pastes coupons/offers from grocery apps or flyers and asks which to use.
- Not for: restaurant ordering, nutrition/medical diet prescriptions, real-time price lookup (no network access).

## Workflow
1. **Collect inputs** (ask only for what is missing; sensible defaults exist):
   - household: adults / children · weekly budget (CNY) · which meals at home (breakfast/lunch/dinner days)
   - diet: restrictions (vegetarian, no_pork, no_spicy …), allergies (花生, 虾, 牛奶 …), dislikes, favourite dishes
   - pantry: what is at home, e.g. `"大米:3000"`, `"鸡蛋:6"` (units: g, ml, pcs)
   - coupons: paste the text as-is into `coupon_texts` (e.g. `"示例超市A：满100减15"`, `"牛奶 买2送1"`)
   - stores: max stores to visit, usual store, time cost per extra store; **real prices** if the user has them
2. **Write the request JSON** (schema: `schemas/input.schema.json`; examples in `examples/`).
3. **Run** (from this skill directory):
   ```bash
   python3 scripts/grocery_saver.py plan --input request.json --format json --out ./out
   ```
   - Real prices: `python3 scripts/grocery_saver.py export-prices > prices.csv`, let the user edit `price`
     (or add pack sizes / stores), then add `--prices prices.csv`.
   - Check coupon parsing first if unsure: `python3 scripts/grocery_saver.py parse-coupons "<text>" ...`
   - Exit code 2 = invalid input; show the listed errors to the user and fix the request.
4. **Present** from the JSON result (do not recompute):
   - `summary` (total vs budget, savings vs baseline, cost per portion) and `savings.steps`
   - `meal_plan`, `shopping_list.stores[*].items` (what to buy where, which deal applies)
   - `coupons.used` / `coupons.unused[*].reason`, `leftovers` (use first next week), `warnings`, `budget_tips`
   - `outputs.xiaohongshu_note` (title ≤ 20 chars, body ≤ 1000 chars, hashtags) — offer it for posting;
     the user posts it themselves.
5. **Learn-do-share loop**: when the user returns with comments/feedback from their note (e.g. "评论区想看
   可乐鸡翅", "孩子不吃茄子"), put them into `diet.favorites` / `diet.dislikes`, move this week's `leftovers`
   into `pantry`, and re-run for next week.

## How the optimiser works (short)
- **Meal planning**: recipes are filtered by diet/allergy tags, then a local search picks dishes minimising
  ingredient cost + a variety penalty (dish repeats, same protein too often, too few meat/fish mains), with
  shared ingredients across days to reduce waste. Pantry stock is consumed first.
- **Shopping**: for each ingredient it chooses the cheapest pack combination per store, evaluates every
  allowed coupon combination, assigns ingredients to at most `max_stores` stores (time cost per extra store),
  and moves items between stores when consolidating them reaches a 满减 threshold or free delivery.
- **Savings breakdown** vs a baseline (same meals at the usual store, no pantry, no coupons):
  pantry → store comparison → item coupons → order-level 满减 / free delivery.
Details: `references/optimization.md`, coupon rules: `references/coupon-rules.md`.

## Files
| Path | Purpose |
|---|---|
| `skill.json` | machine-readable manifest (inputs, outputs, commands, limits) |
| `schemas/input.schema.json` | request JSON Schema |
| `scripts/grocery_saver.py` | CLI: `plan`, `parse-coupons`, `export-prices`, `validate` |
| `scripts/saver/` | engine: `data`, `coupons`, `shopping`, `planner`, `engine`, `report` |
| `data/sample_catalog.json` | **sample** stores, ingredients, pack sizes, prices (editable) |
| `data/recipes.json` | home-style recipes with per-portion quantities and tags |
| `examples/*.json` | example requests; `examples/output/` sample results |

## Guardrails
- Prices/stores in the bundled catalog are samples; say so in every answer that quotes prices.
- Never claim a coupon is valid in a real shop; the user supplies coupons and is responsible for their terms.
- Allergy filtering relies on recipe/ingredient tags; remind users with severe allergies to check labels.
- No network calls, no account access, no purchases — the Skill only plans.
