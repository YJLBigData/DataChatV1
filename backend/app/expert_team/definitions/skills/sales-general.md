# 销售通识 Skill (sales-general)

> 齐增辉的通识层 Skill，覆盖销售领域通用分析框架和数据查询逻辑。
> 优先级：专项(region-sales-diagnosis/terminal-sales-attribution) > 本通识 > 自主推理

## 触发词
| 用户表达 | 动作 |
|---------|------|
| "销售趋势"、"品类表现"、"区域对比" | 启用本通识 |
| "销售诊断"、"达成率异常" | 转专项 region-sales-diagnosis |
| "销售归因"、"变化原因" | 转专项 terminal-sales-attribution |

## 输入
- year, month（时间范围）
- 可选：lev2_name（大区）, lev3_name（省区）, big_system_channel_name（渠道）
- 分析维度提示：趋势/结构/对比

## 输出
- 按 K5 output-format-spec.md 的分析报告格式

## SOP（5步）

### 步骤1：确认分析范围
- 明确时间范围和区域/渠道过滤
- 确认用户关心的指标（终端销售额/出货额/达成率）

### 步骤2：查数据
- 主表：ads_bi_month_shop_item_dan_summary_df
- 目标表：ads_bi_month_shop_item_dan_target_summary_df
- 通过 datachat-query 或 diagnosis.py 获取数据

### 步骤3：趋势分析
- 同比：去年同期对比
- 环比：本月日均 vs 近3月日均
- 判断增长/下滑方向

### 步骤4：结构拆分
- 按区域（大区/省区）拆分贡献
- 按渠道拆分贡献
- 按品类/段位拆分贡献

### 步骤5：结论输出
- 核心发现 ≤3条
- 建议方向 ≤3条
- 引用指标清单（口径见 K2 data-dictionary.md）

## 约束
- 不做归因拆解（归因走专项 terminal-sales-attribution）
- 不做红绿灯诊断（诊断走专项 region-sales-diagnosis）
- 本 Skill 是通识兜底，覆盖专项未匹配的常规销售问题
