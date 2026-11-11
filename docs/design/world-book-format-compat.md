# 世界书格式兼容文档（SillyTavern World Info / Character Card V2 `character_book` → 本项目）

> 范围：**只研究数据格式与触发语义**，不参考 SillyTavern UI（用户明确否决其界面）。
> 依据：`H4-BRIEF.md` §2B、`H4-CONTRACT.md` §3（冻结契约）、SillyTavern 官方文档
> <https://docs.sillytavern.app/usage/core-concepts/worldinfo/>、Character Card V2 规范
> <https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md>。
> 结论日期 2026-11-11。**状态列以当前实现为准**（H4-MVP 冻结字段 + 2026-11-11 P1「酒馆级」
> 升级：正则键 / 定时效果 / 包含组 / position-depth 排序均已落地；差异见 §5）。

## 0. 许可证与「怎么做才安全」

| 来源 | 许可证 | 本项目允许做什么 |
| --- | --- | --- |
| SillyTavern（`WorldInfo` 实现源码） | **AGPL-3.0** | **禁止复制任何代码/文案**；只允许阅读字段名与语义，由本项目独立重写（本文件为语义整理，非代码摘抄） |
| Character Card V2 规范仓库 | **无 LICENSE 文件**（GitHub API `license=null`） | 只依据公开字段语义独立实现；**不整段复制规范文本**；本文只列字段表 |
| 本项目契约/实现（`H4-CONTRACT.md`、`domain/world_book.py`） | 本项目 MIT | 自有实现；`raw_json` 保留未映射字段以向前兼容 |

格式兼容是**数据互换事实**（字段名/语义），不复制实现代码；导入器全部为本项目原创。

## 1. 接受的输入包装形态（检测顺序，契约 §3）

1. **SillyTavern World Info**：`{"entries": {"<uid>": {entry}, ...}}`（官方导出为 map）或
   `{"entries": [entry, ...]}`、或**裸 map** `{"<uid>": {entry}, ...}`。
2. **Character Card V2**：`{"spec":"chara_card_v2","data":{"character_book":{...}}}` 或 `{"character_book":{...}}`。
3. **本项目简化 schema**：`{"name","description","entries":[{"keys","content",...}]}`（手工/示例世界书）。

`uid` 缺失时按数组下标生成 `entry_uid`；重复 `uid` 保留首个并给 warning（`raw_json` 不丢）。
导入上限 256KB，JSON 解析失败/非对象 → 422；未识别包装 → 422（附检测结果）。

## 2. SillyTavern `World Info` 字段 → 本项目映射（兼容性矩阵）

图例：✅ 已实现；🟡 部分/仅解析存储；⛔ 未实现（保留 `raw_json` + 导入 warning）；🚫 不支持（设计决定）。

