import { describe, expect, it } from "vitest";
import type { SystemSnapshot } from "@/lib/types";
import { sysFacts } from "./sys";

const base: SystemSnapshot = {
  at: 1,
  source: "procfs",
  cpus: 2,
  cpu_pct: 37.4,
  cores_pct: [40, 34.8],
  mem_total_mb: 3900,
  mem_used_mb: 2150,
  rss_mb: 1380,
  limit_mb: 3072,
  limit_high_mb: 2600,
  cgroup_mb: 1410,
  load: [1.9, 1.234, 0.8],
};

describe("sysFacts", () => {
  it("words each figure the way the page prints it", () => {
    expect(sysFacts(base)).toEqual({
      cpus: "2 cores",
      cpu: "37%",
      ram: "2.1 / 3.8 GB",
      rss: "1.3 GB",
      "rss-exact": "1.35 GB",
      limit: "2.5 GB",
      load: "1.90  1.23  0.80",
    });
  });

  it("says core for one and keeps a fractional quota", () => {
    expect(sysFacts({ ...base, cpus: 1 }).cpus).toBe("1 core");
    expect(sysFacts({ ...base, cpus: 1.5 }).cpus).toBe("1.5 cores");
  });

  it("takes the lower of the two limits, whichever is set", () => {
    expect(sysFacts({ ...base, limit_high_mb: null }).limit).toBe("3.0 GB");
    expect(sysFacts({ ...base, limit_mb: null }).limit).toBe("2.5 GB");
    expect(sysFacts({ ...base, limit_mb: null, limit_high_mb: null }).limit).toBeNull();
  });

  it("has nothing to say about a figure the machine did not give", () => {
    const none = sysFacts({
      ...base,
      cpus: null,
      cpu_pct: null,
      mem_used_mb: null,
      rss_mb: null,
      load: null,
    });
    expect(none.cpus).toBeNull();
    expect(none.cpu).toBeNull();
    expect(none.ram).toBeNull();
    expect(none.rss).toBeNull();
    expect(none["rss-exact"]).toBeNull();
    expect(none.load).toBeNull();
  });
});
