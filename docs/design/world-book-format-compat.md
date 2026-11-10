# 世界书格式兼容文档（SillyTavern World Info / Character Card V2 `character_book` → 本项目）

> 范围：**只研究数据格式与触发语义**，不参考 SillyTavern UI（用户明确否决其界面）。
> 依据：`H4-BRIEF.md` §2B、`H4-CONTRACT.md` §3（冻结契约）、SillyTavern 官方文档
> <https://docs.sillytavern.app/usage/core-concepts/worldinfo/>、Character Card V2 规范
> <https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md>。
> 结论日期 2026-11-11。**状态列以 H4-CONTRACT §3 冻结范围为准**；实现状态由 backend/algorithm 最终确认。

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

图例：✅ 已实现（契约 §3）；🟡 部分/仅存储；⛔ 未实现（本轮不做，`raw_json` 保留）；🚫 不支持（设计决定）。

| ST 字段（JSON key） | ST 语义 | 本项目字段/列 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `uid` | 条目唯一 id（map key 通常同值） | `entry_uid` | ✅ | 缺失按数组下标生成；`UniqueConstraint(world_book_id,entry_uid)` |
| `key` | 主触发词数组（ST 也接受逗号分隔字符串/正则） | `keys_json` | ✅（纯文本子集） | 逗号字符串→数组；**正则 key 🚫**（确定性/安全：不做 JS 正则求值） |
| `keysecondary` | 次级词数组 | `secondary_keys_json` | ✅ | 逗号字符串→数组 |
| `selective` | 是否要求次级词参与判定 | `selective` | ✅ | 默认 True（契约）；`selective=false` 只看主词 |
| `selectiveLogic` | 0=AND_ANY / 1=NOT_ALL / 2=NOT_ANY / 3=AND_ALL | `selective_logic` | ✅ | **整数枚举与 ST 完全一致**（ST `world_info_logic` 同值） |
| `constant` | 恒定激活（无需关键词） | `constant` | ✅ | 仍受预算/条数上限裁剪 |
| `order`（Insertion Order） | 数值权重，ST 越大越靠后/越强调 | `order` | 🟡 语义有差异 | 本项目按 `(order 升序, uid 升序)` 排序后取条；**导入不转换方向**，见 §5 差异 D-1 |
| `position` | 插入位置（0 before_char/1 after_char/2 AN top/3 AN bottom/4 @D/5 EM top/6 EM bottom/7 outlet） | `position` | 🟡 仅存储 | 模板叙事统一「追加一句」、LLM 统一「世界设定事实段」，不按位置分槽；UI 展示但本轮不影响注入 |
| `depth` | `position=@D` 时的插入深度 | `depth` | 🟡 仅存储 | 默认 4（ST `DEFAULT_DEPTH=4` 常量一致，实测自 ST 源码） |
| `probability` | 0–100 触发概率 | `probability` | ✅ | ST 真随机 → 本项目**确定性伪随机**：`sha256(save_seed:turn_id:uid:context_hash) % 100 < probability`，可回放 |
| `useProbability` | 是否启用概率过滤 | `use_probability` | ✅ | 契约默认 `False`；ST 新建条目默认 True → 导入归一化见 §5 差异 D-2 |
| `disable` | 禁用条目 | `enabled`（取反） | ✅ | `enabled = not disable`；禁用条目不参与激活也不进预算 |
| `caseSensitive` | 大小写敏感 | `case_sensitive` | ✅ | 缺省 false；缺省取全局（ST 全局默认 false，实测自 ST 源码） |
| `matchWholeWords` | 整词匹配 | `match_whole_words` | ✅（ASCII 词边界） | 缺省 false；中文无空格场景按子串匹配（ST 文档亦建议中文关闭整词） |
| `excludeRecursion` | 本条目内容不进入递归上下文 | `exclude_recursion` | ✅ | 递归最多 2 轮 |
| `preventRecursion` | 本条目不被递归触发 | `prevent_recursion` | ✅ | 同上 |
| `delayUntilRecursion` | 仅在递归轮触发 | — | ⛔ | 本轮不实现（保留 raw） |
| `scanDepth` | 条目级扫描窗口覆盖 | `scan_depth` | ✅（覆盖项） | 缺省取全局；**契约缺省 4**，ST 全局缺省 2 → 差异 D-3 |
| `content` | 注入正文 | `content` | ✅ | 长度计入 `char_budget`（chars/2.5 近似 token） |
| `comment`（Entry Title/Memo） | 备注，不参与触发 | —（raw 保留） | ⛔ | 不影响激活；如需要可在 UI 显示，本轮不做 |
| `group` / `groupOverride` / `groupWeight` | Inclusion Group（同组只出一条/权重/覆盖） | — | ⛔ | 本轮不实现；`raw_json` 保留。对示例世界书避免使用 |
| `useGroupScoring` | 组内按命中数评分 | — | ⛔ | 同上 |
| `vectorized` | 允许向量检索匹配 | — | 🚫 | 无 embedding/无网络依赖，明确不支持 |
| `addMemo` / `displayIndex` | UI 辅助字段 | — | ⛔ | 忽略 |
| `automationId` | 联动 Quick Reply 自动化 | — | 🚫 | 安全边界：不执行任何导入内容中的脚本/命令 |
| `role` | @D 插入角色（system/user/assistant） | — | ⛔ | 本轮固定为「设定事实」文本段 |
| `sticky` / `cooldown` / `delay` | Timed Effects（持续/冷却/延迟激活） | — | ⛔ | 本轮不实现（无状态化激活，确定性优先） |
| `characterFilter` | 按角色/标签过滤 | — | ⛔ | 本项目单主角 + 场景，不做角色过滤 |
| `matchPersonaDescription` / `matchCharacterDescription` / `matchCharacterPersonality` / `matchCharacterDepthPrompt` / `matchScenario` / `matchCreatorNotes` | 额外匹配来源 | — | ⛔ | 上下文仅取事件叙事/输入/房间/可见实体（契约 §4） |
| `triggers` | 生成类型过滤（normal/continue/…） | — | ⛔ | 单机回合制无生成类型 |
| `outlet`（`position=7` + `outletName`） | 宏插入点 | — | 🚫 | 无宏系统，明确不支持 |
| `extensions` | 任意扩展字段 | `raw_json` | ✅（保留） | 未知字段一律保留，向前兼容；导入 `validate` 给 warning 列表 |
| （局部）全局激活设置 | `scanDepth`/`budget`/`recursive`/`minActivations`/`maxRecursionSteps`/`includeNames` | 契约固定参数 | 🟡 | 本项目固定：递归 ≤2 轮、最多 8 条、char_budget 800；不支持全局覆盖（示例世界书不得依赖） |

