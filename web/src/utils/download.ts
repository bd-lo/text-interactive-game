/**
 * 浏览器文本文件下载（N1-032 场景导出 / N1-031 编辑器导出共用）。
 *
 * 口径：
 * - 纯浏览器 API（Blob + Object URL + `<a download>`），零依赖、不经过后端额外端点；
 * - 环境不支持（jsdom/旧浏览器）返回 false，由调用方显示「当前浏览器不支持下载」；
 * - 文件名由调用方给（场景导出用服务端 `Content-Disposition` 的 `<slug>.json`）；
 * - 文本按纯文本处理（Blob `application/json`），不注入 HTML。
 */

/** 下载文本文件；成功返回 true（环境不支持返回 false）。 */
export function downloadTextFile(
  filename: string,
  text: string,
  mimeType = 'application/json',
): boolean {
  if (typeof document === 'undefined') {
    return false
  }
  const objectUrlApi = globalThis.URL
  if (typeof objectUrlApi?.createObjectURL !== 'function') {
    return false
  }
  const blob = new Blob([text], { type: mimeType })
  const url = objectUrlApi.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = safeFileName(filename)
  anchor.rel = 'noopener'
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  try {
    anchor.click()
  } finally {
    anchor.remove()
    // 延迟释放，避免部分浏览器下载尚未开始就失效
    setTimeout(() => {
      try {
        objectUrlApi.revokeObjectURL(url)
      } catch {
        // 忽略：已释放/环境不支持
      }
    }, 0)
  }
  return true
}

/** 文件名安全化：只保留常见安全字符，空文件名回退 `scenario.json`。 */
export function safeFileName(filename: string): string {
  const trimmed = filename.trim().replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-')
  return trimmed === '' ? 'scenario.json' : trimmed
}

/** 由场景 slug 派生下载文件名（slug 受 `^[a-z0-9-]{2,32}$` 约束，天然 ASCII 安全）。 */
export function scenarioJsonFileName(slug: string): string {
  return safeFileName(`${slug.trim() === '' ? 'scenario' : slug.trim()}.json`)
}
