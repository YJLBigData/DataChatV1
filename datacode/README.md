# DataCode Java 智能写代码平台

本仓库新增 Spring Boot 版智能写代码平台，默认端口 `18082`，不占用 `8000/8001`。

## 本地启动

```bash
cd /Users/yangjinlong/app/IdeaProjects/FeiHe_ChatCode
cp config/datacode.env.example config/datacode.env
# 按需填写 DATACODE_LLM_API_KEY、ALIYUN_DATA_PLATFORM_AK、ALIYUN_DATA_PLATFORM_SK
./start_datacode_java.sh
```

访问地址：

```text
http://127.0.0.1:18082
```

关闭服务：

```bash
./stop_datacode_java.sh
```

默认管理员账号：

```text
admin / 123456
```

主要能力：
- 上传 Excel/CSV 需求文件，Excel 多 Sheet 自动拆分为多个需求。
- 填写模型提示词、源表结构、来源表样例数据、备注。
- 调用 `qwen3.6-max-preview` 生成 Dataphin/MaxCompute 建表和写入 SQL。
- 校验建表、写入、默认 `ds` 分区、`${bizdate}`、高风险语句和基础语法结构。
- 管理员可直接查询 MaxCompute/Dataphin 数据、任务代码、表级血缘、任务血缘。
- 保留用户管理、生成日志、模型调用日志。

# CDP 数据上报接口文档

