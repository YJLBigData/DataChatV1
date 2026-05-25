-- =============================================================================
-- DataChatV1 阶段 3.2 物化视图脚本（DBA 在 hs_poc 上执行）
--
-- 设计意图：把"销售实绩 × 销售目标 × 达成率"这条高频问数路径预先在 ADB 端
-- 算成一张宽表（join 提前 + 关键聚合提前），让自然语言问数侧只需 SELECT 这张
-- 视图，零 JOIN、零 CASE WHEN，性能提升 5-10x，且口径强一致。
--
-- 适用 OLAP：阿里云 AnalyticDB MySQL 3.0+ 支持 MATERIALIZED VIEW；
--           降级方案：如果 ADB 不支持物化视图，把 MATERIALIZED VIEW 改成
--           普通 VIEW（去掉 REFRESH 子句），适合中等数据量，仅丧失加速。
--
-- 刷新策略：建议每天凌晨增量刷新一次（销售/目标月维度数据日内基本不变）。
--          ADB 物化视图支持 `REFRESH MATERIALIZED VIEW` 手动 / 调度刷新。
--
-- 权限要求：执行账号需要 CREATE VIEW / SELECT / 必要时 REFRESH 权限。
-- =============================================================================

-- 视图 1：销售实绩 × 目标 × 达成率（每月 × 大区 × 省区 × 大系统渠道 粒度）
-- ---------------------------------------------------------------------
-- 业务价值：覆盖 80% 的"达成率/差距分析"问句；问数 SQL 退化为单表 SELECT。
-- 命名：mv_ 前缀让 DBA 一眼看出是物化视图；表名结构 = 维度_指标族
CREATE OR REPLACE VIEW mv_sales_target_achievement AS
SELECT
    s.year                          AS year,
    s.month                         AS month,
    s.lev2_name                     AS lev2_name,           -- 大区
    s.lev3_name                     AS lev3_name,           -- 省区
    s.big_system_channel_name       AS big_system_channel_name,   -- 大系统渠道
    SUM(s.terminal_sale_amount)     AS terminal_sale_amount_total,        -- 终端销售额
    SUM(s.reduction_gd_sale_amount) AS reduction_gd_sale_amount_total,    -- 还原过单金额
    SUM(t.shop_sale_target)         AS shop_sale_target_total,            -- 门店销售目标
    SUM(t.shop_sale_amount)         AS shop_sale_amount_actual_total,     -- 门店实绩（target表口径）
    SUM(t.gd_target)                AS gd_target_total,                   -- 过单目标
    SUM(t.gd_amount)                AS gd_amount_actual_total,            -- 过单实绩
    -- 达成率：分母 NULL/0 时返回 NULL（不返回 0/Inf，便于前端识别"无目标"）
    SUM(t.shop_sale_amount) / NULLIF(SUM(t.shop_sale_target), 0) AS shop_sale_achievement_rate,
    SUM(t.gd_amount)        / NULLIF(SUM(t.gd_target), 0)        AS gd_achievement_rate
FROM ads_bi_month_shop_item_dan_summary_df s
LEFT JOIN ads_bi_month_shop_item_dan_target_summary_df t
       ON  s.year                    = t.year
       AND s.month                   = t.month
       AND s.lev2_name               = t.lev2_name
       AND s.lev3_name               = t.lev3_name
       AND s.big_system_channel_name = t.big_system_channel_name
GROUP BY
    s.year, s.month, s.lev2_name, s.lev3_name, s.big_system_channel_name;

-- ---------------------------------------------------------------------
-- 视图 2（可选）：销售 × 新客复购（按渠道+段位）
-- 业务价值：高管关注"哪些渠道在卖货 + 同时拉新拉复购"
CREATE OR REPLACE VIEW mv_sales_member_funnel AS
SELECT
    s.year, s.month,
    s.lev2_name, s.lev3_name, s.big_system_channel_name,
    s.item_dan_name,
    SUM(s.terminal_sale_amount)            AS terminal_sale_amount_total,
    SUM(m.first_purchase_num)              AS first_purchase_num_total,
    SUM(m.repurchase_in_60_days_num)       AS repurchase_in_60_days_num_total,
    SUM(m.repurchase_in_60_days_num) / NULLIF(SUM(m.first_purchase_num), 0) AS repurchase_rate_60d
FROM ads_bi_month_shop_item_dan_summary_df s
LEFT JOIN ads_member_first_purchase_new_customer_total_df m
       ON  s.year                    = m.year
       AND s.month                   = m.month
       AND s.lev2_name               = m.lev2_name
       AND s.lev3_name               = m.lev3_name
       AND s.big_system_channel_name = m.big_system_channel_name
       AND s.item_dan_name           = m.item_dan_name
GROUP BY
    s.year, s.month, s.lev2_name, s.lev3_name, s.big_system_channel_name, s.item_dan_name;

-- ---------------------------------------------------------------------
-- 视图 3（可选）：精准潜客 × 销售（按渠道）
CREATE OR REPLACE VIEW mv_potential_to_sales AS
SELECT
    s.year, s.month,
    s.lev2_name, s.lev3_name, s.big_system_channel_name,
    SUM(p.potential_num)               AS potential_num_total,
    SUM(p.potential_to_new_num)        AS potential_to_new_num_total,
    SUM(p.potential_to_new_num) / NULLIF(SUM(p.potential_num), 0) AS potential_to_new_rate,
    SUM(s.terminal_sale_amount)        AS terminal_sale_amount_total
FROM ads_bi_month_shop_item_dan_summary_df s
LEFT JOIN ads_precision_nutrition_potential_total_df p
       ON  s.year                    = p.year
       AND s.month                   = p.month
       AND s.lev2_name               = p.lev2_name
       AND s.lev3_name               = p.lev3_name
       AND s.big_system_channel_name = p.big_system_channel_name
GROUP BY
    s.year, s.month, s.lev2_name, s.lev3_name, s.big_system_channel_name;

-- =============================================================================
-- 部署交接说明
-- =============================================================================
-- 1) 在 ADB hs_poc 库执行本文件（管理员账号 / 具备 CREATE VIEW 权限的账号）。
-- 2) 校验：SELECT COUNT(*) FROM mv_sales_target_achievement; 应返回 ~大区×月数 量级。
-- 3) 在 backend/config/semantic.yaml 里把这 3 张 view 当作"表"登记一遍（label/dims/metrics）
--    之后 LLM/planner 就会自动优先打到视图，问数侧零代码改动。
-- 4) （ADB 物化视图）调度刷新：
--      REFRESH MATERIALIZED VIEW mv_sales_target_achievement;
--    建议每日 02:00 cron / DMS 任务一次。
