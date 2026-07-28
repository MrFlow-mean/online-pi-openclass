# OpenClass AI 协作宪法

本文件是 OpenClass AI 链路的规范来源。`AGENTS.md` 只保留日常协作必需的摘要，任何涉及 AI 角色、链路顺序、写入授权、需求清单或路由审计的改动都必须先读本文件。

## OpenClass 宪法：通用能力优先

OpenClass 是通用 AI 课程工作台，不是学科模板系统。最高优先级是保持系统通用性。

核心原则：

- 不要为案例写代码，要为能力写代码。
- 核心代码只处理通用学习能力、内容形态、资料结构、用户意图、文档操作和模型调用。
- 具体学科、具体教材、具体考试、具体语法点、demo 样例不得进入核心默认路径。

核心代码严禁加入：

- 学科关键词分支
- 教材关键词分支
- 固定讲义 HTML
- 固定课程模板
- demo 内容
- 针对单一测试样例的分支

严禁出现类似逻辑：

```python
if "法语" in topic:
if "数学" in topic:
if "文科" in topic:
if "计算机" in topic:
if "考试" in topic:
if "高考" in user_message:
if "CSAPP" in resource_name:
if "统计学习理论" in chapter_title:
```

允许的抽象：

- `request.intent == "generate_dialogue_practice"`
- `document_shape == "procedural"`
- `reference_context.has_numbered_sections`
- `user_goal == "exam_review"`

这些是通用教学形态、资料结构或用户目标，不是具体学科、教材或样例特例。

修改前必须先判断需求属于哪一类：

1. 通用能力
2. 内容形态
3. UI 交互
4. prompt 质量
5. schema / 数据结构
6. 资料解析
7. 专属插件 / adapter
8. 测试样例

如果你认为需要特殊处理，必须先回答：

1. 这是通用能力，还是某个特例？
2. 能不能用内容形态抽象解决？
3. 能不能放进 prompt，而不是 Python/TypeScript 核心代码？
4. 能不能放进 plugin / adapter，而不是默认核心路径？
5. 换成任意主题后，这段逻辑是否仍然成立？

改动前必填自检表：

```text
需求：
要改的文件：
问题属于：
[ ] 通用产品能力
[ ] 内容形态抽象
[ ] prompt 质量问题
[ ] schema / 数据结构问题
[ ] UI 交互问题
[ ] 资料解析问题
[ ] 特定教材 adapter
[ ] demo / 测试样例

是否引入以下内容：
[ ] 学科关键词
[ ] 教材关键词
[ ] 固定 HTML
[ ] 固定讲义内容
[ ] demo 内容
[ ] 针对单一测试样例的分支
```

如果上面任何一项为是，必须停止，重新设计。

文件边界：

- `lesson_factory.py`：只做 lesson、requirements、teaching guide 初始化。
- `fallback_generator.py`：只做领域无关 fallback，不得成为模板仓库。
- `renderer.py`：只做渲染路径选择，不写具体课程内容。
- `resource_library.py`：只做通用资料解析，不内置教材目录。
- `openai_course_ai.py`：只做模型调用、prompt、schema 解析，不写学科分支。
- `course-studio.tsx`：只做顶层组合，不继续堆状态、effect、realtime、editor、model selection 逻辑。

修改后必须说明：

1. 改了哪些文件。
2. 为什么这些改动是通用的。
3. 有没有新增领域硬编码。
4. 有没有让某个文件继续膨胀。
5. 如何验证。

## OpenClass 宪法：链路兼容与 AI 多角色协作

新增 AI 链路、功能、角色或工具调用时，最高约束是：**新能力必须接入现有协作协议，而不是重写、抢占或绕过现有协作协议。**

开发任何新能力前，必须先识别当前已经稳定存在的旧链路，并保证新能力不会隐式改变旧链路的触发条件、角色调用顺序、上下文来源、写入权限、状态归属、历史记录和用户可见输出边界。

标准回合流程：

```text
用户输入
→ TurnDecision 判断本轮任务
→ ResolveTarget 定位板书、选区、资料证据或对话上下文
→ BuildContext 构造最小必要上下文
→ ExecuteRole 执行唯一主动作
→ PersistHistory 写入历史、commit 与可追踪 metadata
→ UpdateRequirement 记录需求变化
```

固定工作流程架构（宪法级约束，普通功能开发不得更改、绕过、弱化或用 prompt 替代）：

本节描述 OpenClass 当前不可动摇的 AI 协作工作流。任何新增功能、bugfix、prompt 调整、UI 优化、模型替换、性能优化、历史记录改动，都必须服从本节；不得以“临时兜底”“体验优化”“为了某个测试更快通过”“模型自己能判断”为理由绕开本节。实现者必须把这些规则当作代码边界，而不是建议。

### 术语与状态边界

