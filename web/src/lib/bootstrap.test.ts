import { describe, expect, it } from "vitest";
import { readEmbedded } from "./bootstrap";

function page(inner: string): Document {
  const doc = document.implementation.createHTMLDocument("t");
  doc.body.innerHTML = `<script type="application/json" id="bootstrap">${inner}</script>`;
  return doc;
}

describe("readEmbedded", () => {
  it("reads what the server wrote", () => {
    expect(readEmbedded(page('{"version":"4.0.0","personas":[]}'))).toEqual({
      version: "4.0.0",
      personas: [],
    });
  });

  it("reads text the server escaped so it could not close the element", () => {
    // What script_json in app/web.py produces for "</script><b>&".
    const boot = readEmbedded(page('{"title":"\\u003c/script\\u003e\\u003cb\\u003e\\u0026"}'));
    expect(boot).toEqual({ title: "</script><b>&" });
  });

  it("is null under the dev server, where nothing was written", () => {
    expect(readEmbedded(page("null"))).toBeNull();
  });

  it("is null when the element is missing, empty or not JSON", () => {
    expect(readEmbedded(document.implementation.createHTMLDocument("t"))).toBeNull();
    expect(readEmbedded(page(""))).toBeNull();
    expect(readEmbedded(page("{not json"))).toBeNull();
    expect(readEmbedded(page('"a string"'))).toBeNull();
  });
});
