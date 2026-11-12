/**
 * 自由制造设置 store（GET/PUT /api/v1/settings/free-craft）。
 *
 * 口径（后端 `routes/settings.py`）：
 * - 默认关闭；开启要求大模型已启用 + 前端二次确认大模型费用风险（R-19 pre-queue：草稿在进入裁决前生成一次，生成失败零写入、不因 CAS 重试重复调用）；
 * - 后端持久化到本机 `data/free_craft.json`，重启保留；
 * - 写端点按值幂等、无需 Idempotency-Key；失败不自动重试，由玩家手动重试；
 * - 状态以服务端返回为准，前端不自行推断 enabled/available。
 */

import { defineStore } from 'pinia'

import { getFreeCraftSettings, saveFreeCraftSettings } from '@/api/client'
import type { FreeCraftSettingsStatus } from '@/api/types'
import { describeFreeCraftError, type PlayerErrorInfo } from '@/utils/errors'

interface FreeCraftState {
  status: FreeCraftSettingsStatus | null
  loading: boolean
  loadError: PlayerErrorInfo | null
  saveBusy: boolean
  saveError: PlayerErrorInfo | null
  saveNotice: string
  confirmArmed: boolean
}

function initialState(): FreeCraftState {
  return {
    status: null,
    loading: false,
    loadError: null,
    saveBusy: false,
    saveError: null,
    saveNotice: '',
    confirmArmed: false,
  }
}

export const useFreeCraftStore = defineStore('freeCraft', {
  state: initialState,
  getters: {
    /** 当前是否已开启（以服务端状态为准）。 */
    enabled(state): boolean {
      return state.status?.enabled === true
    },
    /** 当前是否具备开启条件（大模型已启用）。 */
    canEnable(state): boolean {
      return state.status?.available === true && state.status?.enabled !== true
    },
  },
  actions: {
    /** 读取开关状态；失败写 loadError 供视图手动重试。 */
    async load(): Promise<void> {
      this.loading = true
      this.loadError = null
      try {
        this.status = await getFreeCraftSettings()
      } catch (error) {
        this.loadError = describeFreeCraftError(error)
        throw error
      } finally {
        this.loading = false
      }
    },
    /** 玩家拨到“开”：先亮出风险确认，不直接请求。 */
    armEnable(): void {
      this.confirmArmed = true
      this.saveError = null
      this.saveNotice = ''
    },
    disarmEnable(): void {
      this.confirmArmed = false
    },
    /** 确认风险后真正开启（accept_resolver_llm_risk=true 为既有 API 字段名，语义=确认「调用大模型可能产生费用」）。 */
    async enable(): Promise<boolean> {
      return this._save({ enabled: true, accept_resolver_llm_risk: true })
    },
    /** 关闭不需要风险确认，立即生效。 */
    async disable(): Promise<boolean> {
      this.confirmArmed = false
      return this._save({ enabled: false, accept_resolver_llm_risk: false })
    },
    async _save(body: {
      enabled: boolean
      accept_resolver_llm_risk: boolean
    }): Promise<boolean> {
      this.saveBusy = true
      this.saveError = null
      this.saveNotice = ''
      try {
        const status = await saveFreeCraftSettings(body)
        this.status = status
        this.confirmArmed = false
        this.saveNotice = status.enabled
          ? '自由制造已开启：未命中配方时会调用大模型生成一次草稿（进入裁决前），可能产生费用。'
          : '自由制造已关闭：未命中配方时会返回不支持。'
        return true
      } catch (error) {
        this.saveError = describeFreeCraftError(error)
        return false
      } finally {
        this.saveBusy = false
      }
    },
    resetNotices(): void {
      this.saveNotice = ''
      this.saveError = null
    },
  },
})
