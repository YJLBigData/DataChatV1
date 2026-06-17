# 数据字典 (K2)

> 每个指标的字段口径定义：计算公式、来源字段、注意事项、枚举值。
> 最后更新：2026-06-17

---

## 终端销售类

### terminal_sale_amount（终端销售额）

| 属性 | 值 |
|------|------|
| 定义 | 终端门店实际零售金额（含促销折扣后的实收） |
| 计算公式 | SUM(terminal_sale_amount)，直接汇总 |
| 单位 | 元 |
| 来源表 | ads_bi_month_shop_item_dan_summary_df, ads_bi_month_shop_item_dan_detail_df |
| 注意 | 不含经销商出货额；与 reduction_gd_sale_amount 是两个口径 |
| 枚举值 | 无 |

### reduction_gd_sale_amount（出货销售额）

| 属性 | 值 |
|------|------|
| 定义 | 经销商向门店供货的出货金额（含折扣扣减） |
| 计算公式 | SUM(reduction_gd_sale_amount)，直接汇总 |
| 单位 | 元 |
| 来源表 | ads_bi_month_shop_item_dan_summary_df |
| 注意 | 出货额 > 终端销售额 时说明渠道有库存积压 |
| 枚举值 | 无 |

### shop_sale_target（终端销售目标）

| 属性 | 值 |
|------|------|
| 定义 | 月度终端销售考核目标 |
| 计算公式 | 目标表直接取值，不计算 |
| 单位 | 元 |
| 来源表 | ads_bi_month_shop_item_dan_target_summary_df |
| 注意 | 目标按 lev2+lev3+渠道 拆分，不含城市维度 |
| 枚举值 | 无 |

### shop_sale_amount（终端销售额-目标表）

| 属性 | 值 |
|------|------|
| 定义 | 与 terminal_sale_amount 口径一致，仅存在目标表中 |
| 计算公式 | 同 terminal_sale_amount |
| 单位 | 元 |
| 来源表 | ads_bi_month_shop_item_dan_target_summary_df |

### gd_target / gd_amount（出货目标/出货额）

| 属性 | 值 |
|------|------|
| 定义 | 出货口径的目标和实际额 |
| 来源表 | ads_bi_month_shop_item_dan_target_summary_df |

---

## 新客类

### first_purchase_num（首购新客数）

| 属性 | 值 |
|------|------|
| 定义 | 首次购买飞鹤产品的用户数，按门店会员系统去重 |
| 计算公式 | COUNT(DISTINCT member_id) WHERE first_purchase_flag = 1 |
| 单位 | 人 |
| 来源表 | ads_member_first_purchase_new_customer_total_df |
| 注意 | 按门店去重，非按手机号去重 |
| 枚举值 | 无 |

### repurchase_in_60_days_num（60天复购数）

| 属性 | 值 |
|------|------|
| 定义 | 首购后60天内再次购买的新客数 |
| 计算公式 | 新客中 60 天内产生第2次购买的人数 |
| 单位 | 人 |
| 来源表 | ads_member_first_purchase_new_customer_total_df |
| 注意 | 复购率 = repurchase_in_60_days_num / first_purchase_num |

### heli30_new_customer_num / heli30_repurchase_in_60_days_num（合力30新客/复购）

| 属性 | 值 |
|------|------|
| 定义 | 合力30项目下的新客及复购统计 |
| 来源表 | ads_member_first_purchase_new_customer_total_df |
| 注意 | 当前数据全为0，可能未启用或口径不同 |

---

## 潜客类

### potential_num（潜客数）

| 属性 | 值 |
|------|------|
| 定义 | 门店辐射范围内有购买可能的潜在客户数 |
| 计算公式 | 系统基于周边人口+画像估算 |
| 单位 | 人 |
| 来源表 | ads_precision_nutrition_potential_total_df |
| 注意 | 潜客为估算值，非实际登记 |

### potential_to_new_num（潜客转新客数）

| 属性 | 值 |
|------|------|
| 定义 | 潜客中实际完成首购的人数 |
| 计算公式 | 潜客转化率 = potential_to_new_num / potential_num |
| 单位 | 人 |
| 来源表 | ads_precision_nutrition_potential_total_df |

---

## 维度枚举值

### lev2_name（大区）

| 枚举值 | 说明 |
|--------|------|
| 直辖区 | 北京+特殊区域 |
| 北一区 | 东北+华北 |
| 西一区 | 西北 |
| 南一区 | 华南+东南 |
| 南二区 | 华南偏西 |
| 中一区 | 华中 |
| 中二区 | 华中偏南 |
| 东一区 | 华东 |
| 西二区 | 西南 |

### item_dan_name（段位）

| 枚举值 | 含义 | 目标人群 |
|--------|------|----------|
| 1段 | 0-6个月 | 新生儿，高频+高复购，新客获取核心战场 |
| 2段 | 6-12个月 | 竞品抢夺高峰期，流失率最高 |
| 3段 | 12-36个月 | 客单价较低，但用户基数大 |

### big_system_channel_name（大系统渠道）

常见值：RKSC渠道、KSC东区、KSC西区、KSC南区、KSC北区、常规渠道等。
（完整枚举需从数据库 SELECT DISTINCT 获取，约20+个值）

---

## 计算口径约定

| 指标 | 口径 | 公式 |
|------|------|------|
| 达成率 | 终端销售口径 | shop_sale_amount / shop_sale_target × 100% |
| 环比(新) | 日均对比 | 本月日均销售额 / 近三月日均销售额 |
| 同比 | 同月对比 | (本期 - 去年同期) / 去年同期 × 100% |
| 复购率 | 60天口径 | repurchase_in_60_days_num / first_purchase_num × 100% |
| 潜客转化率 | 当月口径 | potential_to_new_num / potential_num × 100% |
