---
name: mcd-taste-compiler
description: 味觉编译器：用户用一句话或网络用语描述此刻的心情/场景（如"加班到11点牛马麻了""今天emo了""热死了"），编译成附近麦当劳门店当前真实可点的风味组合，附真实价格、热量和风味雷达，确认后可下单。用户说"不知道吃什么""根据我现在的状态推荐麦当劳""编译一下我的心情"时使用。依赖 mcd-mcp 连接器。
---

# 味觉编译器

把"我现在什么状态"编译成"这家店此刻能点到的风味组合"。定位是好玩、轻松，**不是**营养或健康建议。

## 前置

- 已配置 mcd-mcp 连接器（`https://mcp.mcd.cn`）
- 运行脚本需要环境变量 `MCD_MCP_TOKEN`（与连接器同一个 Token）

## 流程

1. **识别心情**：把用户原话（含网络用语、emoji）原样作为 `--mood`，不要改写。
2. **定门店**：问用户在哪（城市+地标），调用 `query-nearby-stores`（`searchType=2`，`beType=1` 到店；想得来速用 `beType=5` 并记下 `beCode`）。外送则走 `delivery-query-addresses` → `delivery-query-stores`（`beType=2`）。
3. **编译**：
   ```bash
   python3 scripts/compile.py --mood "<用户原话>" --store <storeCode> [--be-type 2 --be-code <beCode>] --format json
   ```
   脚本内部调用 `query-meals`、`list-nutrition-foods`、`calculate-price`（全部只读），返回最多 3 套方案。
4. **展示**：用卡片形式展示每套方案：场景、单品、真实价格（`price.price`，单位元）、热量（`kcal` 为 null 时写"部分暂无"）、风味分布、场景梗（`scene.quip`）。结尾固定附一句"仅供娱乐参考，不构成营养建议；价格以下单页为准"。
5. **下单（可选）**：只有用户明确说"就这个/下单"时，才调用 `create-order`。下单前把门店、单品、数量、总价完整复述一遍，得到"确认"后再调用。返回的支付链接交给用户自己支付。

## 红线（必须遵守）

- **退出码 3 = 危机信号**（用户表达轻生等）。不推荐任何食物，直接用温和的语气转述脚本给出的求助热线信息，不说教。
- 不使用"治疗、缓解焦虑、调节激素、补充血清素"这类功效说法；不把食物描述成解决情绪问题的办法。
- 不和其他餐饮品牌做对比；不鼓励暴食、比赛吃、过量点单。
- 未经用户确认，绝不调用 `create-order`、`mall-create-order`、`draw-lottery`、`auto-bind-coupons` 等写操作。
- 不输出、不记录用户的 Token。

## 数据文件

- `data/flavor-tags.json`：单品风味标签（口感/温度/味型/份量）与营养表名称映射
- `data/scenes.json`：场景 → 期望风味向量 + 程序员梗
- `data/slang.json`：网络用语 → 场景；`crisis` 为危机关键词
