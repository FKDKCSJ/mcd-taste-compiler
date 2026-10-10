# MCP 集成说明

## 使用的 MCP Server

麦当劳中国 MCP Server：`https://mcp.mcd.cn`（Streamable HTTP，`Authorization: Bearer <MCP Token>`）

## 使用的 Tool

| Tool | 读/写 | 在本项目中的作用 |
|---|---|---|
| `query-nearby-stores` | 读 | 按用户位置找到店/得来速门店，拿 `storeCode` / `beCode` |
| `delivery-query-addresses` / `delivery-query-stores` | 读 | 外送场景下确定可配送门店 |
| `query-meals` | 读 | 拉取门店**当前时段**真实在售菜单（编码、名称、现价），推荐只在其中挑选 |
| `list-nutrition-foods` | 读 | 营养成分，用于卡片热量展示与"运动后"场景按蛋白质排序 |
| `calculate-price` | 读 | 对每套推荐组合计算真实应付价（含优惠） |
| `create-order` | **写** | 仅在用户明确确认后由 Agent 调用，返回支付链接 |

## 调用流程

```
用户一句话（心情/网络用语）
   │
   ▼
场景识别（data/slang.json + data/scenes.json，本地完成）
   │            └─ 命中危机词 → 停止推荐，给出求助热线
   ▼
query-nearby-stores ──► storeCode
   │
   ▼
query-meals(storeCode) ──► 当前在售单品
list-nutrition-foods   ──► 营养数据
   │
   ▼
风味打分与组合（data/flavor-tags.json，本地完成）
   │   在售单品 ∩ 风味标签表 → 按场景风味向量打分 → 主食/小食/饮品槽位组合 → 取 Top 3
   ▼
calculate-price × 3 ──► 每套真实价格
   │
   ▼
味觉编译卡片（场景 · 单品 · 价格 · 热量 · 风味分布 · 梗）
   │
   ▼（用户确认后）
create-order ──► 支付链接
```

## 设计要点

- **只推荐点得到的东西**：候选集严格取自 `query-meals` 的实时返回，早餐/正餐时段、门店差异自动生效。
- **价格不靠估算**：展示价来自 `calculate-price`，不是菜单价相加。
- **营养名称对齐**：菜单名（如"薯条"）与营养表名（如"中薯条"）不一致，`flavor-tags.json` 的 `nutrition` 字段做显式映射。
- **写操作隔离**：脚本 `scripts/compile.py` 只调用只读工具，下单由 Agent 在用户确认后单独调用。

## 业务价值

- 解决"不知道吃什么"的决策疲劳：用户只需表达状态，不用翻菜单。
- 把一次点餐变成可分享的"味觉编译卡片"，带来自然传播。
- 推荐结果直接可下单，缩短从"想吃"到"下单"的路径。