| ST 字段（JSON key） | ST 语义 | 本项目字段/列 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `uid` | 条目唯一 id（map key 通常同值） | `entry_uid` | ✅ | 缺失按数组下标生成；`UniqueConstraint(world_book_id,entry_uid)` |
| `key` | 主触发词数组（ST 也接受逗号分隔字符串/正则） | `keys_json` | ✅ | 逗号串→数组；**正则键 `/pattern/flags` 已支持**（Python `re.search`，仅 i/m/s，见 §5 D-5） |
| `keysecondary` | 次级词数组 | `secondary_keys_json` | ✅ | 逗号字符串→数组；正则键同样可用于次级词 |
| `selective` | 是否要求次级词参与判定 | `selective` | ✅ | 默认 True（契约）；`selective=false` 只看主词 |
| `selectiveLogic` | 0=AND_ANY / 1=NOT_ALL / 2=NOT_ANY / 3=AND_ALL | `selective_logic` | ✅ | **整数枚举与 ST 完全一致**（ST `world_info_logic` 同值） |
| `constant` | 恒定激活（无需关键词） | `constant` | ✅ | 仍受定时/分组/预算/条数上限裁剪 |
| `order`（Insertion Order） | 数值权重，ST 越大越靠后/越强调 | `order` | 🟡 语义有差异 | 预算/条数截断仍按 `(order 升序, uid 升序)`（D-1）；**包含组内选择改按 order 降序**（D-9），与 ST 方向一致 |
| `position` | 插入位置（0 before_char/1 after_char/2 AN top/3 AN bottom/4 @D/5 EM top/6 EM bottom/7 outlet） | `position` | ✅（排序，不做插槽） | 不做 prompt 分槽注入；按 `(position 升序, depth 降序, order 升序, uid 升序)` 决定 lore 块拼接顺序（D-10）。LLM prompt 消息结构不变 |
| `depth` | `position=@D` 时的插入深度 | `depth` | ✅（排序） | 同上；默认 4（ST `DEFAULT_DEPTH=4` 常量一致，实测自 ST 源码） |
| `probability` | 0–100 触发概率 | `probability` | ✅ | ST 真随机 → 本项目**确定性伪随机**：`sha256(save_seed:turn_id:uid:context_hash) % 100 < probability`，可回放 |
| `useProbability` | 是否启用概率过滤 | `use_probability` | ✅ | 契约默认 `False`；ST 新建条目默认 True → 导入归一化见 §5 差异 D-2 |
| `disable` | 禁用条目 | `enabled`（取反） | ✅ | `enabled = not disable`；禁用条目不参与激活也不进预算 |
| `caseSensitive` | 大小写敏感 | `case_sensitive` | ✅ | 缺省 false；缺省取全局（ST 全局默认 false，实测自 ST 源码） |
| `matchWholeWords` | 整词匹配 | `match_whole_words` | ✅（ASCII 词边界） | 缺省 false；中文无空格场景按子串匹配；**对正则键不生效**（正则自写 `\b`） |
| `excludeRecursion` | 本条目内容不进入递归上下文 | `exclude_recursion` | ✅ | 递归最多 2 轮 |
| `preventRecursion` | 本条目不被递归触发 | `prevent_recursion` | ✅ | 同上 |
| `delayUntilRecursion` | 仅在递归轮触发 | — | ⛔ | 本轮不实现（保留 raw；导入给 `UNSUPPORTED_FIELD` warning） |
| `scanDepth` | 条目级扫描窗口覆盖 | `scan_depth` | ✅ | 缺省取全局；**契约缺省 4**，ST 全局缺省 2 → 差异 D-3 |
| `content` | 注入正文 | `content` | ✅ | 长度计入 `char_budget`（chars/2.5 近似 token） |
| `comment`（Entry Title/Memo） | 备注，不参与触发 | —（raw 保留） | ⛔ | 不影响激活；UI 辅助字段**不产生 warning**（避免噪音） |
| `group` / `groupOverride` / `groupWeight` | Inclusion Group（同组只出一条/权重/覆盖） | `group` / `group_override` / `group_weight` | ✅ | 同组每回合最多 1 条；选择顺序 `groupOverride` 优先 → `groupWeight` 降序 → `order` 降序 → `uid` 升序（D-9） |
| `useGroupScoring` | 组内按命中数评分 | `group_scoring` | 🟡 仅解析存储 | 选择不使用命中评分；导入给 `GROUP_SCORING_UNSUPPORTED` warning |
| `vectorized` | 允许向量检索匹配 | — | 🚫 | 无 embedding/无网络依赖，明确不支持（导入给 warning） |
| `addMemo` / `displayIndex` | UI 辅助字段 | — | ⛔ | 忽略且不告警 |
| `automationId` | 联动 Quick Reply 自动化 | — | 🚫 | 安全边界：不执行任何导入内容中的脚本/命令（导入给 warning） |
| `role` | @D 插入角色（system/user/assistant） | — | ⛔ | 本轮固定为「设定事实」文本段（导入给 warning） |
| `sticky` / `cooldown` / `delay` | Timed Effects（持续/冷却/延迟激活） | `sticky` / `cooldown` / `delay` | ✅ | 0..100 回合，越界收敛；**需要逐回合历史模拟**（D-8），无历史调用等价旧行为 |
| `characterFilter` | 按角色/标签过滤 | — | ⛔ | 本项目单主角 + 场景，不做角色过滤（导入给 warning） |
| `matchPersonaDescription` / `matchCharacterDescription` / `matchCharacterPersonality` / `matchCharacterDepthPrompt` / `matchScenario` / `matchCreatorNotes` | 额外匹配来源 | — | ⛔ | 上下文仅取事件叙事/输入/房间/可见实体（契约 §4；导入给 warning） |
| `triggers` | 生成类型过滤（normal/continue/…） | — | ⛔ | 单机回合制无生成类型（导入给 warning） |
| `outlet`（`position=7` + `outletName`） | 宏插入点 | — | 🚫 | 无宏系统，明确不支持（导入给 warning） |
| `extensions` | 任意扩展字段 | `raw_json` | ✅（保留） | 未知字段一律保留，向前兼容；导入 `validate` 给 warning 列表 |
| （局部）全局激活设置 | `scanDepth`/`budget`/`recursive`/`minActivations`/`maxRecursionSteps`/`includeNames` | 契约固定参数 | 🟡 | 本项目固定：递归 ≤2 轮、最多 8 条、char_budget 800；不支持全局覆盖（示例世界书不得依赖；导入给 `BOOK_GLOBAL_SETTINGS_IGNORED` warning） |

