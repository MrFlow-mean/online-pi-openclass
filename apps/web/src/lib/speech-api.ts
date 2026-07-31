import { getApiBase, readEffectiveAuthToken } from "@/lib/api";

export type SpeechAudioResponse = {
  audio: Blob;
  model: string | null;
  voice: string | null;
};

export type SpeechVoiceOption = {
  id: string;
  label: string;
  description: string;
};

export type SpeechOptionsResponse = {
  provider: string;
  model: string;
  default_voice: string;
  voices: SpeechVoiceOption[];
  minimum_speech_rate: number;
  maximum_speech_rate: number;
  default_speech_rate: number;
  delivery: "buffered_audio" | "buffered_live_audio" | "realtime_audio";
  supports_speech_rate: boolean;
  supports_seek: boolean;
};

export type SpeechSynthesisOptions = {
  voice?: string;
  speechRate?: number;
};

export type LiveSpeechConnectResponse = {
  answer_sdp: string;
  provider: string;
  model: string;
  voice: string;
  delegation_websocket_url?: string | null;
  client_session_id?: string | null;
};

async function speechApiError(response: Response, fallback: string) {
  let message = fallback;
  const body = await response.text();
  if (body.trim()) {
    try {
      const payload = JSON.parse(body) as { detail?: unknown };
      if (typeof payload.detail === "string" && payload.detail.trim()) {
        message = payload.detail.trim();
      }
    } catch {
      message = body.trim();
    }
  }
  return new Error(message);
}

export async function getSpeechOptions(signal?: AbortSignal): Promise<SpeechOptionsResponse> {
  const token = readEffectiveAuthToken();
  const response = await fetch(`${getApiBase()}/api/speech/options`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    cache: "no-store",
    credentials: "include",
    signal,
  });
  if (!response.ok) {
    throw await speechApiError(response, "无法读取语音模型配置");
  }
  return (await response.json()) as SpeechOptionsResponse;
}

export async function synthesizeSpeech(
  input: string,
  options: SpeechSynthesisOptions = {},
  signal?: AbortSignal
): Promise<SpeechAudioResponse> {
  const token = readEffectiveAuthToken();
  const response = await fetch(`${getApiBase()}/api/speech`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      input,
      voice: options.voice,
      speech_rate: options.speechRate,
    }),
    cache: "no-store",
    credentials: "include",
    signal,
  });

  if (!response.ok) {
    throw await speechApiError(response, "语音模型没有成功生成音频");
  }

  return {
    audio: await response.blob(),
    model: response.headers.get("X-Speech-Model"),
    voice: response.headers.get("X-Speech-Voice"),
  };
}

export async function connectLiveSpeech(
  lessonId: string,
  payload: { offerSdp: string; clientSessionId: string; voice: string },
  signal?: AbortSignal
): Promise<LiveSpeechConnectResponse> {
  const token = readEffectiveAuthToken();
  const response = await fetch(
    `${getApiBase()}/api/lessons/${encodeURIComponent(lessonId)}/speech/live/connect`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        offer_sdp: payload.offerSdp,
        client_session_id: payload.clientSessionId,
        voice: payload.voice,
      }),
      cache: "no-store",
      credentials: "include",
      signal,
    }
  );
  if (!response.ok) {
    throw await speechApiError(response, "Codex Live 没有成功建立播报连接");
  }
  return (await response.json()) as LiveSpeechConnectResponse;
}