- `board_document` 为空：右侧板书文档没有可学习内容，系统处于“从零到有板书”的第一层链路。此时用户真正需要的是先把学习需求说清楚，而不是局部讲解、局部编辑或互动练习。
- `board_document` 非空：右侧已经有板书内容，系统处于“围绕已有板书处理具体任务”的第二层链路。此时不再用第一层学习需求清单决定本轮动作，而必须用第二层 `BoardTaskRequirementSheet` 决定“在什么地方、做什么、围绕什么、是否有特殊交互方式要求”。
- `InitialLearningWorkModeDecision`：第一层空白板书入口的学习工作模式判断。它只根据通用学习形态和内容产物形态判断 `knowledge_board`、`narrow_topic`、`practice_artifact` 或 `unknown`，不得根据学科、教材、语法点、旅游场景或样例关键词分支。
- `LearningRequirementSheet`：第一层学习需求清单，只服务于空白板书首次生成。它记录用户想学什么、目标、水平、背景、输出偏好、成功标准等，并可记录 `work_mode` 与 `granularity`。不同 `work_mode` 对清单完整度的要求不同。
- `LearningClarificationStatus`：第一层需求澄清状态，只描述空白板书生成前的需求完整度、缺项、下一问、是否可生成，并可记录当前 `work_mode` 与 `granularity`。
- `BoardTaskRequirementSheet`：第二层已有板书任务清单，只服务于已有板书后的具体任务。它固定记录四个字段：目标位置、动作类型、问题 / 主题内容、是否有练习或特殊交互方式要求。
- `frozen requirement payload`：第一层清单生成板书前的冻结快照。它必须来自数据库版本记录，不得由当前运行态对象、原始对话、临时 prompt 拼接或 Chatbot 自由判断替代。
- `frozen board task` / board task version：第二层任务执行前的可追溯任务快照。写、改、讲、聊执行 commit 必须能追溯到该任务清单版本。
- `Board AI`：包含 BoardEditor、BoardTask route decision、BoardExplanationDirective 等板书侧智能。它拥有板书内容写入、目标内容截取、讲解授权和路线裁决权。
- `Chatbot`：左侧用户可见对话角色。它可以追问、解释状态、承接确认、执行被授权的讲解或互动回复，但不得自行生成右侧板书正文，不得自行裁决跳过板书侧流程。
- `FocusResolver`：目标位置定位角色。它负责把用户的“这里、第二节、刚才那段、选中的内容、某个标题”等定位成具体 `target_focus`，或在多候选 / 缺位置时要求澄清。
- `InteractionSession`：第二层 `chat` 路线启动后的规则互动循环。它必须有目标板书内容、互动规则、合规输入判定、进度和来源 board task 记录。

### 全局不可变门禁

- 每轮必须先判断 `board_document` 是否为空。空白板书和已有板书是两条不同链路，不得混用清单、状态机或生成入口。
- Chatbot 可以先完整回答用户的学习问题，但回答不构成板书写入授权。资料问答仍必须先完成资料选择与证据构造；板书写入则必须来自当前请求的明确修改要求，或用户对上一轮写入提议的明确同意。
- Chatbot、Realtime Chatbot、StrongReasoning 等 Chatbot 侧能力不得直接读取 `board_document` 全文、摘要、选区正文或候选片段正文。它们只能接收板书侧 directive、InteractionSession 或后端工具结果中明确交接的目标摘录、边界和指令。
- BoardEditor / 板书文档编辑 AI 不得读取用户和 Chatbot 的原始聊天记录、recent conversation 或自由对话摘要；它只能接收 frozen requirement payload、BoardTaskRequirementSheet、定位证据、当前文档、目标摘录、资料摘要和结构化 action instruction。
- BoardEditor 不得直接消费原始用户对话来首次生成板书；首次生成只能消费 frozen requirement payload。
- 已有板书后的明确写入 / 改写、目标片段讲解和规则互动不得由旧 action handler 触发；必须经过可审计的 TurnDecision，并在需要目标时完成 `BoardTaskRequirementSheet`、定位证据和 Board AI route decision。普通学习问题使用 `answer_then_offer`，不触发板书写入。
- 清单变动必须可追溯。第一层需求清单和第二层任务清单都必须有 run / version / event 或 commit metadata 记录；执行后不得静默清空。
- “没有变动的聊天”可以不新增清单版本，但任何真实变动、完成、冻结、确认、消费、取消、失败都必须有历史痕迹。
- prompt 只能帮助模型完成某个角色的判断或生成，不能替代状态机、数据库历史、定位器、确认门禁和 commit metadata。

### 第一层：空白板书从零到有

第一层链路只在 `board_document` 为空时启用。它的目标不是局部讲解、局部编辑或已有板书互动，而是先识别用户要进入哪一种通用学习工作模式，再按对应清单粒度冻结需求，交给板书文档编辑 AI 生成右侧板书。

第一层固定工作模式：

