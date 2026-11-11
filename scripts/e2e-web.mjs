#!/usr/bin/env node
/**
 * M6-028 真实浏览器端到端测试（零依赖，Node 26）。
 *
 * 用系统已安装的 Edge/Chrome（headless）经 **CDP**（Chrome DevTools Protocol）驱动真实 SPA，
 * 覆盖「生成人物卡 → 保存 → 按此卡开局 → 提交 1 回合」主回路，并断言：
 *
 * - `/cards/generate` 深链直接 GET 返回 SPA `index.html`（静态托管 + SPA fallback，D-M6-5①）；
 * - 默认离线页有**显著**「离线 → 模板兜底」说明；brief 码点计数 ≤1000（超限禁用生成）；
 * - 生成后草稿预览 + 「模板草稿」徽章 + 9 字段 JSON 可编辑草稿（source=manual / generated_by=null）；
 * - JSON 非法时「保存」禁用、恢复合法后可用；保存成功区 + 「查看卡片」/「按此卡开局」两个显式入口；
 * - 按此卡开局后对局 `HP/背包` 来自该卡（真实调用链，不是默认卡）；
 * - 提交 1 回合后服务端回合数 +1、页面叙事更新；卡库列表/详情/详情页按卡开局可用；
 * - 375px 视口无横向溢出；**全程无 console 错误/未捕获异常**；
 * - 玩家可见文本无内部 ID（R3 AC-17）：生成页/保存成功区/对局页/冒险记录/卡库列表/卡详情/回放页；
 * - S2 体验打磨：新局开场历史（P2-2）、结局通关总结与结局入冒险记录（AC-34/AC-10d）、
 *   只读回合提示（P2-3）、来源徽章中性化（P2-6）、物品中文名（P1-8）、生产不渲染诊断面板（P2-1）。
 *
 * 依赖约定（与 00-PLAN.md §2.2 一致）：
 * - 零依赖：只用 Node 内置模块 + 全局 `fetch`/`WebSocket`（不装 Playwright/puppeteer）；
 * - uvicorn **由外部启动**（建议 87xx 端口，默认离线 `TEXTGAME_LLM_ENABLED=false`），脚本只做
 *   `GET /healthz` 就绪探测；浏览器用临时 `--user-data-dir`，脚本结束（含异常）自动关闭并清理；
 * - 不入 CI（CI 无浏览器/uv 组合，偏差 D-M6-11），由 QA 在 S1 门禁中本地实跑留证。
 *
 * 用法：
 *   node scripts/e2e-web.mjs --base-url http://127.0.0.1:8791 \
 *     --out .work/r3-team/s1/e2e-web [--browser <Edge/Chrome 可执行文件>] [--headful]
 *
 * 参数：
 *   --base-url <url>   被测服务根地址（默认 http://127.0.0.1:8791）
 *   --browser <path>   浏览器可执行文件（缺省按 Edge/Chrome 常见安装路径自动探测）
 *   --out <dir>        证据目录（默认 <repo>/.work/r3-team/s1/e2e-web；写 result.json + screenshots/）
 *   --timeout <ms>     单步等待超时（默认 20000）
 *   --width/--height   桌面视口（默认 1280x900）
 *   --headful          有界面模式（调试用；默认 headless=new）
 *   --keep-open        结束后保留浏览器进程（调试用）
 *
 * 退出码：0 = 全部通过；1 = 有断言/步骤失败或环境不可用（服务未起/浏览器缺失/CDP 连不上，
 *         详见 result.json 的 `fatalError`）；2 = 参数/启动前错误（如未知参数）。
 */

import { spawn, execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as sleep } from 'node:timers/promises'

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const MOBILE_VIEWPORT = { width: 375, height: 812 }
const BRIEF = '一个在旧矿镇长大的拾荒者，沉默但记性极好，随身带着一盏旧提灯。'

/**
 * CDP 就绪等待时间：CI runner（ubuntu-latest/容器）上 Chrome 首次启动会探测 session dbus，
 * 连接重试可能拖慢 20–30s 才输出 "DevTools listening"；给足 90s（可用
 * TEXTGAME_E2E_CDP_TIMEOUT_MS 覆盖），本地默认 25s 保持快速失败。
 */
const CDP_READY_TIMEOUT_MS = Number(
  process.env.TEXTGAME_E2E_CDP_TIMEOUT_MS ??
    (process.env.GITHUB_ACTIONS === 'true' || process.env.CI === 'true' ? 90000 : 25000),
)

/** S2 结局链：story「余烬哨站」12 条胜利脚本（与后端 replay / 玩家验收一致）。 */
const STORY_FLOW = [
  '向东走',
  '对守夜人老兵说信号灯的事',
  '拿起火把',
  '向东走进铁匠铺',
  '拿起铜线',
  '拿起铜线',
  '打造信号火把',
  '向西走',
  '向北走进地窖',
  '拿起火石',
  '向南走',
  '向上走',
]

/** 模板兜底草稿的初始物品模板键（P1-8：UI 必须显示中文「火把」而不是内部键）。 */
const TEMPLATE_ITEM_KEY = 'item.torch'

// ---------------------------------------------------------------------------
// 参数解析
// ---------------------------------------------------------------------------
function printUsage() {
  const text = `用法: node scripts/e2e-web.mjs [--base-url URL] [--browser PATH] [--out DIR]
       [--timeout MS] [--width N] [--height N] [--headful] [--keep-open]`
  process.stdout.write(`${text}\n`)
}

function parseArgs(argv) {
  const opts = {
    baseUrl: 'http://127.0.0.1:8791',
    browser: '',
    out: join(REPO_ROOT, '.work', 'r3-team', 's1', 'e2e-web'),
    timeout: 20000,
    width: 1280,
    height: 900,
    headful: false,
    keepOpen: false,
    help: false,
  }
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    const takeValue = () => {
      const value = argv[index + 1]
      if (value === undefined || value.startsWith('--')) {
        throw new Error(`参数 ${arg} 缺少取值`)
      }
      index += 1
      return value
    }
    if (arg === '--base-url') {
      opts.baseUrl = takeValue()
    } else if (arg === '--browser') {
      opts.browser = takeValue()
    } else if (arg === '--out') {
      opts.out = resolve(REPO_ROOT, takeValue())
    } else if (arg === '--timeout') {
      opts.timeout = Number(takeValue())
    } else if (arg === '--width') {
      opts.width = Number(takeValue())
    } else if (arg === '--height') {
      opts.height = Number(takeValue())
    } else if (arg === '--headful') {
      opts.headful = true
    } else if (arg === '--keep-open') {
      opts.keepOpen = true
    } else if (arg === '--help' || arg === '-h') {
      opts.help = true
    } else {
      throw new Error(`未知参数: ${arg}`)
    }
  }
  opts.baseUrl = opts.baseUrl.replace(/\/+$/, '')
  return opts
}

// ---------------------------------------------------------------------------
// 断言 / 步骤 / 日志
// ---------------------------------------------------------------------------
const steps = []
const assertions = []
const screenshots = []
const consoleEvents = []

function log(line) {
  process.stdout.write(`${line}\n`)
}

/** 细粒度断言：失败即抛错（由所在 step 捕获并中止后续依赖步骤）。 */
function check(name, condition, detail = '') {
  const ok = Boolean(condition)
  assertions.push({ name, ok, detail: String(detail ?? '') })
  if (!ok) {
    throw new Error(`断言失败：${name}${detail ? `（${detail}）` : ''}`)
  }
  return detail
}

/** 一个 e2e 步骤：记录耗时/结果；失败即中止（后续步骤依赖前置状态）。 */
async function step(id, name, fn) {
  const started = Date.now()
  log(`▶ [${id}] ${name}`)
  try {
    const detail = await fn()
    steps.push({ id, name, ok: true, ms: Date.now() - started, detail: detail ?? '' })
    log(`✔ [${id}] ${name}${detail ? ` — ${detail}` : ''}`)
  } catch (error) {
    steps.push({ id, name, ok: false, ms: Date.now() - started, detail: error.message })
    log(`✘ [${id}] ${name} — ${error.message}`)
    throw error
  }
}

function truncate(value, limit) {
  const text = String(value ?? '')
  return text.length <= limit ? text : `${text.slice(0, limit)}…(${text.length} 字符)`
}

// ---------------------------------------------------------------------------
// 浏览器 / CDP
// ---------------------------------------------------------------------------
const BROWSER_CANDIDATES = {
  win32: [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    join(process.env.LOCALAPPDATA ?? '', 'Google\\Chrome\\Application\\chrome.exe'),
    join(process.env.LOCALAPPDATA ?? '', 'Microsoft\\Edge\\Application\\msedge.exe'),
  ],
  darwin: [
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ],
  linux: [
    '/usr/bin/microsoft-edge',
    '/usr/bin/microsoft-edge-stable',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ],
}

function detectBrowser(explicitPath) {
  if (explicitPath) {
    if (!existsSync(explicitPath)) {
      throw new Error(`指定的浏览器不存在: ${explicitPath}`)
    }
    return explicitPath
  }
  const envPath = process.env.TEXTGAME_E2E_BROWSER?.trim()
  if (envPath && existsSync(envPath)) {
    return envPath
  }
  const candidates = BROWSER_CANDIDATES[process.platform] ?? []
  for (const candidate of candidates) {
    if (candidate && existsSync(candidate)) {
      return candidate
    }
  }
  throw new Error(
    '未找到系统 Edge/Chrome：请用 --browser 指定可执行文件，或设置 TEXTGAME_E2E_BROWSER',
  )
}

