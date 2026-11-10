/** Exact outputs and all public authored inputs, including untracked React sources. */
import { createHash } from "node:crypto"
import { readFileSync, writeFileSync, lstatSync, readdirSync } from "node:fs"
import { resolve, dirname, join, extname } from "node:path"
import type { Plugin } from "vite"

export function portableAssets(): Plugin {
  let root: string
  let out: string
  return {
    name: "easel-portable-assets",
    configResolved(config) { root = config.root; out = resolve(root, config.build.outDir) },
    writeBundle(_options, bundle) {
      const repo = resolve(root, "../..")
      const policy = JSON.parse(readFileSync(join(repo, "packaging/pi/public-source-policy.json"), "utf8"))
      const hash = (path: string) => {
        for (let p = path; p !== dirname(p); p = dirname(p)) {
          if (lstatSync(p).isSymbolicLink()) throw new Error(`Symlink frontend input: ${path}`)
        }
        if (!lstatSync(path).isFile()) throw new Error(`Nonregular frontend input: ${path}`)
        return createHash("sha256").update(readFileSync(path)).digest("hex")
      }
      const inputs: string[] = []
      const walk = (directory: string) => {
        for (const name of readdirSync(directory).sort()) {
          const path = join(directory, name)
          const lower = name.toLowerCase()
          if (name.startsWith(".") || policy.excluded_directories.includes(name)) continue
          const stat = lstatSync(path)
          if (stat.isSymbolicLink()) throw new Error(`Symlink frontend input: ${path}`)
          if (stat.isDirectory()) { walk(path); continue }
          if (policy.private_names.includes(lower) || policy.private_fragments.some((x: string) => lower.includes(x))) continue
          if (policy.frontend_extensions.includes(extname(name).toLowerCase())) inputs.push(path.slice(repo.length + 1))
        }
      }
      walk(root)
      const names = new Set(Object.keys(bundle))
      for (const p of inputs.filter(p => p.startsWith("web/frontend/public/"))) names.add(p.slice("web/frontend/public/".length))
      const files = Object.fromEntries([...names].sort().map(name => [name, hash(join(out, name))]))
      const source = Object.fromEntries(inputs.sort().map(name => [name, hash(join(repo, name))]))
      writeFileSync(join(out, "portable-assets.json"), JSON.stringify({ schema_version: 1, files, source }, null, 2))
    },
  }
}