1. `knowledge_board`：用户想学新知识，且目标已经小到一个知识点或一个清晰概念。系统只构造最小清单，记录知识点、用户原始意图、可见背景和必要输出聚焦点；不要求补齐整篇课程需求、完整学习计划、成功标准或练习设计。最小清单冻结后由 BoardEditor 生成聚焦知识板书。生成成功后 Chatbot 只承接询问是否开始讲，不自动讲第一节。
2. `narrow_topic`：用户想学新知识，但主题仍然过宽，无法生成聚焦板书。系统只维护最小清单和缩小主题所需的一个关键追问，不生成板书，不进入完整课程需求清单。
3. `practice_artifact`：用户想练习、测验、角色互动、生成可操练材料、案例任务、对话课文、题目或类似可被练习的学习产物。系统必须维护完整需求清单；当练习目标、学习者背景、材料形态、约束和成功标准足够时，冻结清单并生成练习板书；不足时只追问最关键缺项。
4. `unknown`：学习工作模式不确定时，不得生成板书。Chatbot 必须基于当前已知上下文向用户提出学习内容方向建议，再只问一个选择或缩小问题；不得空泛反问用户“想学什么”，也不得把未知目的直接推进完整需求清单。

工作模式分类依据必须是通用学习形态和内容产物形态：

- 允许依据：用户是否在学新知识、主题是否足够窄、是否要求练习或可操练材料、是否要求生成题目 / 测验 / 对话 / 案例 / 角色场景等通用内容形态。
- 严禁依据：具体学科、语言、教材、考试、语法点、旅游场景、demo 文本或任何样例关键词。
- 单知识点新知识也要生成板书；区别是只使用最小清单，不完善整篇需求清单。
- 完整需求清单主要服务 `practice_artifact` 和明确需要复杂学习产物的首次板书生成，不得把所有新知识请求都拖入完整课程需求澄清。

固定顺序：

1. 用户输入进入 chat orchestrator。
2. 系统确认当前 `board_document` 为空，并且本轮确实是学习 / 练习 / 生成学习材料意图；普通聊天不得更新 `LearningRequirementSheet`。
3. 系统先生成 `InitialLearningWorkModeDecision`，判断 `work_mode` 与 `granularity`。
4. `knowledge_board`：Requirement Manager 构造最小 `LearningRequirementSheet + LearningClarificationStatus`，写入 collecting / ready / completed 或 frozen 相关版本和事件，冻结后交给 BoardEditor 生成聚焦知识板书。
5. `narrow_topic`：Requirement Manager 构造最小清单状态并写入 collecting 版本和事件；Chatbot 只追问一个缩小主题的问题，不生成板书。
6. `practice_artifact`：Requirement Manager 维护完整 `LearningRequirementSheet + LearningClarificationStatus`，围绕练习目标、学习者背景、材料形态、约束、输出偏好和成功标准等继续澄清。
7. `unknown`：Requirement Manager 只记录已知事实和缺失的工作模式；Chatbot 根据 lesson 标题、最近对话、已选资料摘要和用户本轮话语中已经明确的信息，给出 2-3 个通用学习方向或学习产物方向建议，并只问一个选择 / 缩小问题。
8. 如果当前工作模式要求的清单未完整，Chatbot 只能追问关键缺项、提出上下文相关建议或进行需求澄清，不得让 BoardEditor 生成板书。
9. 当对应工作模式达到 `ready_for_board=true` 或用户明确强制生成时，必须先写 completed / forced_frozen 版本，再写 frozen 快照。
10. frozen 快照必须在调用 BoardEditor 前落库，并通过 SSE / response 让前端可见进度变化。
11. BoardEditor 只能接收 frozen requirement payload、冻结澄清状态和必要资源摘要；不得接收未冻结 conversation、临时 PM 状态或 Chatbot 自由总结作为生成依据。
12. BoardEditor 生成右侧板书，Chatbot 不生成板书正文。
13. 生成成功后，必须写 lesson commit，commit metadata 必须包含 requirement run / frozen version、`work_mode` 和 `granularity` 追踪信息。
14. requirement run 必须标记为 consumed；当前 active requirement 清单清空，但历史版本保留。
15. 生成失败时，必须写 generation_failed 事件，不写成功 commit，并允许同一 frozen version 后续重试。
16. 从零生成完板书后，Chatbot 默认承接询问用户是否要从头开始讲解，不自动展开实质讲解。

第一层严禁：

- 空白板书时让 BoardEditor 直接吃原始聊天记录生成板书。
- 未先判断 `work_mode` 就把所有学习请求都推进完整需求清单。
- `narrow_topic` 或 `unknown` 状态下偷偷生成板书。
- `unknown` 状态下只做空泛澄清，不利用已知上下文给出学习方向建议。
- `knowledge_board` 已达到单知识点粒度时继续强迫用户补齐整篇课程需求清单。
- `practice_artifact` 未达到可生成练习材料所需最低信息时偷偷生成板书。
- 生成后用运行态板书标题、摘录、临时 runtime 字段反写成“需求清单快照”。
- 生成成功后静默清空清单而不消费 run、不留版本历史。
- 为某个学科、教材、考试、demo 编写特殊第一层生成分支。