> 字段持久化说明：`world_book_entries` 只落 H4 契约固定列；`probability`/`useProbability`/
> `group`/`scanDepth` 与 P1 新增的 `sticky`/`cooldown`/`delay`/`groupWeight`/`groupOverride`/
> `useGroupScoring` 保留在 `raw_json`，服务层读回时由 `domain.world_book.parse_entry_extras`
> 还原（**无需迁移**；旧数据 raw 里已有这些字段即自动生效）。

## 3. Character Card V2 `data.character_book` 字段映射

CCv2 的 `CharacterBook`（`spec_v2.md`，独立实现，不复制规范文本）：

| CCv2 字段 | 类型 | 本项目映射 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `character_book.name` | string? | `world_books.name` | ✅ | 缺省取角色卡 `data.name` 或文件名 |
| `character_book.description` | string? | `world_books.description` | ✅ | 缺省空串 |
| `character_book.scan_depth` | number? | 全局 scanDepth 提示 | 🟡 | 契约参数固定，仅作为导入信息/raw；导入给 `BOOK_GLOBAL_SETTINGS_IGNORED` warning |
| `character_book.token_budget` | number? | — | 🟡 | 本项目预算为 **chars/2.5 近似**（契约），不做 token 精确映射，raw 保留（同上 warning） |
| `character_book.recursive_scanning` | boolean? | — | 🟡 | 本项目固定递归 ≤2 轮；raw 保留（同上 warning） |
| `character_book.extensions` | object | `raw_json` | ✅ | 不销毁未知键 |
| `entries[].keys` | string[] | `keys_json` | ✅ | 必填；空数组 → warning（条目永不触发，除 constant）；支持 `/pattern/flags` 正则键 |
| `entries[].content` | string | `content` | ✅ | 必填 |
| `entries[].enabled` | boolean | `enabled` | ✅ | 缺省 true |
| `entries[].insertion_order` | number | `order` | ✅（映射） | 与 ST `order` 同义；缺省 100 |
| `entries[].case_sensitive` | boolean? | `case_sensitive` | ✅ | 缺省 false |
| `entries[].selective` | boolean? | `selective` | ✅ | 缺省 false（CCv2 语义：仅显式 true 才要求次级词） |
| `entries[].secondary_keys` | string[]? | `secondary_keys_json` | ✅ | 仅 `selective=true` 时参与 |
| `entries[].constant` | boolean? | `constant` | ✅ | 缺省 false |
| `entries[].position` | `"before_char"`｜`"after_char"` | `position` 0/1 | ✅（排序） | 枚举 → int 映射；参与 lore 块排序（D-10），不做 prompt 插槽 |
| `entries[].id` | number? | `entry_uid` | ✅ | 缺省数组下标 |
| `entries[].name` / `comment` | string? | —（raw） | ⛔ | 展示用；本轮详情页可显示 content/keys，其余忽略（不告警） |
| `entries[].priority` | number? | —（raw） | ⛔ | token 预算优先级，本轮不实现（导入给 `UNSUPPORTED_FIELD` warning） |
| `entries[].extensions` | object | `raw_json` | ✅ | 不销毁 |

