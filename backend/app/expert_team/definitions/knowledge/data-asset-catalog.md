# 数据资产目录 (K1)

> 列出飞鹤 AnalyticDB (hs_poc) 所有可用表，含字段、粒度、更新频率、权限要求。
> 最后更新：2026-06-17（从数据库实时导出）

---

## 核心业务表（月度汇总）

### ads_bi_month_shop_item_dan_summary_df（终端销售月汇总表）

| 属性 | 值 |
|------|------|
| 粒度 | 月 × 大区 × 省区 × 城市 × 大系统渠道 × 品系 × 段位 × 终端圈层级 |
| 主键 | year + month + lev2_name + lev3_name + city + big_system_channel_name + item_series_new_name + item_dan_name |
| 更新频率 | T+1（月度） |
| 数据源 | AnalyticDB hs_poc |
| 权限要求 | 全角色可见（商分/全国无注入；大区总/省区总注入区域 WHERE） |

**维度字段：**

| 字段名 | 含义 | 枚举/说明 |
|--------|------|-----------|
| year | 年份 | "2025","2026",... |
| month | 月份 | "01"~"12" |
| lev2_name | 大区 | 直辖区/北一区/西一区/南二区/南一区/中二区/东一区/西二区/中一区 |
| lev3_name | 省区 | 如"皖南","豫中","鲁西" |
| city | 城市 | 如"宣城市" |
| big_system_channel_name | 大系统渠道 | 如"KSC西区","RKSC渠道" |
| item_series_new_name | 品系 | 如"星飞帆卓耀铂金（有机A2 β-酪蛋白）" |
| item_dan_name | 段位 | 1段/2段/3段 |
| terminal_circle_level | 终端圈层级 | 如"加盟型大系统KSC西区" |

**指标字段：**

| 字段名 | 含义 | 单位 |
|--------|------|------|
| terminal_sale_amount | 终端销售额 | 元 |
| reduction_gd_sale_amount | 出货销售额 | 元 |

---

### ads_bi_month_shop_item_dan_target_summary_df（终端销售目标月汇总表）

| 属性 | 值 |
|------|------|
| 粒度 | 月 × 大区 × 省区 × 大系统渠道 |
| 主键 | year + month + lev2_name + lev3_name + big_system_channel_name |
| 更新频率 | T+1（月度） |
| 权限要求 | 同终端销售表 |

**注意**：此表无 city 列，按区域查询时只能用 lev2_name / lev3_name。

**指标字段：**

| 字段名 | 含义 | 单位 |
|--------|------|------|
| shop_sale_target | 终端销售目标 | 元 |
| shop_sale_amount | 终端销售额 | 元 |
| gd_target | 出货目标 | 元 |
| gd_amount | 出货额 | 元 |

---

### ads_bi_month_shop_item_dan_detail_df（终端销售明细表）

| 属性 | 值 |
|------|------|
| 粒度 | 月 × 大区 × 省区 × 城市 × 门店 × 品系 × 段位 |
| 主键 | year + month + lev2_name + lev3_name + shop_code |
| 更新频率 | T+1（月度） |
| 权限要求 | 同终端销售表 |

额外字段：shop_code, shop_name, province。含终端销售额和出货额。

---

### ads_member_first_purchase_new_customer_total_df（新客月汇总表）

| 属性 | 值 |
|------|------|
| 粒度 | 月 × 大区 × 省区 × 大系统渠道 × 段位 |
| 主键 | year + month + lev2_name + lev3_name + big_system_channel_name + item_dan_name |
| 更新频率 | T+1（月度） |
| 权限要求 | 全角色可见 |

**指标字段：**

| 字段名 | 含义 | 单位 |
|--------|------|------|
| first_purchase_num | 首购新客数 | 人 |
| repurchase_in_60_days_num | 60天内复购数 | 人 |
| heli30_new_customer_num | 合力30新客数 | 人 |
| heli30_repurchase_in_60_days_num | 合力30复购数 | 人 |

---

### ads_precision_nutrition_potential_total_df（潜客月汇总表）

| 属性 | 值 |
|------|------|
| 粒度 | 月 × 大区 × 省区 × 大系统渠道 |
| 主键 | year + month + lev2_name + lev3_name + big_system_channel_name |
| 更新频率 | T+1（月度） |
| 权限要求 | 全角色可见 |

**指标字段：**

| 字段名 | 含义 | 单位 |
|--------|------|------|
| potential_num | 潜客数 | 人 |
| potential_to_new_num | 潜客转新客数 | 人 |

---

## 驾驶舱/看板表

### ads_bi_cockpit_lev3_name_watch_ask_df（省区驾驶舱月表，61列）

| 属性 | 值 |
|------|------|
| 粒度 | 月 × 大区 × 省区 |
| 更新频率 | T+1（月度） |
| 权限要求 | 省区经理以上 |

核心指标：月度出货额、终端销售额、新客数、目标达成率、营销费用、利润额等。
详尽的字段列表见数据库 SHOW COLUMNS。

### ads_bi_awake_shop_index_detail_m_df（门店指标月明细表，64列）

| 属性 | 值 |
|------|------|
| 粒度 | 月 × 门店 × 人员 × 渠道 |
| 更新频率 | T+1（月度） |
| 权限要求 | 省区经理以上 |

核心指标：门店日均销售额、合力3新客、段位复购率、企微任务完成率等。

### ads_bi_awake_person_index_summary_df（人员指标月汇总表，101列）

| 属性 | 值 |
|------|------|
| 粒度 | 月 × 人员 × 省区 |
| 更新频率 | T+1（月度） |
| 权限要求 | 省区经理以上 |

核心指标：个人销售达成率、新客数、复购率、活动场次、企微添加率等。

---

## 明细/交易表

### ads_bi_hs_sale_info_df（销售信息明细表，25列）

| 属性 | 值 |
|------|------|
| 粒度 | 月 × 业务代表 × 导购 × 门店 × 单品 |
| 更新频率 | T+1（月度） |
| 权限要求 | 省区经理以上 |

核心字段：acc_month, business_representative, guide, shop, channel_type, item, shop_sale_qty, shop_sale_amount, gd_amount。

---

## 旧表（保留兼容）

| 表名 | 字段数 | 说明 |
|------|--------|------|
| terminal_sales | 11 | 旧终端销售表（region/sub_region维度） |
| new_customer | 10 | 旧新客表（region/sub_region维度） |
| potential_customer | 7 | 旧潜客表 |
| target | 9 | 旧目标表 |
| hs_test | 1 | 测试表 |

> **注意**：旧表使用 region/sub_region 维度名，新表使用 lev2_name/lev3_name。优先使用新表。

---

## 数据库连接信息

| 属性 | 值 |
|------|------|
| 类型 | AnalyticDB (MySQL兼容) |
| 库名 | hs_poc |
| 连接方式 | 通过 db_query.py（~/.workbuddy/skills/datachat-query/scripts/db_query.py） |
| 认证 | 内置于 db_query.py 配置 |
