/**
 * 把"非体系内"的间距/字号/圆角归一到 token（只改数值，不改语义）。
 *
 * 已有 token 驱动的文件（tokens/base/motion/workspace/element-overrides）跳过。
 *   node scripts/tokenize-spacing.mjs
 */

import { glob, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const TOKEN_FILES = new Set([
  'src/styles/tokens.css',
  'src/styles/element-overrides.css',
  'src/styles/base.css',
  'src/styles/motion.css',
  'src/styles/workspace.css',
])

// 间距：就近吸附到 4 的倍数体系
const SPACING_TOKENS = new Map([
  [4, 'var(--space-1)'],
  [8, 'var(--space-2)'],
  [12, 'var(--space-3)'],
  [16, 'var(--space-4)'],
  [20, 'var(--space-5)'],
  [24, 'var(--space-6)'],
  [32, 'var(--space-8)'],
  [40, 'var(--space-10)'],
  [48, 'var(--space-12)'],
  [64, 'var(--space-16)'],
])
const SPACING_SCALE = [...SPACING_TOKENS.keys()]

const RADIUS_TOKENS = new Map([
  [4, 'var(--radius-xs)'],
  [6, 'var(--radius-sm)'],
  [8, 'var(--radius-md)'],
  [12, 'var(--radius-lg)'],
  [16, 'var(--radius-xl)'],
])

const FONT_TOKENS = new Map([
  [12, 'var(--text-xs)'],
  [13, 'var(--text-sm)'],
  [14, 'var(--text-base)'],
  [15, 'var(--text-base)'],
  [17, 'var(--text-md)'],
  [20, 'var(--text-lg)'],
  [24, 'var(--text-xl)'],
  [32, 'var(--text-2xl)'],
])

/** 就近吸附：取 scale 里最接近的值 */
function nearest(scale, value) {
  return scale.reduce(
    (best, candidate) => (Math.abs(candidate - value) < Math.abs(best - value) ? candidate : best),
    scale[0],
  )
}

const SPACING_DECL = /(?:^|[;{\s])((?:padding|margin|gap|row-gap|column-gap)(?:-(?:top|right|bottom|left|block|inline)(?:-(?:start|end))?)?)\s*:\s*([^;}]+);/gm
const RADIUS_DECL = /(border-radius)\s*:\s*([^;}]+);/gm
const FONT_DECL = /(font-size)\s*:\s*([^;}]+);/gm

function rewriteValues(value, map, scale) {
  // 负号要一起捕获：直接替换数字会产出 `-var(--space-1)`，那是**非法 CSS**
  // （负号不能写在 var() 外面），负值必须以 calc(… * -1) 表达。
  return value.replace(/(-?)(\d+(?:\.\d+)?)px/g, (match, sign, raw) => {
    const number = Number(raw)
    if (number === 0) return match
    const snapped = scale ? nearest(scale, number) : number
    const token = map.get(snapped) ?? map.get(number)
    if (!token) return match
    return sign === '-' ? `calc(${token} * -1)` : token
  })
}

const normalize = path => path.replace(/\\/g, '/')
const report = []

for await (const entry of glob('src/**/*.{vue,css}', { cwd: ROOT })) {
  const file = normalize(entry)
  if (TOKEN_FILES.has(file)) continue

  const full = resolve(ROOT, entry)
  const before = await readFile(full, 'utf8')
  let after = before
  let hits = 0

  after = after.replace(SPACING_DECL, (match, prop, value) => {
    const next = rewriteValues(value, SPACING_TOKENS, SPACING_SCALE)
    if (next !== value) hits += 1
    return `${match.slice(0, match.indexOf(prop))}${prop}: ${next};`
  })

  after = after.replace(RADIUS_DECL, (match, prop, value) => {
    const next = rewriteValues(value, RADIUS_TOKENS)
    if (next !== value) hits += 1
    return `${match.slice(0, match.indexOf(prop))}${prop}: ${next};`
  })

  after = after.replace(FONT_DECL, (match, prop, value) => {
    const next = rewriteValues(value, FONT_TOKENS)
    if (next !== value) hits += 1
    return `${match.slice(0, match.indexOf(prop))}${prop}: ${next};`
  })

  if (hits && after !== before) {
    await writeFile(full, after, 'utf8')
    report.push({ file: relative('.', file), hits })
  }
}

report.sort((a, b) => b.hits - a.hits)
for (const item of report) console.log(`${String(item.hits).padStart(4)}  ${item.file}`)
console.log(`\n共归一 ${report.reduce((sum, item) => sum + item.hits, 0)} 处，涉及 ${report.length} 个文件`)
