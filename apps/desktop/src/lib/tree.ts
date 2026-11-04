import type { WsNode } from "../bridge/types";

export interface TreeNode { node: WsNode; children: TreeNode[]; path: string[] }

export const labelOf = (n: WsNode): string => n.display_name || n.name;

const byOrder = (a: WsNode, b: WsNode) => a.position - b.position || labelOf(a).localeCompare(labelOf(b));

/** Build the forest from the flat list the server delivered. Roots are nodes with no (delivered) parent. Nothing is hard-coded. */
export function buildTree(nodes: WsNode[], showArchived: boolean): TreeNode[] {
  const visible = nodes.filter((n) => showArchived || !n.archived_at);
  const ids = new Set(visible.map((n) => n.id));
  const kids = new Map<string, WsNode[]>();
  const roots: WsNode[] = [];
  for (const n of visible) {
    if (n.parent_id && ids.has(n.parent_id)) kids.set(n.parent_id, [...(kids.get(n.parent_id) ?? []), n]);
    else roots.push(n);
  }
  const make = (n: WsNode, parentPath: string[]): TreeNode => {
    const path = [...parentPath, labelOf(n)];
    return { node: n, path, children: (kids.get(n.id) ?? []).sort(byOrder).map((c) => make(c, path)) };
  };
  return roots.sort(byOrder).map((r) => make(r, []));
}

/** Keep nodes whose label matches and every ancestor of a match; also return the ids to auto-expand. */
export function filterTree(forest: TreeNode[], query: string): { forest: TreeNode[]; expand: Set<string> } {
  const q = query.trim().toLowerCase();
  if (!q) return { forest, expand: new Set() };
  const expand = new Set<string>();
  const walk = (t: TreeNode): TreeNode | null => {
    const kids = t.children.map(walk).filter((c): c is TreeNode => c !== null);
    const self = labelOf(t.node).toLowerCase().includes(q) || t.node.name.toLowerCase().includes(q);
    if (self || kids.length) {
      if (kids.length) expand.add(t.node.id);
      return { ...t, children: kids };
    }
    return null;
  };
  return { forest: forest.map(walk).filter((t): t is TreeNode => t !== null), expand };
}

export function countNodes(forest: TreeNode[]): number {
  return forest.reduce((n, t) => n + 1 + countNodes(t.children), 0);
}
export function findNode(forest: TreeNode[], id: string): TreeNode | null {
  for (const t of forest) {
    if (t.node.id === id) return t;
    const f = findNode(t.children, id);
    if (f) return f;
  }
  return null;
}
