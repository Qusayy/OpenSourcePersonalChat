import { describe, expect, it } from "vitest";
import { pageOf } from "./page";

describe("pageOf", () => {
  it("names the three pages", () => {
    expect(pageOf("/")).toBe("chat");
    expect(pageOf("/bench")).toBe("bench");
    expect(pageOf("/about")).toBe("about");
  });

  it("ignores a trailing slash", () => {
    expect(pageOf("/bench/")).toBe("bench");
    expect(pageOf("/about//")).toBe("about");
  });

  it("treats an address that does not exist as the chat page", () => {
    expect(pageOf("/no/such/page")).toBe("chat");
    expect(pageOf("/benchmark")).toBe("chat");
    expect(pageOf("")).toBe("chat");
  });
});
