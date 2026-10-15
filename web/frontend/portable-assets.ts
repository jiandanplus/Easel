/** Build receipt: exact bundler outputs plus tracked public assets, never a dist scan. */
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync, lstatSync, existsSync } from 'node:fs'
import { resolve, dirname, join } from 'node:path'
import type { Plugin } from 'vite'

export function portableAssets(): Plugin {
  let root: string
  let out: string
  return {
    name: 'easel-portable-assets',
    configResolved(config) { root = config.root; out = resolve(root, config.build.outDir) },
    writeBundle(_options, bundle) {
      const repo = resolve(root, '../..')
      const tracked = execFileSync('git', ['ls-files', '-z'], { cwd: repo }).toString().split('\0').filter(Boolean)
      const baselinePath = join(repo, '.git/sdd/preexisting-ui.json')
      const baseline = existsSync(baselinePath) ? JSON.parse(readFileSync(baselinePath, 'utf8')) : {}
      const inputs = [...new Set([...tracked, ...Object.keys(baseline)])].filter(p => p.startsWith('web/frontend/') && !p.startsWith('web/frontend/dist/'))
      const hash = (path: string) => {
        for (let p = path; p !== dirname(p); p = dirname(p)) {
          if (lstatSync(p).isSymbolicLink()) throw new Error(`Symlink frontend input: ${path}`)
        }
        if (!lstatSync(path).isFile()) throw new Error(`Nonregular frontend input: ${path}`)
        return createHash('sha256').update(readFileSync(path)).digest('hex')
      }
      const names = new Set(Object.keys(bundle))
      for (const p of tracked.filter(p => p.startsWith('web/frontend/public/'))) names.add(p.slice('web/frontend/public/'.length))
      const files = Object.fromEntries([...names].sort().map(name => [name, hash(join(out, name))]))
      const source = Object.fromEntries(inputs.sort().map(name => [name, hash(join(repo, name))]))
      writeFileSync(join(out, 'portable-assets.json'), JSON.stringify({ schema_version: 1, files, source }, null, 2))
    },
  }
}
