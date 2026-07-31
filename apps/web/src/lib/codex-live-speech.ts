import { getApiWebSocketUrl } from "@/lib/api";
import { connectLiveSpeech } from "@/lib/speech-api";

type CodexLiveSpeechOptions = {
  lessonId: string;
  text: string;
  voice: string;
  signal: AbortSignal;
  onPlaying: (audio: HTMLAudioElement, model: string, voice: string) => void;
  onStatus: (message: string) => void;
  onElapsed: (seconds: number) => void;
};

export type CodexLiveSpeechPlayback = {
  audio: HTMLAudioElement;
  model: string;
  voice: string;
  done: Promise<void>;
  stop: () => void;
};

type BridgeEvent = {
  type?: string;
  role?: string;
  message?: string;
};

type RealtimeEvent = {
  type?: string;
  error?: { message?: string };
};

function waitForPeerConnection(
  peerConnection: RTCPeerConnection,
  signal: AbortSignal
): Promise<void> {
  if (peerConnection.connectionState === "connected") {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(() => finish(new Error("Codex Live 音频连接超时")), 20_000);
    const onAbort = () => finish(new DOMException("Aborted", "AbortError"));
    const onStateChange = () => {
      if (peerConnection.connectionState === "connected") {
        finish();
      } else if (["failed", "closed", "disconnected"].includes(peerConnection.connectionState)) {
        finish(new Error("Codex Live 音频连接已断开"));
      }
    };
    const finish = (error?: Error) => {
      window.clearTimeout(timeoutId);
      signal.removeEventListener("abort", onAbort);
      peerConnection.removeEventListener("connectionstatechange", onStateChange);
      if (error) {
        reject(error);
      } else {
        resolve();
      }
    };
    signal.addEventListener("abort", onAbort, { once: true });
    peerConnection.addEventListener("connectionstatechange", onStateChange);
    onStateChange();
  });
}

export async function startCodexLiveSpeech({
  lessonId,
  text,
  voice,
  signal,
  onPlaying,
  onStatus,
  onElapsed,
}: CodexLiveSpeechOptions): Promise<CodexLiveSpeechPlayback> {
  const peerConnection = new RTCPeerConnection();
  const dataChannel = peerConnection.createDataChannel("oai-events");
  const audio = new Audio();
  audio.autoplay = true;
  peerConnection.addTransceiver("audio", { direction: "recvonly" });

  let bridgeSocket: WebSocket | null = null;
  let liveModel = "gpt-live-1-codex";
  let liveVoice = voice;
  let elapsedTimer: number | null = null;
  let completionTimer: number | null = null;
  let startedAt = 0;
  let stopped = false;
  let resolveDone!: () => void;
  let rejectDone!: (error: Error) => void;
  const done = new Promise<void>((resolve, reject) => {
    resolveDone = resolve;
    rejectDone = reject;
  });

  const stop = () => {
    if (stopped) {
      return;
    }
    stopped = true;
    if (elapsedTimer !== null) {
      window.clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    if (completionTimer !== null) {
      window.clearTimeout(completionTimer);
      completionTimer = null;
    }
    bridgeSocket?.close();
    dataChannel.close();
    peerConnection.close();
    audio.pause();
    audio.srcObject = null;
    resolveDone();
  };
  const fail = (error: Error) => {
    if (stopped) {
      return;
    }
    stopped = true;
    if (elapsedTimer !== null) {
      window.clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    if (completionTimer !== null) {
      window.clearTimeout(completionTimer);
      completionTimer = null;
    }
    bridgeSocket?.close();
    dataChannel.close();
    peerConnection.close();
    audio.pause();
    audio.srcObject = null;
    rejectDone(error);
  };
  const finishAfterAudioDrain = () => {
    window.setTimeout(stop, 350);
  };
  const abort = () => fail(new DOMException("Aborted", "AbortError"));
  signal.addEventListener("abort", abort, { once: true });
  done.finally(() => signal.removeEventListener("abort", abort)).catch(() => undefined);

  peerConnection.ontrack = (event) => {
    const [remoteStream] = event.streams;
    audio.srcObject = remoteStream ?? new MediaStream([event.track]);
    void audio.play().then(() => {
      if (stopped) {
        return;
      }
      startedAt = performance.now();
      elapsedTimer = window.setInterval(() => {
        onElapsed((performance.now() - startedAt) / 1000);
      }, 250);
      onPlaying(audio, liveModel, liveVoice);
    }).catch((error: unknown) => {
      fail(error instanceof Error ? error : new Error("浏览器没有成功播放 Codex Live 音频"));
    });
  };

  dataChannel.onmessage = (event) => {
    try {
      const payload = JSON.parse(String(event.data)) as RealtimeEvent;
      if (payload.type === "response.created") {
        onStatus("Codex Live 正在生成实时语音…");
      } else if (payload.type === "response.done") {
        finishAfterAudioDrain();
      } else if (payload.type === "error") {
        fail(new Error(payload.error?.message || "Codex Live 播报失败"));
      }
    } catch {
      // Ignore unrelated transport events.
    }
  };

  try {
    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);
    const clientSessionId = `speech_${crypto.randomUUID()}`;
    const response = await connectLiveSpeech(
      lessonId,
      {
        offerSdp: offer.sdp ?? "",
        clientSessionId,
        voice,
      },
      signal
    );
    if (!response.delegation_websocket_url) {
      throw new Error("Codex Live 响应缺少播报控制通道");
    }
    liveModel = response.model;
    liveVoice = response.voice;
    await peerConnection.setRemoteDescription({ type: "answer", sdp: response.answer_sdp });
    bridgeSocket = new WebSocket(getApiWebSocketUrl(response.delegation_websocket_url));

    const bridgeReady = new Promise<void>((resolve, reject) => {
      const timeoutId = window.setTimeout(() => reject(new Error("Codex Live 播报控制通道连接超时")), 20_000);
      const finishReady = () => {
        window.clearTimeout(timeoutId);
        resolve();
      };
      bridgeSocket!.onerror = () => reject(new Error("Codex Live 播报控制通道连接失败"));
      bridgeSocket!.onclose = () => {
        if (!stopped) {
          fail(new Error("Codex Live 播报控制通道已断开"));
        }
      };
      bridgeSocket!.onmessage = (event) => {
        try {
          const payload = JSON.parse(String(event.data)) as BridgeEvent;
          if (payload.type === "codex_live.ready") {
            finishReady();
          } else if (payload.type === "codex_live.announcement.accepted") {
            onStatus("Codex Live 已接收播报内容");
          } else if (payload.type === "codex_live.announcement.error" || payload.type === "codex_live.error") {
            fail(new Error(payload.message || "Codex Live 播报失败"));
          } else if (payload.type === "codex_live.transcript.done" && payload.role === "assistant") {
            finishAfterAudioDrain();
          }
        } catch {
          // Ignore unrelated bridge events.
        }
      };
    });

    await Promise.all([bridgeReady, waitForPeerConnection(peerConnection, signal)]);
    if (bridgeSocket.readyState !== WebSocket.OPEN) {
      throw new Error("Codex Live 播报控制通道尚未就绪");
    }
    bridgeSocket.send(JSON.stringify({ type: "announcement.play", text }));
    completionTimer = window.setTimeout(
      () => fail(new Error("Codex Live 播报等待超时")),
      120_000
    );
    onStatus("Codex Live 正在准备播报…");
    return {
      audio,
      model: response.model,
      voice: response.voice,
      done,
      stop,
    };
  } catch (error) {
    fail(error instanceof Error ? error : new Error("Codex Live 播报连接失败"));
    throw error;
  }
}