**CCv2 特有差异**：无 `keysecondary`/`selectiveLogic`/`probability`/`order position` 等 ST 字段名；
`secondary_keys` ↔ `keysecondary`、`insertion_order` ↔ `order`；无 `selectiveLogic` 时按 `0=AND_ANY`；
`position` 为字符串枚举（→ 0/1）。CCv2 无 timed effects / inclusion group 字段，本项目的同名扩展字段
（`sticky`/`cooldown`/`delay`/`group`…）在简化 schema 与 ST 字段名下均可解析。

## 4. 字段默认值对照（导入器归一化用）

| 字段 | ST 典型默认 | CCv2 默认 | 本项目契约默认 | 导入策略 |
| --- | --- | --- | --- | --- |
| enabled/disable | `disable=false`→启用 | `enabled=true` | `enabled=True` | 缺省启用 |
| constant | false | false | `False` | 缺省 false |
| selective | true | false | `True`（契约 dataclass） | ST/简化按 true；CCv2 按 `selective ?? false` |
| selectiveLogic | 0 (AND_ANY) | 无此字段 | `0` | 缺省 0 |
| order | 100 | `insertion_order` 必填 | `100` | 缺省 100 |
| position | 0 (before_char) | `before_char` | `0` | 映射枚举 |
| depth | 4（ST `DEFAULT_DEPTH`） | — | `4` | 缺省 4 |
| probability / useProbability | 100 / true | — | `100` / `False` | 若 `probability<100` 而 `useProbability` 缺省，按 **true** 归一化（见 §5 D-2） |
| scanDepth（条目） | null → 全局 2 | — | null → 全局 4 | 见 §5 D-3 |
| caseSensitive | null → 全局 false | false | `False` | 缺省 false |
| matchWholeWords | null → 全局 false（ST 文档与源码口径有出入，见 §5 D-4） | — | `False` | 缺省 false；中文场景保持子串匹配 |
| exclude/preventRecursion | false | — | `False` | 缺省 false |
| sticky / cooldown / delay | 0 | — | `0` | 缺省 0（关闭）；仅接受 0..100，越界/非法收敛并给 warning |
| group | `""` | — | `""` | 空串 = 不参与组选择（旧行为不变） |
| groupWeight | 100（ST 新建条目） | — | `1.0`（仅缺省时） | 显式值原样保留（`100` 即 `100.0`）；组内只比较相对大小，缺省口径差异不影响同书一致选择 |
| groupOverride / useGroupScoring | false / null | — | `False` / `False` | 缺省 false；`useGroupScoring=true` 仅告警不参与选择 |
| 递归/预算/条数 | 全局可配（默认递归关、budget=25%） | `recursive_scanning` | 递归开（≤2 轮）/budget 800 chars/≤8 条 | 契约固定；导入世界书内的全局设置仅 raw 保留 + warning |

> 默认值说明：带 `ST 源码常量` 标注的来自实测 SillyTavern `world-info.js`
> （`DEFAULT_DEPTH=4`、`DEFAULT_WEIGHT=100`、`world_info_depth=2`、`world_info_budget=25`、
> `world_info_case_sensitive=false`、`world_info_match_whole_words=false`、`world_info_recursive=false`、
> `world_info_logic={AND_ANY:0,NOT_ALL:1,NOT_ANY:2,AND_ALL:3}`）。其余条目默认值为 ST 新建条目
> 常见值，**导入器不依赖 ST 默认**：缺省一律按本项目契约默认归一化。

## 5. 语义差异清单（需 Lead/algorithm 知悉）

