/**
 * 一次性迁移脚本：把散落在页面里的裸色值换成语义 token。
 *
 * 只做"同义替换"——每个映射都指向与原色含义一致的 token，不做视觉重设计。
 * 已由 token 驱动的文件（tokens.css / element-overrides.css / base.css / workspace.css）
 * 不在扫描范围内。
 */

import { readFile, writeFile } from 'node:fs/promises'
import { glob } from 'node:fs/promises'
import { dirname, resolve, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SKIP = new Set([
  'src/styles/tokens.css',
  'src/styles/element-overrides.css',
  'src/styles/base.css',
  'src/styles/workspace.css',
])

const MAP = [
  // 品牌蓝（旧）→ 品牌青蓝
  ['#1463ff', 'var(--brand-500)'],
  ['#0f56d9', 'var(--brand-600)'],
  ['#0b45b4', 'var(--brand-800)'],
  ['#1456d9', 'var(--brand-600)'],
  // 品牌浅底
  ['#eef5ff', 'var(--brand-50)'],
  ['#eaf2ff', 'var(--brand-50)'],
  ['#eef4ff', 'var(--brand-50)'],
  ['#f8faff', 'var(--brand-50)'],
  ['#f3f7ff', 'var(--brand-50)'],
  ['#d0ddf7', 'var(--brand-100)'],
  // 文字
  ['#0f172a', 'var(--text-primary)'],
  ['#111827', 'var(--text-primary)'],
  ['#1f2937', 'var(--text-primary)'],
  ['#1e293b', 'var(--text-primary)'],
  ['#374151', 'var(--text-secondary)'],
  ['#475569', 'var(--text-secondary)'],
  ['#334155', 'var(--text-secondary)'],
  ['#64748b', 'var(--text-tertiary)'],
  ['#6b7280', 'var(--text-tertiary)'],
  ['#9ca3af', 'var(--text-tertiary)'],
  ['#94a3b8', 'var(--text-disabled)'],
  // 边框
  ['#e6eaf0', 'var(--border-light)'],
  ['#e4e9f2', 'var(--border-light)'],
  ['#dfe6ef', 'var(--border-light)'],
  ['#dbe3ef', 'var(--border-default)'],
  ['#d7deea', 'var(--border-default)'],
  ['#cbd5e1', 'var(--border-strong)'],
  ['#b8c3d6', 'var(--border-strong)'],
  // 背景与填充
  ['#f7f9fc', 'var(--bg-page)'],
  ['#f4f7fb', 'var(--bg-page)'],
  ['#f8fafc', 'var(--bg-surface-sunken)'],
  ['#f1f5f9', 'var(--neutral-100)'],
  ['#e5e7eb', 'var(--neutral-150)'],
  ['#fbfdff', 'var(--neutral-25)'],
  // 语义状态
  ['#dcfce7', 'var(--success-50)'],
  ['#15803d', 'var(--success-500)'],
  ['#059669', 'var(--success-500)'],
  ['#065f46', 'var(--success-500)'],
  ['#f0fdf4', 'var(--success-50)'],
  ['#ecfdf5', 'var(--success-50)'],
  ['#a7f3d0', 'rgba(15, 157, 118, 0.2)'],
  ['#fee2e2', 'var(--danger-50)'],
  ['#b91c1c', 'var(--danger-600)'],
  ['#ef4444', 'var(--danger-500)'],
  ['#e5e7eb', 'var(--neutral-150)'],
  ['#22c55e', 'var(--success-500)'],
  ['#2563eb', 'var(--brand-500)'],
  ['#10b981', 'var(--success-500)'],
  // 第二轮：剩余零散值
  ['#9fb1c9', 'var(--border-strong)'],
  ['#f5f9ff', 'var(--brand-50)'],
  ['#f6f9ff', 'var(--brand-50)'],
  ['#c7dcff', 'var(--brand-200)'],
  ['#eff6ff', 'var(--brand-50)'],
  ['#16834b', 'var(--success-500)'],
  ['#16a34a', 'var(--success-500)'],
  ['#fffbeb', 'var(--warning-50)'],
  ['#b45309', 'var(--warning-500)'],
  ['#fef2f2', 'var(--danger-50)'],
  ['#ffffff', 'var(--bg-surface)'],
  // 文件类型标识色
  ['#3b82f6', 'var(--file-word)'],
  ['#f97316', 'var(--file-ppt)'],
  // 幻灯片画布配色（与后端渲染成对维护）
  ['#d8dfe9', 'var(--slide-canvas-border)'],
  ['#d3e0f5', 'var(--slide-accent-soft-border)'],
  ['#f5f8ff', 'var(--slide-accent-soft-bg)'],
  ['#0b3fa8', 'var(--slide-accent)'],
  ['#e6f0ff', 'var(--slide-chip-bg)'],
  ['#c2410c', 'var(--slide-warn-text)'],
]

// Windows 下 glob 返回反斜杠路径：统一成正斜杠再比对，否则 SKIP 会失效
// （第一次跑就因为这个问题把 tokens.css 自己的原始色阶改成了自引用）。
const normalize = path => path.replace(/\\/g, '/')

const files = []
for await (const entry of glob('src/**/*.{vue,css,js}', { cwd: ROOT })) {
  if (!SKIP.has(normalize(entry))) files.push(entry)
}

let totalHits = 0
const report = []

for (const file of files) {
  const full = resolve(ROOT, file)
  const before = await readFile(full, 'utf8')
  let after = before
  let hits = 0
  for (const [from, to] of MAP) {
    const pattern = new RegExp(from.replace('#', '#'), 'gi')
    const matches = after.match(pattern)
    if (matches) {
      hits += matches.length
      after = after.replace(pattern, to)
    }
  }
  if (hits) {
    await writeFile(full, after, 'utf8')
    totalHits += hits
    report.push({ file: relative('.', file), hits })
  }
}

report.sort((a, b) => b.hits - a.hits)
for (const item of report) console.log(`${String(item.hits).padStart(4)}  ${item.file}`)
console.log(`\n共替换 ${totalHits} 处，涉及 ${report.length} 个文件`)
