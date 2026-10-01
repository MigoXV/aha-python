export type Event = {
  type: string;
  event_id?: string;
  item_id?: string;
  response_id?: string;
  delta?: string;
  transcript?: string;
  text?: string;
  [key: string]: unknown;
};
export type Entry = {
  id: string;
  role: "user" | "assistant";
  text: string;
  complete: boolean;
  interim?: boolean;
};
export type Log = { seq: number; time: string; type: string; detail: string };

export function updateEntries(entries: Entry[], event: Event): Entry[] {
  if (event.type === "x_aha.input_audio_transcription.interim_cleared")
    return entries.filter(
      (entry) => entry.id !== `input:${event.item_id}` || !entry.interim,
    );
  const interim = event.type === "x_aha.input_audio_transcription.interim";
  const transcription =
    interim ||
    event.type === "conversation.item.input_audio_transcription.delta" ||
    event.type === "conversation.item.input_audio_transcription.completed";
  const output = [
    "response.output_text.delta",
    "response.output_text.done",
    "response.output_audio_transcript.delta",
    "response.output_audio_transcript.done",
  ].includes(event.type);
  if (!transcription && !output) return entries;
  const id = `${transcription ? "input" : "output"}:${event.item_id ?? event.response_id ?? "current"}`;
  const complete =
    event.type.endsWith(".completed") || event.type.endsWith(".done");
  const existing = entries.find((entry) => entry.id === id);
  const text =
    complete || interim
      ? (event.transcript ?? event.text ?? existing?.text ?? "")
      : (existing?.interim ? "" : existing?.text ?? "") + (event.delta ?? "");
  const entry: Entry = {
    id,
    role: transcription ? "user" : "assistant",
    text,
    complete,
    interim,
  };
  return (
    existing
      ? entries.map((value) => (value.id === id ? entry : value))
      : [...entries, entry]
  ).slice(-200);
}

export function inspectEvent(event: Event): string {
  const payload = { ...event };
  if (
    event.type === "response.output_audio.delta" &&
    typeof event.delta === "string"
  ) {
    payload.delta = `[PCM S16LE · 24 kHz · ${Math.floor((event.delta.length * 3) / 4) - (event.delta.match(/=+$/)?.[0].length ?? 0)} bytes]`;
  }
  const json = JSON.stringify(payload, null, 2);
  return json.length <= 32768
    ? json
    : json.slice(0, 32768) + "\n…（内容过长，检查器已截断）";
}
