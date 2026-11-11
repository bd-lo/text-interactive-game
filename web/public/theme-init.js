/**
 * 首帧主题脚本（Aurora Glass）。
 *
 * 为什么是外链而不是内联：
 * - 服务端 CSP 为 `script-src 'self'`（src/textgame/api/app.py），内联脚本会被拦截；
 * - web/tests/unit/security-scan.test.ts 也断言 index.html 不含内联脚本。
 *
 * 为什么必须在 <head> 同步执行：
 * - 深色用户刷新时，若等 Vue 挂载后再切色，会先闪一帧浅色；此脚本在任何绘制前写好
 *   `<html data-theme>`，纯 CSS 即可命中正确皮肤。
 *
 * 约定：读取的键名/结构必须与 web/src/stores/theme.ts 保持一致（有单测比对）。
 */
;(function () {
  var KEY = 'textgame:theme-settings'
  var COLORS = { light: '#f6f7fb', dark: '#070814' }
  var theme = 'light'

  try {
    var raw = window.localStorage.getItem(KEY)
    if (raw) {
      var saved = JSON.parse(raw)
      var mode = saved && saved.theme
      if (mode === 'light' || mode === 'dark') {
        theme = mode
      } else if (mode === 'auto') {
        var mql = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null
        theme = mql && mql.matches ? 'dark' : 'light'
      }
    }
  } catch (error) {
    // 隐私模式 / 数据损坏：按默认日间
  }

  document.documentElement.setAttribute('data-theme', theme)
  var meta = document.querySelector('meta[name="theme-color"]')
  if (meta) {
    meta.setAttribute('content', COLORS[theme])
  }
})()