## 3. Character Card V2 `data.character_book` 字段映射

CCv2 的 `CharacterBook`（`spec_v2.md`，独立实现，不复制规范文本）：

| CCv2 字段 | 类型 | 本项目映射 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| `character_book.name` | string? | `world_books.name` | ✅ | 缺省取角色卡 `data.name` 或文件名 |
| `character_book.description` | string? | `world_books.description` | ✅ | 缺省空串 |
| `character_book.scan_depth` | number? | 全局 scanDepth 提示 | 🟡 | 契约参数固定，仅作为导入信息/raw |
| `character_book.token_budget` | number? | — | 🟡 | 本项目预算为 **chars/2.5 近似**（契约），不做 token 精确映射，raw 保留 |
| `character_book.recursive_scanning` | boolean? | — | 🟡 | 本项目固定递归 ≤2 轮；raw 保留 |
| `character_book.extensions` | object | `raw_json` | ✅ | 不销毁未知键 |
| `entries[].keys` | string[] | `keys_json` | ✅ | 必填；空数组 → warning（条目永不触发，除 constant） |
| `entries[].content` | string | `content` | ✅ | 必填 |
| `entries[].enabled` | boolean | `enabled` | ✅ | 缺省 true |
| `entries[].insertion_order` | number | `order` | ✅（映射） | 与 ST `order` 同义；缺省 100 |
| `entries[].case_sensitive` | boolean? | `case_sensitive` | ✅ | 缺省 false |
| `entries[].selective` | boolean? | `selective` | ✅ | 缺省 false（CCv2 语义：仅显式 true 才要求次级词） |
| `entries[].secondary_keys` | string[]? | `secondary_keys_json` | ✅ | 仅 `selective=true` 时参与 |
| `entries[].constant` | boolean? | `constant` | ✅ | 缺省 false |
| `entries[].position` | `"before_char"`｜`"after_char"` | `position` 0/1 | 🟡 仅存储 | 枚举 → int 映射；不影响本轮注入位置 |
| `entries[].id` | number? | `entry_uid` | ✅ | 缺省数组下标 |
| `entries[].name` / `comment` | string? | —（raw） | ⛔ | 展示用；本轮详情页可显示 content/keys，其余忽略 |
| `entries[].priority` | number? | —（raw） | ⛔ | token 预算优先级，本轮不实现 |
| `entries[].extensions` | object | `raw_json` | ✅ | 不销毁 |
| `data.*`（角色本身 9 字段） | — | 现有 `character_cards` | 现有能力 | 本任务只做 `character_book` 内嵌 lorebook；角色卡导入沿用 M6 卡库 |