/** 找一个空闲 TCP 端口（CDP remote-debugging-port 用）。 */
async function findFreePort() {
  return await new Promise((resolvePort, rejectPort) => {
    const server = createServer()
    server.unref()
    server.once('error', rejectPort)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address !== null ? address.port : 0
      server.close(() => resolvePort(port))
    })
  })
}

async function httpJson(url, timeoutMs = 1500) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${url}`)
  }
  return await response.json()
}

/** 轮询等待 CDP HTTP 端点就绪（浏览器进程启动需要时间）。 */
async function waitForCdp(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  let lastError = null
  while (Date.now() < deadline) {
    try {
      return await httpJson(`http://127.0.0.1:${port}/json/version`)
    } catch (error) {
      lastError = error
      await sleep(100)
    }
  }
  throw new Error(`CDP 端口 ${port} 未在 ${timeoutMs}ms 内就绪: ${lastError?.message ?? '未知错误'}`)
}

async function launchBrowser({ browserPath, port, profileDir, headless, width, height }) {
  const args = [
    `--remote-debugging-port=${port}`,
    '--remote-allow-origins=*',
    `--user-data-dir=${profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--no-proxy-server',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    '--disable-extensions',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-sync',
    '--disable-breakpad',
    '--mute-audio',
    '--hide-scrollbars',
    `--window-size=${width},${height}`,
  ]
  // Linux CI（GitHub Actions ubuntu runner）常见限制：Chrome 的 setuid sandbox 不可用
  // （user namespace 受限/以 CI 用户运行），不加 --no-sandbox 时进程会直接退出、CDP 端口
  // 永不就绪（本地 Windows/macOS 不受影响）。仅对 Linux 且处于 CI 环境时加，降低面。
  if (process.platform === 'linux' && (process.env.CI === 'true' || process.env.GITHUB_ACTIONS === 'true')) {
    args.push('--no-sandbox', '--disable-setuid-sandbox')
  }
  if (headless) {
    args.unshift('--headless=new')
  }
  args.push('about:blank')
  // 收集浏览器 stderr 尾部：启动失败时把原因带进 fatalError（CI 里 stdio 为 ignore 时不可见）。
  let stderrTail = ''
  const child = spawn(browserPath, args, {
    stdio: ['ignore', 'ignore', 'pipe'],
    windowsHide: true,
  })
  child.stderr?.on('data', (chunk) => {
    stderrTail = (stderrTail + String(chunk)).slice(-4000)
  })
  try {
    const version = await waitForCdp(port, CDP_READY_TIMEOUT_MS)
    return { child, version, stderrTail }
  } catch (error) {
    try {
      child.kill('SIGKILL')
    } catch {
      // 进程可能已退出
    }
    const detail = stderrTail.trim().split('\n').slice(-6).join(' | ')
    throw new Error(
      `${error instanceof Error ? error.message : String(error)}` +
        (detail === '' ? '' : `；browser stderr: ${detail}`),
    )
  }
}

class CdpClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl
    this.socket = null
    this.nextId = 0
    this.pending = new Map()
    this.handlers = new Map()
  }

  async connect(timeoutMs = 15000) {
    const socket = new WebSocket(this.wsUrl)
    await new Promise((resolveOpen, rejectOpen) => {
      const timer = setTimeout(() => {
        rejectOpen(new Error(`CDP WebSocket 连接超时（${this.wsUrl}）`))
      }, timeoutMs)
      socket.addEventListener('open', () => {
        clearTimeout(timer)
        resolveOpen()
      })
      socket.addEventListener('error', () => {
        clearTimeout(timer)
        rejectOpen(new Error(`CDP WebSocket 连接失败（${this.wsUrl}）`))
      })
    })
    this.socket = socket
    socket.addEventListener('message', (event) => this.onMessage(event))
    socket.addEventListener('close', () => {
      for (const entry of this.pending.values()) {
        entry.reject(new Error('CDP 连接已关闭'))
      }
      this.pending.clear()
    })
  }

  onMessage(event) {
    let message
    try {
      const raw =
        typeof event.data === 'string' ? event.data : Buffer.from(event.data).toString('utf8')
      message = JSON.parse(raw)
    } catch {
      return
    }
    if (message.id !== undefined) {
      const entry = this.pending.get(message.id)
      if (entry === undefined) {
        return
      }
      this.pending.delete(message.id)
      if (message.error) {
        entry.reject(new Error(`CDP ${entry.method} 失败: ${message.error.message}`))
      } else {
        entry.resolve(message.result ?? {})
      }
      return
    }
    const handlers = this.handlers.get(message.method)
    if (handlers !== undefined) {
      for (const handler of handlers) {
        try {
          handler(message.params ?? {})
        } catch {
          // 事件处理失败不影响主流程
        }
      }
    }
  }

  send(method, params = {}, timeoutMs = 30000) {
    if (this.socket === null || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('CDP 连接未就绪'))
    }
    const id = (this.nextId += 1)
    return new Promise((resolveSend, rejectSend) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        rejectSend(new Error(`CDP ${method} 超时（${timeoutMs}ms）`))
      }, timeoutMs)
      this.pending.set(id, {
        method,
        resolve: (value) => {
          clearTimeout(timer)
          resolveSend(value)
        },
        reject: (error) => {
          clearTimeout(timer)
          rejectSend(error)
        },
      })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }

  on(method, handler) {
    const list = this.handlers.get(method) ?? []
    list.push(handler)
    this.handlers.set(method, list)
  }

  close() {
    try {
      this.socket?.close()
    } catch {
      // 忽略关闭异常
    }
  }
}

// ---------------------------------------------------------------------------
// 运行时上下文
// ---------------------------------------------------------------------------
const opts = parseArgs(process.argv.slice(2))
const state = {
  baseUrl: opts.baseUrl,
  outDir: opts.out,
  screenshotsDir: join(opts.out, 'screenshots'),
  browserPath: '',
  browserVersion: '',
  browserProc: null,
  cdp: null,
  cdpPort: 0,
  profileDir: null,
  cardId: '',
  gameId: '',
  gameFromDetailId: '',
  draft: null,
  initialTurn: 0,
}

function requireCdp() {
  if (state.cdp === null) {
    throw new Error('CDP 尚未连接')
  }
  return state.cdp
}

// ---------------------------------------------------------------------------
// 页面操作助手
// ---------------------------------------------------------------------------
async function evaluate(expression) {
  const result = await requireCdp().send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  })
  if (result.exceptionDetails) {
    const description =
      result.exceptionDetails.exception?.description ??
      result.exceptionDetails.text ??
      '未知页面异常'
    throw new Error(`页面 JS 异常: ${description}`)
  }
  return result.result?.value
}

const expr = (selector) => JSON.stringify(selector)

function visibleExpr(selector) {
  return `(() => {
    const el = document.querySelector(${expr(selector)});
    if (el === null) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  })()`
}

async function waitFor(expression, label, timeoutMs = opts.timeout) {
  const deadline = Date.now() + timeoutMs
  let lastError = null
  while (Date.now() < deadline) {
    try {
      const value = await evaluate(expression)
      if (value) {
        return value
      }
    } catch (error) {
      lastError = error
    }
    await sleep(100)
  }
  const pageText = await pageTextSafe()
  throw new Error(
    `等待超时（${label}）: ${expression}` +
      (lastError ? `；最后错误: ${lastError.message}` : '') +
      `；页面文本片段: ${truncate(pageText, 300)}`,
  )
}

async function pageTextSafe() {
  try {
    return String(await evaluate('document.body ? document.body.innerText : ""'))
  } catch {
    return ''
  }
}

async function pageText() {
  return String(await evaluate('document.body ? document.body.innerText : ""'))
}

async function currentUrl() {
  return String(await evaluate('location.href'))
}

/**
 * 玩家可见文本不得出现内部 ID（R3 AC-17；028 遗留 P1-9 / S2 task-12）。
 *
 * 只看 `document.body.innerText`（玩家真正看得见的文字；`href`/折叠的 `<details>` 内容不计入）。
 * 覆盖人物卡/对局/角色/NPC/物品/房间六类内部 ULID（Crockford base32，含 ULID 大写字母）。
 * **S2 修复落地前该断言预期为红**，修复后必须转绿。
 */
const INTERNAL_ID_PATTERN = /\b(?:card|game|char|npc|item|room)_[0-9A-Za-z]{6,}\b/g

async function checkNoInternalIdsVisible(label) {
  const text = await pageText()
  const hits = text.match(INTERNAL_ID_PATTERN) ?? []
  check(`${label}：玩家可见文本无内部 ID`, hits.length === 0, hits.slice(0, 3).join('、'))
  return text
}

async function waitVisible(selector, label) {
  return await waitFor(visibleExpr(selector), `${label ?? selector} 可见`)
}

async function textOf(selector) {
  return await evaluate(`(() => {
    const el = document.querySelector(${expr(selector)});
    return el === null ? null : el.innerText;
  })()`)
}

async function optionalText(selector) {
  return await textOf(selector)
}

async function valueOf(selector) {
  return await evaluate(`(() => {
    const el = document.querySelector(${expr(selector)});
    return el === null ? null : el.value;
  })()`)
}

async function attrOf(selector, attribute) {
  return await evaluate(`(() => {
    const el = document.querySelector(${expr(selector)});
    return el === null ? null : el.getAttribute(${JSON.stringify(attribute)});
  })()`)
}

async function hrefOfLinkContaining(path) {
  return await evaluate(`(() => {
    const links = [...document.querySelectorAll('a')];
    const target = links.find((link) => (link.getAttribute('href') ?? '') === ${JSON.stringify(path)});
    return target === undefined ? null : target.getAttribute('href');
  })()`)
}

async function click(selector, label) {
  const clicked = await evaluate(`(() => {
    const el = document.querySelector(${expr(selector)});
    if (el === null) return false;
    if (typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'center' });
    el.click();
    return true;
  })()`)
  if (!clicked) {
    throw new Error(`找不到可点击元素：${label ?? selector}（${selector}）`)
  }
}

async function clickButtonByText(containerSelector, text, label) {
  const clicked = await evaluate(`(() => {
    const candidates = [...document.querySelectorAll(${expr(containerSelector)})];
    const target = candidates.find((node) => (node.innerText ?? '').trim() === ${JSON.stringify(text)});
    if (target === undefined) return false;
    if (typeof target.scrollIntoView === 'function') target.scrollIntoView({ block: 'center' });
    target.click();
    return true;
  })()`)
  if (!clicked) {
    throw new Error(`找不到按钮「${text}」：${label ?? containerSelector}`)
  }
}

/** 用原生 setter 写值 + 派发 input/change，兼容 Vue v-model（不是直接改 .value）。 */
async function setValue(selector, value, label) {
  const ok = await evaluate(`(() => {
    const el = document.querySelector(${expr(selector)});
    if (el === null) return false;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    el.focus();
    setter.call(el, ${JSON.stringify(value)});
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  })()`)
  if (!ok) {
    throw new Error(`无法写入输入框：${label ?? selector}（${selector}）`)
  }
}

async function navigate(pathOrUrl, label, readySelector) {
  const target = pathOrUrl.startsWith('http') ? pathOrUrl : `${state.baseUrl}${pathOrUrl}`
  const result = await requireCdp().send('Page.navigate', { url: target })
  if (result.errorText) {
    throw new Error(`导航失败: ${result.errorText}（${target}）`)
  }
  await waitFor(`document.readyState === 'complete'`, `${label ?? target} load`)
  if (readySelector) {
    await waitVisible(readySelector, label ?? target)
  }
}

async function setViewport(width, height, mobile = false) {
  await requireCdp().send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile,
  })
  await sleep(250)
}

async function clearViewport() {
  await requireCdp().send('Emulation.clearDeviceMetricsOverride')
  await sleep(150)
}

async function screenshot(name) {
  mkdirSync(state.screenshotsDir, { recursive: true })
  const result = await requireCdp().send('Page.captureScreenshot', { format: 'png', fromSurface: true })
  const file = join(state.screenshotsDir, name)
  writeFileSync(file, Buffer.from(result.data, 'base64'))
  screenshots.push(file)
  return file
}

/** 375px 无横向溢出（含 body；页面滚动容器溢出也算失败）。 */
async function checkNoHorizontalOverflow(label) {
  const metrics = await evaluate(`(() => {
    const doc = document.documentElement;
    const body = document.body;
    return {
      docScrollWidth: doc.scrollWidth,
      docClientWidth: doc.clientWidth,
      bodyScrollWidth: body === null ? 0 : body.scrollWidth,
      bodyClientWidth: body === null ? 0 : body.clientWidth,
      innerWidth: window.innerWidth,
    };
  })()`)
  const scrollWidth = Math.max(metrics.docScrollWidth, metrics.bodyScrollWidth)
  const clientWidth = Math.min(metrics.docClientWidth, metrics.innerWidth)
  check(`${label}：无横向溢出`, scrollWidth <= clientWidth + 1, JSON.stringify(metrics))
  return metrics
}

// ---------------------------------------------------------------------------
// HTTP 助手（服务端真值校验；不经浏览器缓存）
// ---------------------------------------------------------------------------
async function httpGet(path, timeoutMs = 15000) {
  return await fetch(`${state.baseUrl}${path}`, {
    redirect: 'manual',
    signal: AbortSignal.timeout(timeoutMs),
  })
}

async function getJson(path) {
  const response = await httpGet(path)
  if (!response.ok) {
    throw new Error(`GET ${path} → HTTP ${response.status}`)
  }
  return await response.json()
}

async function waitForApiTurn(gameId, expectedTurn, timeoutMs = opts.timeout) {
  const deadline = Date.now() + timeoutMs
  let lastView = null
  while (Date.now() < deadline) {
    try {
      const view = await getJson(`/api/v1/games/${encodeURIComponent(gameId)}/view`)
      lastView = view
      if (view.turn === expectedTurn) {
        return view
      }
    } catch {
      // 回合处理中可能瞬时失败，继续轮询
    }
    await sleep(120)
  }
  throw new Error(
    `等待回合 ${expectedTurn} 超时（最后 turn=${lastView?.turn ?? '未知'}）`,
  )
}

// ---------------------------------------------------------------------------
// S2 体验打磨验收：HTTP 预驱动 + 浏览器回合助手
// ---------------------------------------------------------------------------
async function postJson(path, body, label = path) {
  const response = await fetch(`${state.baseUrl}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': `e2e-s2-${crypto.randomUUID()}`,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(20000),
  })
  const text = await response.text()
  if (!response.ok) {
    throw new Error(`POST ${label} → HTTP ${response.status}: ${truncate(text, 200)}`)
  }
  return text === '' ? null : JSON.parse(text)
}

