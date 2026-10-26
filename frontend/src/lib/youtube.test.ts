import { describe, expect, it } from "vitest";
import { checkYoutubeUrl } from "./youtube";

const ID = "dQw4w9WgXcQ";

describe("checkYoutubeUrl", () => {
  it.each([
    [`https://www.youtube.com/watch?v=${ID}`, false],
    [`https://youtu.be/${ID}?si=x`, false],
    [`youtu.be/${ID}`, false],
    [`https://m.youtube.com/watch?v=${ID}&t=3`, false],
    [`https://www.youtube.com/shorts/${ID}`, true],
    [`https://www.youtube.com/embed/${ID}`, false],
  ])("accepts %s", (url, short) => {
    const r = checkYoutubeUrl(url);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.videoId).toBe(ID);
      expect(r.isShort).toBe(short);
    }
  });

  it.each([
    `https://evil.com/watch?v=${ID}`,
    `https://youtube.com.evil.com/watch?v=${ID}`,
    `https://user:pw@youtube.com/watch?v=${ID}`,
    `https://youtube.com:8443/watch?v=${ID}`,
    "https://www.youtube.com/watch?v=tooshort",
    "https://www.youtube.com/playlist?list=PL1",
    `ftp://youtube.com/watch?v=${ID}`,
    "not a url",
  ])("rejects %s", (url) => {
    expect(checkYoutubeUrl(url).ok).toBe(false);
  });

  it("treats empty input as not-yet-entered", () => {
    expect(checkYoutubeUrl("  ")).toEqual({ ok: false, reason: "" });
  });
});