- **D-1 `order` 排序方向（预算/条数截断）**：ST 中较大 order 更靠后插入（更强影响）；本项目
  预算与 `max_entries` 截断仍按 `(order 升序, uid 升序)` 排序取前 N 条。**导入不做方向反转**——
  示例世界书按本项目语义编写；若玩家导入 ST 世界书，激活集合可能不同（不会报错）。后续若要严格
  对齐 ST，可在导入层做 `order' = -order` 归一化（需 ADR）。注意 P1 包含组内选择按 ST 方向
  （`order` 降序，见 D-9），与预算口径不同。
- **D-2 概率**：ST 真随机；本项目确定性伪随机（可回放）。`useProbability` 缺省在 ST 为 true，
  契约默认 false；导入层建议：若 payload 显式给 `probability < 100` 且未给 `useProbability`，
  按 true 归一化，避免「概率字段被静默忽略」。
- **D-3 scanDepth 缺省**：ST 全局默认 2，本项目契约默认 4；导入时若条目未给，用本项目全局参数。
- **D-4 matchWholeWords 缺省**：ST 文档写「默认开启整词匹配」，但其源码全局常量默认为 false；
  本项目按 false + 仅 ASCII 词边界处理，中文条目不受影响。
- **D-5 正则键（P1 已支持，与 JS 正则的差异）**：ST 的 `/pattern/flags` 键在**本项目用 Python
  `re`** 求值，不是 JS `RegExp`：
  - 支持的 flags：仅 `i`（忽略大小写）/ `m`（多行）/ `s`（`.` 匹配换行）；`g`/`u`/`y` 等
    **不支持** → 导入给 `REGEX_KEY_INVALID` warning，该键按**字面量**匹配（不静默丢弃）；
  - 条目 `caseSensitive=false`（默认）时正则自动加 `IGNORECASE`（与普通关键词缺省口径一致），
    可用 `(?-i:...)` 或在条目上 `caseSensitive=true` 覆盖；`matchWholeWords` 对正则键不生效；
  - Python/JS 语法差异需注意：命名组语法 `(?P<name>…)`（JS 为 `(?<name>…)`）、
    `\p{...}` Unicode 属性转义（Python `re` 不支持，会编译失败→字面量）、JS `u` 标志语义等；
    **非法 pattern / pattern > 200 字符 → 导入期编译校验失败 → warning + 字面量匹配**；
  - 正则匹配输入文本上限 **2000 字符**（`text[:2000]`，防灾难性回溯；普通关键词不受影响）；
  - `key`/`keysecondary` 的逗号串解析不会切断正则：`["/a,b/i"]`、`"/x/i, /y/m"` 都按正则键
    整体保留；含逗号的纯文本逗号串仍按旧口径拆成多个关键词；
  - 编译函数带 LRU 缓存（纯函数、可回放），正则键数量不影响确定性。
- **D-6 预算口径**：本项目 `chars/2.5` 近似 token，与 ST 精确分词不同 → 极端长条目截断位置可能不同。
- **D-7 全局设置**：ST 世界书文件的全局激活设置（budget/递归/包含名字等）不参与本项目运行；
  仅 `raw_json` 保留，导入给 `BOOK_GLOBAL_SETTINGS_IGNORED` warning（CCv2 的 `scan_depth`/
  `token_budget`/`recursive_scanning` 同样处理）。