### 第二层入口：已有板书四字段任务清单

只要 `board_document` 非空，普通用户请求默认不再走第一层“从零到有板书”的需求清单闭环，而进入第二层已有板书任务链路。第二层的第一步永远是维护 `BoardTaskRequirementSheet`，而不是直接写、改、讲、聊。

四字段固定含义：

1. 目标位置：用户要处理板书中的哪里，例如选区、标题、段落、编号、前后文、某个明确片段。它最终必须解析为 `target_focus`，除非是无目标位置且经确认的内容缺失扩写。
2. 动作类型：用户到底要 `write`、`edit`、`explain` 还是 `chat`。
3. 问题 / 主题内容：用户围绕目标位置想补写、修改、理解、练习或互动的具体内容。
4. 特殊交互方式要求：用户是否要求练习、角色扮演、轮流读、问答、测验、纠错等特定互动规则；没有特殊互动要求时必须明确为无，而不是空悬。

第二层固定顺序：

1. 用户输入进入 orchestrator。
2. 系统确认 `board_document` 非空。
3. Chatbot / task manager 从用户话语、选区和上下文中维护 `BoardTaskRequirementSheet`。
4. 清单未完整时，写 collecting version / event，前端显示进度；Chatbot 只追问一个最关键缺项，不执行写、改、讲、聊。
5. 清单完整时，先写 ready 版本，再调用 FocusResolver 定位目标位置。
6. 定位结果和完整清单交给 Board AI route decision。
7. Board AI 只能裁决 `write / edit / explain / chat / clarify_location / await_write_confirmation`。
8. 多候选、位置缺失或定位不可靠时，只能 `clarify_location`，不得执行写、改、讲、聊。
9. 执行成功后，必须写 commit metadata：`board_task_run_id`、`board_task_version_id`、`board_task_route`、`board_task_decision`、`board_task_cleared`。
10. 执行成功后，board task run 标记 consumed；当前 active board task 清空，但历史保留。
11. 未执行、取消、失败必须写 not_executed / archived / execution_failed 类事件或 commit metadata，不得静默消失。

### 第二层 A：已有板书但目标内容不存在

这个链路用于：用户想学、问、讲某个主题，但当前板书全文找不到相关位置或目标内容。此时系统默认先在聊天中解决用户当下的学习问题，板书保持不变；回答完成后再自然询问是否把新内容写入板书。

固定顺序：

1. TurnDecision 先判断当前请求是普通学习问题、明确板书修改，还是对上一轮写入提议的确认 / 拒绝。
2. 普通学习问题由 Chatbot 先完整回答，不得在本轮修改 `board.md`。
3. Chatbot 在满足当下学习需求后，自然询问用户是否把新讲解内容写入板书，不使用固定兜底话术。
4. 待写入提议必须与问题、已生成讲解和当前 commit 可追溯关联，不能只存在模型短期记忆中。
5. 用户明确同意后，BoardEditor 复用上一轮待写入内容，根据当前板书选择合适位置写入，不得把“同意”扩大成无关改写权限。
6. 用户当前请求已明确要求写入、修改、改写、替换或删除板书时，本轮直接交给 BoardEditor 执行，不再追加一次写入确认。
7. 写入成功后写 lesson commit，commit metadata 必须记录意图信号、命中规则、授权来源、执行角色和 `document_changed`。
8. 用户拒绝、不确认或取消时，板书保持不变，待写入提议清空，历史保留。

此链路严禁：

- 把 Chatbot 的普通问题回答当成板书修改授权。
- 用户未明确修改、也未确认待写入提议时，让 BoardEditor 修改板书。
- 用户拒绝扩写后继续补写。
- 将“编辑不存在的内容”直接当成编辑执行；连续定位失败的编辑任务必须不执行，并按规则转入扩写确认或澄清。

### 第二层 B：已有板书且找到目标位置

这个链路用于：用户已经给出或系统已经定位到板书中的目标位置。此时动作类型可能是有目标位置的 `write`、`edit`、`explain` 或 `chat`。四条路线都必须从完整 board task 清单和定位证据进入，不允许旧执行器绕过。

#### B1. 有目标位置的 `write`

- 语义固定为：在目标位置扩写特定内容。
- 必须具备完整 `BoardTaskRequirementSheet`、`target_focus`、`write_proposal`。
- BoardEditor 只能围绕目标位置扩写，不得无理由改写全文、替换无关段落或追加到任意位置。
- 写入成功后必须刷新板书 runtime、写 commit、记录 `board_task_route=write`，并 consume board task。
- 如果目标位置缺失、多候选或不可靠，必须先澄清位置；不得把有目标位置的写降级成无目标追加。

#### B2. `edit`

