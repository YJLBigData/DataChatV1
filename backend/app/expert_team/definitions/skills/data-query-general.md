# 通用数据查询 Skill (data-query-general)

> 飞鹤 AnalyticDB 快速取数，服务速捷和全分析师的通识层。
> 优先级：各领域专项 Skill > 本通识 > 自主推理

## 触发词

| 用户表达 | 路由 |
|---------|------|
| "XX多少"、"查一下XX"、"XX趋势" | 快速取数 |
| "对比XX和XX"、"XX占比" | 指标对比 |
| "最新数据"、"本月XX" | 当期速览 |

## 输入

| 参数 | 必选 | 说明 |
|------|------|------|
| query | 是 | 自然语言查询意图 |
| entity | 否 | 区域/渠道/产品名称 |
| time_range | 否 | 时间范围（默认当月） |
| metrics | 否 | 指标名（销售额/达成率/新客数等） |

## 输出

按 K5 output-format-spec.md 的快查询格式：
```markdown
[数据查询结果]
### 核心指标摘要
- {指标1}：{值}（同比 {X%} / 环比 {X%}）
- {指标2}：{值}（同比 {X%} / 环比 {X%}）

### 数据表
| 维度 | {指标1} | {指标2} |
|------|---------|---------|
| ... | ... | ... |

### 元信息
- 数据时间范围：YYYY-MM ~ YYYY-MM
- 查询表：{表名}
```

## SOP（5步）

### 步骤1：意图解析
- 从自然语言提取查询对象、指标、维度、时间
- 参照 K1 data-asset-catalog.md 确定目标表
- 参照 K2 data-dictionary.md 确认指标口径

### 步骤2：表路由
| 指标类型 | 目标表 | 关键字段 |
|----------|--------|----------|
| 终端销售额/出货额 | ads_bi_month_shop_item_dan_summary_df | terminal_sale_amount, reduction_gd_sale_amount |
| 目标/达成率 | ads_bi_month_shop_item_dan_target_summary_df | shop_sale_target, shop_sale_amount |
| 新客数/复购 | ads_member_first_purchase_new_customer_total_df | first_purchase_num, repurchase_in_60_days_num |
| 潜客 | ads_precision_nutrition_potential_total_df | potential_num, potential_to_new_num |
| 驾驶舱综合 | ads_bi_cockpit_lev3_name_watch_ask_df | 见K1（61列） |
| 门店明细 | ads_bi_awake_shop_index_detail_m_df | 见K1（64列） |

### 步骤3：SQL 生成
- 生成 SELECT 语句，含 WHERE 条件（时间+维度+实体）
- 注意：target 表无 city 列，按区域查询只用 lev2_name / lev3_name
- 环比计算：本月日均 / 近3月日均（需查3个月数据）
- 同比计算：(本期 - 去年同期) / 去年同期

### 步骤4：执行查询
- MVP 阶段：调用 db_query.py（~/.workbuddy/skills/datachat-query/scripts/db_query.py）
- 或调用 diagnosis.py（已封装常用查询）
- 生产环境：调用 router.py（含权限注入）

### 步骤5：结果格式化
- 返回数值 + 同比环比
- 1句话关键发现
- 元信息（时间范围、查询表）

## 依赖

- `datachat-query` skill（db_query.py）
- `diagnosis-data-router` skill（router.py，含权限注入）
- `region-sales-diagnosis` scripts（diagnosis.py，已封装常用查询）

## 约束

- 所有查询必须走 router.py 权限注入（生产环境）
- MVP 阶段可直连 db_query.py
- 禁止编造不存在的数据
- 禁止自行计算复合指标（必须用 SQL 计算）
- 维度枚举值参照 K1 / K2，不用猜测
