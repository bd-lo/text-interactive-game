import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

// 第 23 章 §23.7：禁 v-html/innerHTML/eval；CSP 同源（无内联脚本/禁 CDN）；无 SSE（Q7=A）。
// 注：vitest 以 web/ 为工作目录运行（`pnpm --dir web test`），直接用 process.cwd()。
const WEB_ROOT = process.cwd()
const SRC_ROOT = join(WEB_ROOT, 'src')

const FORBIDDEN_IN_SRC: ReadonlyArray<{ name: string; pattern: RegExp }> = [
  { name: 'v-html', pattern: /v-html/ },
  { name: 'innerHTML', pattern: /innerHTML/ },
  { name: 'eval()', pattern: /\beval\s*\(/ },
  { name: 'new Function()', pattern: /new\s+Function\s*\(/ },
  { name: 'EventSource/SSE', pattern: /EventSource/ },
  { name: 'document.write()', pattern: /document\.write\s*\(/ },
  { name: 'CDN 外链', pattern: /https?:\/\/cdn\./ },
]

function walk(directory: string): string[] {
  const files: string[] = []
  for (const name of readdirSync(directory)) {
    const path = join(directory, name)
    if (statSync(path).isDirectory()) {
      files.push(...walk(path))
    } else {
      files.push(path)
    }
  }
  return files
}

const SOURCE_FILES = walk(SRC_ROOT).filter((path) => path.endsWith('.ts') || path.endsWith('.vue'))

describe('前端安全基线（第 23 章 §23.7 / D-M6-5⑤）', () => {
  it('web/src 不含 XSS 注入与 SSE 禁入模式', () => {
    const violations = SOURCE_FILES.flatMap((file) => {
      const content = readFileSync(file, 'utf8')
      return FORBIDDEN_IN_SRC.filter((rule) => rule.pattern.test(content)).map(
        (rule) => `${file}: ${rule.name}`,
      )
    })
    expect(violations).toEqual([])
  })

  it('index.html 不含内联脚本（CSP script-src self）', () => {
    const html = readFileSync(join(WEB_ROOT, 'index.html'), 'utf8')
    expect(html).not.toMatch(/<script(?![^>]*\bsrc=)/i)
  })
})