- 语义固定为：改写板书中的目标文段。
- 必须具备 `target_focus + edit instruction`。
- BoardEditor 只能修改目标范围；不得凭用户一句“改一下”改全文。
- 多候选时必须让用户选择；找不到位置时必须追问位置。
- 如果用户明确要编辑某内容但该内容全文没有，连续定位失败后旧 edit task 必须 not_executed；新开的扩写任务只继承用户想补充的主题 / 问题内容，不继承原编辑动作要求。
- 编辑成功后写 commit，记录 route / decision / focus，consume board task 并清空 active board task。

#### B3. `explain`

- 语义固定为：对目标位置的板书内容进行讲解。
- 必须具备 `target_focus` 或等价目标摘录。
- 板书侧必须先产生 `BoardExplanationDirective`，包含目标摘要、目标摘录、讲解边界、允许 / 不允许讲解状态和给 Chatbot 的 teaching instruction。
- Chatbot 只能依据 directive 讲解；directive 不允许或需要澄清时，Chatbot 只能追问或说明状态。
- Chatbot 不得凭原始 conversation、自己的常识、未定位文本、未冻结清单直接讲解。
- 讲解成功后写 chat commit，记录 directive、route、focus 和 board task metadata，consume board task。

#### B4. `chat`

- 语义固定为：围绕目标位置的板书内容，按用户指定的特殊交互规则进行循环互动。
- 只有用户明确提出练习、问答、角色、轮次、朗读、测验、纠错等特殊互动方式时，才允许 `requested_action=chat`。
- 必须具备 `target_focus`、目标文段、`interaction_rule_draft.should_start=true`、规则文本、互动目标、合规输入说明、assistant 行为说明。
- 启动 `InteractionSession` 时必须保存：规则、目标文段、合规输入判定、进度、来源 `board_task_run_id`、来源 `board_task_version_id`。
- 启动成功后 board task 必须 consumed，active board task 清空，但 session 保持 active。
- 每轮互动先由 interaction decision 判断用户输入是否符合规则。
- `continue_rule`：用户输入合规，Chatbot 按规则和目标文段继续互动。
- `rule_violation`：用户仍在当前互动任务内，但输入格式、顺序或内容不符合规则；Chatbot 只做规则内纠错，不跳出到普通讲解。
- `exit_rule`：用户明确结束互动；session 结束，不再继续按规则互动。
- `new_task`：用户输入脱离当前互动规则，或提出新的写 / 改 / 讲 / 学习需求；session 必须结束，并把本轮用户输入重新送回第二层四字段任务清单。
- 规则外内容默认回到 board task 清单，不保留“暂停后旁路讲解”作为默认链路。

### 不可动摇的讲解与写入边界

- 用户问普通学习问题时，Chatbot 先完整讲解，不得同轮擅自写入板书。
- 用户明确指向板书中某个标题、选区或片段并要求讲解时，仍必须依照板书 AI / `BoardExplanationDirective` 给出的目标内容、摘录、边界和指令。
- 资料问答不得把未验证的资料摘要当成事实；但不要求引用板书片段的普通问题，可以使用模型自身知识回答。
- 任何代码路径只要会修改板书，就必须能在 commit metadata 中追溯到当前明确修改请求，或上一轮待写入提议与当前确认。

### 不可动摇的文档生成格式约束

本约束用于防止板书生成、改写、缩短、扩写、导入、流式预览或历史恢复时出现“模型直接输出 HTML”“普通文本被渲染成特殊格式”“旧 Markdown 层级被压扁”等问题。它是通用文档能力约束，不属于某个学科、教材、题型或 demo 的特殊规则。

- AI 生成给右侧板书的正文必须是 ChatGPT 风格的 Markdown / 普通文本：标题用 Markdown 标题，列表用 Markdown 列表，强调用 Markdown 加粗，表格用 Markdown 表格。
- 除了真实数学公式外，AI 生成的正文不得包含 HTML 格式内容或 HTML 标签，例如 `<h1>`、`<p>`、`<strong>`、`<em>`、`<ul>`、`<ol>`、`<li>`、`<table>`、`<span>`、`style=`、`class=`。
- 真实数学公式才允许使用 LaTeX 数学定界符或数学节点；普通语言、例句、语法说明、箭头说明、纠错说明、角色台词、编号、等号、括号、斜杠等都必须保持普通可见文字。
- 文档中的真实公式可以由系统渲染为 HTML / HTML DOM 或等价数学节点用于显示；这只属于展示层输出，不改变 `content_text` 仍以 Markdown / 普通文本与 LaTeX 数学表达作为事实来源的约束。
- `content_text` 是 AI 正文的事实来源。模型不得把 `content_text` 写成 HTML；如果模型返回 HTML，后端必须转换成 Markdown / 普通文本或拒绝本次写入，不能把原始 HTML 当成正式板书正文保存。
- `content_html` 和 `content_json` 只能是系统从受信任的 Markdown / 普通文本派生出来的内部渲染结果；BoardEditor 不得采信模型直接给出的 `content_html` 作为正式文档。
- 前端编辑器可以用 HTML DOM 呈现富文本，这是 UI 渲染层职责；这不等于允许 AI 生成 HTML 正文，也不等于允许把 HTML 作为文档语义源头。
- 历史板书、导入文档或模型输出中如果混入了 HTML，修复路径必须优先做通用 Markdown 化、富文本结构保持和安全清洗；不得为某个语言、课程名、测试样例或固定文本写特殊修复分支。
- prompt 可以提醒模型输出 Markdown，但 prompt 不能替代后端格式门禁。任何写入正式板书的路径都必须在 service 层保证：非公式内容不以 HTML 或数学格式落库。

