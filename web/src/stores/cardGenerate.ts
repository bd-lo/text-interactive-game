/**
 * 生成人物卡 store（M6-028，第 23 章 §23.6）。
 *
 * 口径：
 * - `brief` 按**码点** `[...str].length` 计数 ≤1000；提交前 trim 后非空；
 * - `generate()` 带**写幂等键**：手动重试复用 `pendingGenerateKey`；成功/409 后清键（下次换新键）；
 *   **无自动重试**（本 store 不包含任何定时器/自动重放）；
 * - 429 只记录 `Retry-After` 秒数供展示（`retryAfterSeconds`），不启动倒计时轮询；
 * - 模板兜底判定 = `generated_by === null` 或 warnings 含 `TEMPLATE_FALLBACK`；
 * - 草稿**不自动保存、不自动建局/跳转**；保存走 `POST /character-cards`（同样幂等键 + 手动重试）；
 * - 编辑草稿 JSON 后必须点「重新校验」（服务端 `POST /character-cards/validate` 权威）通过才能保存。
 */

import { defineStore } from 'pinia'

import { generateCard, saveCard, validateCard } from '@/api/client'
import type {
  CardGenerateValidation,
  CharacterCard,
  CharacterCardValidationError,
  CharacterCardWarning,
} from '@/api/types'
import {
  countBriefChars,
  describeGenerateError,
  describeSaveCardError,
  draftBadgeLabel,
  GENERATE_BRIEF_MAX_CHARS,
  isBriefValid,
  isTemplateFallback,
  prettyCardJson,
} from '@/utils/cardGenerate'
import { checkCardText, describeCardError } from '@/utils/cards'
import { clear as clearIdempotencyKey, getOrCreate } from '@/utils/idempotency'

/** 生成请求的幂等键 actionKey（sessionStorage 逻辑动作名）。 */
export const GENERATE_IDEMPOTENCY_ACTION = 'card-generate'
/** 保存请求的幂等键 actionKey。 */
export const SAVE_IDEMPOTENCY_ACTION = 'card-save'

interface CardGenerateState {
  brief: string
  /** `options.tone`（空串 = 不传）。 */
  tone: string
  busy: boolean
  draft: CharacterCard | null
  validation: CardGenerateValidation | null
  generatedBy: string | null
  templateFallback: boolean
  retryAfterSeconds: number
  errorTitle: string
  error: string
  errorCode: string
  errorDetail: string
  pendingGenerateKey: string | null
  saveBusy: boolean
  saveErrorTitle: string
  saveError: string
  saveErrorCode: string
  saveErrorDetail: string
  savedCardId: string
  pendingSaveKey: string | null
  /** 可编辑 JSON 文本（生成成功即预填格式化草稿）。 */
  editedText: string
  /** JSON 自上次校验通过后是否又被修改（修改后保存禁用，直到重新校验）。 */
  editedDirty: boolean
  editedBusy: boolean
  editedValid: boolean
  editedErrors: CharacterCardValidationError[]
  editedError: string
  /** 重新校验通过后待保存的卡片（未编辑时即生成草稿）。 */
  editedCard: CharacterCard | null
}

function initialState(): CardGenerateState {
  return {
    brief: '',
    tone: '',
    busy: false,
    draft: null,
    validation: null,
    generatedBy: null,
    templateFallback: false,
    retryAfterSeconds: 0,
    errorTitle: '',
    error: '',
    errorCode: '',
    errorDetail: '',
    pendingGenerateKey: null,
    saveBusy: false,
    saveErrorTitle: '',
    saveError: '',
    saveErrorCode: '',
    saveErrorDetail: '',
    savedCardId: '',
    pendingSaveKey: null,
    editedText: '',
    editedDirty: false,
    editedBusy: false,
    editedValid: false,
    editedErrors: [],
    editedError: '',
    editedCard: null,
  }
}

