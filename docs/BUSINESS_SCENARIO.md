# 企业售后客服业务场景

## 1. 目标场景

RAGOps Hub 聚焦 B2B SaaS 售后客服。典型问题包括登录失败、知识库容量、账号权限、退款政策、
API 接入和服务有效期。客服需要在较短 SLA 内完成三件事：确认客户是否有权获得服务、从可信知识中
找到答案、在无法直接解决时安全地升级技术工单。

系统不尝试替代 CRM、订单系统或 Helpdesk，而是把它们的关键上下文聚合到客服工作台，并通过受控
工具执行查询或写入。

## 2. 角色

- 客户：通过在线客服、邮件或企业协作工具发起咨询。
- 客服专员：处理分配给自己的会话，查询关联订单和知识，准备升级工单。
- 客服主管：查看本租户所有会话和工单，执行分配、质检和 SLA 管理。
- 知识管理员：上传、更新、删除知识文档并进行检索验收。
- 平台管理员：配置租户、身份、数据源和运行策略。

## 3. 核心业务数据

| 实体 | 关键字段 | 典型来源 | 当前实现 |
| --- | --- | --- | --- |
| Customer | tenant、客户、公司、行业、级别、脱敏联系方式 | CRM | SQLite 示例表 |
| Order / Subscription | 订单、购买人、产品、版本、服务期、金额、套餐配额 | 订单/计费系统 | SQLite 示例表 + 只读工具 |
| Support Case | 渠道、问题、优先级、SLA、分配客服、关联订单 | 客服接入层 | SQLite 示例表 + 队列 API |
| Conversation / Message | 会话所有者、消息、Agent 状态、引用 | 会话服务 | SQLite 持久化 |
| Document / Chunk | 文档版本、权限、来源、结构化片段 | 企业文档源 | SQLite + Milvus/内存向量 |
| Pending Action | 操作类型、参数、action_id、会话所有者 | Agent 服务 | 服务端会话状态 |
| Ticket | 客户、订单、Case、状态、幂等键 | Helpdesk | SQLite 示例表 + 写工具 |
| Audit Log | 操作人、动作、资源、时间、参数摘要 | 治理平台 | SQLite 审计表 |

## 4. 端到端流程

1. 接入层创建 Support Case，并将它分配给某个客服。
2. 工作台只加载当前租户且分配给当前客服的会话。
3. 选择会话后，系统聚合客户、关联订单、服务期和套餐权益。
4. 知识问题进入 Hybrid RAG。Dense 和 BM25 都在召回阶段应用租户与文档权限。
5. 订单查询工具校验租户、客服角色和 Case 分配；普通客户身份只能查询自己的订单。
6. 回答保留文档、Chunk 位置和分数，客服可以打开来源核验。
7. 需要升级时，Agent 先生成 Pending Action，不直接写入工单系统。
8. 客服确认后执行幂等创建，将 Ticket 关联回 Customer、Order 和 Support Case，并写入审计。

## 5. 权限边界

知识权限和业务对象权限是两套不同边界：

- 知识权限使用 tenant、department、visibility 和 owner，在 Dense 与 BM25 召回前过滤。
- 客服会话使用 tenant + assignee 校验；主管和管理员可访问租户内全部会话。
- 订单工具对普通用户使用 tenant + user，对客服使用 tenant + assigned case。
- Pending Action 绑定 tenant + user + conversation，其他客服不能确认或覆盖。
- 所有有副作用动作必须产生审计记录。

## 6. 与真实企业系统的连接方式

当前 SQLite 表是可运行的本地适配器。生产环境可以在不改变 Agent 主流程的前提下替换为：

- CRM Connector：读取客户、公司、级别和脱敏联系方式。
- Order/Billing Connector：只读查询订单、订阅、服务期和配额。
- IAM Connector：从 OIDC/JWT 获取租户、部门、用户与角色。
- Helpdesk Connector：向 Jira Service Management、Zendesk 或企业自研工单系统创建工单。
- Content Connector：从对象存储、SharePoint、Confluence 或内部文档平台同步知识。

连接器应具备超时、重试、熔断、幂等、字段映射、数据脱敏和审计。业务写工具默认采用最小权限的
服务账号，不能直接复用模型供应商凭证。

## 7. 验收标准

- 越权用户无法读取其他租户、其他客户或未分配 Case 的订单。
- 客服能够从来源预览核对答案，未找到证据时系统不编造确定性结论。
- 工单必须经过人工确认；重复确认不能创建重复工单。
- 每个工具调用和安全拦截都可通过 tenant、user、resource 和时间追踪。
- 本地模式可以单机启动，完整模式可以通过 Docker Compose 启动第三方中间件和应用。
- 使用脱敏真实问法持续评估 Recall@K、MRR、无答案表现、权限样本和端到端延迟。
