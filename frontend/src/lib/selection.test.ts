import { describe, expect, it } from "vitest";
import { ALL_SELECTED, DEFAULT_SETTINGS, EMPTY_SELECTION, adaptSettings, buildOutputs, canProcess } from "./selection";
import type { OutputSettings, VideoInfo } from "./types";

const info: VideoInfo = {
  video_id: "dQw4w9WgXcQ",
  title: "t",
  channel: null,
  duration: 10,
  is_short: false,
  thumbnail_url: null,
  webpage_url: "",
  video: {
    available: true,
    qualities: [
      { height: 1080, label: "1080p", fps: 30, hdr: false, mp4: "h264", webm: "native", standard: true },
      { height: 720, label: "720p", fps: 30, hdr: false, mp4: "h264", webm: "unavailable", standard: true },
    ],
    unavailable_standard: [2160, 1440, 480, 360],
  },
  audio: { available: true, source_codec: "OPUS", source_kbps: 130 },
  captions: { tracks: [], default_track_id: null },
  transcription: { stt_available: false, stt_label: null },
};

const settings = (): OutputSettings => structuredClone(DEFAULT_SETTINGS);

describe("buildOutputs", () => {
  it("omits unselected outputs entirely", () => {
    const out = buildOutputs({ ...EMPTY_SELECTION, audio: true }, settings());
    expect(Object.keys(out)).toEqual(["audio"]);
  });

  it("includes every output when all are selected", () => {
    expect(Object.keys(buildOutputs(ALL_SELECTED, settings()))).toEqual(["video", "audio", "thumbnail", "transcript"]);
  });

  it("drops the bitrate for non-MP3 audio", () => {
    const s = settings();
    s.audio.format = "wav";
    expect(buildOutputs({ ...EMPTY_SELECTION, audio: true }, s)).toEqual({ audio: { format: "wav" } });
  });
});

describe("canProcess", () => {
  it("requires fetched info and a selection", () => {
    expect(canProcess(null, ALL_SELECTED, settings(), true).ok).toBe(false);
    expect(canProcess(info, EMPTY_SELECTION, settings(), true).ok).toBe(false);
    expect(canProcess(info, { ...EMPTY_SELECTION, video: true }, settings(), false).ok).toBe(false);
    expect(canProcess(info, { ...EMPTY_SELECTION, video: true }, settings(), true).ok).toBe(true);
  });

  it("blocks unavailable quality and container combinations", () => {
    const s = settings();
    s.video.quality = 2160;
    expect(canProcess(info, { ...EMPTY_SELECTION, video: true }, s, true).reason).toContain("2160p");
    s.video.quality = 720;
    s.video.format = "webm";
    expect(canProcess(info, { ...EMPTY_SELECTION, video: true }, s, true).ok).toBe(false);
  });

  it("blocks transcripts without captions or STT", () => {
    expect(canProcess(info, { ...EMPTY_SELECTION, transcript: true }, settings(), true).ok).toBe(false);
  });
});

describe("adaptSettings", () => {
  it("falls back to best when the saved quality does not exist", () => {
    const s = settings();
    s.video.quality = 360;
    expect(adaptSettings(s, info).video.quality).toBe("best");
  });
});
