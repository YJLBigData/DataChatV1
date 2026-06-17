# 用户运营通识 Skill (user-ops-general)

> 甄客来的通识层 Skill，覆盖用户运营领域通用分析框架。
> 优先级：专项(newcustomer-diagnosis) > 本通识 > 自主推理

## 触发词
| 用户表达 | 动作 |
|---------|------|
| "新客趋势"、"段位结构"、"复购率" | 启用本通识 |
| "新客诊断"、"新客结构异常" | 转专项 newcustomer-diagnosis |

## 输入
- year, month
- 可选：lev2_name, big_system_channel_name, item_dan_name（段位）
- 分析维度提示：趋势/结构/转化

## 输出
- 按 K5 分析报告格式

## SOP（5步）

### 步骤1：确认分析范围
- 明确时间范围和区域/渠道/段位过滤

### 步骤2：查数据
- 新客表：ads_member_first_purchase_new_customer_total_df
- 潜客表：ads_precision_nutrition_potential_total_df
- 销售表：ads_bi_month_shop_item_dan_summary_df

### 步骤3：新客趋势分析
- 总新客数同比/环比
- 按段位拆分（1段/2段/3段占比）

### 步骤4：转化漏斗
- 潜客→新客转化率
- 新客→复购转化率
- 各环节同比变化

### 步骤5：结论输出
- 核心发现 ≤3条
- 建议方向 ≤3条
- 引用指标清单（口径见 K2）

## 约束
- 不做深度新客诊断（走专项 newcustomer-diagnosis）
- 本 Skill 是通识兜底