- **D-8 定时效果（sticky / cooldown / delay）口径**：纯函数、确定性、可回放；**不写 GameState、
  不新增事件、不做迁移**。
  - 语义：`sticky=N` → 触发回合起其后 N 回合内即使关键词不再命中仍持续激活（第 N+1 回合消失）；
    `cooldown=N` → 触发后 N 回合内禁止再次触发（第 N+1 回合起可再触发）；
    `delay=N` → 首次命中后延迟 N 回合才可生效（延迟窗内命中只计时不激活）。三者可组合，
    值域 0..100（越界/非法收敛并给 `TIMED_EFFECT_CLAMPED` warning）。
  - **历史重建口径**：`activate_entries(..., history=Sequence[ActivationTurn])`；
    `history=None`（不传）时**逐字等价旧实现**（定时字段被忽略，旧测试零改动）。
    历史由纯函数 `domain.world_book.history_from_events(events)` 从**事件日志**重建：按 `turn_id`
    升序，每回合取该回合已提交事件的 `result["narrative"]`（去空去重）作为该回合关键词匹配上下文。
  - **玩家输入无落库**：事件只存 `narrative`，因此历史回合的匹配上下文以该回合叙事为准；
    零事件只读回合（如「环顾四周」）没有事件/叙事落库，**不进入历史**。
  - 时间窗按**已提交回合的 `turn_id`** 推进：零事件只读回合不写事件、`state.turn` 也不推进，
    因此不会推进 sticky/cooldown/delay 窗口（与既有「只读回合不推进版本」口径一致）。
  - 历史最多回放最近 **100 个有叙事的回合**（`_MAX_HISTORY_TURNS`）；更早的回合被丢弃
    （timed 窗口 ≤100 回合，窗口外状态视为过期；delay 的首次命中若早于窗口会在窗口内重新计时）。
  - 定时效果只作用于**首轮关键词命中**；递归轮（由已激活 content 触发）不受 timed 约束。
  - 回合注入（`TurnOrchestrator._lore_entries_for`）与只读视图（`QueryService` 的
    `/view.lore`）使用同一 `history_from_events` 重建历史 → 两侧口径一致。
- **D-9 包含组（inclusion group）**：同 `group` 的命中条目每回合最多激活 1 条；选择顺序
  `groupOverride=true` 优先 → `groupWeight` 降序 → `order` 降序 → `uid` 升序（组内 order 方向与
  ST 一致，与 D-1 预算口径不同）。跨递归轮同样去重：组已在更早轮激活时，后续轮命中的
  `groupOverride=true` 条目**替换**组内当前条目（被替换者从结果移除），普通条目丢弃。
  无 `group`（空串）的条目行为与旧版逐字一致。`useGroupScoring` 仅解析/保留，不参与选择。
- **D-10 position/depth 排序**：激活结果先按 `(order, uid)` 做预算/条数截断，再按
  `(position 升序, depth 降序, order 升序, uid 升序)` 排序后返回，用于 lore 块拼接顺序；
  **不改变 LLM prompt 的消息结构**（仍是 system + user 两条；世界设定事实段以条目顺序追加），
  因此 `narrate@v2` cassette 不破。缺省 position=0/depth=4 时等价旧 `(order, uid)` 排序。
- **D-11 详情 API 可见性（P1 后补）**：`GET /api/v1/world-books/{world_book_id}` 的
  `entries[]` 在 H4 固定字段之外，再返回 14 个触发语义字段：`probability`/`use_probability`/
  `group`/`group_weight`/`group_override`/`group_scoring`/`scan_depth`/`sticky`/`cooldown`/
  `delay`/`case_sensitive`/`match_whole_words`/`exclude_recursion`/`prevent_recursion`。
  - 前 10 个「固定列之外」的字段由 `domain.world_book.parse_entry_extras` 从 `raw_json`
    还原——**与激活决策同一解析函数**，保证「详情里看到的」=「运行时会用的」；无新表/迁移。
  - 缺字段的旧数据按解析默认值返回（`100`/`False`/`""`/`1.0`/`None`/`0`），前端无需判空。
  - 只增不改：H4 既有响应字段逐字保留（`extra="forbid"` 契约同步更新
    `api/schemas.py` + `web/src/api/types.ts` + `web/src/api/client.ts` 运行时校验 +
    `tests/integration/test_frontend_contract.py`）。
  - 详情页 `WorldBookEntryTable.vue` 展示：概率/包含组/定时效果/position-depth/扫描深度/
    递归与大小写开关；展示层零业务判断，激活行为仍由 `domain/world_book.py` 决定。

## 6. 示例 payload（最小可导入）

