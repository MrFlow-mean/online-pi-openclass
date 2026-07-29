"use client";

import { useCallback, useEffect, useEffectEvent, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { api, getApiWebSocketUrl } from "@/lib/api";
import {
  PROVIDER_LABELS,
  googleRealtimeErrorMessage,
  modelButtonLabel,
  realtimeConnectionErrorMessage,
  websocketMessageText,
  type GoogleRealtimeAudioMessage,
} from "@/components/course-studio/model-catalog";
import type { AutoSaveReason } from "@/hooks/course-studio/use-board-draft";
import { useRealtimeLogQueue } from "@/hooks/use-realtime-log-queue";
import { pcmFloatToBase64, playPcmBase64, resampleLinear } from "@/lib/realtime-audio";
import {
  addRealtimeBoardReference,
  mergeRealtimeBoardReferenceResults,
} from "@/lib/realtime-board-references";
import type {
  AIModelOption,
  AIModelSelection,
  Lesson,
  RealtimeToolCallResponse,
  RealtimeToolName,
  SelectionRef,
} from "@/types";

export type RealtimeTranscriptUpdate = {
  lessonId: string;
  turnId: string;
  messageId: string;
  role: "user" | "assistant";
  text: string;
  final: boolean;
};

export type RealtimeToolStatusUpdate = {
  lessonId: string;
  turnId: string;
  label: string;
  status: "pending" | "completed" | "error";
};

type RealtimeFunctionCall = {
  callId: string;
  name: RealtimeToolName;
  arguments: Record<string, unknown>;
};

type OpenAIRealtimeEvent = {
  type?: string;
  transcript?: string;
  delta?: string;
  item_id?: string;
  response_id?: string;
  name?: string;
  call_id?: string;
  arguments?: string;
  item?: Record<string, unknown>;
  response?: { output?: Array<Record<string, unknown>> };
};

type CodexLiveBridgeEvent = {
  type?: string;
  role?: "user" | "assistant";
  text?: string;
  message?: string;
  delegation_id?: string;
  result?: RealtimeToolCallResponse;
};

function parseFunctionCall(item: Record<string, unknown> | undefined): RealtimeFunctionCall | null {
  if (item?.type !== "function_call" || typeof item.name !== "string" || typeof item.call_id !== "string") {
    return null;
  }
  try {
    return {
      callId: item.call_id,
      name: item.name as RealtimeToolName,
      arguments: JSON.parse(typeof item.arguments === "string" ? item.arguments : "{}") as Record<string, unknown>,
    };
  } catch {
    return null;
  }
}

export function realtimeFunctionCallsFromEvent(event: OpenAIRealtimeEvent): RealtimeFunctionCall[] {
  if (
    event.type === "response.function_call_arguments.done" &&
    typeof event.name === "string" &&
    typeof event.call_id === "string"
  ) {
    try {
      return [{
        callId: event.call_id,
        name: event.name as RealtimeToolName,
        arguments: JSON.parse(event.arguments || "{}") as Record<string, unknown>,
      }];
    } catch {
      return [];
    }
  }
  const direct = parseFunctionCall(event.item);
  if (direct) {
    return [direct];
  }
  return (event.response?.output ?? []).flatMap((item) => {
    const call = parseFunctionCall(item);
    return call ? [call] : [];
  });
}

function sendOpenAIFunctionOutput(
  dataChannel: RTCDataChannel,
  callId: string,
  output: Record<string, unknown>
) {
  if (dataChannel.readyState !== "open") {
    return;
  }
  dataChannel.send(JSON.stringify({
    type: "conversation.item.create",
    item: {
      type: "function_call_output",
      call_id: callId,
      output: JSON.stringify(output),
    },
  }));
  dataChannel.send(JSON.stringify({
    type: "response.create",
    response: { tools: [] },
  }));
}

function sendOpenAITurnDecisionRequest(dataChannel: RTCDataChannel) {
  if (dataChannel.readyState !== "open") {
    return false;
  }
  dataChannel.send(JSON.stringify({
    type: "response.create",
    response: {
      tool_choice: { type: "function", name: "run_chatbot_workflow" },
    },
  }));
  return true;
}

type UseRealtimeVoiceOptions = {
  activeLesson: Lesson | null;
  latestAssistantMessageContent: string | null;
  selectedRealtimeModel: AIModelSelection;
  selectedRealtimeOption: AIModelOption | null | undefined;
  selectedRealtimeTransport: string;
  busyAction: string | null;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
  setError: Dispatch<SetStateAction<string | null>>;
  flushAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  chatRequestInFlightRef: MutableRefObject<boolean>;
  onSubmitTranscript: (message: string) => void;
  currentSelection: SelectionRef | null;
  currentSelections: SelectionRef[];
  onTranscriptUpdate: (update: RealtimeTranscriptUpdate) => void;
  onToolStatusUpdate: (update: RealtimeToolStatusUpdate) => void;
  onToolResult: (lessonId: string, result: RealtimeToolCallResponse) => void;
};

function createClientSessionId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID()}`;
}

export function useRealtimeVoice({
  activeLesson,
  latestAssistantMessageContent,
  selectedRealtimeModel,
  selectedRealtimeOption,
  selectedRealtimeTransport,
  busyAction,
  setBusyAction,
  setError,
  flushAutoSave,
  chatRequestInFlightRef,
  onSubmitTranscript,
  currentSelection,
  currentSelections,
  onTranscriptUpdate,
  onToolStatusUpdate,
  onToolResult,
}: UseRealtimeVoiceOptions) {
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const realtimePeerRef = useRef<RTCPeerConnection | null>(null);
  const realtimeChannelRef = useRef<RTCDataChannel | null>(null);
  const realtimeStreamRef = useRef<MediaStream | null>(null);
  const googleRealtimeSocketRef = useRef<WebSocket | null>(null);
  const codexLiveSocketRef = useRef<WebSocket | null>(null);
  const googleAudioContextRef = useRef<AudioContext | null>(null);
  const googleAudioProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const googleAudioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const googlePlaybackContextRef = useRef<AudioContext | null>(null);
  const googlePlaybackTimeRef = useRef(0);
  const googlePlaybackSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const googleInputTranscriptRef = useRef("");
  const googleOutputTranscriptRef = useRef("");
  const openAIResponseInProgressRef = useRef(false);
  const openAIRealtimeToolsEnabledRef = useRef(false);
  const openAIClientDelegationEnabledRef = useRef(false);
  const openAIAssistantTranscriptRef = useRef("");
  const openAIAssistantMessageIdRef = useRef<string | null>(null);
  const openAIInputTranscriptsRef = useRef(new Map<string, string>());
  const openAIProcessedToolCallsRef = useRef(new Set<string>());
  const realtimeBoardReferencesRef = useRef<SelectionRef[]>([]);
  const realtimeTurnIdRef = useRef<string | null>(null);
  const currentSelectionRef = useRef<SelectionRef | null>(currentSelection);
  const realtimeLessonIdRef = useRef<string | null>(null);
  const realtimeClientSessionIdRef = useRef<string | null>(null);
  const realtimeLessonTitleRef = useRef<string | null>(null);
  const getRealtimeClientSessionId = useCallback(() => realtimeClientSessionIdRef.current, []);
  const getRealtimeLessonTitle = useCallback(() => realtimeLessonTitleRef.current, []);
  const { enqueueRealtimeLogEvent, flushRealtimeLogQueue, flushRealtimeLogQueueWithBeacon } = useRealtimeLogQueue({
    getClientSessionId: getRealtimeClientSessionId,
    getLessonTitle: getRealtimeLessonTitle,
  });

  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceStatusText, setVoiceStatusText] = useState("点击麦克风，连接实时语音 Chatbot");

  useEffect(() => {
    currentSelectionRef.current = currentSelection;
    const lessonId = realtimeLessonIdRef.current;
    if (!lessonId) {
      return;
    }
    const nextReferences = currentSelections.reduce(
      (references, selection) => addRealtimeBoardReference(references, selection, lessonId),
      [] as SelectionRef[]
    );
    const previousCount = realtimeBoardReferencesRef.current.length;
    realtimeBoardReferencesRef.current = nextReferences;
    if (voiceActive && nextReferences.length !== previousCount) {
      setVoiceStatusText(
        nextReferences.length
          ? `Realtime 已保留 ${nextReferences.length} 个板书引用`
          : "Realtime 已清空板书引用"
      );
    }
    const codexLiveSocket = codexLiveSocketRef.current;
    if (codexLiveSocket?.readyState === WebSocket.OPEN) {
      codexLiveSocket.send(JSON.stringify({
        type: "selection.update",
        selection: currentSelection,
      }));
    }
  }, [currentSelection, currentSelections, voiceActive]);

  function currentTurnId() {
    if (!realtimeTurnIdRef.current) {
      realtimeTurnIdRef.current = createClientSessionId("turn");
    }
    return realtimeTurnIdRef.current;
  }

  function beginRealtimeTurn() {
    const turnId = createClientSessionId("turn");
    realtimeTurnIdRef.current = turnId;
    openAIAssistantTranscriptRef.current = "";
    openAIAssistantMessageIdRef.current = null;
    return turnId;
  }

  function currentAssistantMessageId() {
    if (!openAIAssistantMessageIdRef.current) {
      openAIAssistantMessageIdRef.current = createClientSessionId("realtime-message");
    }
    return openAIAssistantMessageIdRef.current;
  }

  function stopGoogleQueuedPlayback() {
    googlePlaybackSourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch {
        // Already ended or never started.
      }
      try {
        source.disconnect();
      } catch {
        // Already disconnected.
      }
    });
    googlePlaybackSourcesRef.current.clear();
    const playbackContext = googlePlaybackContextRef.current;
    googlePlaybackTimeRef.current = playbackContext?.currentTime ?? 0;
  }

  function queueGooglePlayback(base64: string, mimeType?: string) {
    const playbackContext = googlePlaybackContextRef.current;
    if (!playbackContext) {
      return;
    }
    const source = playPcmBase64(base64, mimeType, playbackContext, googlePlaybackTimeRef);
    googlePlaybackSourcesRef.current.add(source);
    source.addEventListener(
      "ended",
      () => {
        googlePlaybackSourcesRef.current.delete(source);
      },
      { once: true }
    );
  }

  function resetOpenAIRemoteAudioPlayback() {
    const remoteAudio = remoteAudioRef.current;
    const remoteStream = remoteAudio?.srcObject;
    if (!remoteAudio || !remoteStream) {
      return;
    }
    remoteAudio.pause();
    remoteAudio.srcObject = null;
    remoteAudio.srcObject = remoteStream;
    void remoteAudio.play().catch(() => undefined);
  }

  function disposeRealtimeSession() {
    void flushRealtimeLogQueue();
    realtimeChannelRef.current?.close();
    realtimeChannelRef.current = null;
    codexLiveSocketRef.current?.close();
    codexLiveSocketRef.current = null;
    googleRealtimeSocketRef.current?.close();
    googleRealtimeSocketRef.current = null;

    googleAudioProcessorRef.current?.disconnect();
    googleAudioProcessorRef.current = null;
    googleAudioSourceRef.current?.disconnect();
    googleAudioSourceRef.current = null;
    void googleAudioContextRef.current?.close().catch(() => undefined);
    googleAudioContextRef.current = null;
    stopGoogleQueuedPlayback();
    void googlePlaybackContextRef.current?.close().catch(() => undefined);
    googlePlaybackContextRef.current = null;
    googlePlaybackTimeRef.current = 0;
    googleInputTranscriptRef.current = "";
    googleOutputTranscriptRef.current = "";
    openAIResponseInProgressRef.current = false;
    openAIRealtimeToolsEnabledRef.current = false;
    openAIClientDelegationEnabledRef.current = false;
    openAIAssistantTranscriptRef.current = "";
    openAIAssistantMessageIdRef.current = null;
    openAIInputTranscriptsRef.current.clear();
    openAIProcessedToolCallsRef.current.clear();
    realtimeBoardReferencesRef.current = [];
    realtimeTurnIdRef.current = null;

    if (realtimePeerRef.current) {
      realtimePeerRef.current.ontrack = null;
      realtimePeerRef.current.onconnectionstatechange = null;
      realtimePeerRef.current.close();
      realtimePeerRef.current = null;
    }

    realtimeStreamRef.current?.getTracks().forEach((track) => track.stop());
    realtimeStreamRef.current = null;

    if (remoteAudioRef.current) {
      remoteAudioRef.current.pause();
      remoteAudioRef.current.srcObject = null;
    }

    realtimeLessonIdRef.current = null;
    realtimeClientSessionIdRef.current = null;
    realtimeLessonTitleRef.current = null;
  }

  function stopRealtimeSession(statusText = "语音 Chatbot 已断开") {
    disposeRealtimeSession();
    window.speechSynthesis?.cancel();
    setVoiceActive(false);
    setVoiceStatusText(statusText);
    setBusyAction((current) => (current === "voice-connect" ? null : current));
  }

  const stopRealtimeSessionEvent = useEffectEvent((statusText: string) => {
    stopRealtimeSession(statusText);
  });

  function speakControlledChatbotMessage(content: string) {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      return;
    }
    const text = content.trim();
    if (!text) {
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-CN";
    utterance.rate = 1;
    utterance.pitch = 1;
    window.speechSynthesis.speak(utterance);
  }

  function handleRealtimeUserTranscript(lessonId: string, transcript: string, eventType: string) {
    const normalized = transcript.trim();
    if (!normalized) {
      return;
    }
    const turnId = currentTurnId();
    const messageId = `realtime:${turnId}:user`;
    if (!openAIClientDelegationEnabledRef.current) {
      enqueueRealtimeLogEvent(lessonId, "user", eventType, normalized, {
        clientEventId: messageId,
        turnId,
      });
    }
    onTranscriptUpdate({
      lessonId,
      turnId,
      messageId,
      role: "user",
      text: normalized,
      final: true,
    });
    if (openAIRealtimeToolsEnabledRef.current) {
      const dataChannel = realtimeChannelRef.current;
      if (!dataChannel || !sendOpenAITurnDecisionRequest(dataChannel)) {
        setVoiceStatusText("Realtime 回合决策通道未就绪");
        return;
      }
      setVoiceStatusText("Realtime 正在判断本轮需求与板书路径");
      return;
    }
    if (openAIClientDelegationEnabledRef.current) {
      setVoiceStatusText("Codex Live 正在交给 Chatbot 工作流处理");
      return;
    }
    if (chatRequestInFlightRef.current) {
      setVoiceStatusText("正在处理上一句语音，请稍等片刻");
      return;
    }
    onSubmitTranscript(normalized);
  }

  function flushGoogleRealtimeTranscripts(lessonId: string) {
    const userTranscript = googleInputTranscriptRef.current.trim();
    const assistantTranscript = googleOutputTranscriptRef.current.trim();
    if (userTranscript) {
      handleRealtimeUserTranscript(lessonId, userTranscript, "google.input_transcription");
      googleInputTranscriptRef.current = "";
    }
    if (assistantTranscript) {
      const turnId = currentTurnId();
      const messageId = currentAssistantMessageId();
      enqueueRealtimeLogEvent(lessonId, "assistant", "google.output_transcription", assistantTranscript, {
        clientEventId: messageId,
        turnId,
      });
      onTranscriptUpdate({
        lessonId,
        turnId,
        messageId,
        role: "assistant",
        text: assistantTranscript,
        final: true,
      });
      googleOutputTranscriptRef.current = "";
    }
    realtimeTurnIdRef.current = null;
    openAIAssistantMessageIdRef.current = null;
  }

  function beginGoogleAudioStreaming(socket: WebSocket, mediaStream: MediaStream, audioContext: AudioContext) {
    const source = audioContext.createMediaStreamSource(mediaStream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);
    source.connect(processor);
    processor.connect(audioContext.destination);
    googleAudioSourceRef.current = source;
    googleAudioProcessorRef.current = processor;
    processor.onaudioprocess = (event) => {
      if (socket.readyState !== WebSocket.OPEN) {
        return;
      }
      const input = event.inputBuffer.getChannelData(0);
      const resampled = resampleLinear(input, audioContext.sampleRate, 16000);
      socket.send(
        JSON.stringify({
          realtimeInput: {
            audio: {
              mimeType: "audio/pcm;rate=16000",
              data: pcmFloatToBase64(resampled),
            },
          },
        })
      );
    };
  }

  function handleGoogleRealtimeMessage(message: GoogleRealtimeAudioMessage) {
    const lessonId = realtimeLessonIdRef.current;
    if (!lessonId) {
      return;
    }
    const serverContent = message.serverContent;
    if (!serverContent) {
      return;
    }
    const inputText = serverContent.inputTranscription?.text;
    if (inputText) {
      currentTurnId();
      googleInputTranscriptRef.current += inputText;
    }
    if (serverContent.interrupted) {
      beginRealtimeTurn();
      stopGoogleQueuedPlayback();
      googleOutputTranscriptRef.current = "";
      setVoiceStatusText("检测到插话，已停止上一段回答");
    }
    const outputText = serverContent.outputTranscription?.text;
    if (outputText && !serverContent.interrupted) {
      googleOutputTranscriptRef.current += outputText;
    }
    serverContent.modelTurn?.parts?.forEach((part) => {
      const inlineData = part.inlineData;
      if (!inlineData?.data || serverContent.interrupted) {
        return;
      }
      queueGooglePlayback(inlineData.data, inlineData.mimeType);
    });
    if (serverContent.turnComplete) {
      flushGoogleRealtimeTranscripts(lessonId);
    }
  }

  async function startGoogleRealtimeSession(lesson: Lesson, mediaStream: MediaStream, clientSessionId: string) {
    const session = await api.createGoogleRealtimeSession(lesson.id, {
      latest_assistant_message: latestAssistantMessageContent,
      client_session_id: clientSessionId,
      realtime_model: selectedRealtimeModel,
    });
    const audioContext = new AudioContext();
    const playbackContext = new AudioContext();
    googleAudioContextRef.current = audioContext;
    googlePlaybackContextRef.current = playbackContext;
    googlePlaybackTimeRef.current = playbackContext.currentTime;
    await audioContext.resume();
    await playbackContext.resume();

    const socket = new WebSocket(getApiWebSocketUrl(session.websocket_url));
    googleRealtimeSocketRef.current = socket;
    await new Promise<void>((resolve, reject) => {
      let streamingStarted = false;
      let settled = false;
      const resolveStart = () => {
        if (settled) {
          return;
        }
        settled = true;
        resolve();
      };
      const rejectStart = (message: string) => {
        if (settled) {
          return;
        }
        settled = true;
        reject(new Error(message));
      };
      socket.onopen = () => {
        socket.send(JSON.stringify(session.setup));
      };
      socket.onerror = () => {
        rejectStart("Google Gemini Live WebSocket 连接失败");
      };
      socket.onclose = (event) => {
        if (!streamingStarted) {
          rejectStart(
            `Google Gemini Live WebSocket 在初始化前关闭（${event.code}${event.reason ? `：${event.reason}` : ""}）`
          );
        }
        if (googleRealtimeSocketRef.current === socket) {
          stopRealtimeSession("Google Gemini Live 会话已结束");
        }
      };
      socket.onmessage = (event) => {
        void (async () => {
          try {
            const messageText = await websocketMessageText(event.data);
            const payload = JSON.parse(messageText) as GoogleRealtimeAudioMessage;
            if (payload.error) {
              const message = googleRealtimeErrorMessage(payload.error);
              if (!streamingStarted) {
                rejectStart(message);
                return;
              }
              stopRealtimeSession("Google Gemini Live 会话已结束");
              setError(message);
              return;
            }
            if (payload.setupComplete && !streamingStarted) {
              streamingStarted = true;
              beginGoogleAudioStreaming(socket, mediaStream, audioContext);
              setVoiceActive(true);
              setBusyAction((current) => (current === "voice-connect" ? null : current));
              setVoiceStatusText(`Google Gemini Live 已连接，语音音色：${session.voice}`);
              resolveStart();
              return;
            }
            handleGoogleRealtimeMessage(payload);
          } catch {
            // ignore malformed realtime events
          }
        })();
      };
    });
  }

  async function startCodexLiveBridge(websocketUrl: string) {
    const socket = new WebSocket(getApiWebSocketUrl(websocketUrl));
    codexLiveSocketRef.current = socket;
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const timeoutId = window.setTimeout(() => {
        if (!settled) {
          settled = true;
          socket.close();
          reject(new Error("Codex Live Chatbot 工作流通道连接超时"));
        }
      }, 20_000);
      const resolveStart = () => {
        if (settled) {
          return;
        }
        settled = true;
        window.clearTimeout(timeoutId);
        resolve();
      };
      const rejectStart = (message: string) => {
        if (settled) {
          return;
        }
        settled = true;
        window.clearTimeout(timeoutId);
        reject(new Error(message));
      };
      socket.onopen = () => {
        socket.send(JSON.stringify({
          type: "selection.update",
          selection: currentSelectionRef.current,
        }));
      };
      socket.onerror = () => {
        rejectStart("Codex Live Chatbot 工作流通道连接失败");
      };
      socket.onclose = (event) => {
        if (!settled) {
          rejectStart(`Codex Live Chatbot 工作流通道在初始化前关闭（${event.code}）`);
        }
        if (codexLiveSocketRef.current === socket) {
          codexLiveSocketRef.current = null;
          stopRealtimeSession("Codex Live Chatbot 工作流通道已结束");
        }
      };
      socket.onmessage = (event) => {
        void (async () => {
          try {
            const messageText = await websocketMessageText(event.data);
            const payload = JSON.parse(messageText) as CodexLiveBridgeEvent;
            const lessonId = realtimeLessonIdRef.current;
            if (payload.type === "codex_live.ready") {
              resolveStart();
              return;
            }
            if (!lessonId) {
              return;
            }
            if (payload.type === "codex_live.transcript.delta" && payload.role && payload.text) {
              if (payload.role === "user") {
                const transcriptKey = "codex-live-user";
                const transcript = `${openAIInputTranscriptsRef.current.get(transcriptKey) ?? ""}${payload.text}`;
                openAIInputTranscriptsRef.current.set(transcriptKey, transcript);
                const turnId = currentTurnId();
                onTranscriptUpdate({
                  lessonId,
                  turnId,
                  messageId: `realtime:${turnId}:user`,
                  role: "user",
                  text: transcript,
                  final: false,
                });
              } else {
                openAIAssistantTranscriptRef.current += payload.text;
                onTranscriptUpdate({
                  lessonId,
                  turnId: currentTurnId(),
                  messageId: currentAssistantMessageId(),
                  role: "assistant",
                  text: openAIAssistantTranscriptRef.current,
                  final: false,
                });
              }
              return;
            }
            if (payload.type === "codex_live.transcript.done" && payload.role) {
              const transcript = (payload.text ?? "").trim();
              if (payload.role === "user") {
                openAIInputTranscriptsRef.current.delete("codex-live-user");
                handleRealtimeUserTranscript(lessonId, transcript, payload.type);
              } else if (transcript) {
                const turnId = currentTurnId();
                const messageId = currentAssistantMessageId();
                onTranscriptUpdate({
                  lessonId,
                  turnId,
                  messageId,
                  role: "assistant",
                  text: transcript,
                  final: true,
                });
                openAIAssistantTranscriptRef.current = "";
                openAIAssistantMessageIdRef.current = null;
                realtimeTurnIdRef.current = null;
              }
              return;
            }
            if (payload.type === "codex_live.workflow.started") {
              const turnId = currentTurnId();
              const label = "正在交给 Chatbot 工作流处理";
              setVoiceStatusText(label);
              onToolStatusUpdate({ lessonId, turnId, label, status: "pending" });
              enqueueRealtimeLogEvent(lessonId, "tool", payload.type, payload.delegation_id ?? "delegation", {
                turnId,
              });
              return;
            }
            if (payload.type === "codex_live.workflow.result" && payload.result) {
              const turnId = currentTurnId();
              onToolResult(lessonId, payload.result);
              const succeeded = payload.result.status === "ok" && payload.result.model_output.status === "ok";
              const label = succeeded ? "Chatbot 工作流已完成" : "Chatbot 工作流执行失败";
              onToolStatusUpdate({
                lessonId,
                turnId,
                label,
                status: succeeded ? "completed" : "error",
              });
              setVoiceStatusText(succeeded ? `${label}，Codex Live 正在回答` : label);
              return;
            }
            if (payload.type === "codex_live.workflow.error" || payload.type === "codex_live.error") {
              const message = payload.message || "Codex Live Chatbot 工作流通道发生错误";
              onToolStatusUpdate({
                lessonId,
                turnId: currentTurnId(),
                label: message,
                status: "error",
              });
              setError(message);
            }
          } catch {
            // Ignore malformed bridge events while keeping the media session alive.
          }
        })();
      };
    });
  }

  async function handleVoiceToggle() {
    if (typeof window === "undefined") {
      return;
    }
    if (voiceActive || busyAction === "voice-connect") {
      stopRealtimeSession("语音 Chatbot 已手动断开");
      return;
    }
    if (!activeLesson) {
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("当前浏览器无法访问麦克风。请使用支持麦克风的浏览器，并通过 localhost 或 HTTPS 打开页面。");
      return;
    }
    if (selectedRealtimeOption && !selectedRealtimeOption.enabled) {
      setError(`当前未配置 ${PROVIDER_LABELS[selectedRealtimeModel.provider]} 的实时语音 API Key。`);
      return;
    }
    if (!(await flushAutoSave("voice"))) {
      return;
    }

    setBusyAction("voice-connect");
    const realtimeLabel = modelButtonLabel(selectedRealtimeOption ?? null, selectedRealtimeModel);
    setVoiceStatusText(`正在连接 ${realtimeLabel}…`);
    setError(null);

    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      realtimeStreamRef.current = mediaStream;

      const clientSessionId = createClientSessionId("realtime");
      realtimeLessonIdRef.current = activeLesson.id;
      realtimeClientSessionIdRef.current = clientSessionId;
      realtimeLessonTitleRef.current = activeLesson.title;
      realtimeBoardReferencesRef.current = currentSelections.reduce(
        (references, selection) => addRealtimeBoardReference(references, selection, activeLesson.id),
        [] as SelectionRef[]
      );

      if (selectedRealtimeTransport === "gemini_live_websocket" || selectedRealtimeModel.provider === "google") {
        await startGoogleRealtimeSession(activeLesson, mediaStream, clientSessionId);
        return;
      }

      const peerConnection = new RTCPeerConnection();
      realtimePeerRef.current = peerConnection;

      mediaStream.getTracks().forEach((track) => {
        peerConnection.addTrack(track, mediaStream);
      });

      peerConnection.ontrack = (event) => {
        const [remoteStream] = event.streams;
        if (remoteAudioRef.current && remoteStream) {
          remoteAudioRef.current.srcObject = remoteStream;
          void remoteAudioRef.current.play().catch(() => undefined);
        }
      };

      peerConnection.onconnectionstatechange = () => {
        if (peerConnection.connectionState === "connected") {
          setVoiceActive(true);
          setVoiceStatusText(`${realtimeLabel} 已连接，说话后会先进入 Chatbot 工作流`);
          setBusyAction((current) => (current === "voice-connect" ? null : current));
          return;
        }
        if (
          peerConnection.connectionState === "failed" ||
          peerConnection.connectionState === "closed" ||
          peerConnection.connectionState === "disconnected"
        ) {
          stopRealtimeSession("语音会话已结束");
        }
      };

      const dataChannel = peerConnection.createDataChannel("oai-events");
      realtimeChannelRef.current = dataChannel;
      dataChannel.onmessage = (messageEvent) => {
        void (async () => {
          try {
            const payload = JSON.parse(messageEvent.data) as OpenAIRealtimeEvent;
          if (payload.type === "response.created") {
            openAIResponseInProgressRef.current = true;
            openAIAssistantMessageIdRef.current = createClientSessionId("realtime-message");
          }
          if (
            payload.type === "response.done" ||
            payload.type === "response.audio.done" ||
            payload.type === "response.output_audio.done" ||
            payload.type === "response.output_text.done"
          ) {
            openAIResponseInProgressRef.current = false;
          }
          if (payload.type === "input_audio_buffer.speech_started") {
            beginRealtimeTurn();
            if (openAIResponseInProgressRef.current && dataChannel.readyState === "open") {
              dataChannel.send(JSON.stringify({ type: "response.cancel" }));
              openAIResponseInProgressRef.current = false;
            }
            openAIAssistantTranscriptRef.current = "";
            resetOpenAIRemoteAudioPlayback();
          }
          const lessonId = realtimeLessonIdRef.current;
          if (!lessonId || !payload.type) {
            return;
          }
          const functionCalls = realtimeFunctionCallsFromEvent(payload);
          for (const functionCall of functionCalls) {
            if (openAIProcessedToolCallsRef.current.has(functionCall.callId)) {
              continue;
            }
            openAIProcessedToolCallsRef.current.add(functionCall.callId);
            const turnId = currentTurnId();
            const toolLabel = functionCall.name === "read_board_context" ? "正在定位并读取板书" : "正在交给 Chatbot 工作流处理";
            setVoiceStatusText(toolLabel);
            onToolStatusUpdate({ lessonId, turnId, label: toolLabel, status: "pending" });
            enqueueRealtimeLogEvent(lessonId, "tool", payload.type, `${functionCall.name} (${functionCall.callId})`, {
              turnId,
            });
            const clientSessionId = realtimeClientSessionIdRef.current;
            if (!clientSessionId) {
              const message = "Realtime 客户端会话标识已失效";
              onToolStatusUpdate({ lessonId, turnId, label: message, status: "error" });
              sendOpenAIFunctionOutput(dataChannel, functionCall.callId, { status: "error", message });
              continue;
            }
            let toolResult: RealtimeToolCallResponse;
            try {
              const useAccumulatedReferences =
                functionCall.name === "read_board_context" &&
                functionCall.arguments.mode === "current_selection" &&
                realtimeBoardReferencesRef.current.length > 1;
              if (useAccumulatedReferences) {
                const referenceResults = await Promise.all(
                  realtimeBoardReferencesRef.current.map((selection, index) =>
                    api.callRealtimeTool(lessonId, {
                      client_session_id: clientSessionId,
                      call_id: `${functionCall.callId}_reference_${index + 1}`,
                      name: functionCall.name,
                      arguments: functionCall.arguments,
                      selection,
                    })
                  )
                );
                toolResult = mergeRealtimeBoardReferenceResults(referenceResults);
              } else {
                toolResult = await api.callRealtimeTool(lessonId, {
                  client_session_id: clientSessionId,
                  call_id: functionCall.callId,
                  name: functionCall.name,
                  arguments: functionCall.arguments,
                  selection: currentSelectionRef.current,
                });
              }
            } catch (toolError) {
              const message = toolError instanceof Error ? toolError.message : "Realtime 工具执行失败";
              onToolStatusUpdate({ lessonId, turnId, label: message, status: "error" });
              setVoiceStatusText(message);
              sendOpenAIFunctionOutput(dataChannel, functionCall.callId, { status: "error", message });
              continue;
            }
            onToolResult(lessonId, toolResult);
            const modelStatus = toolResult.model_output.status;
            const succeeded = toolResult.status === "ok" && modelStatus === "ok";
            const referenceCount = Number(toolResult.model_output.reference_count ?? 0);
            const completedLabel = functionCall.name === "read_board_context"
              ? referenceCount > 1
                ? `${referenceCount} 个板书引用已就绪`
                : "板书上下文已就绪"
              : "Chatbot 工作流已完成";
            const failedLabel = toolResult.status === "ok" && modelStatus === "not_found"
              ? "未定位到明确板书范围"
              : "Realtime 工具执行失败";
            onToolStatusUpdate({
              lessonId,
              turnId,
              label: succeeded ? completedLabel : failedLabel,
              status: succeeded ? "completed" : "error",
            });
            setVoiceStatusText(succeeded ? `${completedLabel}，Realtime 正在回答` : failedLabel);
            sendOpenAIFunctionOutput(dataChannel, functionCall.callId, toolResult.model_output);
          }
          const inputItemId = payload.item_id ?? currentTurnId();
          if (payload.type === "conversation.item.input_audio_transcription.delta" && payload.delta) {
            const transcript = `${openAIInputTranscriptsRef.current.get(inputItemId) ?? ""}${payload.delta}`;
            openAIInputTranscriptsRef.current.set(inputItemId, transcript);
            const turnId = currentTurnId();
            onTranscriptUpdate({
              lessonId,
              turnId,
              messageId: `realtime:${turnId}:user`,
              role: "user",
              text: transcript,
              final: false,
            });
          }
          if (
            (payload.type === "conversation.item.input_audio_transcription.completed" ||
              payload.type === "conversation.item.input_audio_transcription.done")
          ) {
            const transcript = payload.transcript ?? openAIInputTranscriptsRef.current.get(inputItemId) ?? "";
            openAIInputTranscriptsRef.current.delete(inputItemId);
            handleRealtimeUserTranscript(lessonId, transcript, payload.type);
          }
          if (payload.type === "response.output_audio_transcript.delta" && payload.delta) {
            openAIAssistantTranscriptRef.current += payload.delta;
            onTranscriptUpdate({
              lessonId,
              turnId: currentTurnId(),
              messageId: currentAssistantMessageId(),
              role: "assistant",
              text: openAIAssistantTranscriptRef.current,
              final: false,
            });
          }
          if (payload.type === "response.output_text.delta" && payload.delta) {
            openAIAssistantTranscriptRef.current += payload.delta;
            onTranscriptUpdate({
              lessonId,
              turnId: currentTurnId(),
              messageId: currentAssistantMessageId(),
              role: "assistant",
              text: openAIAssistantTranscriptRef.current,
              final: false,
            });
          }
          if (
            payload.type === "response.audio_transcript.done" ||
            payload.type === "response.output_audio_transcript.done" ||
            payload.type === "response.output_text.done"
          ) {
            const transcript = payload.transcript ?? openAIAssistantTranscriptRef.current;
            const turnId = currentTurnId();
            const messageId = currentAssistantMessageId();
            enqueueRealtimeLogEvent(lessonId, "assistant", payload.type, transcript, {
              clientEventId: messageId,
              turnId,
            });
            onTranscriptUpdate({ lessonId, turnId, messageId, role: "assistant", text: transcript, final: true });
            openAIAssistantTranscriptRef.current = "";
            openAIAssistantMessageIdRef.current = null;
          }
          } catch (toolError) {
            const lessonId = realtimeLessonIdRef.current;
            if (lessonId) {
              onToolStatusUpdate({
                lessonId,
                turnId: currentTurnId(),
                label: toolError instanceof Error ? toolError.message : "Realtime 工具执行失败",
                status: "error",
              });
            }
            setError(toolError instanceof Error ? toolError.message : "Realtime 工具执行失败");
          }
        })();
      };

      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);

      const realtimeResponse = await api.connectRealtime(activeLesson.id, {
        offer_sdp: offer.sdp ?? "",
        latest_assistant_message: latestAssistantMessageContent,
        client_session_id: clientSessionId,
        realtime_model: selectedRealtimeModel,
        selection: currentSelection,
      });
      if (realtimeResponse.client_session_id) {
        realtimeClientSessionIdRef.current = realtimeResponse.client_session_id;
      }
      openAIRealtimeToolsEnabledRef.current = Boolean(realtimeResponse.tools_enabled);
      openAIClientDelegationEnabledRef.current = Boolean(realtimeResponse.client_delegation_enabled);

      if (realtimeResponse.client_delegation_enabled) {
        if (!realtimeResponse.delegation_websocket_url) {
          throw new Error("Codex Live 响应缺少 Chatbot 工作流通道");
        }
        await startCodexLiveBridge(realtimeResponse.delegation_websocket_url);
      }

      await peerConnection.setRemoteDescription({
        type: "answer",
        sdp: realtimeResponse.answer_sdp,
      });

      setVoiceStatusText(
        `${PROVIDER_LABELS[realtimeResponse.provider]} ${realtimeResponse.model} 已就绪${
          realtimeResponse.tools_enabled || realtimeResponse.client_delegation_enabled
            ? "，可读取板书并运行 Chatbot 工作流"
            : "，正在受控转写"
        }`
      );
    } catch (voiceError) {
      stopRealtimeSession("语音连接失败");
      setError(realtimeConnectionErrorMessage(voiceError, selectedRealtimeModel));
    }
  }

  function sendRealtimeText(message: string) {
    const normalized = message.trim();
    const lessonId = realtimeLessonIdRef.current;
    const dataChannel = realtimeChannelRef.current;
    if (!normalized || !lessonId) {
      return false;
    }
    const codexLiveSocket = codexLiveSocketRef.current;
    const usesClientDelegation = openAIClientDelegationEnabledRef.current;
    if (usesClientDelegation) {
      if (!codexLiveSocket || codexLiveSocket.readyState !== WebSocket.OPEN) {
        return false;
      }
    } else if (!dataChannel || dataChannel.readyState !== "open") {
      return false;
    }
    const turnId = beginRealtimeTurn();
    const messageId = `realtime:${turnId}:user`;
    onTranscriptUpdate({ lessonId, turnId, messageId, role: "user", text: normalized, final: true });
    if (!usesClientDelegation) {
      enqueueRealtimeLogEvent(lessonId, "user", "conversation.item.input_text", normalized, {
        clientEventId: messageId,
        turnId,
      });
    }
    if (usesClientDelegation) {
      codexLiveSocket?.send(JSON.stringify({ type: "input_text", text: normalized }));
      setVoiceStatusText("Codex Live 正在处理文字消息");
      return true;
    }
    dataChannel?.send(JSON.stringify({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "user",
        content: [{ type: "input_text", text: normalized }],
      },
    }));
    if (openAIRealtimeToolsEnabledRef.current) {
      sendOpenAITurnDecisionRequest(dataChannel as RTCDataChannel);
    } else {
      dataChannel?.send(JSON.stringify({ type: "response.create" }));
    }
    setVoiceStatusText("Realtime 正在处理文字消息");
    return true;
  }

  const scheduleRealtimeLogFlushEffectEvent = useEffectEvent(() => {
    void flushRealtimeLogQueue();
  });

  const flushRealtimeLogQueueWithBeaconEffectEvent = useEffectEvent(() => {
    flushRealtimeLogQueueWithBeacon();
  });

  const disposeRealtimeSessionEffectEvent = useEffectEvent(() => {
    disposeRealtimeSession();
  });

  useEffect(() => {
    return () => {
      flushRealtimeLogQueueWithBeaconEffectEvent();
      disposeRealtimeSessionEffectEvent();
    };
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      scheduleRealtimeLogFlushEffectEvent();
    }, 2000);

    function handlePageHide() {
      flushRealtimeLogQueueWithBeaconEffectEvent();
    }

    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("beforeunload", handlePageHide);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("beforeunload", handlePageHide);
    };
  }, []);

  useEffect(() => {
    if (!realtimeLessonIdRef.current || realtimeLessonIdRef.current === activeLesson?.id) {
      return;
    }
    stopRealtimeSessionEvent("已切换课程，语音会话已自动断开");
  }, [activeLesson?.id]);

  return {
    remoteAudioRef,
    voiceActive,
    voiceStatusText,
    setVoiceStatusText,
    handleVoiceToggle,
    stopRealtimeSession,
    speakControlledChatbotMessage,
    sendRealtimeText,
  };
}
