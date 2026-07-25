"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import {
  FALLBACK_MODEL_CATALOG,
  MODEL_CREDENTIALS_CHANGED_EVENT,
  REALTIME_MODEL_STORAGE_KEY,
  TEXT_MODEL_STORAGE_KEY,
  findModelOption,
  normalizeCourseStudioModelCatalog,
  optionToSelection,
  persistModelSelection,
  readStoredModelSelection,
  resolveModelSelection,
  selectionForModelOption,
} from "@/components/course-studio/model-catalog";
import type { AIModelCatalog, AIModelOption, AIModelSelection } from "@/types";

export function useModelCatalog() {
  const [modelCatalog, setModelCatalog] = useState<AIModelCatalog>(() =>
    normalizeCourseStudioModelCatalog(FALLBACK_MODEL_CATALOG)
  );
  const [selectedTextModel, setSelectedTextModel] = useState<AIModelSelection>(FALLBACK_MODEL_CATALOG.defaults.text);
  const [selectedRealtimeModel, setSelectedRealtimeModel] = useState<AIModelSelection>(
    FALLBACK_MODEL_CATALOG.defaults.realtime
  );
  const [openModelMenu, setOpenModelMenu] = useState<"text" | "realtime" | null>(null);

  useEffect(() => {
    async function loadModelCatalog() {
      try {
        const catalog = normalizeCourseStudioModelCatalog(await api.getAIModels());
        setModelCatalog(catalog);
        setSelectedTextModel(
          resolveModelSelection(catalog.text, readStoredModelSelection(TEXT_MODEL_STORAGE_KEY), catalog.defaults.text)
        );
        setSelectedRealtimeModel(
          resolveModelSelection(
            catalog.realtime,
            readStoredModelSelection(REALTIME_MODEL_STORAGE_KEY),
            catalog.defaults.realtime
          )
        );
      } catch {
        const fallbackCatalog = normalizeCourseStudioModelCatalog(FALLBACK_MODEL_CATALOG);
        setModelCatalog(fallbackCatalog);
        setSelectedTextModel(
          resolveModelSelection(
            fallbackCatalog.text,
            readStoredModelSelection(TEXT_MODEL_STORAGE_KEY),
            fallbackCatalog.defaults.text
          )
        );
        setSelectedRealtimeModel(
          resolveModelSelection(
            fallbackCatalog.realtime,
            readStoredModelSelection(REALTIME_MODEL_STORAGE_KEY),
            fallbackCatalog.defaults.realtime
          )
        );
      }
    }
    void loadModelCatalog();
    window.addEventListener(MODEL_CREDENTIALS_CHANGED_EVENT, loadModelCatalog);
    return () => {
      window.removeEventListener(MODEL_CREDENTIALS_CHANGED_EVENT, loadModelCatalog);
    };
  }, []);

  const selectedTextOption = findModelOption(modelCatalog.text, selectedTextModel);
  const selectedRealtimeOption = findModelOption(modelCatalog.realtime, selectedRealtimeModel);
  const selectedRealtimeTransport =
    selectedRealtimeOption?.transport ??
    (selectedRealtimeModel.provider === "openai" ? "openai_webrtc" : "gemini_live_websocket");

  function selectTextModel(selection: AIModelSelection) {
    const option = findModelOption(modelCatalog.text, selection);
    if (!option?.enabled) {
      return;
    }
    const nextSelection = selectionForModelOption(option, selection);
    setSelectedTextModel(nextSelection);
    persistModelSelection(TEXT_MODEL_STORAGE_KEY, nextSelection);
    setOpenModelMenu(null);
  }

  function selectRealtimeModel(option: AIModelOption) {
    if (!option.enabled) {
      return;
    }
    const nextSelection = optionToSelection(option);
    setSelectedRealtimeModel(nextSelection);
    persistModelSelection(REALTIME_MODEL_STORAGE_KEY, nextSelection);
    setOpenModelMenu(null);
  }

  return {
    modelCatalog,
    selectedTextModel,
    selectedRealtimeModel,
    selectedTextOption,
    selectedRealtimeOption,
    selectedRealtimeTransport,
    openModelMenu,
    setOpenModelMenu,
    selectTextModel,
    selectRealtimeModel,
  };
}
