/**
 * Client-side URL pre-check for instant feedback. The server performs the
 * authoritative validation and normalization.
 */
const ID_RE = /^[A-Za-z0-9_-]{11}$/;
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

export type UrlCheck = { ok: true; videoId: string; isShort: boolean } | { ok: false; reason: string };

export function checkYoutubeUrl(raw: string): UrlCheck {
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
  if (url.port && url.port !== "80" && url.port !== "443") return { ok: false, reason: "Only standard YouTube links are supported." };

  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  const segments = url.pathname.split("/").filter(Boolean);
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
    return { ok: false, reason: "Only YouTube links (youtube.com, youtu.be, Shorts) are supported." };
  }
  if (!id) return { ok: false, reason: "Couldn't find a video ID in that link." };
  if (!ID_RE.test(id)) return { ok: false, reason: "The video ID in that link is malformed." };
  return { ok: true, videoId: id, isShort };
}
