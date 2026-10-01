import { describe, expect, it } from "vitest";
import { inspectEvent, updateEntries } from "./events";

describe("SDK events", () => {
  it("uses AHA interim revisions as snapshots before the completed result", () => {
    let entries = updateEntries([], {
      type: "x_aha.input_audio_transcription.interim",
      item_id: "i",
      transcript: "你",
      revision: 1,
    });
    entries = updateEntries(entries, {
      type: "x_aha.input_audio_transcription.interim",
      item_id: "i",
      transcript: "你好",
      revision: 2,
    });
    expect(entries).toHaveLength(1);
    expect(entries[0].text).toBe("你好");
    expect(
      updateEntries(entries, {
        type: "conversation.item.input_audio_transcription.completed",
        item_id: "i",
        transcript: "你好。",
      })[0],
    ).toMatchObject({ text: "你好。", complete: true });
  });
  it("replaces partial transcripts with the canonical completed text", () => {
    const entries = updateEntries([], {
      type: "conversation.item.input_audio_transcription.delta",
      item_id: "i",
      delta: "你",
    });
    expect(entries[0].complete).toBe(false);
    expect(
      updateEntries(entries, {
        type: "conversation.item.input_audio_transcription.completed",
        item_id: "i",
        transcript: "你好",
      })[0],
    ).toMatchObject({ text: "你好", complete: true });
  });
  it("clears superseded interim text before primary deltas without duplicating it", () => {
    const interim = updateEntries([], {
      type: "x_aha.input_audio_transcription.interim",
      item_id: "i",
      transcript: "临时文本",
    });
    const cleared = updateEntries(interim, {
      type: "x_aha.input_audio_transcription.interim_cleared",
      item_id: "i",
      reason: "primary_transcript_started",
    });
    expect(cleared).toHaveLength(0);
    const delta = {
      type: "conversation.item.input_audio_transcription.delta",
      item_id: "i",
      delta: "正式文本",
    };
    expect(updateEntries(cleared, delta)[0].text).toBe("正式文本");
    expect(updateEntries(interim, delta)[0].text).toBe("正式文本");
    const completed = updateEntries(interim, {
      type: "conversation.item.input_audio_transcription.completed",
      item_id: "i",
      transcript: "完整文本",
    });
    expect(updateEntries(completed, {
      type: "x_aha.input_audio_transcription.interim_cleared",
      item_id: "i",
      reason: "superseded",
    })[0].text).toBe("完整文本");
  });
  it("keeps unknown events visible and audio payloads bounded", () => {
    expect(
      inspectEvent({ type: "future.event", detail: { retained: true } }),
    ).toContain("retained");
    expect(
      inspectEvent({ type: "response.output_audio.delta", delta: "AAEC/w==" }),
    ).toContain("4 bytes");
    expect(
      inspectEvent({ type: "future.event", detail: "x".repeat(100000) }).length,
    ).toBeLessThan(33000);
  });
});
