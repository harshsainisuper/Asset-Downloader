/**
 * Client-side URL pre-check for instant feedback. The server performs the
 * authoritative validation and normalization.
 */
export type Platform = "youtube" | "instagram";

const YT_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const IG_ID_RE = /^[A-Za-z0-9_-]{5,64}$/;
const YT_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtube-nocookie.com",
  "www.youtube-nocookie.com",
]);
const SHORT_HOSTS = new Set(["youtu.be", "www.youtu.be"]);
const PATH_PREFIXES = new Set(["shorts", "embed", "v", "live", "e"]);
const IG_HOSTS = new Set(["instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"]);
const IG_KINDS = new Set(["p", "reel", "reels", "tv"]);

export type UrlCheck =
  | { ok: true; platform: Platform; videoId: string; isShort: boolean }
  | { ok: false; reason: string };

export function checkMediaUrl(raw: string): UrlCheck {
  const text = raw.trim();
  if (!text) return { ok: false, reason: "" };
  if (text.length > 2048) return { ok: false, reason: "That link is too long." };
  if (/\s/.test(text)) return { ok: false, reason: "The link contains spaces." };
  let url: URL;
  try {
    url = new URL(text.includes("://") ? text : `https://${text}`);
  } catch {
    return { ok: false, reason: "That doesn't look like a valid link." };
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return { ok: false, reason: "Only http(s) links are supported." };
  if (url.username || url.password) return { ok: false, reason: "Links containing credentials are not allowed." };
  if (url.port && url.port !== "80" && url.port !== "443") {
    return { ok: false, reason: "Only standard YouTube and Instagram links are supported." };
  }

  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  const segments = url.pathname.split("/").filter(Boolean);
  if (IG_HOSTS.has(host)) return checkInstagram(segments);

  let id: string | null = null;
  let isShort = false;
  if (SHORT_HOSTS.has(host)) {
    if (segments.length === 1) id = segments[0];
  } else if (YT_HOSTS.has(host)) {
    if (segments.length === 1 && segments[0] === "watch") id = url.searchParams.get("v");
    else if (segments.length === 2 && PATH_PREFIXES.has(segments[0])) {
      id = segments[1];
      isShort = segments[0] === "shorts";
    } else if (segments[0] === "playlist") return { ok: false, reason: "Playlists are not supported. Paste a single video link." };
  } else {
    return { ok: false, reason: "Only YouTube (youtube.com, youtu.be, Shorts) and Instagram (posts, reels) links are supported." };
  }
  if (!id) return { ok: false, reason: "Couldn't find a video ID in that link." };
  if (!YT_ID_RE.test(id)) return { ok: false, reason: "The video ID in that link is malformed." };
  return { ok: true, platform: "youtube", videoId: id, isShort };
}

function checkInstagram(segments: string[]): UrlCheck {
  if (segments[0] === "share") {
    return { ok: false, reason: "Instagram share links are not supported. Open the link and copy the post or reel address." };
  }
  if (segments[0] === "stories") return { ok: false, reason: "Instagram stories are not supported. Paste a post or reel link." };
  const segs = segments.length === 3 ? segments.slice(1) : segments;
  if (segs.length !== 2 || !IG_KINDS.has(segs[0])) {
    return { ok: false, reason: "That Instagram link doesn't point to a specific post or reel." };
  }
  if (!IG_ID_RE.test(segs[1])) return { ok: false, reason: "The post ID in that Instagram link is malformed." };
  return { ok: true, platform: "instagram", videoId: segs[1], isShort: segs[0] !== "p" && segs[0] !== "tv" };
}
