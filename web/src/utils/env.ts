/**
 * 构建期环境开关（Vite 静态注入，第 23 章 §23.4）。
 *
 * 口径（R3 P2-1）：开发/诊断信息（对局 ID、turn/version 等）只在 DEV 构建渲染；
 * 生产构建 `import.meta.env.DEV === false`，玩家页面不出现内部调试字段。
 * 抽成独立模块便于单测用 `vi.mock('@/utils/env')` 覆盖生产分支。
 */

/** 是否为开发构建（`vite build` / 生产为 `false`）。 */
export const IS_DEV: boolean = import.meta.env.DEV
