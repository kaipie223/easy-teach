/** 前端代码级审计：对比度 / 令牌遵守 / 一致性 / 可访问性。 */

import { readFile, readdir, stat } from 'node:fs/promises'
import { join } from 'node:path'

const ROOT = 'src'

// ── 1. 对比度（WCAG 相对亮度） ─────────────────────────────
function parseColor(color) {
  const hex = color.trim()
  if (hex.startsWith('#')) {
    const value = hex.slice(1)
    const full = value.length === 3 ? value.split('').map((c) => c + c).join('') : value
    return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16))
  }
  const rgb = hex.match(/rgba?\(([^)]+)\)/)
  if (rgb) {
    return rgb[1].split(',').slice(0, 3).map((part) => Number.parseFloat(part))
  }
  return null
}

function luminance([r, g, b]) {
  const channel = (value) => {
    const scaled = value / 255
    return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

function contrast(foreground, background) {
  const a = parseColor(foreground)
  const b = parseColor(background)
  if (!a || !b) return null
  const l1 = luminance(a)
  const l2 = luminance(b)
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)
}

const tokens = {}
const tokenText = await readFile(`${ROOT}/styles/tokens.css`, 'utf8')
for (const match of tokenText.matchAll(/(--[\w-]+):\s*([^;]+);/g)) {
  tokens[match[1]] = match[2].trim()
}
function resolve(name, depth = 0) {
  const value = tokens[name]
  if (!value || depth > 4) return value || name
  if (value.startsWith('var(')) return resolve(value.slice(4, -1).trim(), depth + 1)
  return value
}

console.log('═══ 1. 对比度（正文 ≥ 4.5:1，大字/辅助 ≥ 3:1） ═══')
const pairs = [
  ['text-primary', 'bg-page'],
  ['text-primary', 'bg-surface'],
  ['text-secondary', 'bg-surface'],
  ['text-secondary', 'bg-surface-sunken'],
  ['text-tertiary', 'bg-surface'],
  ['text-tertiary', 'bg-page'],
  ['text-inverse', 'bg-inverse'],
  ['text-brand', 'bg-surface'],
  ['text-brand', 'bg-page'],
  ['danger-500', 'neutral-0'],
  ['danger-600', 'danger-50'],
  ['success-600', 'success-50'],
  ['warning-600', 'warning-50'],
  ['info-600', 'info-50'],   /* 浅底文字档（修复后应达标） */
  ['brand-500', 'neutral-0'],
  ['brand-600', 'neutral-0'],
  ['brand-700', 'neutral-0'],
  ['brand-800', 'neutral-0'],
  ['brand-900', 'neutral-0'],
  ['neutral-0', 'brand-500'],
  ['neutral-0', 'brand-600'],
  ['neutral-0', 'brand-700'],
  ['neutral-0', 'brand-800'],  /* 主按钮底色（修复后应达标） */
  ['neutral-0', 'brand-900'],
  ['neutral-0', 'success-500'],
  ['neutral-0', 'danger-500'],
]
for (const [foreground, background] of pairs) {
  const ratio = contrast(resolve(`--${foreground}`), resolve(`--${background}`))
  const flag = ratio === null ? '?' : ratio < 3 ? '✗✗' : ratio < 4.5 ? '✗ ' : ' ✓'
  console.log(`  ${flag} ${foreground} on ${background}: ${ratio ? ratio.toFixed(2) : '?'}:1`)
}

// ── 2. 逐文件扫描 ────────────────────────────────────────
async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) files.push(...(await walk(path)))
    else if (/\.(vue|css|js)$/.test(entry.name)) files.push(path)
  }
  return files
}

const files = (await walk(ROOT)).filter((path) => !path.includes('node_modules'))
const findings = {
  rawFontSizes: [],
  rawFontWeights: [],
  rawSpacing: [],
  rawZIndex: [],
  vhUnits: [],
  tables: [],
  iconButtonsWithoutLabel: [],
  imgsWithoutAlt: [],
  outdatedComment: [],
}

for (const path of files) {
  const text = await readFile(path, 'utf8')
  const short = path.replace(/\\/g, '/').replace('src/', '')

  for (const match of text.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)) {
    if (!['12', '13', '15', '17', '20', '24', '32'].includes(match[1])) {
      findings.rawFontSizes.push(`${short}: font-size ${match[1]}px`)
    }
  }
  for (const match of text.matchAll(/font-weight:\s*(\d{3})/g)) {
    if (!['400', '500', '600', '700'].includes(match[1])) {
      findings.rawFontWeights.push(`${short}: font-weight ${match[1]}`)
    }
  }
  for (const match of text.matchAll(/z-index:\s*(-?\d+)/g)) {
    const value = Number(match[1])
    if (![0, 10, 20, 40, 60, 1000, -1].includes(value)) {
      findings.rawZIndex.push(`${short}: z-index ${match[1]}`)
    }
  }
  for (const match of text.matchAll(/(\d+)vh\b/g)) {
    findings.vhUnits.push(`${short}: ${match[0]}`)
  }
  if (text.includes('data-table') || text.includes('table-wrap')) {
    findings.tables.push(short)
  }
  // 无 aria-label 的图标按钮（只有图标、无文字）
  for (const match of text.matchAll(/<el-button(?![^>]*aria-label)[^>]*>\s*<el-icon>/g)) {
    const after = text.slice(match.index, match.index + 220)
    if (!/<\/el-icon>\s*\S/.test(after)) {
      findings.iconButtonsWithoutLabel.push(`${short}: 图标按钮缺 aria-label`)
    }
  }
  for (const match of text.matchAll(/<img(?![^>]*alt=)[^>]*>/g)) {
    findings.imgsWithoutAlt.push(`${short}: <img> 缺 alt`)
  }
}

console.log('\n═══ 2. 令牌遵守与一致性 ═══')
console.log(`  vh 单位（应改 dvh）: ${findings.vhUnits.length}`)
findings.vhUnits.slice(0, 8).forEach((item) => console.log(`    ${item}`))
console.log(`  非标准字号: ${findings.rawFontSizes.length}`)
findings.rawFontSizes.slice(0, 10).forEach((item) => console.log(`    ${item}`))
console.log(`  非标准字重: ${findings.rawFontWeights.length}`)
findings.rawFontWeights.slice(0, 6).forEach((item) => console.log(`    ${item}`))
console.log(`  裸 z-index: ${findings.rawZIndex.length}`)
findings.rawZIndex.slice(0, 8).forEach((item) => console.log(`    ${item}`))
console.log(`  仍用表格的文件: ${[...new Set(findings.tables)].join(', ') || '无'}`)
console.log(`  图标按钮缺 aria-label: ${findings.iconButtonsWithoutLabel.length}`)
findings.iconButtonsWithoutLabel.slice(0, 8).forEach((item) => console.log(`    ${item}`))
console.log(`  <img> 缺 alt: ${findings.imgsWithoutAlt.length}`)
findings.imgsWithoutAlt.slice(0, 8).forEach((item) => console.log(`    ${item}`))

// ── 3. 文件体量（找过大的组件） ──────────────────────────
console.log('\n═══ 3. 体量最大的 8 个文件（KB） ═══')
const sizes = []
for (const path of files.filter((p) => p.endsWith('.vue'))) {
  sizes.push([path.replace(/\\/g, '/').replace('src/', ''), (await stat(path)).size / 1024])
}
sizes.sort((a, b) => b[1] - a[1])
sizes.slice(0, 8).forEach(([name, size]) => console.log(`  ${size.toFixed(0).padStart(4)} KB  ${name}`))
