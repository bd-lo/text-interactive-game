/**
 * 启动器控制面前端常量/辅助（网页「退出游戏」+ 心跳）。
 *
 * 口径（后端 Task A 冻结契约）：标签与文案是 e2e/vitest 的稳定断言点，**不得随意改写**；
 * 非启动器模式 status 仍 200，但 heartbeat/shutdown 会 404，因此 UI 只在
 * `shutdown_supported=true` 时走「确认 → 已退出」分支，否则显示 Ctrl+C 说明面板。
 */

import type { LauncherStatus } from '@/api/types'

/** 心跳间隔（毫秒；仅 launcher_mode=true 时发送）。 */
export const LAUNCHER_HEARTBEAT_INTERVAL_MS = 15_000

/** 「退出游戏」触发按钮文案（固定）。 */
export const QUIT_GAME_LABEL = '退出游戏'

/** 二次确认按钮文案（固定）。 */
export const QUIT_CONFIRM_LABEL = '确认退出（进度已自动保存）'

/** 取消按钮文案（固定）。 */
export const QUIT_CANCEL_LABEL = '取消'

/** 非启动器模式说明面板固定文案（e2e 断言）。 */
export const QUIT_INFO_TEXT = '当前不是启动器模式：请在运行服务的窗口按 Ctrl+C 退出。'

/** 说明面板关闭按钮文案（固定）。 */
export const QUIT_INFO_CLOSE_LABEL = '知道了'

/** 已退出覆盖层标题/正文（固定）。 */
export const EXIT_OVERLAY_TITLE = '游戏已退出'
export const EXIT_OVERLAY_BODY = '可以关闭此页面了。'

/** 是否启动器托管（决定是否发心跳）。 */
export function shouldHeartbeat(status: LauncherStatus | null): boolean {
  return status?.launcher_mode === true
}

/** 是否支持页内退出（决定点「退出游戏」走确认还是说明面板）。 */
export function canShutdownInPage(status: LauncherStatus | null): boolean {
  return status?.shutdown_supported === true
}
