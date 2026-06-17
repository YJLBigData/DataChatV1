# 渠道通识 Skill (channel-general)

> 路通达的通识层 Skill，覆盖渠道领域通用分析框架。
> 优先级：专项(channel-diagnosis) > 本通识 > 自主推理

## 触发词
| 用户表达 | 动作 |
|---------|------|
| "渠道结构"、"渠道占比"、"RKSC表现" | 启用本通识 |
| "渠道诊断"、"渠道健康" | 转专项 channel-diagnosis |

## 输入
- year, month
- 可选：big_system_channel_name, lev2_name
- 分析维度提示：结构/效率/对比

## 输出
- 按 K5 分析报告格式

## SOP（5步）

### 步骤1：确认分析范围
- 明确时间范围和渠道/区域过滤

### 步骤2：查数据
- 主表：ads_bi_month_shop_item_dan_summary_df（按 big_system_channel_name 汇总）
- 目标表：ads_bi_month_shop_item_dan_target_summary_df

### 步骤3：渠道结构分析
- 各渠道销售额占比
- 渠道占比变化趋势

### 步骤4：渠道效率对比
- 各渠道单店产出
- 各渠道达成率对比

### 步骤5：结论输出
- 核心发现 ≤3条
- 建议方向 ≤3条
- 引用指标清单（口径见 K2）

## 约束
- 不做深度渠道诊断（走专项 channel-diagnosis）
- 本 Skill 是通识兜底
