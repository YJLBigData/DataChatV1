# 记忆管理 Skill（骨架）

## 触发条件
主理人在以下场景触发：会话结束（暂存上行）、月度记忆压缩、个人记忆读写

## 角色调用
调用者：卓见全（主理人） | 输入：暂存内容/记忆操作指令 | 输出：[暂存确认/记忆读写结果]

## 核心流程（骨架，W4 完善）

### 1. 暂存写入
- 条件：分析产出含可复用经验/框架/记录
- 格式：按 schema/staging-schema.yaml
- 存储：写入暂存区，推送飞书审核群

### 2. 暂存上行（异步）
- session_end 时推送暂存到飞书审核群
- 运营人员周度批量审核
- 通过 → 写入 analysis-records.md 或 lessons-learned.md

### 3. 个人记忆
- 存什么：用户偏好/常用维度/关注区域
- 存哪里：~/.workbuddy/feihe-decision/personal/
- 主理人自动收集

### 4. 集团记忆压缩（月度）
- 超30天记录 → 提炼为 lessons-learned 条目
- 原始记录 → archive/ 目录

## 输入契约
- 暂存：{ type, category, proposed_content, trigger_context, confidence }
- 读取：{ user_id, memory_type }

## 输出契约
- 暂存确认：写入成功 + 推送状态
- 记忆读取：对应记忆内容

## 引用资源
- @schema/staging-schema.yaml
- @memory/knowledge-base/analysis-records.md
- @memory/knowledge-base/lessons-learned.md
- @config/feishu.yaml（飞书推送）

## 错误处理
暂存写入失败 → 重试1次 → 仍失败则记录到 incidents
飞书推送失败 → 记录到 incidents，下次 session_end 重推

## 踩坑（运行时累积）
暂无