### 主动讲解与被动行动边界

本边界用于防止系统在需求清单还不充分时擅自开始教学，同时保证用户已经明确要求行动时不会被“继续完善清单”无限拖住。

- 主动讲解：指用户没有明确要求本轮开始讲解、写入、改写或规则互动时，系统根据自己判断想推进教学内容。主动讲解只允许在对应清单已经足够完整、目标已经定位、板书侧已经授权时发生。
- 被动行动：指用户本轮已经明确要求“讲解、解释、说明、开始讲、写、补充、修改、改写、练习、互动、按规则来”等动作。被动行动不要求清单达到理想丰满度，但必须达到可执行最低条件，并且仍然必须经过第二层任务清单、定位、route decision 和讲解 directive / 编辑器门禁。
- 第一层空白板书中，如果学习需求清单未完整且用户没有明确要求生成，Chatbot 只能继续澄清学习需求，不得主动生成板书或展开教学。
- 第一层空白板书中，如果用户明确要求“直接生成、开始生成、别问了”，系统可以强制冻结当前清单并生成，但必须写 forced_frozen / frozen 历史；这不是绕过清单，而是把不完整清单以强制开始的方式审计下来。
- 第二层已有板书中，如果四字段任务清单未完整且用户没有明确要求执行，Chatbot 只能追问缺项，不得主动讲解、写入、改写或启动互动。
- 第二层已有板书中，普通学习问题默认进入 `answer_then_offer`：先回答、不改板书、再询问是否写入。只有明确指向板书目标片段的讲解请求继续进入 `BoardTaskRequirementSheet + BoardExplanationDirective` 链路。
- 第二层已有板书中，如果用户在多候选澄清后明确说“都讲、全部讲、逐个、按顺序”，这表示目标是这些候选的顺序集合；对 `explain` 路线可以先从第一个候选生成 board directive 并开始讲解，不得无意义地反复要求用户选择单一位置。此规则只适用于讲解，不适用于写入或改写。
- “尽可能完善清单”是主动阶段的策略；“用户明确要求行动”是被动阶段的触发。两者冲突时，以被动行动触发为准，但绝不取消板书侧授权、定位和历史审计门禁。

### 不可更改声明

- 本固定工作流程架构是 OpenClass 默认 AI 协作宪法，不属于普通业务逻辑、普通 prompt、普通 UI 体验或单点 bugfix 的可修改范围。
- 后续开发只能在这些链路内增加更细的能力、测试、UI 展示、日志或模型质量改进；不得改变角色写权限、资料证据门禁、冻结门禁和历史审计要求。普通问题的讲解先于写入确认，是本宪法已定义的默认顺序。
- 任何实现如果绕过本节，即使测试通过，也视为架构违规，必须重做。
- 如果未来用户明确要求设计新层级或替换本宪法，必须先用独立设计说明列出旧链路、替换原因、兼容方案、迁移方式和回归测试；在新宪法落库前，不得通过代码偷偷改变现有链路。

核心原则：

- 新增能力只能作为明确的新分支、新步骤或可替换模块接入，不得隐式抢占旧流程。
- 不得让 Chatbot 把最终回答当成默认写入权；资料问答仍需先选择可验证证据，板书写入仍需明确修改请求或待写入提议确认。
- 不得让 PM / Requirement Manager 在用户可见回答之后才决定本轮动作类型。
- 不得用 prompt 弥补缺失的流程判断、目标定位、资料证据或写入确认。
- 如果新能力必须调整旧链路，必须先说明旧链路原本如何工作、为什么必须替换、如何兼容旧行为，以及用哪些回归测试证明没有退化。
- 如果本轮只需要当前板书，就不要默认注入无关资料摘要；如果本轮需要资料证据，必须先由 ResourceResolver 明确选择相关资料。

AI 角色权责：