**CCv2 特有差异**：无 `keysecondary`/`selectiveLogic`/`probability`/`order position` 等 ST 字段名；
`secondary_keys` ↔ `keysecondary`、`insertion_order` ↔ `order`；无 `selectiveLogic` 时按 `0=AND_ANY`；
`position` 为字符串枚举（→ 0/1）。

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
| 递归/预算/条数 | 全局可配（默认递归关、budget=25%） | `recursive_scanning` | 递归开（≤2 轮）/budget 800 chars/≤8 条 | 契约固定；导入世界书内的全局设置仅 raw 保留 |

> 默认值说明：带 `ST 源码常量` 标注的来自实测 SillyTavern `world-info.js`
> （`DEFAULT_DEPTH=4`、`DEFAULT_WEIGHT=100`、`world_info_depth=2`、`world_info_budget=25`、
> `world_info_case_sensitive=false`、`world_info_match_whole_words=false`、`world_info_recursive=false`、
> `world_info_logic={AND_ANY:0,NOT_ALL:1,NOT_ANY:2,AND_ALL:3}`）。其余条目默认值为 ST 新建条目
> 常见值，**导入器不依赖 ST 默认**：缺省一律按本项目契约默认归一化。

## 5. 语义差异清单（需 Lead/algorithm 知悉，均不阻塞 MVP）

- **D-1 `order` 排序方向**：ST 中较大 order 更靠后插入（更强影响）；契约按 `(order 升序, uid 升序)`
  排序取前 N 条。**导入不做方向反转**——示例世界书按本项目语义编写；若玩家导入 ST 世界书，
  激活集合可能不同（不会报错）。后续若要严格对齐 ST，可在导入层做 `order' = -order` 归一化（需 ADR）。
- **D-2 概率**：ST 真随机；本项目确定性伪随机（可回放）。`useProbability` 缺省在 ST 为 true，
  契约默认 false；导入层建议：若 payload 显式给 `probability < 100` 且未给 `useProbability`，
  按 true 归一化，避免「概率字段被静默忽略」。
- **D-3 scanDepth 缺省**：ST 全局默认 2，本项目契约默认 4；导入时若条目未给，用本项目全局参数。
- **D-4 matchWholeWords 缺省**：ST 文档写「默认开启整词匹配」，但其源码全局常量默认为 false；
  本项目按 false + 仅 ASCII 词边界处理，中文条目不受影响。
- **D-5 regex keys**：ST 支持 JS 正则 key；本项目**不支持**（确定性、安全、可回放优先），
  导入时检测到 `/.../flags` 形式 key 会给 warning 并按**纯文本**处理（或跳过该 key——以 algorithm 实现为准）。
- **D-6 预算口径**：本项目 `chars/2.5` 近似 token，与 ST 精确分词不同 → 极端长条目截断位置可能不同。
- **D-7 全局设置**：ST 世界书文件的全局激活设置（budget/递归/包含名字等）不参与本项目运行；
  仅 `raw_json` 保留，`validate` 给 warning。

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

## 7. 安全与校验（导入侧必须）

- 仅接受 JSON（≤256KB）；不接受 HTML/markdown→HTML 渲染；`content` 前端一律**文本插值**（禁 `v-html`），
  由 `security-scan` 守护；后端不做任何 `eval`/命令执行；`automationId`/`outlet` 等可执行语义一律忽略。
- `validate` 返回 `{valid, errors[], warnings[], preview{name, entry_count, detected_format}}`；
  未知字段 → warning（保留 raw）；非法类型 → error（422）。
- 幂等与软删见 H4-CONTRACT §2；导入/删除均不写入事件流，直到 `POST /games {world_book_id}` 才在
  `game_created.params` 记录（旧请求 params 逐字不变）。

## 8. 验收/测试建议（与 QA/algorithm 对齐）

- 解析：3 种包装 ×（map/array/裸 map）× 缺失字段/类型错误/重复 uid；CCv2 `character_book` 路径；
- 触发矩阵：keys 大小写/整词/keysecondary × 4 种 selectiveLogic/constant/order/scanDepth/
  递归 exclude-prevent/预算截断/最多 8 条/概率确定性（同 seed 同 turn 同结果）；
- 兼容缺口：group/vectorized/timed effects 字段存在时→warning 且不影响其它条目激活；
- 安全：256KB 边界、非法 JSON、HTML 内容原样保留不渲染、`../../` 路径无关（内容非文件路径）。