/** HTTP 新建对局（不占浏览器；用于开场历史/结局场景预置）。 */
async function createGameHttp(scenario) {
  const payload = await postJson('/api/v1/games', { seed: 42, scenario }, `创建对局(${scenario})`)
  return { gameId: String(payload.game_id), version: Number(payload.version) }
}

/** HTTP 顺序提交多个回合（断言每个回合 ok=True）；返回最后版本。 */
async function playTurnsHttp(gameId, commands, label = '预驱动') {
  const view = await getJson(`/api/v1/games/${encodeURIComponent(gameId)}/view`)
  let version = Number(view.version)
  for (const command of commands) {
    const payload = await postJson(
      '/api/v1/turns',
      { game_id: gameId, input: command, expected_version: version },
      `${label}回合(${command})`,
    )
    if (payload.ok !== true) {
      throw new Error(`${label}回合失败：${command} → ${truncate(JSON.stringify(payload), 200)}`)
    }
    version = Number(payload.version)
  }
  return version
}

/** 浏览器打开对局页并等 /view 状态载入。 */
async function openGamePage(gameId, label) {
  await navigate(`/game/${encodeURIComponent(gameId)}`, label, '#command-input')
  await waitFor(`document.querySelector('.game-meta') !== null`, `${label} 状态加载`)
}

/** 浏览器内提交一个有事件的回合，等 /view 回合数到达 expectedTurn。 */
async function submitTurnInBrowser(input, gameId, expectedTurn) {
  await setValue('#command-input', input, `指令 ${input}`)
  await waitFor(
    `document.querySelector('form.command-input button[type="submit"]')?.disabled === false`,
    `发送按钮可用（${input}）`,
  )
  await click('form.command-input button[type="submit"]', `发送「${input}」`)
  const view = await waitForApiTurn(gameId, expectedTurn)
  await waitFor(
    `document.body.innerText.includes(${JSON.stringify(`第 ${expectedTurn} 回合`)})`,
    `页面回合数更新为 ${expectedTurn}`,
  )
  return view
}

/**
 * 浏览器内提交只读回合（零事件、回合数不增）：处理完成判定 = 冒险记录 +1 行 且 输入框恢复可编辑。
 *
 * 口径（H4 前端修复后）：新局「开场」派生行会保留到真正的推进回合（turnId≥2），首个只读回合
 * 会**新增** 1 行叙事（1→2）；同时等 `#command-input` 的 readOnly 释放（busy/ended 结束）。
 * 不能等 `.turn-text-body`（生产构建下 TurnTextStream 仅在开发诊断面板内渲染，玩家不可见）。
 */
async function submitReadonlyTurnInBrowser(input) {
  // 先等冒险记录就绪（新局开场行出现），避免「加载中 vs 追加」竞态导致计数偏差。
  await waitFor(
    `document.querySelectorAll('.narrative-history li').length >= 1`,
    '冒险记录就绪（至少开场行）',
  )
  const beforeCount = Number(
    await evaluate(`document.querySelectorAll('.narrative-history li').length`),
  )
  const beforeText = String(
    await evaluate(`document.querySelector('.narrative-history')?.innerText ?? ''`),
  )
  await setValue('#command-input', input, `只读指令 ${input}`)
  await waitFor(
    `document.querySelector('form.command-input button[type="submit"]')?.disabled === false`,
    `发送按钮可用（${input}）`,
  )
  await click('form.command-input button[type="submit"]', `发送「${input}」`)
  await waitFor(
    `document.querySelectorAll('.narrative-history li').length === ${beforeCount + 1}`,
    `「${input}」冒险记录 +1 行`,
  )
  await waitFor(
    `(() => {
      const el = document.querySelector('.narrative-history');
      if (el === null) return false;
      const text = (el.innerText ?? '').trim();
      return text !== '' && text !== ${JSON.stringify(beforeText.trim())};
    })()`,
    `「${input}」冒险记录正文更新`,
  )
  await waitFor(
    `(() => {
      const el = document.querySelector('#command-input');
      return el !== null && el.readOnly === false;
    })()`,
    `「${input}」输入框恢复可编辑`,
  )
  const afterCount = Number(
    await evaluate(`document.querySelectorAll('.narrative-history li').length`),
  )
  check(
    `只读回合「${input}」在冒险记录新增 1 行（开场行保留）`,
    afterCount === beforeCount + 1,
    `${beforeCount}→${afterCount}`,
  )
  await sleep(100)
}

// ---------------------------------------------------------------------------
// console 错误采集（Runtime + Log）
// ---------------------------------------------------------------------------
function recordConsole(kind, text, extra = {}) {
  consoleEvents.push({
    kind,
    text: truncate(text, 500),
    url: extra.url ?? '',
    source: extra.source ?? '',
    ts: new Date().toISOString(),
  })
}

