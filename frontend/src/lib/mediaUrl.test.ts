import { describe, expect, it } from "vitest";
import { checkMediaUrl } from "./mediaUrl";

const ID = "dQw4w9WgXcQ";
const IG = "C9xYz_AbC-1";

describe("checkMediaUrl", () => {
  it.each([
    [`https://www.youtube.com/watch?v=${ID}`, false],
    [`https://youtu.be/${ID}?si=x`, false],
    [`youtu.be/${ID}`, false],
    [`https://m.youtube.com/watch?v=${ID}&t=3`, false],
    [`https://www.youtube.com/shorts/${ID}`, true],
    [`https://www.youtube.com/embed/${ID}`, false],
  ])("accepts YouTube %s", (url, short) => {
    const r = checkMediaUrl(url);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.platform).toBe("youtube");
      expect(r.videoId).toBe(ID);
      expect(r.isShort).toBe(short);
    }
  });

  it.each([
    `https://www.instagram.com/p/${IG}/`,
    `https://www.instagram.com/reel/${IG}/?igsh=abc`,
    `https://instagram.com/reels/${IG}`,
    `https://www.instagram.com/some.user/reel/${IG}/`,
    `instagram.com/tv/${IG}`,
    `https://instagr.am/p/${IG}`,
  ])("accepts Instagram %s", (url) => {
    const r = checkMediaUrl(url);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.platform).toBe("instagram");
      expect(r.videoId).toBe(IG);
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
    "https://www.instagram.com/some.user/",
    `https://www.instagram.com/${IG}/`,
    "https://www.instagram.com/share/reel/BAbc123/",
    "https://www.instagram.com/stories/some.user/123456789/",
    "https://www.instagram.com/p/abc/",
    `https://instagram.com.evil.com/p/${IG}/`,
  ])("rejects %s", (url) => {
    expect(checkMediaUrl(url).ok).toBe(false);
  });

  it("treats empty input as not-yet-entered", () => {
    expect(checkMediaUrl("  ")).toEqual({ ok: false, reason: "" });
  });
});
