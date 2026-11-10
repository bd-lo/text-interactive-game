<script setup lang="ts">
/**
 * 全屏余烬 / 星尘粒子层（H4 炫酷化，**零依赖 Canvas 2D**）。
 *
 * 设计取舍（参考 tsparticles / particles.js 的「粒子背景」思路，自写实现、不复制代码）：
 * - 懒加载：由 App 用 `defineAsyncComponent` 动态 import，关特效时整块代码不下载；
 * - 低开销：预渲染发光 sprite（radial gradient）+ `drawImage`，DPR 上限 2，帧率上限 30fps
 *   （低功耗 20fps）、粒子数按视口面积与低功耗档收缩、页面隐藏时暂停 rAF；
 * - 无障碍：`aria-hidden`，纯装饰；系统 reduced-motion 时 App 不渲染本组件；
 * - 安全：只用 `requestAnimationFrame`（security-scan 禁 `setInterval`），不注入 HTML。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useVisualStore } from '@/stores/visual'

interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  size: number
  /** 0..1 生命进度（1 = 刚生成）。 */
  life: number
  /** 每秒生命衰减。 */
  decay: number
  /** 闪烁相位。 */
  phase: number
  kind: 'ember' | 'dust'
  /** 预渲染 sprite 索引。 */
  tint: number
}

const FPS_FULL = 30
const FPS_LOW = 20
/** 发光 sprite 调色：余烬橙 / 琥珀 / 星尘青。 */
const TINTS: ReadonlyArray<string> = ['rgba(255,157,92,0.95)', 'rgba(255,214,120,0.85)', 'rgba(127,255,212,0.8)']

const canvas = ref<HTMLCanvasElement | null>(null)
const visual = useVisualStore()

let ctx: CanvasRenderingContext2D | null = null
let sprites: HTMLCanvasElement[] = []
let particles: Particle[] = []
let rafId = 0
let running = false
let lastDraw = 0
let width = 0
let height = 0
let dpr = 1
let visibilityHandler: (() => void) | null = null
let resizeHandler: (() => void) | null = null

/** 粒子数量：随视口面积与低功耗档收缩（移动端更少）。 */
function particleCount(): number {
  const base = Math.round((width * height) / 26_000)
  const cap = visual.lowPower ? 18 : width < 640 ? 30 : 64
  return Math.max(10, Math.min(cap, base))
}

/** 预渲染带辉光的圆点 sprite（避免每帧 createRadialGradient）。 */
function buildSprite(color: string): HTMLCanvasElement {
  const size = 48
  const sprite = document.createElement('canvas')
  sprite.width = size
  sprite.height = size
  const spriteCtx = sprite.getContext('2d')
  if (spriteCtx !== null) {
    const gradient = spriteCtx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
    gradient.addColorStop(0, color)
    gradient.addColorStop(0.35, color.replace(/0\.\d+\)$/, '0.45)'))
    gradient.addColorStop(1, 'rgba(0,0,0,0)')
    spriteCtx.fillStyle = gradient
    spriteCtx.fillRect(0, 0, size, size)
  }
  return sprite
}

function newParticle(spawnAnywhere: boolean): Particle {
  const ember = Math.random() < 0.72
  return {
    x: Math.random() * width,
    y: spawnAnywhere ? Math.random() * height : height + Math.random() * 40,
    vx: (Math.random() - 0.5) * (ember ? 12 : 6),
    vy: ember ? -(18 + Math.random() * 34) : -(4 + Math.random() * 8),
    size: ember ? 0.5 + Math.random() * 1.1 : 0.3 + Math.random() * 0.7,
    life: spawnAnywhere ? Math.random() : 1,
    decay: 0.04 + Math.random() * 0.12,
    phase: Math.random() * Math.PI * 2,
    kind: ember ? 'ember' : 'dust',
    tint: Math.floor(Math.random() * TINTS.length),
  }
}

function resetParticles(): void {
  if (width === 0 || height === 0) {
    particles = []
    return
  }
  particles = Array.from({ length: particleCount() }, () => newParticle(true))
}