export const useCardGenerateStore = defineStore('cardGenerate', {
  state: initialState,
  getters: {
    /** brief 码点数（展示 `N/1000`）。 */
    briefChars(state): number {
      return countBriefChars(state.brief)
    },
    briefOverLimit(state): boolean {
      return countBriefChars(state.brief) > GENERATE_BRIEF_MAX_CHARS
    },
    /** 可点「生成人物卡」：非空、未超限、当前不在请求中。 */
    canGenerate(state): boolean {
      return !state.busy && isBriefValid(state.brief)
    },
    /** 可点「保存到卡库」：有已校验卡片、未再编辑、未在保存中、尚未保存过。 */
    canSave(state): boolean {
      return (
        !state.saveBusy &&
        state.savedCardId === '' &&
        state.editedCard !== null &&
        state.editedValid &&
        !state.editedDirty
      )
    },
    /** 草稿徽章文案：「模板草稿」/「LLM 草稿」。 */
    badgeLabel(state): string {
      return draftBadgeLabel(state.templateFallback)
    },
    warnings(state): CharacterCardWarning[] {
      return state.validation?.warnings ?? []
    },
    draftErrors(state): CharacterCardValidationError[] {
      return state.validation?.errors ?? []
    },
    hasDraft(state): boolean {
      return state.draft !== null
    },
  },
  actions: {
    /** 生成成功：记录草稿/校验/兜底判定，并预填可编辑 JSON。 */
    applyGenerated(response: {
      draft: CharacterCard
      validation: CardGenerateValidation
      generated_by?: string | null
    }): void {
      const generatedBy = response.generated_by ?? null
      this.draft = response.draft
      this.validation = response.validation
      this.generatedBy = generatedBy
      this.templateFallback = isTemplateFallback(generatedBy, response.validation.warnings)
      this.retryAfterSeconds = 0
      this.editedText = prettyCardJson(response.draft)
      this.editedDirty = false
      this.editedValid = response.validation.valid
      this.editedErrors = response.validation.errors ?? []
      this.editedError = ''
      this.editedCard = response.validation.valid ? response.draft : null
      this.savedCardId = ''
      this.saveErrorTitle = ''
      this.saveError = ''
      this.saveErrorCode = ''
      this.saveErrorDetail = ''
    },
    clearGenerateError(): void {
      this.errorTitle = ''
      this.error = ''
      this.errorCode = ''
      this.errorDetail = ''
      this.retryAfterSeconds = 0
    },
    /**
     * 生成人物卡：预检 brief → `POST /character-cards/generate`。
     *
     * 手动重试复用同一幂等键（网络失败时服务端可回放首次结果）；409 清键后下次换新键；
     * 429 记录 `Retry-After` 秒数但**不自动重试**。返回是否成功（失败文案进 `error*`）。
     */
    async generate(): Promise<boolean> {
      if (this.busy) {
        return false
      }
      this.clearGenerateError()
      const brief = this.brief.trim()
      if (brief === '') {
        this.errorTitle = '还差一点'
        this.error = '请先写一句人物设定（brief），再生成人物卡。'
        this.errorCode = 'CLIENT'
        return false
      }
      if (countBriefChars(brief) > GENERATE_BRIEF_MAX_CHARS) {
        this.errorTitle = '人物设定太长了'
        this.error = `人物设定不能超过 ${GENERATE_BRIEF_MAX_CHARS} 字，请删减后再生成。`
        this.errorCode = 'CLIENT'
        return false
      }
      const options: Record<string, unknown> = this.tone === '' ? {} : { tone: this.tone }
      const actionKey = this.pendingGenerateKey ?? getOrCreate(GENERATE_IDEMPOTENCY_ACTION)
      this.pendingGenerateKey = actionKey
      this.busy = true
      try {
        const response = await generateCard(
          { brief, schema_version: '1.0', options },
          actionKey,
        )
        clearIdempotencyKey(GENERATE_IDEMPOTENCY_ACTION)
        this.pendingGenerateKey = null
        this.applyGenerated(response)
        return true
      } catch (error) {
        const info = describeGenerateError(error)
        this.errorTitle = info.title
        this.error = info.message
        this.errorCode = info.code
        this.errorDetail = info.detail
        this.retryAfterSeconds = info.retryAfterSeconds
        if (info.clearKey) {
          clearIdempotencyKey(GENERATE_IDEMPOTENCY_ACTION)
          this.pendingGenerateKey = null
        }
        return false
      } finally {
        this.busy = false
      }
    },
    /** 手动重试生成（复用同一逻辑动作的幂等键；不是自动重试）。 */
    async retryGenerate(): Promise<boolean> {
      return this.generate()
    },
    /** 编辑草稿 JSON：标记未校验（保存按钮随即禁用，直到重新校验）。 */
    markEdited(): void {
      this.editedDirty = true
      this.editedValid = false
      this.editedErrors = []
      this.editedError = ''
      this.editedCard = null
    },
    /** 设定编辑文本并标记未校验（等价于 v-model + markEdited）。 */
    setEditedText(text: string): void {
      this.editedText = text
      this.markEdited()
    },
    /**
     * 重新校验编辑后的 JSON：本地 JSON 预检 → `POST /character-cards/validate`（只读）。
     * 通过后该 JSON 即成为待保存卡片；不通过则保存保持禁用。
     */
    async revalidate(): Promise<boolean> {
      this.editedError = ''
      this.editedErrors = []
      this.editedBusy = true
      try {
        const check = checkCardText(this.editedText)
        if (!check.ok) {
          this.editedValid = false
          this.editedCard = null
          this.editedError = check.error
          return false
        }
        const payload: unknown = JSON.parse(this.editedText)
        const result = await validateCard(payload)
        this.editedDirty = false
        this.editedValid = result.valid
        this.editedErrors = result.errors
        this.editedCard = result.valid ? (payload as CharacterCard) : null
        return result.valid
      } catch (error) {
        this.editedValid = false
        this.editedCard = null
        this.editedError = describeCardError(error).message
        return false
      } finally {
        this.editedBusy = false
      }
    },
    /**
     * 保存到卡库：`POST /character-cards`（写幂等键，手动重试复用同键；409 清键）。
     * 保存前禁止自动建局/跳转（由视图在成功提示里给显式按钮）。
     */
    async save(): Promise<string | null> {
      if (this.saveBusy) {
        return null
      }
      this.saveErrorTitle = ''
      this.saveError = ''
      this.saveErrorCode = ''
      this.saveErrorDetail = ''
      if (this.savedCardId !== '') {
        this.saveErrorTitle = '已经保存过了'
        this.saveError = '这张草稿已保存到卡库，请用下方「查看卡片」或「按此卡开局」。'
        this.saveErrorCode = 'CLIENT'
        return null
      }
      const card = this.editedCard
      if (card === null && this.draft === null) {
        this.saveErrorTitle = '还没有可保存的草稿'
        this.saveError = '请先生成人物卡，或把修改后的 JSON 重新校验通过。'
        this.saveErrorCode = 'CLIENT'
        return null
      }
      if (this.editedDirty || !this.editedValid) {
        this.saveErrorTitle = '还没通过校验'
        this.saveError = '修改后的 JSON 还没通过校验，请先点「重新校验」。'
        this.saveErrorCode = 'CLIENT'
        return null
      }
      if (card === null) {
        this.saveErrorTitle = '还没有可保存的草稿'
        this.saveError = '请先生成人物卡，或把修改后的 JSON 重新校验通过。'
        this.saveErrorCode = 'CLIENT'
        return null
      }
      const actionKey = this.pendingSaveKey ?? getOrCreate(SAVE_IDEMPOTENCY_ACTION)
      this.pendingSaveKey = actionKey
      this.saveBusy = true
      try {
        const response = await saveCard(card, actionKey)
        clearIdempotencyKey(SAVE_IDEMPOTENCY_ACTION)
        this.pendingSaveKey = null
        this.savedCardId = response.card_id
        return response.card_id
      } catch (error) {
        const info = describeSaveCardError(error)
        this.saveErrorTitle = info.title
        this.saveError = info.message
        this.saveErrorCode = info.code
        this.saveErrorDetail = info.detail
        if (info.clearKey) {
          clearIdempotencyKey(SAVE_IDEMPOTENCY_ACTION)
          this.pendingSaveKey = null
        }
        return null
      } finally {
        this.saveBusy = false
      }
    },
    /** 清空生成状态（新一次生成流程）。 */
    reset(): void {
      this.$reset()
    },
  },
})
