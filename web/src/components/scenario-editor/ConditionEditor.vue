<script setup lang="ts">
/**
 * 条件编辑器（N1-031 场景编辑器）：一个「恰好一个键」的条件节点的表单编辑。
 *
 * - 原子谓词用类型下拉 + 参数输入；`all`/`any`/`not` 递归渲染子条件（组合子最多展示 3 层，
 *   更深的嵌套提示改用「JSON 原文」面板编辑——服务端仍会完整校验）；
 * - 只产出 JSON 条件对象（`update:modelValue`），不执行任何用户内容、不拼 HTML；
 * - 空条件 = `null`（`when` 可省略；`flags[].when` 必填由编辑器上层校验）；
 * - a11y：原生 `<select>`/`<input>` + `<label for>`，键盘可完整操作，条件 JSON 实时可见便于核对。
 */
import { computed, useId } from 'vue'

import {
  SCENARIO_CONDITION_KINDS,
  createCondition,
  type ScenarioCondition,
  type ScenarioConditionKind,
} from '@/utils/scenarioEditor'

const props = withDefaults(
  defineProps<{
    /** 当前条件（null = 未设置）。 */
    modelValue: ScenarioCondition | null
    /** 展示名称（如「结局条件」），用于 label / aria-label。 */
    label?: string
    /** 递归深度（内部使用；超过 2 层只读提示改用 JSON 原文编辑）。 */
    depth?: number
  }>(),
  { label: '条件', depth: 0 },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: ScenarioCondition | null): void
}>()

const uid = useId()
const MAX_VISIBLE_DEPTH = 2
const MAX_COMBO_CHILDREN = 16

const kinds = SCENARIO_CONDITION_KINDS
const kind = computed<string>(() => {
  const condition = props.modelValue
  if (condition === null) {
    return ''
  }
  const keys = Object.keys(condition)
  return keys.length === 1 && (kinds.some((option) => option.kind === keys[0]) ? true : false)
    ? String(keys[0])
    : ''
})
const kindOption = computed(() =>
  kinds.find((option) => option.kind === kind.value) ?? null,
)
const canNest = computed<boolean>(() => props.depth < MAX_VISIBLE_DEPTH)
const conditionJson = computed<string>(() =>
  props.modelValue === null ? '（未设置）' : JSON.stringify(props.modelValue),
)

function recordValue(key: string): Record<string, unknown> {
  const condition = props.modelValue
  const value = condition?.[key]
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function scalar(key: string, fallback = ''): string {
  const value = props.modelValue?.[key]
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number') {
    return String(value)
  }
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false'
  }
  return fallback
}

function payloadText(key: string, payloadKey: string): string {
  const payload = recordValue(key)
  const value = payload[payloadKey]
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number') {
    return String(value)
  }
  return ''
}

function updateNode(next: ScenarioCondition | null): void {
  emit('update:modelValue', next)
}

/** 切换谓词：换成骨架节点（作者再改参数）；选「无条件」→ null。 */
function onKindChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  if (value === '') {
    updateNode(null)
    return
  }
  updateNode(createCondition(value as ScenarioConditionKind))
}

function onScalarInput(key: string, event: Event): void {
  const raw = (event.target as HTMLInputElement).value
  const numericKeys = new Set([
    'hp_lte',
    'hp_gte',
    'turn_gte',
    'turn_lte',
    'world_time_gte',
    'world_time_lte',
  ])
  if (numericKeys.has(key)) {
    const parsed = Number(raw)
    updateNode({ [key]: Number.isFinite(parsed) ? Math.trunc(parsed) : 0 })
    return
  }
  updateNode({ [key]: raw })
}

function onPayloadInput(key: string, payloadKey: string, event: Event): void {
  const raw = (event.target as HTMLInputElement).value
  const payload = recordValue(key)
  const numeric = payloadKey === 'at_least'
  updateNode({
    [key]: {
      ...payload,
      [payloadKey]: numeric ? (Number.isFinite(Number(raw)) ? Math.trunc(Number(raw)) : 1) : raw,
    },
  })
}

