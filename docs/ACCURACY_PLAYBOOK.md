# 问数准确率运维手册（P0–P2 改造配套）

> 本次改造的目标：把"选错表/选错指标"从概率问题变成结构问题，并建立可测量的优化闭环。
> 业界依据：Spider 2.0（schema linking 占企业场景错误 27.6%）、Pinterest（表文档化使
> 选表命中率 40%→90%）、语义层配对基准（+17~23pp）、Vanna（验证问答对 RAG）。

---

## 一、这次改了什么（速览）

| 项 | 内容 | 关键文件 |
|----|------|----------|
| P0-1 | **修复 L2 缓存串数据 bug**：plan 签名移到行级权限注入之后计算。旧实现下，行级权限不同的两个用户问同一句话会互相命中对方的数字（错数 + 越权） | `orchestrator.py` Stage 3.7 |
| P0-2 | **检索分域**：召回候选按用户 `allowed_tables` 过滤（指标/维度/表/few-shot 四类全部生效）；guard 白名单按用户收紧；direct-SQL 的 schema 上下文同样分域 | `retrieval/hybrid.py` `guard/sql_guard.py` `direct_sql.py` |
| P0-3 | **超范围拒答**：问题落在用户表范围之外 → 明确拒答并告知范围与可问示例（宁拒答不硬答，管住准确率分母） | `planner.py::_out_of_scope_reason` |
| P0.5 | **全量表卡片**：用户域 ≤20 张表时，planner 看到的是全部表的语义卡片（含粒度/定位/指标清单/注意事项），选表不再依赖召回命中 | `planner.py::_table_cards` |
| P1 | **认证工作流**：语义实体加 `status: draft/verified`；管理端语义层页一键切换；已认证条目检索加权 + 提示词标注 | `semantic/layer.py` `semantic_editor.py` 语义层管理页 |
| P1.5 | **歧义强制澄清**：top-2 指标候选分差 < 0.10 且用户未点名 → 不硬选，抛选项让用户点选 | `planner.py::_maybe_ambiguity_clarify` |
| P2-1 | **评测闭环**：选表/选指标准确率回归脚本 + 种子评测集 | `scripts/eval_selection.py` `eval/selection_eval.yaml` |
| P2-2 | **采纳飞轮**：答案卡"👍采纳/👎不准"按钮；采纳沉淀为同域 few-shot 注入 planner；点踩进 bad case 库 | `fewshot_store.py` `/api/chat/feedback` |
| 附带 | 权限指纹进入 L1/q2p 缓存 key：**任何权限变更立即让该用户的问题缓存失效** | `permissions.py::PermissionBundle.fingerprint` |

## 二、20 人分域怎么配（管理员）

1. 管理端 → 数据权限 → 给每个用户配置 `allowed_tables`（≤15 张表）。
2. 配置即生效：检索候选、planner 表卡片、guard 白名单、超范围拒答全部跟随。
3. **不配 = 不分域**（沿用全语义层）；生产环境无任何规则的普通用户仍会被默认拒绝（既有行为）。
4. 行级（row_rules）与列级（allowed_columns）权限照旧；变更后该用户缓存自动失效。

## 三、语义认证冲刺（P1，建议下周做，1~2 天人力）

当前 **43 个语义实体全部是"草稿"**（机器起草未经业务确认）。操作：

1. 管理端 → 语义层：每个 tab（数据表/维度/指标）按状态列走查，草稿排在前面。
2. 每条只确认三件事，确认完点状态徽章切到"已认证"：
   - **表**：一句话定位是否准确（粒度 + 域 + *和相似表的区别、什么问题该用我*）；
     易混表要写"负向提示"进 description/notes，如「问'销量'默认动销表，不是出货表」。
   - **指标**：口径（expression）和别名（aliases）是否符合业务定义；
     **重点**：终端销售额 vs 门店销售金额、新客数 vs 转新人数 这类近义指标的别名要互斥。
   - **维度**：关键维度的 sample_values / value_dict 是否覆盖常用值（值对齐靠它）。