## 目录
- [概述](#概述)
- [支持的 API 类型](#支持的-api-类型)
- [环境配置](#环境配置)
- [命令行参数说明](#命令行参数说明)
- [请求数据接结构](#请求数据结构)
- [Dataphin数据构建](#Dataphin数据构建)
- [部署指南](#部署指南)
- [性能优化建议](#性能优化建议)
- [常见问题](#常见问题)
- [技术支持](#技术支持)

---

## 概述

飞鹤 CDP（Customer Data Platform）数据上报程序是一个基于 Java 的数据同步工具，用于将 MaxCompute（ODPS）中的数据批量上报到 CDP 系统。程序支持多线程并发处理，可高效完成大规模数据同步任务。

### 核心功能
- **客户批量创建**：批量创建用户信息
- **实体批量创建**：批量创建自定义实体数据
- **行为事件上报**：支持实时、历史、回溯三种事件上报模式

---

## 支持的 API 类型

| 命令类型 | 命令值 | API 路径 | 说明 | 批量大小 |
|---------|--------|---------|------|---------|
| CUSTOMER | `CUSTOMER` | `/api/gateway/v1/cdp-entity/user/batchCreate` | 客户批量创建 | 100 条/批 |
| ENTITY | `ENTITY` | `/api/gateway/v1/cdp-entity/open/dataBatchAdd` | 实体批量创建 | 500 条/批 |
| EVENT_REALTIME | `EVENT_REALTIME` | `/events/api/trace` | 实时事件上报 | 100 条/批 |
| EVENT_HISTORY | `EVENT_HISTORY` | `/events/history/trace` | 历史事件上报 | 100 条/批 |
| EVENT_BACKTRACK | `EVENT_HISTORY` | `/events/backtrack/trace` | 回溯事件上报 | 100 条/批 |

---

## 环境配置

### 运行环境要求
- JDK 版本：JDK 8+
- 内存配置：默认 4GB（可根据实际情况调整）
- 依赖管理：Maven

---

## 命令行参数说明

### 必需参数

| 参数名 | 说明 | 示例值 |
|-------|------|--------|
| `--mc-ak` | MaxCompute AccessKey | `<your-mc-ak>` |
| `--mc-sk` | MaxCompute SecretKey | `<your-mc-sk>` |
| `--command` | CDP 系统 API 命令 | `customer` / `entity` / `event-history` |
| `--cdp-sk` | CDP SecretKey | `<your-cdp-sk>` |
| `--cdp-sk-id` | CDP SecretKey ID | `<your-cdp-sk-id>` |
| `--cdp-corp-id` | CDP 企业 ID | `<your-cdp-corp-id>` |
| `--project` | MaxCompute 项目名 | `firmus_dataphin_prd_cdm` |
| `--table` | MaxCompute 表名 | `fct_customer_care_pwa_task_detail` |
| `--partition` | MaxCompute 分区 | `ds=20260330` |
| `--thread-num` | 并发线程数 | `4` |
| `--do-main` | CDP API 域名 | `https://cdp-dev.feihe.com` |
| `--workspace-id` | CDP 工作空间 ID | `your-workspace-id` |

### 可选参数

| 参数名 | 说明 | 示例值 |
|-------|------|--------|
| `--app-key` | CDP 事件上报 Key（仅事件上报需要） | `1SERVE06WLDAPGS12N` |

---

## 使用示例

### 1. 客户批量创建

```bash
java -jar FeiHeCDP-1.0.jar \
  --mc-ak=${ALIYUN_DATA_PLATFORM_AK} \
  --mc-sk=${ALIYUN_DATA_PLATFORM_SK} \
  --command= CUSTOMER\
  --cdp-sk=${CDP_DATA_PLATFORM_KEY} \
  --cdp-sk-id=${CDP_DATA_PLATFORM_KEY_ID} \
  --cdp-corp-id=${CDP_CORPORATION_ID} \
  --project=firmus_dataphin_prd_cdm \
  --table=dim_customer \
  --partition=pt=20240101 \
  --thread-num=4 \
  --do-main=${CDP_DOMAIN} \
  --workspace-id=your-workspace-id
```

### 2. 实体批量创建

```bash
java -jar FeiHeCDP-1.0.jar \
  --mc-ak=${ALIYUN_DATA_PLATFORM_AK} \
  --mc-sk=${ALIYUN_DATA_PLATFORM_SK} \
  --command=ENTITY \
  --cdp-sk=${CDP_DATA_PLATFORM_KEY} \
  --cdp-sk-id=${CDP_DATA_PLATFORM_KEY_ID} \
  --cdp-corp-id=${CDP_CORPORATION_ID} \
  --project=firmus_dataphin_prd_cdm \
  --table=dim_shop \
  --partition=pt=20240101 \
  --thread-num=4 \
  --do-main=${CDP_DOMAIN} \
  --workspace-id=your-workspace-id
```

### 3. 历史事件上报

```bash
java -jar FeiHeCDP-1.0.jar \
  --mc-ak=${ALIYUN_DATA_PLATFORM_AK} \
  --mc-sk=${ALIYUN_DATA_PLATFORM_SK} \
  --command=EVENT_HISTORY \
  --cdp-sk=${CDP_DATA_PLATFORM_KEY} \
  --cdp-sk-id=${CDP_DATA_PLATFORM_KEY_ID} \
  --cdp-corp-id=${CDP_CORPORATION_ID} \
  --project=firmus_dataphin_prd_cdm \
  --table=fct_customer_care_pwa_task_detail \
  --partition=pt=20240101 \
  --thread-num=4 \
  --do-main=${CDP_LOG_DOMAIN} \
  --workspace-id=your-workspace-id \
  --app-key=1SERVE06WLDAPGS12N
```

### 4. 实时事件上报

```bash
java -jar FeiHeCDP-1.0.jar \
  --mc-ak=${ALIYUN_DATA_PLATFORM_AK} \
  --mc-sk=${ALIYUN_DATA_PLATFORM_SK} \
  --command=EVENT_REALTIME \
  --cdp-sk=${CDP_DATA_PLATFORM_KEY} \
  --cdp-sk-id=${CDP_DATA_PLATFORM_KEY_ID} \
  --cdp-corp-id=${CDP_CORPORATION_ID} \
  --project=firmus_dataphin_prd_odm \
  --table=fct_user_behavior_log \
  --partition=pt=20240101 \
  --thread-num=8 \
  --do-main=${CDP_LOG_DOMAIN} \
  --workspace-id=your-workspace-id \
  --app-key=YOUR_APP_KEY
```

### 5. 回溯事件上报

```bash
java -jar FeiHeCDP-1.0.jar \
  --mc-ak=${ALIYUN_DATA_PLATFORM_AK} \
  --mc-sk=${ALIYUN_DATA_PLATFORM_SK} \
  --command=EVENT_BACKTRACK \
  --cdp-sk=${CDP_DATA_PLATFORM_KEY} \
  --cdp-sk-id=${CDP_DATA_PLATFORM_KEY_ID} \
  --cdp-corp-id=${CDP_CORPORATION_ID} \
  --project=firmus_dataphin_prd_cdm \
  --table=fct_order_backtrack \
  --partition=pt=20240101 \
  --thread-num=4 \
  --do-main=${CDP_LOG_DOMAIN} \
  --workspace-id=your-workspace-id \
  --app-key=YOUR_APP_KEY
```

---

## 请求数据结构

### 1. 客户批量创建请求

```json
{
  "contents": [
    {
      "identity": [
        {
          "identityType": 51,
          "identityValue": "M123456"
        }
      ],
      "property": {
        "customerName": "张三",
        "mobile": "13800138000",
        "age": 25,
        "gender": "male"
      }
    }
  ]
}
```

### 2. 实体批量创建请求

```json
{
  "entityKey": "Product",
  "contents": [
    {
      "property": {
        "keyId": "SHOP001",
        "shop_ode": "BJ001",
        "shop_ame": "北京朝阳店",
        "province": "北京市",
        "city": "朝阳区"
      }
    }
  ]
}
```

### 3. 行为事件上报请求

```json
{
  "appkey": "1SERVE06WLDAPGS12N",
  "data": "[
    {
      "eventId": "evt_123456",
      "time": "1704067200000",
      "event": "GuidesCare_Task_Issued_Success",
      "type": "track",
      "sessionId": "session_001",
      "properties": {
        "shop_ode": "BJ001,
        "task_ype": "VISIT"
      },
      "account": {
        "business_id": "M123456"
      }
    }
  ]",
  "ext": 3,
  "sign": "MD5 签名值"
}
```

---



## Dataphin数据构建

### 1. 用户实体

**identity** 表示用户在飞鹤系统中的社交账号、身份证明等字段

通过 数组+Map 的数据结构存储, 每一个单独的身份字段单独存一个map

map中的key 固定为 identityType: 当前用户身份在cdp中的枚举 可通过cdp接口获取; identityValue: 身份值



**property** 表示用户在飞鹤系统中除社交账号外其他的信息

通过Map 数据结构存储

map中的key需要在cdp系统中新建, 尽量与字段名保持一致

> ⚠️ 根据CDP系统要求, 用户身份类型和用户身份值需要用驼峰

```sql
select array(
  						map("identityType", 3, "identityValue", wechat_openid),
    					map("identityType", 10, "identityValue", member_id)
            ) as identity,
       map('first_follow_time',first_follow_time,
           'member_type',member_type
          )  as property
from project.table
```



### 2.自定义实体

⚠️ 定义自定义实体 entityKey 需要与表名称一致, 只需在cdp操作.

**property** 表示当前实体的全部属性

通过 Map 数据结构存储

map中的key需要在cdp系统中新建, map中需要包含一个 **keyId** 来表示当前这个实体的唯一性(pk, uk)

> ⚠️ 除了keyId 需要驼峰外, 其他字段与cdp保持一致即可

```sql
select map('keyId',sku_id,
           'mater_style',mater_style,
           'mater_classify',mater_classify
          ) as property
from project.table
```



### 3. 事件上报

事件上报区分回溯事件和非回溯事件, 非回溯事件中对于早于系统时间10天及以上的数据使用历史事件数据上传.

**eventId** 事件的唯一ID, 仅在可回溯事件里包含

**event** 事件Code, 根据当前事件匹配cdp系统中的预置事件编码, 例如广告点击: $click_ad

**type** 事件类型, 普通事件填充track, 默认 track

**time** 事件时间, 需要处理为13位时间戳, 例如: 1704067200000

**account** 账号信息, Map 类型 当前事件中所有用户身份相关的全部存在account, key需要与用户实体中上传身份一致

**properties** 其他属性, Map类型 除了上述字段外, 其他字段全部存在properties

> ⚠️ 每个事件上报都需要一个AppKey, 在cdp系统中获取

```sql
-- java -jar FeiheCdp.jar FeiheApplication --app-key 1SERVE06WLDAPGS12N
select id as eventId,
       '$click_ad' as event,
       'trace' as type,
       unix_timestamp(create_time) as time,
       map('business_id',member_id,
           'wechat_openid', wechat_openid) as account,
       map('oraginIp',oragin_ip, 'User-Agent', user_agent) as properties
```

---



## 部署指南

### 1. 打包项目

```bash
mvn clean package -Dmaven.test.skip=true
```

生成的 JAR 包位置：`$PROJECT_DIR/target/FeiHeCDP-1.0.jar`

### 2. 启动脚本

使用提供的 `bin/start.sh` 脚本：

```bash
#!/bin/bash

MAIN_CLASS="com.feihe.FeiHeCdpApplication"
APP_JAR="FeiHeCDP-1.0.jar"

JAVA_OPTS="
-server
-Xms4g
-Xmx4g
-XX:+UseG1GC
-XX:MaxGCPauseMillis=100
-XX:InitiatingHeapOccupancyPercent=35
-XX:+UseStringDeduplication
-XX:+OptimizeStringConcat
-Dlogback.configurationFile=logback-prod.xml
"

exec java $JAVA_OPTS -cp "$APP_JAR" "$MAIN_CLASS" --command ${command} \
--mc-ak ${aliyun_access_key} \
--mc-sk ${aliyun_secret_key} \
--do-main ${cdp_domain} \
--cdp-corp-id ${cdp_corp_id} \
--workspace-id ${cdp_workspace_id} \
--cdp-sk ${cdp_secret_key} \
--cdp-sk-id ${cdp_secret_key_id} \
--thread-num 4 \
--app-key ${app_key} \
--project ${projectName} \
--table ${tableName} \
--partition ${partition}
```

### 3. 日志配置

根据环境选择日志配置文件：
- 开发环境：`logback-dev.xml` 
- 生产环境：`logback-prod.xml` Dataphin默认不会聚合pod 中的日志, 这里使用 Console appender.

---

## 性能优化建议

### 1. 线程数配置
- 小数据量（< 10 万条）：1-2 个线程
- 中等数据量（10 万 -100 万条）：2-4个线程
- 大数据量（> 500 万条）：8-16 个线程

### 2. JVM 参数调优
```bash
# 根据数据量调整堆内存
-Xms4g -Xmx4g  # 基础配置

-Xms8g -Xmx8g  # 大数据量配置

# GC 优化
-XX:+UseG1GC
-XX:MaxGCPauseMillis=100
-XX:+UseStringDeduplication
```

### 3. 批量大小
- 客户创建：100 条/批（CDP API 限制）
- 实体创建：500 条/批（CDP API 限制）
- 事件上报：100 条/批（CDP API 限制）

---

## 常见问题

### Q1: 如何查看执行日志？
A: 日志文件默认输出到控制台。生产环境使用 `logback-prod.xml` 配置。

### Q2: 上报失败如何排查？
A: 
1. 检查日志中的错误信息
2. 验证 MaxCompute 表数据格式
3. 确认 CDP API 认证信息正确
4. 确认域名和空间等参数是否正确

### Q3: 如何确认数据上报成功？
A: 
1. 查看日志中的 success 计数
2. 登录 CDP 系统后台查看数据
3. 调用 CDP 查询 API 验证

### Q4: 分区参数如何传递？
A: 使用 `--partition` 参数，具体格式取决于 MaxCompute 表分区设计。(ds or pt)

### Q5: 支持断点续传吗？
A: 当前版本不支持断点续传。如需重新执行，建议先清理已上报的数据或使用新的分区。

### Q6: 如何监控执行进度？
A: 
1. 日志会输出每个线程的处理情况
2. 最后会输出总计录数和完成数

---

## 技术支持

- CDP 系统文档：https://tmc.qidian.qq.com/base/console/doc/14154?version=20260318
