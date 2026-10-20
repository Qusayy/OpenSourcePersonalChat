import { describe, expect, it } from "vitest";
import {
  buildTree,
  byName,
  fileCount,
  folderPaths,
  foldersAbove,
  type TreeNode,
  treeOrder,
  visibleRows,
} from "./tree";

const files = (...paths: string[]) => paths.map((path) => ({ path }));

/** A tree as names, folders with "/" and what is in them. */
function shape(nodes: readonly TreeNode[]): unknown[] {
  return nodes.map((n) =>
    n.kind === "file" ? n.name : { [`${n.name}/ ${n.count}`]: shape(n.children) },
  );
}

describe("buildTree", () => {
  it("puts each file in the folders its path names", () => {
    const tree = buildTree(
      files(
        "app/main.py",
        "app/routes/health.py",
        "app/routes/users.py",
        "app/models.py",
        "tests/test_main.py",
        "README.md",
        "requirements.txt",
      ),
    );
    expect(shape(tree)).toEqual([
      { "app/ 4": [{ "routes/ 2": ["health.py", "users.py"] }, "main.py", "models.py"] },
      { "tests/ 1": ["test_main.py"] },
      "README.md",
      "requirements.txt",
    ]);
    const app = tree[0];
    expect(app).toMatchObject({ kind: "folder", path: "app", depth: 0 });
    const routes = app?.kind === "folder" ? app.children[0] : null;
    expect(routes).toMatchObject({ kind: "folder", path: "app/routes", depth: 1, count: 2 });
    const health = routes?.kind === "folder" ? routes.children[0] : null;
    expect(health).toMatchObject({
      kind: "file",
      path: "app/routes/health.py",
      name: "health.py",
      depth: 2,
      file: { path: "app/routes/health.py" },
    });
  });

  it("lists folders before files, by name, whatever the case, numbers by their value", () => {
    const tree = buildTree(
      files("b.py", "Z/x.py", "a/x.py", "file10.txt", "File2.txt", "file1.txt", "B/y.py"),
    );
    expect(shape(tree)).toEqual([
      { "a/ 1": ["x.py"] },
      { "B/ 1": ["y.py"] },
      { "Z/ 1": ["x.py"] },
      "b.py",
      "file1.txt",
      "File2.txt",
      "file10.txt",
    ]);
    // Two names that differ only in case still have one order.
    expect(byName("a.py", "A.py")).toBeGreaterThan(0);
    expect(byName("A.py", "a.py")).toBeLessThan(0);
    expect(byName("same", "same")).toBe(0);
  });

  it("keeps a file and a folder of the same name apart", () => {
    const tree = buildTree(files("app.py", "app/main.py", "app"));
    expect(shape(tree)).toEqual([{ "app/ 1": ["main.py"] }, "app", "app.py"]);
  });

  it("goes as deep as the paths do, and counts every file below a folder", () => {
    const deep = `${Array.from({ length: 12 }, (_, i) => `d${i + 1}`).join("/")}/leaf.py`;
    const tree = buildTree(files(deep, "d1/top.py"));
    let node: TreeNode | undefined = tree[0];
    const seen: string[] = [];
    while (node?.kind === "folder") {
      seen.push(`${node.name}:${node.depth}:${node.count}`);
      node = node.children[0];
    }
    expect(seen).toHaveLength(12);
    expect(seen[0]).toBe("d1:0:2");
    expect(seen[11]).toBe("d12:11:1");
    expect(node).toMatchObject({ kind: "file", name: "leaf.py", depth: 12, path: deep });
    expect(folderPaths(tree)).toHaveLength(12);
    expect(foldersAbove(deep)).toHaveLength(12);
  });

  it("makes no folder of an empty part of a path, and has nothing for no files", () => {
    // "d/" names a file "d": nothing is in it to make it a folder.
    expect(shape(buildTree(files("a//b.py", "/c.py", "d/")))).toEqual([
      { "a/ 1": ["b.py"] },
      "c.py",
      "d",
    ]);
    expect(buildTree([])).toEqual([]);
  });
});

describe("the rows on screen", () => {
  const tree = buildTree(
    files("app/main.py", "app/routes/health.py", "app/routes/users.py", "tests/t.py", "README.md"),
  );
  const rows = (collapsed: string[]) =>
    visibleRows(tree, new Set(collapsed)).map(
      (r) =>
        `${"  ".repeat(r.node.depth)}${r.node.name}${r.node.kind === "folder" ? (r.open ? "/" : "/+") : ""}`,
    );

  it("are every folder and file while everything is open", () => {
    expect(rows([])).toEqual([
      "app/",
      "  routes/",
      "    health.py",
      "    users.py",
      "  main.py",
      "tests/",
      "  t.py",
      "README.md",
    ]);
  });

  it("leave out what is inside a closed folder, at any depth", () => {
    expect(rows(["app/routes"])).toEqual([
      "app/",
      "  routes/+",
      "  main.py",
      "tests/",
      "  t.py",
      "README.md",
    ]);
    expect(rows(["app", "tests"])).toEqual(["app/+", "tests/+", "README.md"]);
    // A folder closed inside a closed one stays closed when its parent opens.
    expect(rows(["app/routes", "tests"])[1]).toBe("  routes/+");
  });

  it("say where each row is among the rows beside it, and which folder it is in", () => {
    const all = visibleRows(tree, new Set());
    expect(all.map((r) => [r.node.name, r.parent, r.position, r.siblings])).toEqual([
      ["app", null, 1, 3],
      ["routes", "app", 1, 2],
      ["health.py", "app/routes", 1, 2],
      ["users.py", "app/routes", 2, 2],
      ["main.py", "app", 2, 2],
      ["tests", null, 2, 3],
      ["t.py", "tests", 1, 1],
      ["README.md", null, 3, 3],
    ]);
  });
});

describe("tree order and folders", () => {
  it("lists every file in the order the tree shows them, open or not", () => {
    const tree = buildTree(files("z.py", "app/b.py", "app/a/x.py"));
    expect(treeOrder(tree)).toEqual(["app/a/x.py", "app/b.py", "z.py"]);
    expect(folderPaths(tree)).toEqual(["app", "app/a"]);
  });

  it("names the folders a path is in, outermost first", () => {
    expect(foldersAbove("a/b/c.py")).toEqual(["a", "a/b"]);
    expect(foldersAbove("top.py")).toEqual([]);
  });

  it("counts in words", () => {
    expect(fileCount(1)).toBe("1 file");
    expect(fileCount(0)).toBe("0 files");
    expect(fileCount(6)).toBe("6 files");
  });
});