- `Chatbot`：只负责左侧可见对话、讲解、确认、承接和状态说明；不得生成整篇板书正文，不得假装已写入文档。
- `BoardEditor`：只负责右侧板书 / 文档的生成、替换、扩写、改写、简化和结构化写入。
- `PM / Requirement Manager`：只负责维护学习需求、动作意图和任务状态；不得事后改变已经执行的本轮动作。
- `FocusResolver`：只负责定位板书中的目标段落、标题、列表项、选区或上下文，并在不确定时要求确认。
- `ResourceResolver`：只负责选择资料证据、章节、片段和引用范围；不得默认污染与资料无关的 Chatbot prompt。
- `StrongReasoning`：只作为隐藏工具提供推理材料，不直接成为新的可见老师，不改变 Chatbot 的 learner-facing 身份。
- `Realtime`：只是同一个 Chatbot 的实时形态，不是另一个独立教师角色。

新功能接入前必须回答：

1. 这个能力改变标准回合流程的哪一步？
2. 本轮谁拥有最终决策权？
3. 这个角色的输入、输出、写权限是什么？
4. 它是否需要板书定位？如果需要，是否已经在回答或写入前完成？
5. 它是否需要资料证据？如果需要，是否已经明确选择相关资料？
6. 它失败或不确定时，是追问用户、降级处理，还是停止？
7. 它会不会让 Chatbot、PM、BoardEditor、FocusResolver、ResourceResolver 的职责重叠？
8. 它有没有破坏普通聊天、板书生成、板书局部讲解、板书编辑、资料问答、资料写入板书这些主链路？

链路兼容自检表：

```text
本次新增的是：
[ ] 新链路
[ ] 旧链路增强
[ ] 旧链路替换
[ ] 单点 bugfix

会影响哪些旧链路：
[ ] 普通 Chatbot 问答
[ ] 板书生成
[ ] 板书局部讲解
[ ] 板书编辑 / 改写 / 扩写 / 简化
[ ] 资料问答
[ ] 资料写入板书
[ ] 互动规则 / session
[ ] 强推理辅助
[ ] 历史记录 / commit
[ ] 流式输出 / loading 状态

是否改变以下内容：
[ ] 触发条件
[ ] 角色调用顺序
[ ] 上下文来源
[ ] 写入权限
[ ] 状态更新时机
[ ] 历史记录格式
[ ] 用户可见回复边界
```

如果上面任何一项为是，必须补充旧链路回归测试或明确说明为什么不需要。不能因为新增一个场景，就让既有链路里的目标定位、资料证据、写入确认、历史记录和状态更新步骤被绕过。

新增或修改链路后必须说明：

1. 保留了哪些旧链路。
2. 新链路接入在标准回合流程的哪一步。
3. 每个 AI 角色的输入、输出和权限有没有变化。
4. 是否存在 PM 事后决策、Chatbot 抢先回答、资料上下文污染或板书定位绕过。
5. 用哪些测试或日志证明旧链路协作流程没有被破坏。

### 反补丁式工程治理

OpenClass 禁止通过“眼前行为坏了就往核心链路再补一个判断”的方式开发。AI 工作流必须像严格工程项目一样维护：每个模块知道自己不该做什么，每个行为有测试固定，每个路由决策有 trace 可查，每个 PR 只改变一个清晰方向。

补丁式开发的典型风险：

- 同一句自然语言被多个规则同时抢占，触发优先级越来越难解释。
- Chatbot 越过需求清单、目标定位、资料选择、写入确认或板书侧 directive，提前给出最终回答。
- BoardEditor 混入原始聊天、临时摘要或旧需求，吃错上下文后改错板书。
- 历史记录看不出是谁在什么时候清空了清单、消费了任务或改了文档。
- 测试只覆盖某个补丁样例，其他主链路被旁路破坏。

任何改动前必须先回答：

```text
这次要解决的是：
[ ] 通用能力
[ ] 内容形态
[ ] 链路状态
[ ] 角色边界
[ ] UI 交互
[ ] 测试缺口
[ ] 单个样例补丁
```

如果本次改动属于“单个样例补丁”，必须停止，重新设计为通用能力、内容形态、链路状态或测试缺口修复。

任何开发前必须定位本次改动属于标准链路的哪一步：

```text
TurnDecision:
ResolveTarget:
BuildContext:
ExecuteRole:
PersistHistory:
UpdateRequirement:
```

如果说不清属于哪一步，不得修改代码。严格工程化开发依赖职责归位，而不是靠更多隐藏判断。

测试先于规则：

- 任何自然语言规则、路由规则、任务判断，必须先写 golden fixture。
- 每个正例至少配两个反例。
- 没有反例，不允许新增规则。
- 如果一个 bug 只能通过新增 `if` / regex 修好，必须先证明这是通用信号，而不是单句补丁。

PR 形状约束：

```text
本 PR 只解决一个问题。
最多改 5 个文件。
不改数据库。
不改认证。
不改部署。
不新增依赖。
不改无关 UI。
不顺手重构。
```

如果预计超过 5 个文件，必须先拆 PR。任何需要新增依赖、改数据库、改认证、改上传、改日志、改部署或改环境变量的方案，都必须标为“暂停，需要人工确认”。

核心文件不得继续吞职责：

