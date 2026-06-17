# 飞鹤决策编排 Skill (feihe-decision)

> 管理编排流程、快慢路由判断、Skill 注册表。

## 触发词

| 用户表达 | 路由 |
|---------|------|
| 单指标查询、"多少"、"查一下"、"怎么样" | 快通道→速捷 |
| "分析"、"归因"、"诊断"、"为什么" | 慢通道→单分析师 |
| "报告"、"全面分析"、"综合诊断"、"汇报" | 慢通道→多分析师 |
| 追问"怎么办"、"建议" | 进入诊断建议交互 |

## 6 Phase 编排流程

### Phase 1: 身份识别
- 读取 identity.yaml 确认用户身份
- 身份未知 → 引导身份配置，不可跳过
- 身份已知 → 确定权限范围

### Phase 2: 意图理解 + 快慢路由
- 解析问题类型（取数 / 单域分析 / 跨域综合）
- 匹配触发词 → 确定路由
- 快通道：直接调度速捷
- 慢通道：确定需要调度的分析师列表

### Phase 3: 任务调度
- 快通道：1条消息调度速捷
- 慢通道单域：调度对应分析师 + 明确子任务
- 慢通道跨域：并行调度多分析师（同1条消息 spawn）
- 每个 prompt 包含：原始问题 + 分析维度 + 时间范围 + 深度要求

### Phase 4: 执行监控
- 等待成员回传
- 超时/产出不完整 → 要求补充
- 不代写成员产出

### Phase 5: 数据检核
- 多分析师产出时必须检核
- 检核项：口径一致性、逻辑自洽性、异常值排查
- 检核不通过 → 反馈对应成员重做

### Phase 6: 报告合成 + 反馈
- 提取关键发现
- 处理矛盾结论（标注分歧+主理人判断）
- 跨域交叉策略建议
- 按汇报对象适配格式

## 知识库路径

知识库统一存放在专家团目录下：`~/.workbuddy/plugins/marketplaces/my-experts/plugins/feihe-decision-team/knowledge/`

| 编号 | 文件 | 内容 | 使用角色 |
|------|------|------|----------|
| K1 | data-asset-catalog.md | 所有可用表：表名、维度/指标字段、粒度、更新频率、权限要求 | 全分析师 |
| K2 | data-dictionary.md | 每指标口径定义：计算公式、来源字段、注意事项、枚举值 | 全分析师 |
| K3 | analysis-frameworks.md | 5领域分析方法论：常用维度+方法论+分析步骤 | 5分析师 |
| K4 | industry-knowledge.md | 行业知识：段位体系、渠道特征、竞品格局、季节性、政策影响 | 全角色 |
| K5 | output-format-spec.md | 3种标准输出格式：快查询/分析报告/检核报告 | 全角色 |
| K6 | few-shot-orchestration.md | 编排调度案例（5-10个占位，从实际会话积累） | 卓见全 |

## Skill 注册表

### 主编排
| Skill 名 | 绑定角色 | 状态 | 路径 |
|-----------|---------|------|------|
| feihe-decision | 卓见全（主理人） | 骨架 | skills/feihe-decision/ |

### 通识 Skill（每个分析师都有）
| Skill 名 | 绑定角色 | 状态 | 路径 |
|-----------|---------|------|------|
| data-query-general | 速捷 + 全分析师 | 骨架 | skills/data-query-general/ |
| sales-general | 齐增辉（销售） | 骨架 | skills/sales-general/ |
| channel-general | 路通达（渠道） | 骨架 | skills/channel-general/ |
| user-ops-general | 甄客来（用户） | 骨架 | skills/user-ops-general/ |
| market-general | 严观澜（市场） | 骨架 | skills/market-general/ |
| finance-general | 贝精诚（财务） | 骨架 | skills/finance-general/ |

### 专项 Skill（领域深度分析）
| Skill 名 | 绑定角色 | 状态 | 路径 |
|-----------|---------|------|------|
| region-sales-diagnosis | 齐增辉（销售） | **可用** | skills/region-sales-diagnosis/ |
| terminal-sales-attribution | 齐增辉（销售） | **可用** | skills/terminal-sales-attribution/ |
| newcustomer-diagnosis | 甄客来（用户） | **可用** | skills/newcustomer-diagnosis/ |
| channel-diagnosis | 路通达（渠道） | 骨架 | skills/channel-diagnosis/ |
| market-intelligence | 严观澜（市场） | 骨架 | skills/market-intelligence/ |
| finance-analysis | 贝精诚（财务） | 骨架 | skills/finance-analysis/ |

### 能力 Skill
| Skill 名 | 绑定角色 | 状态 | 路径 |
|-----------|---------|------|------|
| feihe-decision-report | 卓见全（报告合成） | 骨架 | skills/feihe-decision-report/ |
| feihe-decision-memory | 卓见全（记忆管理） | 骨架 | skills/feihe-decision-memory/ |

### 审计侧 Skill
| Skill 名 | 绑定角色 | 状态 | 路径 |
|-----------|---------|------|------|
| data-audit | 查实真（数据检核） | 骨架 | skills/data-audit/ |
| feihe-decision-audit | 纪鉴明（知识审计） | 骨架 | skills/feihe-decision-audit/ |

### Skill 优先级
**专项 > 通识 > 自主推理**

### 新 Skill 注入流程
1. 知识审计专员（纪鉴明）评估 Skill 质量
2. 主理人确认路由绑定
3. 更新本注册表
4. 更新对应分析师 MD 的 skills 字段

## 预设 Workflow

| 场景 | 触发词 | 编排 |
|------|--------|------|
| 销售诊断 | "销售涨跌"、"达成率异常" | 速捷→齐增辉→查实真→合成 |
| 新客诊断 | "新客分析"、"复购率" | 速捷→甄客来+齐增辉→查实真→合成 |
| 区域经营 | "区域诊断"、"大区异常" | 齐增辉+路通达+甄客来→查实真→合成 |
| 综合报告 | "月度报告"、"总裁汇报" | 全5分析师→查实真→合成→纪鉴明记录 |
| 费用效率 | "费用率"、"ROI" | 贝精诚+齐增辉→查实真→合成 |