### 6.1 SillyTavern World Info（map 包装）

```json
{
  "entries": {
    "0": {
      "uid": 0,
      "key": ["哨站", "灯"],
      "keysecondary": [],
      "comment": "信号灯机制",
      "content": "哨站的信号灯靠一支信号火把与火石点亮，灯亮处即有人看见这条路。",
      "constant": false,
      "selective": true,
      "selectiveLogic": 0,
      "order": 100,
      "position": 0,
      "depth": 4,
      "probability": 100,
      "useProbability": true,
      "disable": false,
      "caseSensitive": false,
      "matchWholeWords": false,
      "excludeRecursion": false,
      "preventRecursion": false,
      "extensions": {}
    },
    "1": {
      "uid": 1,
      "key": ["老兵", "守夜人"],
      "content": "守夜人老兵在哨站入口的火盆旁守着，他知道信号灯为什么灭。",
      "constant": false,
      "order": 120,
      "disable": false
    }
  }
}
```

### 6.2 本项目简化 schema

```json
{
  "name": "余烬哨站·设定集",
  "description": "示例世界书（MVP）",
  "entries": [
    {"keys": ["枯井", "寒气"], "content": "枯井里常年积着寒气，多待一刻就多一分失温的危险。", "order": 90},
    {"keys": ["河谷", "封冻"], "content": "河谷入夜便封冻，桥板底下是空的，别往河边去。", "order": 110}
  ]
}
```

### 6.3 Character Card V2 内嵌 `character_book`

```json
{
  "spec": "chara_card_v2",
  "spec_version": "2.0",
  "data": {
    "name": "守夜人老兵",
    "description": "在余烬哨站守了半辈子的人。",
    "personality": "寡言、谨慎",
    "scenario": "风雪封路前的最后一夜",
    "first_mes": "……又是来问灯的事？",
    "mes_example": "",
    "creator_notes": "",
    "system_prompt": "",
    "post_history_instructions": "",
    "alternate_greetings": [],
    "character_book": {
      "name": "老兵的手札",
      "description": "与哨站历史相关的设定",
      "scan_depth": 4,
      "token_budget": 500,
      "recursive_scanning": false,
      "extensions": {},
      "entries": [
        {
          "keys": ["信号灯", "灯座"],
          "content": "灯座空着，得有一支信号火把；火把在门口，铜线在铁匠铺。",
          "enabled": true,
          "insertion_order": 100,
          "case_sensitive": false,
          "selective": false,
          "constant": false,
          "position": "after_char",
          "id": 0,
          "comment": "点亮条件",
          "extensions": {}
        }
      ]
    },
    "tags": [],
    "creator": "textgame",
    "character_version": "0.1.0",
    "extensions": {}
  }
}
```

### 6.4 酒馆级 P1 示例（正则键 / 包含组 / 定时效果）

```json
{
  "name": "余烬哨站·酒馆级设定",
  "entries": [
    {
      "uid": 0,
      "key": ["/枯井|井底/i"],
      "content": "枯井里积着化不开的寒气，井底比井口还要暖得反常。",
      "order": 10
    },
    {
      "uid": 1,
      "key": ["信号灯", "灯芯"],
      "content": "信号灯得用浸过松脂的火把才点得着。",
      "order": 20,
      "group": "signal_light",
      "groupWeight": 100,
      "groupOverride": false,
      "sticky": 2,
      "cooldown": 3
    },
    {
      "uid": 2,
      "key": ["信号灯", "火石"],
      "content": "火石在哨站地窖的墙角，敲两下就能迸出火星。",
      "order": 30,
      "group": "signal_light",
      "groupWeight": 80
    },
    {
      "uid": 3,
      "key": ["老兵", "守夜人"],
      "content": "老兵知道信号灯为什么灭，但他不轻易开口。",
      "order": 40,
      "delay": 2
    }
  ]
}
```

