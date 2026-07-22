"use client";

import { GitFork } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { GitHubConnectionView, GitHubRepositoryView } from "@/types";

export function GitHubRepositoryImport({
  disabled,
  sourceUri,
  learningGoal,
  onSourceUriChange,
  onLearningGoalChange,
  onError,
}: {
  disabled: boolean;
  sourceUri: string;
  learningGoal: string;
  onSourceUriChange: (value: string) => void;
  onLearningGoalChange: (value: string) => void;
  onError: (message: string) => void;
}) {
  const [status, setStatus] = useState<GitHubConnectionView | null>(null);
  const [repositories, setRepositories] = useState<GitHubRepositoryView[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const nextStatus = await api.getGitHubConnectionStatus();
      setStatus(nextStatus);
      setRepositories(nextStatus.connected ? await api.listGitHubRepositories() : []);
    } catch (error) {
      onError(error instanceof Error ? error.message : "GitHub 连接状态读取失败");
    } finally {
      setIsLoading(false);
    }
  }, [onError]);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  async function connect() {
    setIsLoading(true);
    try {
      const result = await api.startGitHubInstall(
        typeof window === "undefined" ? "/studio" : `${window.location.pathname}${window.location.search}`
      );
      window.location.assign(result.install_url);
    } catch (error) {
      onError(error instanceof Error ? error.message : "GitHub 连接启动失败");
      setIsLoading(false);
    }
  }

  return (
    <div className="mt-3 rounded-md border border-gray-200 bg-gray-50 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-gray-700">
            <GitFork className="h-3.5 w-3.5" /> GitHub 仓库
          </p>
          <p className="mt-1 text-[10px] leading-4 text-gray-500">
            {status?.connected
              ? `已连接 ${status.installations.filter((item) => item.status === "connected").length} 个安装`
              : status?.configured
                ? "可选择全部或指定仓库，只读取元数据和内容。"
                : status?.message || "GitHub App 尚未配置。"}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void (status?.connected ? refresh() : connect())}
          disabled={disabled || isLoading || !status?.enabled || !status?.configured}
          className="shrink-0 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-gray-700 hover:border-gray-300 disabled:opacity-50"
        >
          {isLoading ? "读取中" : status?.connected ? "刷新仓库" : "连接 GitHub"}
        </button>
      </div>
      {repositories.length ? (
        <select
          value={repositories.some((repository) => repository.html_url === sourceUri) ? sourceUri : ""}
          onChange={(event) => onSourceUriChange(event.target.value)}
          className="mt-2 h-8 w-full rounded-md border border-gray-200 bg-white px-2 text-xs outline-none focus:border-black"
          disabled={disabled}
        >
          <option value="">选择已授权仓库</option>
          {repositories.map((repository) => (
            <option key={`${repository.installation_id}-${repository.id}`} value={repository.html_url}>
              {repository.private ? "私有" : "公开"} · {repository.full_name}
            </option>
          ))}
        </select>
      ) : null}
      <input
        value={learningGoal}
        onChange={(event) => onLearningGoalChange(event.target.value)}
        placeholder="可选：你希望怎样学习这个项目"
        className="mt-2 h-8 w-full rounded-md border border-gray-200 bg-white px-2 text-xs outline-none focus:border-black"
        disabled={disabled}
      />
    </div>
  );
}
