/* The project's files as a tree of folders, the way a code editor lists them.
 *
 * Pure: the store keeps a flat list of files by path, and this builds the
 * folders from the paths ("app/routes/health.py" is in "routes", which is in
 * "app"), orders them, and flattens what is open into the rows the panel
 * draws, top to bottom.
 */

/** A file in the tree: the store's file, under its name. */
export interface TreeFile<F extends { path: string } = { path: string }> {
  kind: "file";
  /** Its whole path, which is what names it everywhere else. */
  path: string;
  /** The last part of its path. */
  name: string;
  /** How many folders it is in: 0 at the top. */
  depth: number;
  file: F;
}

/** A folder: it holds files and folders, and knows how many files are in it. */
export interface TreeFolder<F extends { path: string } = { path: string }> {
  kind: "folder";
  /** "app/routes": the folders above it and its own name. */
  path: string;
  name: string;
  depth: number;
  children: TreeNode<F>[];
  /** Every file below it, at any depth. */
  count: number;
}

export type TreeNode<F extends { path: string } = { path: string }> = TreeFile<F> | TreeFolder<F>;

/** Names in the order a person reads a listing: letters without regard to
 *  case, numbers by their value ("file2" before "file10"). Two names that
 *  read the same keep one order all the same. */
export function byName(a: string, b: string): number {
  return (
    a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }) ||
    (a < b ? -1 : a > b ? 1 : 0)
  );
}

/** Folders first, then files, each by name. */
function order<F extends { path: string }>(nodes: TreeNode<F>[]): TreeNode<F>[] {
  return nodes.sort((a, b) =>
    a.kind !== b.kind ? (a.kind === "folder" ? -1 : 1) : byName(a.name, b.name),
  );
}

/** The tree of a list of files. A path's empty parts ("a//b", a trailing
 *  "/") are not folders. */
export function buildTree<F extends { path: string }>(files: readonly F[]): TreeNode<F>[] {
  interface Draft {
    folders: Map<string, Draft>;
    files: TreeFile<F>[];
    path: string;
    name: string;
    depth: number;
  }
  const root: Draft = { folders: new Map(), files: [], path: "", name: "", depth: -1 };
  for (const file of files) {
    const parts = file.path.split("/").filter(Boolean);
    const name = parts.pop();
    if (!name) continue;
    let at = root;
    for (const part of parts) {
      let next = at.folders.get(part);
      if (!next) {
        next = {
          folders: new Map(),
          files: [],
          path: at.path ? `${at.path}/${part}` : part,
          name: part,
          depth: at.depth + 1,
        };
        at.folders.set(part, next);
      }
      at = next;
    }
    at.files.push({ kind: "file", path: file.path, name, depth: at.depth + 1, file });
  }
  const done = (draft: Draft): TreeNode<F>[] =>
    order<F>([
      ...[...draft.folders.values()].map((d): TreeFolder<F> => {
        const children = done(d);
        const count = children.reduce((n, c) => n + (c.kind === "file" ? 1 : c.count), 0);
        return { kind: "folder", path: d.path, name: d.name, depth: d.depth, children, count };
      }),
      ...draft.files,
    ]);
  return done(root);
}

/** One row of the tree as the panel draws it. */
export interface TreeRow<F extends { path: string } = { path: string }> {
  node: TreeNode<F>;
  /** The folder it is in; null at the top. */
  parent: string | null;
  /** Its place among the rows beside it, counting from one, and how many
   *  there are: what a screen reader says of it. */
  position: number;
  siblings: number;
  /** For a folder: whether it is open. */
  open: boolean;
}

/** The rows that are on screen, top to bottom: everything but what is inside
 *  a closed folder. */
export function visibleRows<F extends { path: string }>(
  tree: readonly TreeNode<F>[],
  collapsed: ReadonlySet<string>,
): TreeRow<F>[] {
  const rows: TreeRow<F>[] = [];
  const walk = (nodes: readonly TreeNode<F>[], parent: string | null) => {
    nodes.forEach((node, i) => {
      const open = node.kind === "folder" && !collapsed.has(node.path);
      rows.push({ node, parent, position: i + 1, siblings: nodes.length, open });
      if (node.kind === "folder" && open) walk(node.children, node.path);
    });
  };
  walk(tree, null);
  return rows;
}

/** Every file's path in the order the tree lists them, open or not. */
export function treeOrder<F extends { path: string }>(tree: readonly TreeNode<F>[]): string[] {
  return tree.flatMap((node) => (node.kind === "file" ? [node.path] : treeOrder(node.children)));
}

/** Every folder's path, outermost first. */
export function folderPaths<F extends { path: string }>(tree: readonly TreeNode<F>[]): string[] {
  return tree.flatMap((node) =>
    node.kind === "folder" ? [node.path, ...folderPaths(node.children)] : [],
  );
}

/** The folders a path is in, outermost first: "a/b/c.py" is in "a" and "a/b". */
export function foldersAbove(path: string): string[] {
  const parts = path.split("/").filter(Boolean);
  parts.pop();
  return parts.map((_, i) => parts.slice(0, i + 1).join("/"));
}

/** "1 file", "6 files". */
export function fileCount(n: number): string {
  return `${n} file${n === 1 ? "" : "s"}`;
}