function installConsoleCapture(cdp) {
  cdp.on('Runtime.consoleAPICalled', (params) => {
    if (params.type !== 'error' && params.type !== 'assert') {
      return
    }
    const text = (params.args ?? [])
      .map((argument) => argument.value ?? argument.description ?? argument.unserializableValue ?? '')
      .join(' ')
    recordConsole(params.type === 'assert' ? 'console.assert' : 'console.error', text)
  })
  cdp.on('Runtime.exceptionThrown', (params) => {
    const details = params.exceptionDetails ?? {}
    recordConsole(
      'uncaught-exception',
      details.exception?.description ?? details.text ?? '未知未捕获异常',
      { url: details.url ?? '' },
    )
  })
  cdp.on('Log.entryAdded', (params) => {
    const entry = params.entry ?? {}
    if (entry.level !== 'error') {
      return
    }
    recordConsole('log.error', entry.text ?? '', { url: entry.url ?? '', source: entry.source ?? '' })
  })
}

/**
 * 预期内的失败响应：e2e 主动触发 429（验证限流中文提示）时，浏览器会把 HTTP 错误记入
 * network 日志；这是被测行为本身，不是页面 JS 错误，按 URL + 状态码精确过滤。
 */
const EXPECTED_NETWORK_ERRORS = [
  { urlIncludes: '/api/v1/character-cards/generate', status: '429' },
]

/** 已知良性噪声过滤：favicon 404、页面切换时被中止的资源请求、主动触发的预期 429。 */
function isBenignConsoleError(entry) {
  const text = `${entry.text ?? ''} ${entry.url ?? ''}`
  if (text.includes('favicon')) {
    return true
  }
  if (text.includes('ERR_ABORTED')) {
    return true
  }
  const url = String(entry.url ?? '')
  return EXPECTED_NETWORK_ERRORS.some(
    (expected) =>
      (url.includes(expected.urlIncludes) || text.includes(expected.urlIncludes)) &&
      (text.includes(`a status of ${expected.status}`) ||
        text.includes(`status ${expected.status}`)),
  )
}

