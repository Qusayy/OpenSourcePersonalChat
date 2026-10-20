import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  collapseAll,
  collapsedKey,
  expandAll,
  fileEvent,
  flushProject,
  loadProject,
  openFile,
  projectConversation,
  resetProject,
  toggleFolder,
  useProject,
} from "./store";

const state = () => useProject.getState();
const paths = () => state().files.map((f) => f.path);
const closed = () => [...state().collapsed].sort();

/** Write a whole file down the stream, as an answer does. */
function write(path: string, text = "x = 1\n", version = 1) {
  fileEvent({ type: "open", path, lang: "python" });
  fileEvent({ type: "line", path, text });
  fileEvent({ type: "close", path, lang: "python", lines: 1, version, kept: true });
}

beforeEach(() => {
  localStorage.clear();
  resetProject("c1");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("a file written down the stream", () => {
  it("opens, takes its lines on the paint tick, and closes with its version", () => {
    expect(fileEvent({ type: "open", path: "app/main.py", lang: "python" })).toBe(true);
    expect(state().files[0]).toMatchObject({ path: "app/main.py", writing: true, version: null });
    expect(state().selected).toBe("app/main.py");
    fileEvent({ type: "line", path: "app/main.py", text: "a = 1\n" });
    fileEvent({ type: "line", path: "app/main.py", text: "b = 2\n" });
    // Held until the page paints.
    expect(state().files[0]?.content).toBe("");
    flushProject();
    expect(state().files[0]).toMatchObject({ content: "a = 1\nb = 2\n", lines: 2 });
    expect(
      fileEvent({
        type: "close",
        path: "app/main.py",
        lang: "python",
        lines: 2,
        version: 3,
        kept: true,
      }),
    ).toBe(false);
    expect(state().files[0]).toMatchObject({ writing: false, version: 3, why: null });
  });

  it("says why it was not kept, and keeps the version it had", () => {
    write("a.py");
    fileEvent({ type: "open", path: "a.py", lang: "python" });
    fileEvent({
      type: "close",
      path: "a.py",
      lang: "python",
      lines: 0,
      kept: false,
      why: "too big",
    });
    expect(state().files[0]).toMatchObject({ why: "too big", writing: false });
  });

  it("opens every folder it is in as it is written, and only those", () => {
    toggleFolder("app");
    toggleFolder("app/routes");
    toggleFolder("tests");
    write("app/routes/health.py");
    expect(closed()).toEqual(["tests"]);
  });
});

describe("a file moved", () => {
  it("keeps what it held under its new path, with the new version, in order", () => {
    write("b.py", "print('b')\n");
    write("z.py", "print('z')\n");
    fileEvent({ type: "move", from: "z.py", to: "app/a.py", version: 2, lang: "python" });
    expect(paths()).toEqual(["app/a.py", "b.py"]);
    expect(state().files[0]).toMatchObject({ content: "print('z')\n", version: 2 });
  });

  it("is followed when it was the file shown, and opens the folders it went into", () => {
    write("main.py");
    write("other.py");
    openFile("main.py");
    toggleFolder("app"); // a folder of that name, closed beforehand
    fileEvent({ type: "move", from: "main.py", to: "app/main.py", version: 2 });
    expect(state().selected).toBe("app/main.py");
    expect(closed()).toEqual([]);
  });

  it("leaves another file shown where it was, and a move of nothing changes nothing", () => {
    write("a.py");
    write("b.py");
    openFile("a.py");
    fileEvent({ type: "move", from: "b.py", to: "c.py", version: 2 });
    expect(state().selected).toBe("a.py");
    const before = state().files;
    fileEvent({ type: "move", from: "nope.py", to: "x.py" });
    expect(state().files).toBe(before);
  });
});

describe("a file removed", () => {
  beforeEach(() => {
    write("app/main.py");
    write("app/models.py");
    write("app/routes/health.py");
    write("README.md");
  });

  it("goes, and the file shown after it in the tree is shown in its place", () => {
    // The tree lists app/routes/health.py, app/main.py, app/models.py, README.md.
    openFile("app/main.py");
    fileEvent({ type: "remove", path: "app/main.py" });
    expect(paths()).not.toContain("app/main.py");
    expect(state().selected).toBe("app/models.py");
  });

  it("falls back to the file before it when it was the last, and to none at all", () => {
    openFile("README.md");
    fileEvent({ type: "remove", path: "README.md" });
    expect(state().selected).toBe("app/models.py");
    for (const path of ["app/models.py", "app/main.py", "app/routes/health.py"]) {
      fileEvent({ type: "remove", path });
    }
    expect(state().files).toEqual([]);
    expect(state().selected).toBeNull();
  });

  it("leaves the file shown alone when it was another, and opens the folders of the next", () => {
    openFile("README.md");
    fileEvent({ type: "remove", path: "app/models.py" });
    expect(state().selected).toBe("README.md");
    collapseAll();
    openFile("app/main.py");
    toggleFolder("app");
    fileEvent({ type: "remove", path: "app/main.py" });
    // The next in the tree is shown, and so is where it is.
    expect(state().selected).toBe("README.md");
    openFile("app/routes/health.py");
    collapseAll();
    fileEvent({ type: "remove", path: "app/routes/health.py" });
    expect(state().selected).toBe("README.md");
    // A folder with nothing left in it is no longer remembered as closed.
    expect(closed()).toEqual([]);
  });
});

describe("folders", () => {
  beforeEach(() => {
    write("app/main.py");
    write("app/routes/health.py");
    write("tests/test_main.py");
  });

  it("are all open until one is closed, and toggle", () => {
    expect(closed()).toEqual([]);
    toggleFolder("app");
    expect(closed()).toEqual(["app"]);
    toggleFolder("app");
    expect(closed()).toEqual([]);
  });

  it("all close and all open at once", () => {
    collapseAll();
    expect(closed()).toEqual(["app", "app/routes", "tests"]);
    expect(state().selected).toBe("tests/test_main.py");
    expandAll();
    expect(closed()).toEqual([]);
    const same = state().collapsed;
    expandAll();
    expect(state().collapsed).toBe(same);
  });

  it("open above a file that is opened, whatever opened it", () => {
    collapseAll();
    openFile("app/routes/health.py");
    expect(closed()).toEqual(["tests"]);
    expect(state()).toMatchObject({ selected: "app/routes/health.py", open: true });
  });

  it("are remembered for each conversation in this browser", () => {
    toggleFolder("tests");
    expect(JSON.parse(localStorage.getItem(collapsedKey("c1")) ?? "null")).toEqual(["tests"]);
    // Another conversation has folders of its own.
    projectConversation("c2");
    expect(closed()).toEqual([]);
    write("tests/x.py");
    toggleFolder("tests");
    toggleFolder("tests");
    expect(localStorage.getItem(collapsedKey("c2"))).toBeNull();
    // Back to the first: as it was left.
    projectConversation("c1");
    expect(closed()).toEqual(["tests"]);
  });

  it("work without storage, and ignore what is not theirs in it", () => {
    localStorage.setItem(collapsedKey("c3"), "{not json");
    resetProject("c3");
    expect(closed()).toEqual([]);
    localStorage.setItem(collapsedKey("c4"), JSON.stringify(["a", 7, null]));
    resetProject("c4");
    expect(closed()).toEqual(["a"]);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    resetProject("c5");
    write("app/x.py");
    toggleFolder("app");
    expect(closed()).toEqual(["app"]);
  });

  it("are not remembered for a project with no conversation yet", () => {
    resetProject(null);
    write("app/x.py");
    toggleFolder("app");
    expect(closed()).toEqual(["app"]);
    expect(localStorage.length).toBe(0);
  });
});

describe("a stored project", () => {
  it("shows the first file in the tree when none was shown, and keeps the one that was", async () => {
    const row = (path: string) => ({
      path,
      version: 1,
      lang: "python",
      bytes: 1,
      lines: 1,
      created_at: 0,
      content: "x",
    });
    const { api } = await import("@/lib/api");
    const project = vi
      .spyOn(api, "project")
      .mockResolvedValue([row("z.py"), row("app/routes/a.py"), row("app/b.py")]);
    resetProject("c9");
    await loadProject("c9");
    expect(state().selected).toBe("app/routes/a.py");
    openFile("z.py");
    await loadProject("c9");
    expect(state().selected).toBe("z.py");
    expect(project).toHaveBeenCalledTimes(2);
  });
});