- `chatbot.py` 不允许新增自然语言正则，不允许新增大 handler。
- `course-studio.tsx` 不允许继续新增复杂 state / effect / realtime / editor / model selection 逻辑。
- `openai_course_ai.py` 不允许写业务分支、学科分支或路由特例。
- `board_document_editor.py` 不允许混入聊天路由、任务判断或目标定位职责。
- 如果没有合适模块承接新能力，必须先设计模块边界，再实现。

每次 AI 路由相关改动必须回答：

1. 这个行为在 metadata 或 response 里能看到什么？
2. 能不能知道匹配了什么信号？
3. 能不能知道为什么选择这个 action？
4. 能不能知道为什么没有选择另一个 action？
5. 能不能知道哪个角色执行了？
6. 能不能知道文档是否被修改？

如果调试只能靠猜，说明代码正在变成黑箱。此时应增加 `DecisionTrace` 或测试断言，而不是继续增加隐藏分支。

开发口令：

```text
不要补丁式开发。

修改前必须先说明：
1. 这个问题属于哪条标准链路。
2. 当前旧行为是什么。
3. 要固定哪些 golden tests。
4. 本次改动的模块边界是什么。
5. 哪些文件绝对不能动。
6. 是否会新增自然语言规则；如果会，必须有正例和至少两个反例。
7. 是否会增加 DecisionTrace。
8. 如果需要超过 5 个文件，先拆 PR，不要直接实现。

没有完成以上说明，不要写代码。
```

OpenClass 以后不得靠“更多判断”变强，而要靠更清楚的链路、更稳定的角色边界、更可审计的决策和更可复用的测试来变强。任何修法都必须证明：它不会让系统继续变乱。

### Definition of done

A task is done only when:

- The intended behavior is covered by tests or fixtures.
- The implementation touches the smallest reasonable set of files.
- Public behavior changes are documented.
- Relevant tests were run and results are reported.
- Remaining risks are explicitly listed.

### Natural Language Rule Governance

自然语言规则是 OpenClass 最容易补丁化的区域。任何新增或修改自然语言行为，都必须先证明它是通用信号、通用动作、通用目标定位或通用内容形态，而不是某个单句、单资料、单 demo 的特殊分支。

- Do not add a new regex directly inside `chatbot.py`.
- When adding or changing natural-language behavior, add a golden fixture first.
- Add at least two negative examples for each new positive fixture.
- Put signal extraction in `turn_intent.py`.
- Put action decisions in `board_task_decider.py`.
- Put target location in `target_resolvers/`.
- Put sequence decisions in `sequence_planner.py`.
- Put exercise / paragraph atom extraction in `explanation_atom_extractors/`.
- Include the matched rule name in `DecisionTrace`.
- A regex without positive and negative tests is not acceptable.

自然语言规则的职责边界：

- `turn_intent.py` 只抽取用户话语中的意图信号，例如 `wants_explain`、`wants_collection`、`wants_edit`、`wants_interaction`；不得直接决定写、改、讲、聊。
- `board_task_decider.py` 只根据意图信号、板书状态、任务清单和定位状态决定动作；不得直接做目标定位或生成回复。
- `target_resolvers/` 只做目标位置解析，例如选区、标题、编号、段落、练习集合、前后文；不得直接执行讲解或写入。
- `sequence_planner.py` 只决定是否进入顺序讲解、逐段讲解、逐题讲解或继续当前 sequence；不得直接生成最终讲解。
- `explanation_atom_extractors/` 只把板书内容切成可讲解的原子单元，例如段落、条目、练习题、问答对；不得写入板书或改变任务清单。

### DecisionTrace

AI 路由必须可审计。每次修改 AI 路由时，必须保证 response 或 commit metadata 里能看到本轮为什么走到这个行为。可追踪信息至少应覆盖：

```json
{
  "intent_signals": ["wants_explain", "wants_collection"],
  "matched_rules": ["collection_explanation_request"],
  "selected_action": "explain",
  "target_resolver": "ExerciseCollectionResolver",
  "sequence_mode": "atomic_explanation",
  "role_executed": "chatbot",
  "document_changed": false,
  "reason": "collection explanation requested for exercise group"
}
```

- Any AI routing change must preserve or improve `DecisionTrace`.
- If a behavior is hard to debug, add trace fields instead of adding hidden branching.
- `DecisionTrace` 必须描述通用决策原因，不得记录学科关键词、教材关键词、demo 内容或固定讲义内容作为路由依据。
- 如果新增规则会改变 `TurnDecision -> ResolveTarget -> BuildContext -> ExecuteRole -> PersistHistory -> UpdateRequirement` 中任一步，必须在 `DecisionTrace` 中标明被改变的步骤和原因。
- 如果某条规则匹配了用户输入，但最终没有被选为动作，也应在 `DecisionTrace` 或测试断言中说明它为什么被拒绝，防止多个自然语言规则静默抢占。
