/**
 * `v-reveal` 指令（H4 炫酷化）：滚动进场动效，纯 CSS 过渡 + IntersectionObserver。
 *
 * 降级链（任何一环不满足都**立即显示**，绝不隐藏内容）：
 * 1. `html[data-visual="off"]` / `html[data-reduced-motion="true"]` → 直接可见；
 * 2. 环境无 `IntersectionObserver`（测试/旧浏览器）→ 直接可见；
 * 3. 其余情况：加 `.reveal` 等元素进入视口后加 `.reveal-visible`。
 *
 * 安全：不读写 HTML 字符串，只增删 class；元素卸载时断开 observer。
 */

import type { Directive } from 'vue'

const observers = new WeakMap<Element, IntersectionObserver>()

/** 当前环境是否应播放进场动效（与视觉偏好 store 写入的 `<html>` 属性一致）。 */
export function revealAnimationsEnabled(): boolean {
  if (typeof document === 'undefined') {
    return false
  }
  const root = document.documentElement
  return root.dataset.visual !== 'off' && root.dataset.reducedMotion !== 'true'
}

export const vReveal: Directive<HTMLElement> = {
  mounted(element) {
    if (!revealAnimationsEnabled() || typeof IntersectionObserver === 'undefined') {
      element.classList.add('reveal-visible')
      return
    }
    element.classList.add('reveal')
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            element.classList.add('reveal-visible')
            observer.disconnect()
            observers.delete(element)
            break
          }
        }
      },
      { threshold: 0.12, rootMargin: '0px 0px -8% 0px' },
    )
    observer.observe(element)
    observers.set(element, observer)
  },
  unmounted(element) {
    const observer = observers.get(element)
    if (observer !== undefined) {
      observer.disconnect()
      observers.delete(element)
    }
  },
}