// ---------------------------------------------------------------------------
// 主流程
// ---------------------------------------------------------------------------
async function runFlow() {
  let worldBookId = ''
  await step('env.server', '服务健康检查（uvicorn 由外部启动，默认离线）', async () => {
    const deadline = Date.now() + 20000
    let lastError = null
    let health = null
    while (Date.now() < deadline) {
      try {
        const response = await httpGet('/healthz', 3000)
        if (response.status === 200) {
          health = response
          break
        }
        lastError = new Error(`GET /healthz → HTTP ${response.status}`)
      } catch (error) {
        lastError = error
      }
      await sleep(200)
    }
    if (health === null) {
      throw new Error(
        `服务不可用（${state.baseUrl}）：${lastError?.message ?? '未知错误'}；` +
          '请先启动 uvicorn（参考 .work/r3-team/s1/run-e2e.ps1）',
      )
    }
    check('GET /healthz = 200', health.status === 200, `status=${health.status}`)
    return `base-url=${state.baseUrl}`
  })

  await step('deep-link.http', '/cards/generate 深链直接返回 SPA index.html', async () => {
    const response = await httpGet('/cards/generate')
    const body = await response.text()
    check('GET /cards/generate = 200', response.status === 200, `status=${response.status}`)
    check(
      'content-type 为 text/html',
      (response.headers.get('content-type') ?? '').includes('text/html'),
      response.headers.get('content-type') ?? '',
    )
    check('返回 SPA index.html（含 #app）', body.includes('id="app"'), `body 长度=${body.length}`)
    check(
      'SPA 响应带同源 CSP',
      (response.headers.get('content-security-policy') ?? '').includes("default-src 'self'"),
      response.headers.get('content-security-policy') ?? '',
    )
    return `status=${response.status}, bytes=${body.length}`
  })

  await step('page.generate.open', '浏览器打开 /cards/generate：离线提示显著', async () => {
    await navigate('/cards/generate', '生成页', '[data-testid="generate-card-page"]')
    const hint = await textOf('[data-testid="offline-hint"]')
    check('离线提示存在', typeof hint === 'string' && hint.trim().length > 0, String(hint))
    check('离线提示含「离线」', String(hint).includes('离线'), truncate(hint, 160))
    check('离线提示含「模板」', String(hint).includes('模板'), truncate(hint, 160))
    const visible = await evaluate(visibleExpr('[data-testid="offline-hint"]'))
    check('离线提示在首屏可见', visible, '')
    const heading = await pageText()
    check('页面标题含「生成人物卡」', heading.includes('生成人物卡'), '')
    await screenshot('01-generate-offline-hint-1280.png')
    return truncate(String(hint), 120)
  })

  await step('page.generate.375', '375px 无横向溢出（生成页）', async () => {
    await setViewport(MOBILE_VIEWPORT.width, MOBILE_VIEWPORT.height, true)
    const metrics = await checkNoHorizontalOverflow('生成页 375px')
    await screenshot('02-generate-375.png')
    await clearViewport()
    return JSON.stringify(metrics)
  })

  await step('form.brief-limit', 'brief 码点计数 ≤1000：超限禁用生成', async () => {
    await waitVisible('[data-testid="brief-input"]', 'brief 输入框')
    const initialCounter = String(await textOf('[data-testid="brief-counter"]')).trim()
    check('初始计数为 0/1000', initialCounter === '0/1000', initialCounter)

    await setValue('[data-testid="brief-input"]', 'a'.repeat(1001), 'brief 超限输入')
    await waitFor(
      `(document.querySelector(${expr('[data-testid="brief-counter"]')})?.innerText ?? '').trim() === '1001/1000'`,
      '计数变为 1001/1000',
    )
    const overDisabled = await evaluate(
      `document.querySelector(${expr('[data-testid="generate-button"]')})?.disabled === true`,
    )
    check('超限时生成按钮 disabled', overDisabled, '1001 码点')

    await setValue('[data-testid="brief-input"]', BRIEF, 'brief 合法输入')
    await waitFor(
      `document.querySelector(${expr('[data-testid="generate-button"]')})?.disabled === false`,
      '生成按钮恢复可用',
    )
    const counter = String(await textOf('[data-testid="brief-counter"]')).trim()
    const expected = `${[...BRIEF].length}/1000`
    check('合法 brief 计数正确（码点）', counter === expected, `实际=${counter} 期望=${expected}`)
    return `1001→禁用；合法 → ${counter}`
  })

  await step('generate.template-draft', '生成 → 草稿预览 +「模板草稿」徽章（默认离线兜底）', async () => {
    await click('[data-testid="generate-button"]', '生成人物卡')
    await waitVisible('[data-testid="draft-preview"]', '草稿预览')
    const badge = String(await textOf('[data-testid="draft-badge"]'))
    check('草稿徽章为「模板草稿」', badge.includes('模板草稿'), badge)
    const errorText = await optionalText('[data-testid="generate-error"]')
    check(
      '生成无错误提示',
      errorText === null || String(errorText).trim() === '',
      truncate(String(errorText ?? '（无）'), 120),
    )
    check('保存前不自动跳转/建局', (await currentUrl()).includes('/cards/generate'), await currentUrl())

    const raw = await valueOf('[data-testid="draft-json"]')
    check('JSON 编辑域已预填草稿', typeof raw === 'string' && raw.trim().length > 2, `长度=${String(raw).length}`)
    let draft = null
    try {
      draft = JSON.parse(String(raw))
    } catch (error) {
      check('草稿 JSON 可解析', false, error.message)
    }
    state.draft = draft
    const expectedFields = [
      'schema_version',
      'name',
      'attributes',
      'hp',
      'starting_items',
      'background',
      'appearance',
      'source',
      'generated_by',
    ]
    check(
      '草稿恰为 9 字段',
      JSON.stringify(Object.keys(draft).sort()) === JSON.stringify(expectedFields.slice().sort()),
      Object.keys(draft).join(','),
    )
    check('草稿 schema_version=1.0', draft.schema_version === '1.0', String(draft.schema_version))
    check('草稿 source=manual（模板兜底）', draft.source === 'manual', String(draft.source))
    check('草稿 generated_by=null', draft.generated_by === null, String(draft.generated_by))
    check(
      '草稿属性恰为 7 键',
      draft.attributes !== null && typeof draft.attributes === 'object'
        ? Object.keys(draft.attributes).length === 7
        : false,
      JSON.stringify(draft.attributes),
    )
    check('草稿名非空', typeof draft.name === 'string' && draft.name.trim() !== '', String(draft.name))
    const preview = String(await textOf('[data-testid="draft-preview"]'))
    check('预览展示草稿名', preview.includes(draft.name), truncate(preview, 160))
    // P1-8：草稿 9 字段「初始物品」必须显示中文名，不能暴露 item.* 模板键。
    check(
      '草稿预览初始物品显示中文「火把」',
      preview.includes('火把') && !preview.includes(TEMPLATE_ITEM_KEY),
      truncate(preview, 160),
    )
    // R3 AC-17：生成页正文（草稿 9 字段/提示）不得出现内部 ID 或 item.* 模板键。
    const generatePageText = await pageText()
    check('生成页无 item.* 模板键可见', !generatePageText.includes('item.'), '')
    await checkNoInternalIdsVisible('生成页草稿区')
    await screenshot('03-draft-template.png')
    return `name=${draft.name}, source=${draft.source}, 9 字段`
  })

  await step('form.revalidate-gate', 'JSON 非法时保存禁用；恢复合法后可保存', async () => {
    const raw = await valueOf('[data-testid="draft-json"]')
    await setValue('[data-testid="draft-json"]', '{"broken": true,', '非法 JSON')
    await click('[data-testid="revalidate-button"]', '重新校验（非法 JSON）')
    await waitFor(
      `document.querySelector(${expr('[data-testid="save-card-button"]')})?.disabled === true`,
      '非法 JSON 后保存按钮 disabled',
    )
    check('JSON 非法时保存按钮禁用', true, '')
    await setValue('[data-testid="draft-json"]', String(raw), '恢复合法 JSON')
    await click('[data-testid="revalidate-button"]', '重新校验（合法 JSON）')
    await waitFor(
      `document.querySelector(${expr('[data-testid="save-card-button"]')})?.disabled === false`,
      '合法 JSON 后保存按钮恢复',
    )
    check('JSON 合法时保存按钮可用', true, '')
    return '非法→禁用 / 合法→可用'
  })

  await step('save.card', '保存到卡库：成功区 + 显式入口 + 服务端读回', async () => {
    check('保存前仍停留在生成页（不自动建局/跳转）', (await currentUrl()).includes('/cards/generate'), await currentUrl())
    await click('[data-testid="save-card-button"]', '保存到卡库')
    await waitVisible('[data-testid="save-result"]', '保存成功区')
    const saveText = String(await textOf('[data-testid="save-result"]'))
    check('保存成功区文案含「保存」', saveText.includes('保存'), truncate(saveText, 160))
    check('保存成功区含「查看卡片」入口', saveText.includes('查看卡片'), truncate(saveText, 160))
    check('保存成功区含「按此卡开局」入口', saveText.includes('开局'), truncate(saveText, 160))
    // S2 起：028 遗留 P1-9「保存成功区展示内部 card_ ID」必须消失（R3 AC-17）。
    await checkNoInternalIdsVisible('生成页保存成功区')

    const href = await attrOf('[data-testid="open-card"]', 'href')
    check(
      '「查看卡片」链接指向 /cards/{card_id}',
      typeof href === 'string' && href.startsWith('/cards/'),
      String(href),
    )
    state.cardId = String(href).split('/').filter(Boolean).pop() ?? ''
    check('card_id 形如 card_xxx', /^card_[A-Za-z0-9_-]+$/.test(state.cardId), state.cardId)
    const startVisible = await evaluate(visibleExpr('[data-testid="start-game-with-card"]'))
    check('「按此卡开局」按钮可见', startVisible, '')

    const detail = await getJson(`/api/v1/character-cards/${encodeURIComponent(state.cardId)}`)
    check(
      '服务端卡片详情 200 且名称与草稿一致',
      detail?.card?.name === state.draft.name,
      `服务端=${detail?.card?.name} 草稿=${state.draft.name}`,
    )
    check('服务端卡片 source=manual', detail?.card?.source === 'manual', String(detail?.card?.source))
    check('服务端卡片 9 字段', Object.keys(detail?.card ?? {}).length === 9, Object.keys(detail?.card ?? {}).join(','))
    await screenshot('04-saved.png')
    return `card_id=${state.cardId}, name=${detail?.card?.name}`
  })

  await step('start.game-with-card', '「按此卡开局」→ /game/{id}，HP/背包来自该卡', async () => {
    await click('[data-testid="start-game-with-card"]', '按此卡开局')
    await waitFor(`location.pathname.startsWith('/game/')`, '跳转对局页')
    const url = await currentUrl()
    state.gameId = new URL(url).pathname.split('/').filter(Boolean)[1] ?? ''
    check('对局 URL 含 game_id', /^game_[A-Za-z0-9_-]+$/.test(state.gameId), state.gameId)
    await waitVisible('#command-input', '指令输入框')
    await waitFor(`document.querySelector('.game-meta') !== null`, '对局状态元信息')

    const view = await getJson(`/api/v1/games/${encodeURIComponent(state.gameId)}/view`)
    check(
      '/view max_hp 来自草稿卡（模板兜底 20）',
      view.max_hp === state.draft.attributes.max_hp,
      `view=${view.max_hp} 草稿=${state.draft.attributes.max_hp}`,
    )
    check('/view hp 来自草稿卡', view.hp === state.draft.hp, `view=${view.hp} 草稿=${state.draft.hp}`)
    const inventoryNames = (view.inventory ?? []).map((item) => item.name)
    check('背包含草稿初始物品「火把」', inventoryNames.includes('火把'), inventoryNames.join('、'))
    const errorBanner = await optionalText('.error-banner')
    check('对局页无错误横幅', errorBanner === null || String(errorBanner).trim() === '', truncate(String(errorBanner ?? '（无）'), 120))
    // P2-1：生产构建不得渲染「诊断信息（开发用）」面板。
    const diagnostics = await evaluate(`document.querySelector('.diagnostics') === null`)
    check('生产构建不渲染诊断面板（P2-1）', diagnostics, '')
    const gamePageText = await pageText()
    check('对局页无「诊断信息（开发用）」文本', !gamePageText.includes('诊断信息'), '')
    // S2 起：P2-1「诊断信息（开发用）」在生产构建不渲染，对局页不应再有可见内部 ID。
    await checkNoInternalIdsVisible('对局页')
    state.initialTurn = view.turn
    await screenshot('05-game-start.png')
    return `game_id=${state.gameId}, turn=${view.turn}, max_hp=${view.max_hp}`
  })

  await step('turn.submit', '提交 1 回合：服务端回合 +1、页面叙事更新', async () => {
    const beforeText = await pageText()
    await setValue('#command-input', '拿起火把', '回合指令')
    await waitFor(
      `document.querySelector('form.command-input button[type="submit"]')?.disabled === false`,
      '发送按钮可用',
    )
    await click('form.command-input button[type="submit"]', '发送')
    const view = await waitForApiTurn(state.gameId, state.initialTurn + 1)
    await waitFor(
      `document.body.innerText.includes(${JSON.stringify(`第 ${state.initialTurn + 1} 回合`)})`,
      '页面回合数更新',
    )
    const afterText = await pageText()
    check(
      '页面叙事文本已更新',
      afterText !== beforeText && afterText.length > 0,
      `前=${beforeText.length} 后=${afterText.length} 字符`,
    )
    const gameView = await getJson(`/api/v1/games/${encodeURIComponent(state.gameId)}`)
    const narrative = String(gameView.last_text ?? '').trim()
    check('服务端 latest 叙事非空', narrative.length > 0, truncate(narrative, 120))
    check(
      '页面展示服务端最新叙事',
      afterText.includes(narrative.slice(0, Math.min(8, narrative.length))),
      `叙事片段=${truncate(narrative, 60)}`,
    )
    const errorBanner = await optionalText('.error-banner')
    check('回合后无错误横幅', errorBanner === null || String(errorBanner).trim() === '', truncate(String(errorBanner ?? '（无）'), 120))
    // R3 AC-17：冒险记录（叙事文本）不得出现内部 ID。
    await checkNoInternalIdsVisible('对局页（回合后冒险记录）')
    await screenshot('06-game-turn.png')
    return `turn=${view.turn}, 文本长度 ${beforeText.length}→${afterText.length}`
  })

  await step('page.game.375', '375px 无横向溢出（对局页）', async () => {
    await setViewport(MOBILE_VIEWPORT.width, MOBILE_VIEWPORT.height, true)
    const metrics = await checkNoHorizontalOverflow('对局页 375px')
    await screenshot('07-game-375.png')
    await clearViewport()
    return JSON.stringify(metrics)
  })

  await step('library.and-detail', '卡库列表 → 详情 9 字段 → 详情页按卡开局', async () => {
    await navigate('/cards', '卡库页', '.cards-view')
    await waitFor(`document.querySelectorAll('.card-list-item').length > 0`, '卡库列表出现卡片')
    const libraryText = await pageText()
    check('卡库列表含新卡名称', libraryText.includes(state.draft.name), `name=${state.draft.name}`)
    check('卡库列表显示卡片总数', /共\s*\d+\s*张/.test(libraryText), '')
    // task-12：卡库列表不得再展示内部 card_ ID（R3 AC-17）。
    await checkNoInternalIdsVisible('卡库列表')
    const href = await hrefOfLinkContaining(`/cards/${state.cardId}`)
    check('卡库含该卡详情链接', href === `/cards/${state.cardId}`, String(href))
    await screenshot('08-card-library.png')

    await click(`.card-list-item a.card-name[href="${`/cards/${state.cardId}`}"]`, '卡片名称链接')
    await waitFor(`location.pathname === '/cards/${state.cardId}'`, '跳转卡片详情页')
    await waitVisible('.card-fields', '卡片详情 9 字段')
    const detailText = await pageText()
    check('详情页含卡片名称', detailText.includes(state.draft.name), `name=${state.draft.name}`)
    for (const label of ['名称', 'HP', '属性', '背景', '来源']) {
      check(`详情页展示「${label}」字段`, detailText.includes(label), '')
    }
    // P1-8：卡详情的初始物品同样要中文（与生成页草稿预览口径一致）。
    check(
      '卡详情初始物品显示中文「火把」',
      detailText.includes('火把') && !detailText.includes(TEMPLATE_ITEM_KEY),
      truncate(detailText, 160),
    )
    // task-12：卡详情标题/404 文案/提示均不得含内部 card_ ID（R3 AC-17）。
    await checkNoInternalIdsVisible('人物卡详情')
    await screenshot('09-card-detail.png')

    await clickButtonByText('.card-detail-view button', '开始游戏', '详情页开始游戏')
    await waitFor(`location.pathname.startsWith('/game/')`, '详情页开局跳转对局页')
    const url = await currentUrl()
    state.gameFromDetailId = new URL(url).pathname.split('/').filter(Boolean)[1] ?? ''
    check('详情页开局得到新对局', state.gameFromDetailId !== state.gameId, `${state.gameFromDetailId}`)
    await waitVisible('#command-input', '新对局指令输入框')
    await waitFor(`document.querySelector('.game-meta') !== null`, '新对局状态加载')
    const view = await getJson(`/api/v1/games/${encodeURIComponent(state.gameFromDetailId)}/view`)
    check(
      '详情页开局同样按该卡（HP 一致）',
      view.hp === state.draft.hp && view.max_hp === state.draft.attributes.max_hp,
      `hp=${view.hp}/${view.max_hp}`,
    )
    await screenshot('10-game-from-detail.png')
    return `detail→game_id=${state.gameFromDetailId}, hp=${view.hp}/${view.max_hp}`
  })

  await step(
    's2.opening-history',
    'P2-2 新局开场历史：空记录第 1 条「开场」，首回合后不重复',
    async () => {
      const created = await createGameHttp('story')
      state.openingGameId = created.gameId
      const view = await getJson(`/api/v1/games/${encodeURIComponent(created.gameId)}/view`)
      const description = String(view.room?.description ?? '')
      const goalTitle = String(view.goal?.title ?? '')
      check('HTTP 新局 room.description 非空', description.length > 0, '')
      check('HTTP 新局 goal 非空', goalTitle.length > 0, '')
      await openGamePage(created.gameId, '新局对局页（开场历史）')
      await waitVisible('.history-opening', '开场历史行')
      const openingCount = await evaluate(`document.querySelectorAll('.history-opening').length`)
      check('空局冒险记录恰有 1 条「开场」', openingCount === 1, String(openingCount))
      const openingText = String(await textOf('.history-opening'))
      check('开场行标签为「开场」', openingText.includes('开场'), truncate(openingText, 120))
      check(
        '开场正文含房间描述',
        openingText.includes(description.slice(0, 12)),
        truncate(openingText, 160),
      )
      check(
        '开场正文含目标引导',
        openingText.includes(`目标：${goalTitle}`),
        truncate(openingText, 160),
      )
      await screenshot('12-opening-history-1280.png')
      await checkNoInternalIdsVisible('对局页（开场历史）')
      await submitTurnInBrowser('向东走', created.gameId, 2)
      const openingAfter = await evaluate(`document.querySelectorAll('.history-opening').length`)
      check('首回合后「开场」行不再出现（不重复）', openingAfter === 0, String(openingAfter))
      return `opening len=${description.length}, goal=${goalTitle}`
    },
  )

  await step('s2.readonly-hint', 'P2-3 只读回合提示（对局页 + 回放页）', async () => {
    const hint = String(await textOf('.history-readonly-hint'))
    check(
      '对局页只读提示存在（只读正文只在当前会话显示、不写入回放）',
      hint.includes('只读') && hint.includes('不写入回放'),
      truncate(hint, 120),
    )
    const gameId = state.openingGameId
    await navigate(`/game/${encodeURIComponent(gameId)}/replay`, '回放页', '.replay-view')
    await waitFor(`document.querySelectorAll('.replay-note').length >= 2`, '回放页只读提示')
    const replayText = await pageText()
    check(
      '回放页提示含「不会出现在回放里」',
      replayText.includes('不会出现在回放里'),
      truncate(replayText, 160),
    )
    check(
      '回放页提示含「只在当前会话的冒险记录中显示」',
      replayText.includes('只在当前会话'),
      truncate(replayText, 160),
    )
    // task-12：回放页（标题/事件面板角色行）不得出现内部 game_/npc_/char_ 等 ID。
    await checkNoInternalIdsVisible('回放页')
    await screenshot('13-replay-readonly-hint-1280.png')
    const replayNote = String(await textOf('.replay-note')).trim()
    return truncate(replayNote, 120)
  })

  await step(
    's2.badge.no-event',
    'P2-6 来源徽章：无事件回合显示中性「来源未知」，事件回合恢复「离线模板」',
    async () => {
      const created = await createGameHttp('m2')
      state.badgeGameId = created.gameId
      await openGamePage(created.gameId, 'm2 对局页（来源徽章）')
      await waitVisible('.source-badge', '来源徽章')
      const initialBadge = String(await textOf('.source-badge')).trim()
      check('新局空来源徽章为中性「来源未知」', initialBadge === '来源未知', initialBadge)
      await submitReadonlyTurnInBrowser('环顾四周')
      const afterLookBadge = String(await textOf('.source-badge')).trim()
      check('只读回合（零事件）不误标「离线模板」', afterLookBadge === '来源未知', afterLookBadge)
      await screenshot('14-badge-no-event-1280.png')
      const view = await getJson(`/api/v1/games/${encodeURIComponent(created.gameId)}/view`)
      await submitTurnInBrowser('向北走', created.gameId, Number(view.turn) + 1)
      const afterMoveBadge = String(await textOf('.source-badge')).trim()
      check('有事件回合恢复「离线模板」徽章', afterMoveBadge === '离线模板', afterMoveBadge)
      return `空→${initialBadge}；look→${afterLookBadge}；move→${afterMoveBadge}`
    },
  )

  await step(
    's2.ending.summary',
    'AC-34/AC-10d 结局：通关总结 + 结局进冒险记录（HTTP 预驱动 12 回合）',
    async () => {
      const created = await createGameHttp('story')
      await playTurnsHttp(created.gameId, STORY_FLOW, '结局链')
      const view = await getJson(`/api/v1/games/${encodeURIComponent(created.gameId)}/view`)
      check('预驱动 12 回合后状态 ended', view.status === 'ended', String(view.status))
      check(
        'ending_text 非空',
        typeof view.ending_text === 'string' && view.ending_text.length > 0,
        truncate(String(view.ending_text ?? ''), 80),
      )
      const objectives = view.goal?.objectives ?? []
      check(
        '目标 3/3 全部完成',
        objectives.length > 0 && objectives.every((item) => item.done === true),
        JSON.stringify(objectives.map((item) => item.done)),
      )
      state.endingGameId = created.gameId
      await openGamePage(created.gameId, '结局对局页')
      await waitVisible('.ending-summary', '结局通关总结')
      const summary = String(await textOf('.ending-summary')).trim()
      const doneCount = objectives.filter((item) => item.done).length
      check(`通关总结含「本局 ${view.turn} 回合」`, summary.includes(`本局 ${view.turn} 回合`), summary)
      check(
        `通关总结含「目标 ${doneCount}/${objectives.length}」`,
        summary.includes(`目标 ${doneCount}/${objectives.length}`),
        summary,
      )
      check(
        `通关总结含「生命值 ${view.hp}/${view.max_hp}」`,
        summary.includes(`生命值 ${view.hp}/${view.max_hp}`),
        summary,
      )
      // 冒险记录由 replay 异步重建：等「结局」派生行真正渲染后再断言（避免 loading 竞态）。
      await waitVisible('.history-ending', '结局历史行')
      const endingRowCount = await evaluate(`document.querySelectorAll('.history-ending').length`)
      check('冒险记录恰有 1 条「结局」行（不重复渲染）', endingRowCount === 1, String(endingRowCount))
      const endingRowText = String(await textOf('.history-ending'))
      const endingSnippet = String(view.ending_text).slice(0, 12)
      check(
        '结局行进冒险记录且含结局正文',
        endingRowText.includes(endingSnippet),
        truncate(endingRowText, 160),
      )
      check('结局行标签为「结局」', endingRowText.includes('结局'), truncate(endingRowText, 80))
      const historyEndingOccurrences = await evaluate(`(() => {
        const history = document.querySelector('.narrative-history');
        if (history === null) return 0;
        return history.innerText.split(${JSON.stringify(endingSnippet)}).length - 1;
      })()`)
      check('结局正文在冒险记录中只出现 1 次', historyEndingOccurrences === 1, String(historyEndingOccurrences))
      check('结局页无诊断面板', (await evaluate(`document.querySelector('.diagnostics') === null`)) === true, '')
      await checkNoInternalIdsVisible('结局页')
      await screenshot('15-ending-1280.png')
      await setViewport(MOBILE_VIEWPORT.width, MOBILE_VIEWPORT.height, true)
      const metrics = await checkNoHorizontalOverflow('结局页 375px')
      await screenshot('16-ending-375.png')
      await clearViewport()
      return `summary=${summary}；${JSON.stringify(metrics)}`
    },
  )

  await step('generate.rate-limit', '限流提示：超过 5 次/分钟显示可读中文「请 N 秒后重试」', async () => {
    // 限流按「IP（+可选 session）」进程内滑窗；同一 uvicorn 进程内继续生成即可触发。
    await navigate('/cards/generate', '生成页（限流用例）', '[data-testid="generate-card-page"]')
    await setValue('[data-testid="brief-input"]', '限流验收用的旅人，测试生成过于频繁时的提示', 'brief')
    let errorText = ''
    let attempts = 0
    for (let index = 0; index < 8; index += 1) {
      await click('[data-testid="generate-button"]', '生成人物卡')
      attempts += 1
      await sleep(120)
      await waitFor(
        `document.querySelector(${expr('[data-testid="generate-button"]')})?.disabled === false`,
        `第 ${attempts} 次生成请求结束`,
      )
      const current = await optionalText('[data-testid="generate-error"]')
      if (current !== null && String(current).trim() !== '') {
        errorText = String(current)
        break
      }
    }
    check('触发 429 限流提示', errorText !== '', `连续 ${attempts} 次生成仍无错误提示`)
    check('限流文案为中文且含「秒后重试」', errorText.includes('秒后重试'), truncate(errorText, 160))
    // 不做自动重试：429 后错误提示保持，2.5s 后既不清空也不会自动出现新草稿。
    const errorSnapshot = String(await optionalText('[data-testid="generate-error"]'))
    await sleep(2500)
    const stillError = await optionalText('[data-testid="generate-error"]')
    check(
      '429 后不自动重试（2.5s 后错误提示仍在）',
      stillError !== null && String(stillError).trim() === errorSnapshot.trim(),
      truncate(String(stillError ?? '（已消失）'), 120),
    )
    await screenshot('11-rate-limit.png')
    return `第 ${attempts} 次生成触发限流：${truncate(errorText.replace(/\s+/g, ' '), 120)}`
  })

  // -------------------------------------------------------------------------
  // H4：世界书（导入 → Home 绑定开局 → 对局「世界设定」面板 → 375px → 删除）
  // -------------------------------------------------------------------------
  const WORLD_BOOK_NAME = 'E2E 余烬设定'
  const WORLD_BOOK_PAYLOAD = {
    name: WORLD_BOOK_NAME,
    entries: [
      {
        uid: 1,
        keys: ['断桥', '雪', '河'],
        content: '断桥之下是封冻的河面，桥板缺了三块，雪从缺口里直灌上来，风声像有人在哭。',
        order: 10,
      },
      {
        uid: 2,
        keys: ['老兵', '守夜人'],
        content: '守夜人老兵在哨站守了很多年，火盆边永远放着一根旧烟斗，他记得每一个没能回来的人。',
        order: 20,
      },
    ],
  }

  await step('wb.import', '世界书：UI 粘贴 JSON → 校验 → 导入并跳转详情', async () => {
    await navigate('/world-books/import', '导入世界书页', '.world-book-import-view')
    await setValue(
      '.card-json-input',
      JSON.stringify(WORLD_BOOK_PAYLOAD, null, 2),
      '世界书 JSON',
    )
    await clickButtonByText('.world-book-import-form button', '校验（只看不存）', '校验世界书')
    await waitVisible('.world-book-preview-panel', '格式识别通过面板')
    const previewText = String(await textOf('.world-book-preview-panel'))
    check(
      '校验通过并展示书名/条目数',
      previewText.includes(WORLD_BOOK_NAME) && previewText.includes('2 条'),
      truncate(previewText, 160),
    )
    await clickButtonByText('.world-book-import-form button', '导入并保存', '导入世界书')
    await waitFor(
      `location.pathname.startsWith('/world-books/') && !location.pathname.endsWith('/import')`,
      '导入成功后跳转世界书详情页',
    )
    await waitVisible('.world-book-detail-view', '世界书详情页')
    // 详情页先渲染壳、条目是异步 GET：CI 上可能仍显示「正在读取世界书…」，等加载完成再断言。
    // 以「标题已是书名 + 条目表至少 2 行 + 无加载态」为完成标志（纯文本匹配在 SPA 路由切换
    // 瞬间可能命中旧页面残留文本，CI 曾因此误判）。
    await waitFor(
      `(() => {
        const root = document.querySelector('.world-book-detail-view');
        if (root === null) return false;
        const text = root.innerText ?? '';
        if (text.includes('正在读取世界书')) return false;
        const title = document.querySelector('.world-books-title')?.textContent ?? '';
        const rows = document.querySelectorAll('.world-entry-list .world-entry').length;
        return title.includes(${JSON.stringify(WORLD_BOOK_NAME)}) && rows >= 2;
      })()`,
      '世界书详情加载完成（标题+条目表）',
    )
    const detailTitle = String(await textOf('.world-books-title'))
    const detailRows = Number(
      await evaluate(`document.querySelectorAll('.world-entry-list .world-entry').length`),
    )
    const detailText = String(await pageText())
    check(
      '详情页展示书名与条目触发词/内容',
      detailTitle.includes(WORLD_BOOK_NAME) && detailRows >= 2 && detailText.includes('断桥'),
      `${detailTitle} / rows=${detailRows}`,
    )
    await checkNoInternalIdsVisible('世界书详情页')
    worldBookId = String(
      await evaluate(`location.pathname.split('/').filter(Boolean).pop() ?? ''`),
    )
    check('详情 URL 含 world_book_id', worldBookId !== '', worldBookId)
    await screenshot('17-world-book-detail-1280.png')
    return `world_book_id=${worldBookId}`
  })

  await step('wb.bind-and-game', '世界书：Home 选择世界书开局 → 对局内「世界设定」面板触发', async () => {
    await navigate('/', '首页（世界书绑定）', '.home-form')
    await waitFor(
      `(() => {
        const labels = [...document.querySelectorAll('.home-form label.field')];
        const label = labels.find((node) => (node.innerText ?? '').includes('世界书（可选）'));
        return label !== undefined && label.querySelectorAll('select option').length >= 2;
      })()`,
      '首页世界书下拉出现可选项',
    )
    const selected = await evaluate(`(() => {
      const labels = [...document.querySelectorAll('.home-form label.field')];
      const label = labels.find((node) => (node.innerText ?? '').includes('世界书（可选）'));
      if (label === undefined) return null;
      const select = label.querySelector('select');
      const option = [...select.options].find((item) => (item.text ?? '').includes(${JSON.stringify(WORLD_BOOK_NAME)}));
      if (option === undefined) return null;
      select.value = option.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      return select.value;
    })()`)
    check('世界书下拉已选中导入的书', selected !== null && String(selected) !== '', String(selected))
    await clickButtonByText('.home-form button', '开始冒险', '开始冒险（绑定世界书）')
    await waitFor(`location.pathname.startsWith('/game/')`, '进入对局页（世界书）')
    await waitVisible('.world-lore-panel', '世界设定面板')
    const loreText = String(await textOf('.world-lore-panel'))
    check('世界设定面板展示绑定书名', loreText.includes(WORLD_BOOK_NAME), truncate(loreText, 200))
    const loreCount = await evaluate(`document.querySelectorAll('.world-lore-entry').length`)
    check('世界设定面板展示本回合激活条目（触发成功）', loreCount >= 1, String(loreCount))
    await checkNoInternalIdsVisible('世界书对局页')
    await screenshot('18-world-book-game-1280.png')
    return `lore_entries=${loreCount}`
  })

  await step('wb.mobile-375', '世界书：列表页 375px 无横向溢出', async () => {
    await navigate('/world-books', '世界书列表页（375）', '.world-books-view')
    await waitVisible('.world-book-item', '世界书列表项')
    await setViewport(MOBILE_VIEWPORT.width, MOBILE_VIEWPORT.height, true)
    const metrics = await checkNoHorizontalOverflow('世界书列表 375px')
    await screenshot('19-world-books-375.png')
    await clearViewport()
    return JSON.stringify(metrics)
  })

  await step('wb.delete', '世界书：详情页两段确认删除（软删）后从列表消失', async () => {
    await navigate(`/world-books/${worldBookId}`, '世界书详情（删除）', '.world-book-detail-view')
    // 删除按钮在详情异步加载完成前是 disabled；等详情加载完成再点。
    await waitFor(
      `(() => {
        const text = document.body.innerText ?? '';
        return text.includes(${JSON.stringify(WORLD_BOOK_NAME)}) && !text.includes('正在读取世界书');
      })()`,
      '世界书详情加载完成（可删除）',
    )
    await clickButtonByText('.world-book-detail-view button', '删除', '删除按钮')
    await clickButtonByText('.world-book-detail-view button', '确认删除（不可恢复）', '确认删除')
    await waitFor(`location.pathname === '/world-books'`, '删除后回到世界书列表')
    await waitFor(
      `!document.body.innerText.includes(${JSON.stringify(WORLD_BOOK_NAME)})`,
      '列表不再出现已删除的世界书',
    )
    const listText = String(await pageText())
    check('删除后列表无残留书名', !listText.includes(WORLD_BOOK_NAME), truncate(listText, 160))
    return 'deleted=true'
  })

  // -------------------------------------------------------------------------
  // S5 用户内容平台：场景 JSON 上传（场景库）+ 运行期 LLM 设置页
  // -------------------------------------------------------------------------
  const SCENARIO_PAYLOAD = {
    schema_version: 1,
    slug: 'e2e-tower',
    meta: { title: 'E2E 小塔', author: 'e2e', description: '端到端测试用最小场景' },
    player: { name: '旅人', hp: 10, max_hp: 10, location: 'gate', inventory: [] },
    rooms: [
      { key: 'gate', name: '塔门', description: '一扇虚掩的木门，门缝里透出风。', exits: { north: 'hall' } },
      { key: 'hall', name: '塔厅', description: '螺旋楼梯向上，墙上挂着一盏没点亮的灯。', exits: { south: 'gate' }, items: ['flint'] },
    ],
    items: [{ key: 'flint', name: '火石', description: '一块干燥的火石。', tags: ['quest'] }],
    npcs: [],
    goal: {
      title: '爬上塔顶',
      description: '带着火石到塔厅。',
      objectives: [
        { key: 'climb', text: '带着火石进入塔厅', when: { all: [{ in_room: 'hall' }, { has_item: 'flint' }] } },
      ],
    },
    endings: [],
  }
  let e2eScenarioId = ''

  await step('scenario.import-and-list', '场景库：上传场景 JSON → 列表出现条目', async () => {
    const imported = await postJson('/api/v1/scenarios', SCENARIO_PAYLOAD, '导入场景 JSON')
    e2eScenarioId = String(imported?.scenario_id ?? '')
    check('导入返回场景 id', e2eScenarioId.startsWith('scn_'), e2eScenarioId)
    await navigate('/scenarios', '场景库列表', '.scenario-library-view')
    await waitFor(
      `document.querySelectorAll('.scenario-item').length >= 1`,
      '场景列表出现至少一个条目',
    )
    await waitFor(
      `document.body.innerText.includes(${JSON.stringify(SCENARIO_PAYLOAD.meta.title)})`,
      '列表出现上传场景标题',
    )
    await checkNoInternalIdsVisible('场景库列表')
    await screenshot('20-scenario-library-1280.png')
    return `scenario_id=${e2eScenarioId}`
  })

  await step('scenario.start', '场景库：按上传场景开局 → 对局页目标面板与场景徽章', async () => {
    await navigate(
      `/scenarios/${encodeURIComponent(e2eScenarioId)}`,
      '场景详情页',
      '.scenario-detail-view',
    )
    await waitFor(
      `document.querySelector('.scenario-detail-view')?.innerText.includes(${JSON.stringify(SCENARIO_PAYLOAD.meta.title)})`,
      '详情页显示场景标题',
    )
    await clickButtonByText('.scenario-detail-view button', '按此场景开局', '按此场景开局')
    await waitFor(`location.pathname.startsWith('/game/')`, '进入对局页（上传场景）')
    await waitVisible('.scenario-badge', '对局页场景徽章')
    const badgeText = String(await textOf('.scenario-badge'))
    check(
      '徽章显示上传场景标题',
      badgeText.includes(SCENARIO_PAYLOAD.meta.title),
      truncate(badgeText, 120),
    )
    await waitVisible('.goal-panel', '目标面板')
    const goalText = String(await textOf('.goal-panel'))
    check(
      '目标面板显示上传场景目标',
      goalText.includes(SCENARIO_PAYLOAD.goal.title),
      truncate(goalText, 160),
    )
    await checkNoInternalIdsVisible('上传场景对局页')
    await screenshot('21-scenario-game-1280.png')
    return `badge=${truncate(badgeText, 40)}`
  })

  await step('settings.llm', '设置页：AI 状态卡 + API Key 表单可用（不提交真实密钥）', async () => {
    await navigate('/settings', '设置页', '.settings-view')
    await waitFor(
      `document.body.innerText.includes('当前 AI 状态')`,
      '设置页展示当前 AI 状态卡',
    )
    await waitFor(`document.querySelector('input[type="password"]') !== null`, 'API Key 密码框存在')
    const text = String(await pageText())
    check(
      '设置页提供保存/测试入口与安全说明',
      text.includes('保存并启用') && text.includes('测试连接') && text.includes('不会'),
      truncate(text, 200),
    )
    await screenshot('22-settings-1280.png')
    await setViewport(MOBILE_VIEWPORT.width, MOBILE_VIEWPORT.height, true)
    await checkNoHorizontalOverflow('设置页 375px')
    await screenshot('23-settings-375.png')
    await clearViewport()
    return 'settings-viewed'
  })

  await step('console.no-errors', '全程无 console 错误 / 未捕获异常', async () => {
    const bad = consoleEvents.filter((entry) => !isBenignConsoleError(entry))
    check(
      '无 console 错误',
      bad.length === 0,
      bad
        .slice(0, 5)
        .map((entry) => `${entry.kind}: ${truncate(entry.text, 160)}`)
        .join(' | '),
    )
    const benign = consoleEvents.filter((entry) => isBenignConsoleError(entry))
    return `捕获 ${consoleEvents.length} 条控制台事件（良性/预期 ${benign.length} 条），错误 ${bad.length} 条`
  })
}

