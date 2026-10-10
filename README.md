# 味觉编译器 · mcd-taste-compiler

> 不用看图选餐。说一句你现在的状态，编译出这家麦当劳**此刻真能点到**的风味组合。

```
$ taste-compiler --mood "加班到11点，牛马麻了"
✔ 识别场景：加班到深夜（依据：加班、牛马）

[方案 1]  ¥54.4  ·  913 kcal
  巨无霸        多汁/软嫩/咸香/浓郁/酸·热
  麦辣鸡翅      脆/多汁/辣/咸香·热
  浓浓黑巧      顺滑/浓郁/甜·热
  风味：咸香███  甜█  酸█  辣█  浓郁███

// 编译还没过，先给胃打个补丁
```

## 它做什么

- **听得懂网络用语**：emo、破防、牛马、蚌埠住、热死、饿醒……映射到 13 个场景
- **只推荐点得到的**：候选来自门店实时菜单（麦当劳 MCP `query-meals`），早餐/正餐时段自动切换
- **价格是真的**：每套方案调用 `calculate-price` 算出应付价
- **风味可视化**：79 个单品的口感/温度/味型标签，生成风味分布
- **确认后可下单**：说"就这个"，复述确认后调用 `create-order` 返回支付链接

## 目标用户

不知道吃什么、懒得翻菜单的打工人和学生；想把点餐变成一点小乐趣的人。

## 安装

### 1. 申请 MCP Token

登录 [open.mcd.cn/mcp](https://open.mcd.cn/mcp) → 控制台 → 激活，复制 Token。

### 2. 配置 MCP 连接器

把 [`mcp-config.example.json`](./mcp-config.example.json) 加到你的 MCP 客户端（WorkBuddy / Claude Code / Cursor 等），将 `${MCD_MCP_TOKEN}` 替换为你的 Token。

### 3. 安装 Skill

```bash
git clone https://github.com/<you>/mcd-taste-compiler
# WorkBuddy
ln -s "$PWD/mcd-taste-compiler" ~/.workbuddy/skills/mcd-taste-compiler
# Claude Code
ln -s "$PWD/mcd-taste-compiler" ~/.claude/skills/mcd-taste-compiler
```

### 4. 命令行直接用（可选）

只需 Python 3.8+，无第三方依赖：

```bash
export MCD_MCP_TOKEN=你的Token
python3 scripts/compile.py --mood "今天emo了" --store <storeCode>
```

## 使用示例

在 Agent 里直接说：

- "加班到现在，牛马麻了，国贸附近"
- "热死了，想吃点冰的"
- "周末带娃，在望京"
- "凌晨饿醒了"

## 场景一览

| 场景 | 典型说法 |
|---|---|
| 加班到深夜 | 加班、牛马、996、修仙 |
| 周一早上起不来 | 周一、早八、困成狗 |
| 刚考完试 / 刚上线 | 考完、发版、狂喜 |
| 下雨不想出门 | 下雨、宅家、降温 |
| 热到不想动 | 热死、化了 |
| 想吃点刺激的 | 嘴里没味、上头 |
| 下午有点饿 | 嘴巴寂寞、垫一口 |
| 刚运动完 | 撸铁、跑完 |
| 和朋友一起 | 开黑、团建 |
| 今天有点丧 | emo、破防、麻了、蚌埠住 |
| 约会 | 约会、见网友 |
| 带娃 | 遛娃、神兽 |
| 凌晨饿醒 | 半夜、睡不着 |

## 项目结构

```
SKILL.md                 Agent 使用说明
scripts/compile.py       编译引擎（只调用只读 MCP 工具）
data/flavor-tags.json    单品风味标签 + 营养名称映射
data/scenes.json         场景 → 风味向量
data/slang.json          网络用语 → 场景
MCP_INTEGRATION.md       MCP 工具与调用流程
```

## 声明

本项目为麦当劳程序员节创意开发大赛参赛作品，由参赛者独立开发，非麦当劳官方产品。输出仅供娱乐参考，不构成医疗、营养或其他专业建议；餐品信息、价格及供应状态以麦当劳官方渠道实时结果为准。详见 [CONTEST_DECLARATION.md](./CONTEST_DECLARATION.md)。