function resize(): void {
  const element = canvas.value
  if (element === null) {
    return
  }
  dpr = Math.min(window.devicePixelRatio || 1, 2)
  width = window.innerWidth
  height = window.innerHeight
  element.width = Math.max(1, Math.floor(width * dpr))
  element.height = Math.max(1, Math.floor(height * dpr))
  ctx = element.getContext('2d')
  if (ctx !== null) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.globalCompositeOperation = 'lighter'
  }
  sprites = TINTS.map((color) => buildSprite(color))
  resetParticles()
}

function step(deltaSeconds: number): void {
  for (let index = 0; index < particles.length; index += 1) {
    const particle = particles[index]
    if (particle === undefined) {
      continue
    }
    particle.life -= particle.decay * deltaSeconds
    particle.x += particle.vx * deltaSeconds
    particle.y += particle.vy * deltaSeconds
    particle.phase += deltaSeconds * 2.2
    if (particle.kind === 'dust') {
      // 星尘只缓慢漂浮，不跟随上升速度衰减
      particle.x += Math.sin(particle.phase) * 2 * deltaSeconds
    }
    if (
      particle.life <= 0 ||
      particle.y < -40 ||
      particle.x < -40 ||
      particle.x > width + 40
    ) {
      particles[index] = newParticle(false)
    }
  }
}

function draw(elapsedSeconds: number): void {
  if (ctx === null) {
    return
  }
  ctx.clearRect(0, 0, width, height)
  for (const particle of particles) {
    const sprite = sprites[particle.tint]
    if (sprite === undefined) {
      continue
    }
    const twinkle = particle.kind === 'dust' ? 0.35 + 0.45 * (0.5 + 0.5 * Math.sin(particle.phase + elapsedSeconds)) : 0.55 + 0.45 * particle.life
    const alpha = Math.max(0, Math.min(1, particle.life)) * twinkle
    const scale = particle.size * (particle.kind === 'ember' ? 18 : 10)
    ctx.globalAlpha = alpha * 0.85
    ctx.drawImage(sprite, particle.x - scale / 2, particle.y - scale / 2, scale, scale)
  }
  ctx.globalAlpha = 1
}

function loop(time: number): void {
  if (!running) {
    return
  }
  rafId = requestAnimationFrame(loop)
  const frameInterval = 1000 / (visual.lowPower ? FPS_LOW : FPS_FULL)
  if (time - lastDraw < frameInterval) {
    return
  }
  const deltaSeconds = lastDraw === 0 ? 0.016 : Math.min((time - lastDraw) / 1000, 0.1)
  lastDraw = time
  if (document.hidden) {
    return
  }
  step(deltaSeconds)
  draw(time / 1000)
}

function start(): void {
  if (running || ctx === null) {
    return
  }
  running = true
  lastDraw = 0
  rafId = requestAnimationFrame(loop)
}

function stop(): void {
  running = false
  if (rafId !== 0) {
    cancelAnimationFrame(rafId)
    rafId = 0
  }
}

onMounted(() => {
  resize()
  if (visual.particlesEnabled) {
    start()
  }
  visibilityHandler = (): void => {
    if (document.hidden) {
      stop()
    } else if (visual.particlesEnabled) {
      resize()
      start()
    }
  }
  resizeHandler = (): void => {
    resize()
  }
  document.addEventListener('visibilitychange', visibilityHandler)
  window.addEventListener('resize', resizeHandler, { passive: true })
})

// 档位在组件存活期间变化（低功耗/全特效）时按新档重建粒子并保持运行
watch(
  () => [visual.lowPower, visual.particlesEnabled] as const,
  () => {
    resize()
    if (visual.particlesEnabled) {
      start()
    } else {
      stop()
    }
  },
)

onBeforeUnmount(() => {
  stop()
  if (visibilityHandler !== null) {
    document.removeEventListener('visibilitychange', visibilityHandler)
    visibilityHandler = null
  }
  if (resizeHandler !== null) {
    window.removeEventListener('resize', resizeHandler)
    resizeHandler = null
  }
  particles = []
  sprites = []
  ctx = null
})
</script>

<template>
  <canvas ref="canvas" class="ember-field" aria-hidden="true"></canvas>
</template>
