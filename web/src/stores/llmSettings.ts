/**
 * LLM 设置 store（DeepSeek API Key）：状态读取、保存并热切换、连接测试、清除本机 key。
 *
 * 口径（后端 `routes/settings.py` + `llm/runtime.py`）：
 * - 响应只含打码 key，绝不回传明文；前端不持久化明文，保存成功后立即清空密码框；
 * - 写端点按值幂等、契约不要求 Idempotency-Key；**无自动重试**（失败由用户手动重试）；
 * - 清除只清「本机保存」的 key；存在 `.env` 开发 key 时来源仍是 env（以服务端 status 为准）；
 * - 状态以服务端返回为准，前端不自行推断 configured/enabled。
 */

import { defineStore } from 'pinia'

import {
  clearLlmSettings,
  getLlmSettings,
  saveLlmSettings,
  testLlmConnection,
} from '@/api/client'
import type {
  LlmConnectionTest,
  LlmSettingsSaveRequest,
  LlmSettingsStatus,
} from '@/api/types'
import { describeError, type PlayerErrorInfo } from '@/utils/errors'
import { labelLlmSource } from '@/utils/llm'

interface LlmSettingsState {
  status: LlmSettingsStatus | null
  loading: boolean
  loadError: PlayerErrorInfo | null
  saveBusy: boolean
  saveError: PlayerErrorInfo | null
  saveNotice: string
  testBusy: boolean
  testError: PlayerErrorInfo | null
  lastTest: LlmConnectionTest | null
  clearBusy: boolean
  clearError: PlayerErrorInfo | null
  clearNotice: string
  clearArmed: boolean
}

function initialState(): LlmSettingsState {
  return {
    status: null,
    loading: false,
    loadError: null,
    saveBusy: false,
    saveError: null,
    saveNotice: '',
    testBusy: false,
    testError: null,
    lastTest: null,
    clearBusy: false,
    clearError: null,
    clearNotice: '',
    clearArmed: false,
  }
}

export const useLlmSettingsStore = defineStore('llmSettings', {
  state: initialState,
  getters: {
    /** 本机是否保存过玩家 key（决定「清除本机 key」是否可用）。 */
    canClear(state): boolean {
      return state.status?.has_saved_user_key === true
    },
    /** 是否已配置可用的大模型（Home 横幅 / 状态卡徽章共用）。 */
    configured(state): boolean {
      return state.status?.configured === true
    },
  },
  actions: {
    /** 读取当前 LLM 配置状态（只读；失败写 loadError 供视图手动重试）。 */
    async load(): Promise<void> {
      this.loading = true
      this.loadError = null
      try {
        this.status = await getLlmSettings()
      } catch (error) {
        this.loadError = describeError(error)
        throw error
      } finally {
        this.loading = false
      }
    },
    /** 保存本机 key 并热切换（成功后用服务端 status 覆盖本地；明文不落任何存储）。 */
    async save(body: LlmSettingsSaveRequest): Promise<boolean> {
      this.saveBusy = true
      this.saveError = null
      this.saveNotice = ''
      this.clearNotice = ''
      this.clearArmed = false
      try {
        const response = await saveLlmSettings(body)
        this.status = response.status
        this.lastTest = response.test ?? null
        this.saveNotice = `已保存并启用（来源：${labelLlmSource(response.status.source)}，模型 ${response.status.model}）。`
        return true
      } catch (error) {
        this.saveError = describeError(error)
        return false
      } finally {
        this.saveBusy = false
      }
    },
    /** 测试当前生效配置（不保存表单、不改状态；失败原因人话展示）。 */
    async test(): Promise<boolean> {
      this.testBusy = true
      this.testError = null
      this.lastTest = null
      try {
        this.lastTest = await testLlmConnection()
        return true
      } catch (error) {
        this.testError = describeError(error)
        return false
      } finally {
        this.testBusy = false
      }
    },
    /** 清除本机保存的 key（二次确认由视图控制；存在 .env 开发 key 时仍会用 .env）。 */
    async clear(): Promise<boolean> {
      this.clearBusy = true
      this.clearError = null
      this.clearNotice = ''
      try {
        const response = await clearLlmSettings()
        this.status = response.status
        this.lastTest = null
        this.saveNotice = ''
        this.clearArmed = false
        this.clearNotice =
          response.status.source === 'env'
            ? '已清除本机保存的 key；当前仍在使用 .env 开发 key。'
            : '已清除本机保存的 key；当前回到离线模板叙事。'
        return true
      } catch (error) {
        this.clearError = describeError(error)
        return false
      } finally {
        this.clearBusy = false
      }
    },
    armClear(): void {
      this.clearArmed = true
      this.clearError = null
    },
    disarmClear(): void {
      this.clearArmed = false
    },
    resetTest(): void {
      this.lastTest = null
      this.testError = null
    },
    resetNotices(): void {
      this.saveNotice = ''
      this.clearNotice = ''
    },
  },
})