3. 已认证的条目：检索排序加权（+0.05），提示词标注"已认证"，LLM 被指示口径冲突时优先采用。
4. API：`GET /api/admin/semantic/certification`（认证清单）/ `POST /api/admin/semantic/{kind}/{name}/status`。

> ⚠️ **注意**：语义层编辑器（含状态切换）保存时会用 `yaml.safe_dump` 重写整个
> `backend/config/semantic.yaml`，**手写注释会丢失**（自动留 `.bak` 备份）。这是编辑器的
> 既有行为；如果你珍惜文件里的手写注释，第一次走认证前先把注释内容挪进各实体的
> `description`/`notes` 字段，或单独备份一份。

## 四、评测闭环（P2，每次改动后跑）

```bash
cd backend

# 一次性：生成种子评测集（已生成 13 条，来自 semantic.yaml few_shots）
python -m scripts.eval_selection seed

# 周期性：从真实日志导出待标注模板（预填线上预测，人工只改错的，标注成本极低）
python -m scripts.eval_selection export --limit 100
#   → eval/selection_labeling_*.yaml，复核后合并进 eval/selection_eval.yaml

# 每次改 prompt/语义层/检索/换模型后跑（不调 LLM，秒级）：
python -m scripts.eval_selection run --mode retrieval

# 发版前跑全链路（调 LLM，温度 0）：
python -m scripts.eval_selection run --mode planner --min-table-acc 0.85 --min-metric-acc 0.75
#   未达门槛退出码=1，可挂部署前置检查
```

**当前基线（retrieval top-1，2026-06-12）：选表 69.2% / 选指标 61.5% / top3 召回 69.2%。**
错例集中在：终端销售额↔门店销售金额、新客数↔转新人数——正是 P1 认证 + P1.5 澄清的目标。
做完认证冲刺后重跑，预期两个数字显著上移；以后用这两个数字说话。

## 五、采纳飞轮（P2，自然增长）

- 用户对正确答案点 **👍采纳** → (问题, QueryPlan) 沉淀为验证样例，**按表分域**注入同域用户的
  planner few-shot 区（行级权限过滤条件已剥离，不会泄露数据范围）。
- 点 **👎不准** → 进 bad case 库（`vote=down`，不参与召回），是评测集挖掘素材：
  `GET /api/admin/fewshots/stats` 看累计量；下次 export 标注时优先核对这些问题。
- 存储：`backend/logs/fewshots.db`（可用 `DATACHAT_FEWSHOT_DB` 改路径）。

## 六、可调参数（环境变量，零密钥，可进 production.env）

| 变量 | 默认 | 含义 |
|------|------|------|
| `DATACHAT_SCOPE_REJECT_THRESHOLD` | `0.35` | 超范围拒答的检索分数阈值；`0` 关闭低分拒答（显式点名域外指标的拒答不受影响） |
| `DATACHAT_FULL_TABLE_CARDS_MAX` | `20` | 用户域表数 ≤ 该值时全量呈现表卡片；超过回退召回 top-k |
| `DATACHAT_AMBIGUITY_GAP` | `0.10` | top-2 指标分差小于该值触发强制澄清；`0` 关闭 |
| `DATACHAT_FEWSHOT_DB` | `logs/fewshots.db` | 采纳样例库路径 |

## 七、部署注意

1. **缓存一次性失效**：L1/q2p 的 key 编码变了（加入权限指纹），上线后首问会全部 miss 一轮，属预期。
2. **检索索引无需重建**：认证状态实时读取，不进向量索引指纹。
3. 回滚开关：把两个阈值设为 `0` + 不给用户配 `allowed_tables` ≈ 回到旧行为（L2 修复除外，那个不该回滚）。
4. 本机 e2e 提示：`tests/test_api.py::test_chat_full` 需要本地 MySQL；当前本机 `.env` 的
   MySQL 密码疑似中文占位符（pymysql latin-1 编码报错），与代码无关，改成真实密码即可。
