/**
 * 设计规范自检：防止色值与尺度悄悄漂回去。
 *
 *   node scripts/check-design-tokens.mjs          # 报告问题，有错则退出码 1
 *
 * 规则来源：tokens.css 顶部的约束注释。
 *   1. 组件层禁止裸十六进制色（只能引用 --* token）
 *   2. 阴影必须来自 --shadow-* token（禁止手写阴影，禁止纯黑硬阴影）
 *   3. 间距应为 4 的倍数（4/8/12/16/20/24/32/40/48/64）
 *   4. 视口高度用 dvh 而不是 vh
 */

import { glob } from 'node:fs/promises'
import { readFile } from 'node:fs/promises'
import { dirname, resolve, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
// token 层是唯一允许出现原始色值与阴影定义的地方
const TOKEN_FILES = new Set([
  'src/styles/tokens.css',
  'src/styles/element-overrides.css',
  'src/styles/base.css',
  'src/styles/workspace.css',
])
const ALLOWED_SPACING = new Set([0, 1, 2, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96])
const HEX = /#[0-9a-fA-F]{3,8}\b/g
const SHADOW = /box-shadow:\s*([^;}]+)/g
const SPACING = /(?:padding|margin|gap)(?:-(?:top|right|bottom|left))?:\s*([^;}]+)/g
const VH = /\b\d+(?:\.\d+)?vh\b/g

const normalize = path => path.replace(/\\/g, '/')
const problems = []

for await (const entry of glob('src/**/*.{vue,css}', { cwd: ROOT })) {
  const path = normalize(entry)
  const text = await readFile(resolve(ROOT, entry), 'utf8')
  const isTokenLayer = TOKEN_FILES.has(path)
  const lineOf = index => text.slice(0, index).split('\n').length

  if (!isTokenLayer) {
    for (const match of text.matchAll(HEX)) {
      problems.push({
        path,
        line: lineOf(match.index),
        rule: '裸色值',
        detail: `${match[0]} —— 请改成语义 token（var(--text-/--bg-/--border-…)）`,
      })
    }
  }

  for (const match of text.matchAll(SHADOW)) {
    const value = match[1].trim()
    // 允许：三层层级阴影 token、焦点光环 token、以及 inset 细线（那是"边框"不是"层级"）
    const allowed =
      value === 'none' ||
      value.startsWith('var(--shadow-') ||
      value.startsWith('var(--ring-') ||
      value.includes('inset')
    if (!allowed) {
      problems.push({
        path,
        line: lineOf(match.index),
        rule: '手写阴影',
        detail: `${value} —— 层级阴影只允许 var(--shadow-card|raised|overlay)；焦点用 var(--ring-brand|danger)`,
      })
    }
  }

  for (const match of text.matchAll(SPACING)) {
    for (const part of match[1].split(/\s+/)) {
      const px = part.match(/^(\d+(?:\.\d+)?)px$/)
      if (!px) continue
      const value = Number(px[1])
      if (!ALLOWED_SPACING.has(value)) {
        problems.push({
          path,
          line: lineOf(match.index),
          rule: '间距不是 4 的倍数',
          detail: `${value}px（允许：4/8/12/16/20/24/32/40/48/64）`,
        })
      }
    }
  }

  for (const match of text.matchAll(VH)) {
    problems.push({
      path,
      line: lineOf(match.index),
      rule: '视口单位',
      detail: `${match[0]} —— 移动端用 dvh（100dvh）`,
    })
  }
}

if (!problems.length) {
  console.log('设计规范自检通过：无裸色值、无手写阴影、间距合规 ✓')
  process.exit(0)
}

const byRule = new Map()
for (const item of problems) {
  if (!byRule.has(item.rule)) byRule.set(item.rule, [])
  byRule.get(item.rule).push(item)
}

for (const [rule, items] of byRule) {
  console.log(`\n【${rule}】${items.length} 处`)
  for (const item of items.slice(0, 20)) {
    console.log(`  ${relative('.', item.path)}:${item.line}  ${item.detail}`)
  }
  if (items.length > 20) console.log(`  …另有 ${items.length - 20} 处`)
}
console.log(`\n合计 ${problems.length} 个问题`)
process.exit(1)
