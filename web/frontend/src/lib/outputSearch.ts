import type { OutputNode } from './api';

/** Search the current folder using the same names/titles shown in the library. */
export function filterLibraryNodes(nodes: OutputNode[], query: string): OutputNode[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return nodes;
  return nodes.filter(node => `${node.name} ${node.meta?.title || ''}`.toLocaleLowerCase().includes(needle));
}
