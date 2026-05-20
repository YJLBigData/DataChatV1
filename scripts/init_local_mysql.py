#!/usr/bin/env python3
"""Create a local MySQL chatbi schema with small deterministic demo data.

This is intentionally local-dev only.  It prevents the common
`Can't connect to MySQL server on 127.0.0.1` failure from blocking UI testing,
and gives the NL2SQL path real tables to query when no company dump is present.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

import pymysql


HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
PORT = int(os.environ.get("MYSQL_PORT", "3306"))
USER = os.environ.get("MYSQL_USER", "root")
PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
DB = os.environ.get("MYSQL_DATABASE", "chatbi")

if not PASSWORD:
    sys.stderr.write(
        "[init_local_mysql] 缺少 MYSQL_PASSWORD 环境变量。\n"
        "  请在 backend/.env 中设置 MYSQL_PASSWORD 后重试（start.sh 会自动生成并写回）。\n"
    )
    sys.exit(2)


TABLES: dict[str, dict[str, str]] = {
    "ads_bi_month_shop_item_dan_summary_df": {
        "id": "BIGINT PRIMARY KEY AUTO_INCREMENT",
        "year": "VARCHAR(4) NOT NULL",
        "month": "VARCHAR(2) NOT NULL",
        "lev2_name": "VARCHAR(64) NOT NULL",
        "lev3_name": "VARCHAR(64) NOT NULL",
        "city": "VARCHAR(64) NOT NULL",
        "big_system_channel_name": "VARCHAR(64) NOT NULL",
        "item_series_new_name": "VARCHAR(128) NOT NULL",
        "item_dan_name": "VARCHAR(32) NOT NULL",
        "terminal_circle_level": "VARCHAR(64) NOT NULL",
        "terminal_sale_amount": "DECIMAL(18,2) NOT NULL DEFAULT 0",
        "reduction_gd_sale_amount": "DECIMAL(18,2) NOT NULL DEFAULT 0",
        "ds": "VARCHAR(16) NOT NULL DEFAULT ''",
    },
    "ads_bi_month_shop_item_dan_target_summary_df": {
        "id": "BIGINT PRIMARY KEY AUTO_INCREMENT",
        "year": "VARCHAR(4) NOT NULL",
        "month": "VARCHAR(2) NOT NULL",
        "lev2_name": "VARCHAR(64) NOT NULL",
        "lev3_name": "VARCHAR(64) NOT NULL",
        "big_system_channel_name": "VARCHAR(64) NOT NULL",
        "shop_sale_target": "DECIMAL(18,2) NOT NULL DEFAULT 0",
        "shop_sale_amount": "DECIMAL(18,2) NOT NULL DEFAULT 0",
        "gd_target": "DECIMAL(18,2) NOT NULL DEFAULT 0",
        "gd_amount": "DECIMAL(18,2) NOT NULL DEFAULT 0",
        "ds": "VARCHAR(16) NOT NULL DEFAULT ''",
    },
    "ads_member_first_purchase_new_customer_total_df": {
        "id": "BIGINT PRIMARY KEY AUTO_INCREMENT",
        "year": "VARCHAR(4) NOT NULL",
        "month": "VARCHAR(2) NOT NULL",
        "lev2_name": "VARCHAR(64) NOT NULL",
        "lev3_name": "VARCHAR(64) NOT NULL",
        "big_system_channel_name": "VARCHAR(64) NOT NULL",
        "item_dan_name": "VARCHAR(32) NOT NULL",
        "first_purchase_num": "INT NOT NULL DEFAULT 0",
        "repurchase_in_60_days_num": "INT NOT NULL DEFAULT 0",
        "heli30_new_customer_num": "INT NOT NULL DEFAULT 0",
        "heli30_repurchase_in_60_days_num": "INT NOT NULL DEFAULT 0",
        "ds": "VARCHAR(16) NOT NULL DEFAULT ''",
    },
    "ads_precision_nutrition_potential_total_df": {
        "id": "BIGINT PRIMARY KEY AUTO_INCREMENT",
        "year": "VARCHAR(4) NOT NULL",
        "month": "VARCHAR(2) NOT NULL",
        "lev2_name": "VARCHAR(64) NOT NULL",
        "lev3_name": "VARCHAR(64) NOT NULL",
        "big_system_channel_name": "VARCHAR(64) NOT NULL",
        "potential_num": "INT NOT NULL DEFAULT 0",
        "potential_to_new_num": "INT NOT NULL DEFAULT 0",
        "ds": "VARCHAR(16) NOT NULL DEFAULT ''",
    },
    "ads_bi_hs_sale_info_df": {
        "id": "BIGINT PRIMARY KEY AUTO_INCREMENT",
        "acc_month": "VARCHAR(7) NOT NULL",
        "lev2_name": "VARCHAR(64) NOT NULL",
        "lev3_name": "VARCHAR(64) NOT NULL",
        "official_city": "VARCHAR(64) NOT NULL",
        "channel_type": "VARCHAR(16) NOT NULL",
        "shop_type": "VARCHAR(32) NOT NULL",
        "dealer_name": "VARCHAR(128) NOT NULL",
        "dealer_code": "VARCHAR(64) NOT NULL",
        "shop_name": "VARCHAR(128) NOT NULL",
        "shop_code": "VARCHAR(64) NOT NULL",
        "guide_name": "VARCHAR(64) NOT NULL",
        "guide_code": "VARCHAR(64) NOT NULL",
        "item_name": "VARCHAR(128) NOT NULL",
        "item_code": "VARCHAR(64) NOT NULL",
        "item_series_new": "VARCHAR(128) NOT NULL",
        "item_stage": "VARCHAR(32) NOT NULL",
        "item_spec": "VARCHAR(32) NOT NULL",
        "business_representative_name": "VARCHAR(64) NOT NULL",
        "business_representative_code": "VARCHAR(64) NOT NULL",
        "lev4_name_manager_name": "VARCHAR(64) NOT NULL",
        "lev4_name_manager_code": "VARCHAR(64) NOT NULL",
        "is_guide_shop": "VARCHAR(8) NOT NULL",
        "shop_sale_qty": "INT NOT NULL DEFAULT 0",
        "shop_sale_amount": "DECIMAL(18,2) NOT NULL DEFAULT 0",
        "gd_amount": "DECIMAL(18,2) NOT NULL DEFAULT 0",
        "ds": "VARCHAR(16) NOT NULL DEFAULT ''",
    },
}

REGIONS = [
    ("北一区", "北京", "北京市", 612100),
    ("东一区", "苏中", "苏州市", 314900),
    ("东二区", "闽南", "厦门市", 204200),
    ("中二区", "豫东", "郑州市", 193200),
    ("西一区", "陕西", "西安市", 189200),
    ("南一区", "粤东", "广州市", 175600),
    ("中一区", "鄂东", "武汉市", 163700),
    ("直辖区", "天津", "天津市", 122100),
]
MONTHS = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04"]


def connect(database: str | None = None):
    return pymysql.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=PASSWORD,
        database=database,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
    )


def wait_mysql() -> None:
    last: Exception | None = None
    for _ in range(60):
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return
        except Exception as exc:  # pragma: no cover - startup race only
            last = exc
            time.sleep(1)
    raise RuntimeError(f"MySQL not ready: {last}")


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        for table, columns in TABLES.items():
            ddl_cols = ", ".join(f"`{name}` {ddl}" for name, ddl in columns.items())
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{table}` ({ddl_cols}) "
                "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            )
            cur.execute(f"SHOW COLUMNS FROM `{table}`")
            existing = {row[0] for row in cur.fetchall()}
            for name, ddl in columns.items():
                if name not in existing:
                    cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {ddl}")


def insert_many(conn, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ", ".join(["%s"] * len(cols))
    col_sql = ", ".join(f"`{c}`" for c in cols)
    values = [[row.get(c) for c in cols] for row in rows]
    with conn.cursor() as cur:
        cur.executemany(f"INSERT INTO `{table}` ({col_sql}) VALUES ({placeholders})", values)


def seed_if_empty(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM `ads_bi_month_shop_item_dan_summary_df`")
        if int(cur.fetchone()[0]) > 0:
            return

    summary_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    member_rows: list[dict[str, Any]] = []
    potential_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []

    for m_idx, ym in enumerate(MONTHS):
        year, month = ym.split("-")
        month_factor = 0.78 + m_idx * 0.06
        for idx, (region, sub_region, city, base) in enumerate(REGIONS):
            channel = f"CS{idx + 10001}"
            series = "卓睿" if idx % 2 == 0 else "星飞帆经典1-3段"
            item_dan = ["1段", "2段", "3段", "4段"][idx % 4]
            circle = ["核心终端", "成长终端", "基础终端"][idx % 3]
            amount = round(base * month_factor, 2)
            summary_rows.append({
                "year": year, "month": month, "lev2_name": region, "lev3_name": sub_region,
                "city": city, "big_system_channel_name": channel, "item_series_new_name": series,
                "item_dan_name": item_dan, "terminal_circle_level": circle,
                "terminal_sale_amount": amount,
                "reduction_gd_sale_amount": round(amount * 1.08, 2),
                "ds": ym.replace("-", ""),
            })
            target = round(base * 1.05, 2)
            target_rows.append({
                "year": year, "month": month, "lev2_name": region, "lev3_name": sub_region,
                "big_system_channel_name": channel, "shop_sale_target": target,
                "shop_sale_amount": amount, "gd_target": round(target * 1.12, 2),
                "gd_amount": round(amount * 1.08, 2), "ds": ym.replace("-", ""),
            })
            first_purchase = int(360 + idx * 21 + m_idx * 18)
            repurchase = int(first_purchase * (0.28 + (idx % 4) * 0.025))
            member_rows.append({
                "year": year, "month": month, "lev2_name": region, "lev3_name": sub_region,
                "big_system_channel_name": channel, "item_dan_name": item_dan,
                "first_purchase_num": first_purchase,
                "repurchase_in_60_days_num": repurchase,
                "heli30_new_customer_num": int(first_purchase * 0.42),
                "heli30_repurchase_in_60_days_num": int(repurchase * 0.46),
                "ds": ym.replace("-", ""),
            })
            potential = int(900 + idx * 60 + m_idx * 45)
            potential_rows.append({
                "year": year, "month": month, "lev2_name": region, "lev3_name": sub_region,
                "big_system_channel_name": channel, "potential_num": potential,
                "potential_to_new_num": int(potential * (0.12 + (idx % 3) * 0.015)),
                "ds": ym.replace("-", ""),
            })
            detail_rows.append({
                "acc_month": ym, "lev2_name": region, "lev3_name": sub_region,
                "official_city": city, "channel_type": str((idx % 4) + 1),
                "shop_type": "专职门店" if idx % 2 == 0 else "无导门店",
                "dealer_name": f"{sub_region}经销商", "dealer_code": f"D{idx + 1:04d}",
                "shop_name": f"{city}飞鹤样例门店{idx + 1}", "shop_code": f"S{idx + 1:04d}",
                "guide_name": f"导购{idx + 1}", "guide_code": f"G{idx + 1:04d}",
                "item_name": f"{series}{item_dan}样例产品", "item_code": f"I{idx + 1:04d}",
                "item_series_new": series, "item_stage": item_dan,
                "item_spec": ["大听", "中听", "小听"][idx % 3],
                "business_representative_name": f"业务代表{idx + 1}",
                "business_representative_code": f"BR{idx + 1:04d}",
                "lev4_name_manager_name": f"地区经理{idx + 1}",
                "lev4_name_manager_code": f"AM{idx + 1:04d}",
                "is_guide_shop": "是" if idx % 2 == 0 else "否",
                "shop_sale_qty": int(amount / 420),
                "shop_sale_amount": amount,
                "gd_amount": round(amount * 1.08, 2),
                "ds": ym.replace("-", ""),
            })

    insert_many(conn, "ads_bi_month_shop_item_dan_summary_df", summary_rows)
    insert_many(conn, "ads_bi_month_shop_item_dan_target_summary_df", target_rows)
    insert_many(conn, "ads_member_first_purchase_new_customer_total_df", member_rows)
    insert_many(conn, "ads_precision_nutrition_potential_total_df", potential_rows)
    insert_many(conn, "ads_bi_hs_sale_info_df", detail_rows)


def main() -> int:
    wait_mysql()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    with connect(DB) as conn:
        ensure_schema(conn)
        seed_if_empty(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM `ads_bi_month_shop_item_dan_summary_df`")
            rows = int(cur.fetchone()[0])
    print(f"local mysql ready: {USER}@{HOST}:{PORT}/{DB}, summary_rows={rows}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"init local mysql failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