> 该示例导入后：`/枯井|井底/i` 正则命中含「枯井」或「井底」的上下文；两条 `signal_light`
> 组条目同时命中时按 `groupWeight` 取 uid=1 一条（uid=1 触发后按 `sticky=2` 续期 2 回合，
> `cooldown=3` 内不重复触发）；uid=3 首次命中后延迟 2 回合才激活。导入/校验响应会附带
> `REGEX_KEY_INVALID`（无）、`TIMED_EFFECTS_HISTORY_ONLY` 等说明性 warning。

## 7. 安全与校验（导入侧必须）

- 仅接受 JSON（≤256KB）；不接受 HTML/markdown→HTML 渲染；`content` 前端一律**文本插值**（禁 `v-html`），
  由 `security-scan` 守护；后端不做任何 `eval`/命令执行；`automationId`/`outlet` 等可执行语义一律忽略。
- 正则键在**导入期编译校验**（`re.compile`）：非法 pattern / `pattern` 长度 > 200 / flags 不在
  `i,m,s` → 每条给 `REGEX_KEY_INVALID` warning 且按字面量匹配；正则匹配输入上限 2000 字符，
  编译结果 LRU 缓存（纯函数），不引入 `eval`/JS 引擎/网络。
- 定时效果历史由**只读事件叙事**重建：`history_from_events` 纯函数，最多回放 100 回合；
  不写事件/快照/幂等表，不联网，不随机（概率仍用 sha256 确定性派生）。
- `validate` 返回 `{valid, errors[], warnings[], preview{name, entry_count, detected_format}}`；
  warnings 形状保持既有 `[{code, message}]`（只增条目，不改形状）。当前告警码：
  `REGEX_KEY_INVALID`、`TIMED_EFFECT_CLAMPED`、`TIMED_EFFECTS_HISTORY_ONLY`、
  `GROUP_WEIGHT_INVALID`、`GROUP_SCORING_UNSUPPORTED`、`UNSUPPORTED_FIELD`、
  `BOOK_GLOBAL_SETTINGS_IGNORED`，以及既有的 `CHARACTER_BOOK_EXTRACTED`/`EMPTY_WORLD_BOOK`；
  未知字段 → warning（保留 raw）；非法类型 → error（422）。
- 幂等与软删见 H4-CONTRACT §2；导入/删除均不写入事件流，直到 `POST /games {world_book_id}` 才在
  `game_created.params` 记录（旧请求 params 逐字不变）。

## 8. 验收/测试建议（与 QA/algorithm 对齐）

- 解析：3 种包装 ×（map/array/裸 map）× 缺失字段/类型错误/重复 uid；CCv2 `character_book` 路径；
- 触发矩阵：keys 大小写/整词/keysecondary × 4 种 selectiveLogic/constant/order/scanDepth/
  递归 exclude-prevent/预算截断/最多 8 条/概率确定性（同 seed 同 turn 同结果）；
- P1 酒馆级（`tests/unit/test_world_book_tavern.py` + `tests/integration/test_world_book_tavern_flow.py`）：
  - 定时效果：sticky/cooldown/delay 边界与组合、delay 首次命中、sticky 免关键词续期、
    100 回合历史上限、`history=None` 与旧实现逐字等价（硬门禁）；
  - 正则键：i/m/s flags、分支匹配、非法/超长/非法 flags → 字面量回退、2000 字符输入上限、
    逗号串不被切断、存储往返（DB→provider）后仍生效；
  - 包含组：groupWeight 降序 / groupOverride 优先 / order 降序后 uid 升序 / 跨递归轮去重与
    override 替换 / 无 group 行为不变；
  - 排序：position 升序 → depth 降序 → order 升序 → uid 升序；预算截断仍按 (order, uid)；
  - 导入体验：告警形状 `[{code, message}]`、逐条正则/定时/未支持字段说明、书级全局设置说明；
- 兼容缺口：group/vectorized/timed effects 字段存在时→warning 且不影响其它条目激活；
- 安全：256KB 边界、非法 JSON、HTML 内容原样保留不渲染、`../../` 路径无关（内容非文件路径）、
  灾难性回溯输入（>2000 字符）不阻塞。