// ---------------------------------------------------------------------------
// 清理：关浏览器、释放 CDP 端口、删临时 profile
// ---------------------------------------------------------------------------
async function stopBrowser() {
  const cdp = state.cdp
  state.cdp = null
  if (cdp !== null) {
    cdp.close()
  }
  const child = state.browserProc
  state.browserProc = null
  if (child !== null && child.pid !== undefined) {
    try {
      if (process.platform === 'win32') {
        execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' })
      } else {
        child.kill('SIGTERM')
      }
    } catch {
      // 进程可能已自行退出
    }
  }
  let released = false
  if (state.cdpPort > 0) {
    const deadline = Date.now() + 10000
    while (Date.now() < deadline) {
      try {
        await httpJson(`http://127.0.0.1:${state.cdpPort}/json/version`, 1000)
      } catch {
        released = true
        break
      }
      await sleep(100)
    }
  }
  if (state.profileDir !== null) {
    try {
      rmSync(state.profileDir, { recursive: true, force: true })
    } catch {
      // Windows 上浏览器可能稍后才释放文件；不影响结论
    }
  }
  return released
}

function writeResult({ ok, fatalError, startedAt, released }) {
  mkdirSync(state.outDir, { recursive: true })
  const finishedAt = new Date()
  const payload = {
    tool: 'scripts/e2e-web.mjs',
    scenario: 'M6-028 生成人物卡 UI + 主回路（真实浏览器 CDP）',
    ok,
    baseUrl: state.baseUrl,
    browser: {
      path: state.browserPath,
      version: state.browserVersion,
      headless: !opts.headful,
      cdpPortReleased: released,
    },
    viewport: {
      desktop: `${opts.width}x${opts.height}`,
      mobile: `${MOBILE_VIEWPORT.width}x${MOBILE_VIEWPORT.height}`,
    },
    startedAt: startedAt.toISOString(),
    finishedAt: finishedAt.toISOString(),
    durationMs: finishedAt.getTime() - startedAt.getTime(),
    cardId: state.cardId,
    gameId: state.gameId,
    gameFromDetailId: state.gameFromDetailId,
    steps,
    assertions,
    consoleErrors: consoleEvents,
    benignConsoleErrors: consoleEvents.filter((entry) => isBenignConsoleError(entry)),
    screenshots,
    fatalError: fatalError === null ? null : fatalError.message,
  }
  const file = join(state.outDir, 'result.json')
  writeFileSync(file, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
  return file
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
async function main() {
  const startedAt = new Date()
  let fatalError = null
  let released = false
  try {
    log(`M6-028 e2e（CDP 真实浏览器）：base-url=${state.baseUrl}`)
    log(`证据目录：${state.outDir}`)
    mkdirSync(state.screenshotsDir, { recursive: true })

    const probe = await httpGet('/healthz', 5000)
    if (probe.status !== 200) {
      throw new Error(`服务未就绪：GET /healthz → HTTP ${probe.status}`)
    }
    log(`▶ 服务就绪：${state.baseUrl}`)

    state.browserPath = detectBrowser(opts.browser)
    log(`▶ 浏览器：${state.browserPath}`)
    state.cdpPort = await findFreePort()
    state.profileDir = mkdtempSync(join(tmpdir(), 'textgame-e2e-'))
    const launched = await launchBrowser({
      browserPath: state.browserPath,
      port: state.cdpPort,
      profileDir: state.profileDir,
      headless: !opts.headful,
      width: opts.width,
      height: opts.height,
    })
    state.browserProc = launched.child
    state.browserVersion = String(launched.version.Browser ?? launched.version.product ?? '')

    const targets = await httpJson(`http://127.0.0.1:${state.cdpPort}/json/list`)
    const pageTarget = (targets ?? []).find((target) => target.type === 'page')
    if (pageTarget === undefined || typeof pageTarget.webSocketDebuggerUrl !== 'string') {
      throw new Error('未找到可用的 CDP page target（浏览器可能未正常启动）')
    }
    const cdp = new CdpClient(pageTarget.webSocketDebuggerUrl)
    await cdp.connect()
    state.cdp = cdp
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Log.enable')
    await cdp.send('Network.enable')
    installConsoleCapture(cdp)
    log(`▶ CDP 已连接：${truncate(state.browserVersion, 120)}`)

    await runFlow()
  } catch (error) {
    fatalError = error instanceof Error ? error : new Error(String(error))
    log(`✘ 终止：${fatalError.message}`)
    // 失败时尽量留一张现场截图/页面文本，便于定位
    if (state.cdp !== null) {
      try {
        await screenshot('99-fatal-page.png')
        const text = await pageTextSafe()
        assertions.push({ name: '失败现场页面文本', ok: true, detail: truncate(text, 400) })
      } catch {
        // 现场采集失败不影响结论
      }
    }
  } finally {
    if (!opts.keepOpen) {
      released = await stopBrowser()
      log(`▶ 浏览器已关闭，CDP 端口 ${state.cdpPort} ${released ? '已释放' : '可能未释放'}`)
    }
    const ok = fatalError === null && steps.every((entry) => entry.ok)
    const file = writeResult({ ok, fatalError, startedAt, released })
    const total = steps.length
    const passed = steps.filter((entry) => entry.ok).length
    const failedAssertions = assertions.filter((entry) => !entry.ok)
    log('')
    log('==================== M6-028 e2e 结果 ====================')
    log(`步骤：${passed}/${total} 通过；断言：${assertions.length - failedAssertions.length}/${assertions.length} 通过`)
    log(`截图：${screenshots.length} 张（${state.screenshotsDir}）`)
    log(`结果 JSON：${file}`)
    if (fatalError !== null) {
      log(`失败原因：${fatalError.message}`)
    }
    log(`结论：${ok ? 'PASS' : 'FAIL'}`)
    log('========================================================')
    process.exitCode = ok ? 0 : 1
  }
}

if (opts.help) {
  printUsage()
  process.exit(0)
}

main().catch((error) => {
  process.stderr.write(`致命错误：${error instanceof Error ? error.stack : String(error)}\n`)
  process.exit(2)
})