function onAlwaysChange(event: Event): void {
  updateNode({ always: (event.target as HTMLSelectElement).value === 'true' })
}

function comboChildren(key: 'all' | 'any'): ScenarioCondition[] {
  const value = props.modelValue?.[key]
  return Array.isArray(value)
    ? value.filter(
        (child): child is ScenarioCondition =>
          typeof child === 'object' && child !== null && !Array.isArray(child),
      )
    : []
}

function updateChild(key: 'all' | 'any', index: number, next: ScenarioCondition | null): void {
  const children = [...comboChildren(key)]
  if (next === null) {
    children.splice(index, 1)
  } else {
    children[index] = next
  }
  updateNode({ [key]: children })
}

function addChild(key: 'all' | 'any'): void {
  const children = comboChildren(key)
  if (children.length >= MAX_COMBO_CHILDREN) {
    return
  }
  updateNode({ [key]: [...children, createCondition('in_room')] })
}

function notChild(): ScenarioCondition {
  const value = props.modelValue?.not
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as ScenarioCondition)
    : createCondition('in_room')
}

function updateNotChild(next: ScenarioCondition | null): void {
  updateNode({ not: next ?? createCondition('in_room') })
}
</script>

<template>
  <fieldset class="condition-editor">
    <legend>
      {{ label }}
      <span class="condition-json" aria-hidden="true">{{ conditionJson }}</span>
    </legend>
    <label class="field">
      <span>条件类型</span>
      <select
        :id="`${uid}-kind`"
        class="input"
        data-testid="condition-kind-select"
        :value="kind"
        @change="onKindChange"
      >
        <option value="">（无条件 / 清除）</option>
        <optgroup v-for="group in ['v1 原子', 'v2 原子', '组合子']" :key="group" :label="group">
          <option
            v-for="option in kinds.filter((item) => item.group === group)"
            :key="option.kind"
            :value="option.kind"
          >
            {{ option.label }}
          </option>
        </optgroup>
      </select>
      <span v-if="kindOption !== null" class="field-hint muted">{{ kindOption.hint }}</span>
    </label>

    <!-- v1 单值谓词 -->
    <label v-if="kind === 'in_room' || kind === 'has_item' || kind === 'has_item_tag'" class="field">
      <span>
        {{ kind === 'in_room' ? '房间 key' : kind === 'has_item' ? '物品 key' : '物品标签' }}
      </span>
      <input
        class="input"
        type="text"
        data-testid="condition-scalar"
        :value="scalar(kind)"
        @input="onScalarInput(kind, $event)"
      />
    </label>

    <!-- v2 单值谓词 -->
    <label v-if="kind === 'flag' || kind === 'objective_done'" class="field">
      <span>{{ kind === 'flag' ? '剧情开关 key' : '目标 key' }}</span>
      <input
        class="input"
        type="text"
        data-testid="condition-scalar"
        :value="scalar(kind)"
        @input="onScalarInput(kind, $event)"
      />
    </label>

    <!-- 数值阈值谓词 -->
    <label
      v-if="
        kind === 'hp_lte' ||
        kind === 'hp_gte' ||
        kind === 'turn_gte' ||
        kind === 'turn_lte' ||
        kind === 'world_time_gte' ||
        kind === 'world_time_lte'
      "
      class="field"
    >
      <span>阈值（整数）</span>
      <input
        class="input"
        type="number"
        inputmode="numeric"
        data-testid="condition-number"
        :value="scalar(kind)"
        @input="onScalarInput(kind, $event)"
      />
    </label>

    <!-- npc_state -->
    <div v-if="kind === 'npc_state'" class="condition-row">
      <label class="field">
        <span>NPC key</span>
        <input
          class="input"
          type="text"
          data-testid="condition-payload-npc"
          :value="payloadText('npc_state', 'npc')"
          @input="onPayloadInput('npc_state', 'npc', $event)"
        />
      </label>
      <label class="field">
        <span>对话态（dialogue_state）</span>
        <input
          class="input"
          type="text"
          data-testid="condition-payload-state"
          :value="payloadText('npc_state', 'state')"
          @input="onPayloadInput('npc_state', 'state', $event)"
        />
      </label>
    </div>

    <!-- has_item_tag_count -->
    <div v-if="kind === 'has_item_tag_count'" class="condition-row">
      <label class="field">
        <span>物品标签</span>
        <input
          class="input"
          type="text"
          data-testid="condition-payload-tag"
          :value="payloadText('has_item_tag_count', 'tag')"
          @input="onPayloadInput('has_item_tag_count', 'tag', $event)"
        />
      </label>
      <label class="field">
        <span>至少持有（1..64）</span>
        <input
          class="input"
          type="number"
          inputmode="numeric"
          data-testid="condition-payload-at-least"
          :value="payloadText('has_item_tag_count', 'at_least')"
          @input="onPayloadInput('has_item_tag_count', 'at_least', $event)"
        />
      </label>
    </div>

    <!-- item_in_room / npc_in_room -->
    <div v-if="kind === 'item_in_room' || kind === 'npc_in_room'" class="condition-row">
      <label class="field">
        <span>{{ kind === 'item_in_room' ? '物品 key' : 'NPC key' }}</span>
        <input
          class="input"
          type="text"
          data-testid="condition-payload-subject"
          :value="payloadText(kind, kind === 'item_in_room' ? 'item' : 'npc')"
          @input="onPayloadInput(kind, kind === 'item_in_room' ? 'item' : 'npc', $event)"
        />
      </label>
      <label class="field">
        <span>房间 key</span>
        <input
          class="input"
          type="text"
          data-testid="condition-payload-room"
          :value="payloadText(kind, 'room')"
          @input="onPayloadInput(kind, 'room', $event)"
        />
      </label>
    </div>

    <!-- always -->
    <label v-if="kind === 'always'" class="field">
      <span>恒真 / 恒假</span>
      <select
        class="input"
        data-testid="condition-always"
        :value="scalar('always', 'true')"
        @change="onAlwaysChange"
      >
        <option value="true">恒真（true）</option>
        <option value="false">恒假（false）</option>
      </select>
    </label>

    <!-- 组合子：all / any -->
    <div v-if="(kind === 'all' || kind === 'any') && canNest" class="condition-combo">
      <div
        v-for="(child, index) in comboChildren(kind === 'all' ? 'all' : 'any')"
        :key="`${index}`"
        class="condition-child"
      >
        <ConditionEditor
          :model-value="child"
          :depth="depth + 1"
          :label="`子条件 ${index + 1}`"
          @update:model-value="updateChild(kind === 'all' ? 'all' : 'any', index, $event)"
        />
        <button
          class="btn btn-small btn-secondary"
          type="button"
          :aria-label="`删除子条件 ${index + 1}`"
          @click="updateChild(kind === 'all' ? 'all' : 'any', index, null)"
        >
          删除
        </button>
      </div>
      <button class="btn btn-small btn-secondary" type="button" @click="addChild(kind === 'all' ? 'all' : 'any')">
        添加子条件
      </button>
    </div>
    <p v-else-if="(kind === 'all' || kind === 'any') && !canNest" class="muted field-hint">
      嵌套层级较深：请在「JSON 原文」面板中编辑该条件（服务端会完整校验）。
    </p>

    <!-- 组合子：not -->
    <div v-if="kind === 'not' && canNest" class="condition-combo">
      <ConditionEditor
        :model-value="notChild()"
        :depth="depth + 1"
        label="取反的子条件"
        @update:model-value="updateNotChild"
      />
    </div>
    <p v-else-if="kind === 'not' && !canNest" class="muted field-hint">
      嵌套层级较深：请在「JSON 原文」面板中编辑该条件（服务端会完整校验）。
    </p>

    <p v-if="kind === '' && modelValue !== null" class="field-hint warn">
      这个条件不是「恰好一个白名单键」：请在 JSON 原文里修正，或重新选择类型。
    </p>
  </fieldset>
</template>
