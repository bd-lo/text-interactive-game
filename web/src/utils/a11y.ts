/**
 * 无障碍焦点工具（041–042 a11y 批次）：零依赖、纯 DOM API。
 *
 * 口径：
 * - 只处理**焦点**，不改视觉/结构；不读写 HTML 字符串（security-scan 禁入模式不适用）；
 * - `firstFocusable` 按 DOM 顺序取第一个可键盘聚焦元素（含正 tabindex，天然 DOM 顺序优先）；
 * - `focusElement` 静默容错：元素已被 v-if 移除/未挂载时不做任何事，绝不抛错；
 * - `isEditableTarget` 供全局快捷键判断：输入框/文本域/下拉/可编辑区内不抢 `/`、`Esc` 等按键。
 */

/** 可聚焦元素选择器：原生可交互元素 + 显式非负 tabindex。 */
export const FOCUSABLE_SELECTOR = [
  'a[href]',
  'area[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'details > summary',
  'iframe',
  'audio[controls]',
  'video[controls]',
  '[contenteditable="true"]',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

/** 元素是否位于文档中（`isConnected` 在 jsdom/浏览器均可用）。 */
function inDocument(element: Element | null): boolean {
  return element !== null && element.isConnected
}

/**
 * 返回容器内第一个可键盘聚焦元素（DOM 顺序）。
 *
 * 过滤 `display:none`/`visibility:hidden`/`[hidden]` 祖先：弹层关闭时不把焦点送去不可见控件。
 * jsdom 无布局盒，`getClientRects()` 恒为空——因此只做「显式隐藏」判断，不依赖尺寸。
 */
export function firstFocusable(root: ParentNode | null): HTMLElement | null {
  if (root === null) {
    return null
  }
  const candidates = root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
  for (const candidate of candidates) {
    if (!inDocument(candidate)) {
      continue
    }
    if (candidate.closest('[hidden]') !== null) {
      continue
    }
    const style = globalThis.getComputedStyle?.(candidate)
    if (style !== undefined && (style.display === 'none' || style.visibility === 'hidden')) {
      continue
    }
    return candidate
  }
  return null
}

/** 下一帧把焦点交给元素；元素不存在/已卸载时静默跳过（不抛错）。 */
export function focusElement(element: HTMLElement | null): void {
  if (element === null || !inDocument(element)) {
    return
  }
  element.focus()
}

/** 全局快捷键守卫：目标在可编辑控件内时返回 true（不要抢键）。 */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false
  }
  if (target.isContentEditable) {
    return true
  }
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}

/** 把焦点移到页面主区域（跳过链接/路由切换共用；`main` 需 `tabindex="-1"`）。 */
export function focusMainContent(): void {
  if (typeof document === 'undefined') {
    return
  }
  focusElement(document.getElementById('main-content'))
}
