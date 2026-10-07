/**
 * 图标使用审计：算出"实际用到但没在文件里本地导入"的图标。
 *
 * 这些才是必须全局注册的最小集合 —— 其余几百个图标白白进了首屏包。
 *   node scripts/audit-icons.mjs
 */

import { glob, readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import * as iconSet from '@element-plus/icons-vue'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const ALL_ICONS = new Set(Object.keys(iconSet))

const used = new Map() // 图标名 -> 使用它的文件
const importedLocally = new Set()

for await (const entry of glob('src/**/*.vue', { cwd: ROOT })) {
  const text = await readFile(resolve(ROOT, entry), 'utf8')
  const template = text.match(/<template>([\s\S]*)<\/template>/)?.[1] ?? ''
  const script = text.match(/<script[^>]*>([\s\S]*)<\/script>/)?.[1] ?? ''

  // 本地导入的图标（这些不需要全局注册）
  const importBlock = script.match(/import\s*\{([^}]+)\}\s*from\s*'@element-plus\/icons-vue'/)
  if (importBlock) {
    for (const raw of importBlock[1].split(',')) {
      const name = raw.trim().split(/\s+as\s+/)[0].trim()
      if (name) importedLocally.add(name)
    }
  }

  // 模板里用到的 PascalCase 组件名
  for (const match of template.matchAll(/<([A-Z][A-Za-z0-9]*)[\s/>]/g)) {
    const name = match[1]
    if (!ALL_ICONS.has(name)) continue
    if (!used.has(name)) used.set(name, new Set())
    used.get(name).add(entry.replace(/\\/g, '/'))
  }
}

const needGlobal = [...used.keys()].filter(name => !importedLocally.has(name)).sort()
const localOnly = [...used.keys()].filter(name => importedLocally.has(name)).sort()

console.log(`图标库总数: ${ALL_ICONS.size}`)
console.log(`项目实际用到: ${used.size}`)
console.log(`  已本地导入: ${localOnly.length} -> ${localOnly.join(', ')}`)
console.log(`\n需要全局注册的最小集合（${needGlobal.length} 个）:`)
for (const name of needGlobal) {
  console.log(`  ${name.padEnd(20)} ${[...used.get(name)].join(', ')}`)
}
console.log('\n可直接放进 main.js 的数组：')
console.log(JSON.stringify(needGlobal, null, 0))
