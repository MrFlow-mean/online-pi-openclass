import { compactText, metadataBool, metadataText } from "@/components/course-studio/history-utils";
import type { CommitRecord, Lesson, SelectionRef } from "@/types";

export type HistoryNodeKind = "chat" | "document" | "restore" | "merge" | "system";

export type HistoryGraphLane = {
  branchName: string;
  index: number;
  color: string;
  isCurrent: boolean;
};

export type HistoryGraphConnector = {
  fromLane: number;
  toLane: number;
  color: string;
};

export type HistoryGraphRow = {
  commit: CommitRecord;
  lane: HistoryGraphLane;
  nodeKind: HistoryNodeKind;
  title: string;
  summary: string;
  active: boolean;
  head: boolean;
  continuationLaneIndexes: number[];
  connectors: HistoryGraphConnector[];
};

const LANE_COLORS = [
  "#2563eb",
  "#16a34a",
  "#dc2626",
  "#9333ea",
  "#d97706",
  "#0891b2",
  "#e11d48",
  "#4f46e5",
];

const CHAT_COMMIT_KINDS = new Set([
  "basic_chat",
  "learning_requirement_refinement",
  "board_task_requirement_refinement",
  "chat_flow",
  "board_section_teaching",
]);

const DOCUMENT_COMMIT_KINDS = new Set([
  "manual_document_save",
  "manual_document_edit",
  "auto_document_save",
  "board_document_generation",
  "board_document_edit",
  "import_docx",
]);

function commitKind(commit: CommitRecord) {
  return typeof commit.metadata?.kind === "string" ? commit.metadata.kind : "";
}

export function historyNodeKind(commit: CommitRecord): HistoryNodeKind {
  const explicit = commit.metadata?.history_node_kind;
  if (explicit === "chat" || explicit === "document" || explicit === "restore" || explicit === "merge" || explicit === "system") {
    return explicit;
  }
  const kind = commitKind(commit);
  if (kind === "restore_snapshot") {
    return "restore";
  }
  if (kind === "branch_merge") {
    return "merge";
  }
  if (kind === "initial_document") {
    return "system";
  }
  if (metadataBool(commit, "document_changed") || commit.operations.length || DOCUMENT_COMMIT_KINDS.has(kind)) {
    return "document";
  }
  if (CHAT_COMMIT_KINDS.has(kind) || metadataText(commit, "user_message") || metadataText(commit, "assistant_message")) {
    return "chat";
  }
  return "system";
}

export function historyNodeTitle(commit: CommitRecord) {
  const explicit = metadataText(commit, "history_node_title");
  if (explicit) {
    return explicit;
  }
  const userMessage = metadataText(commit, "user_message");
  if (historyNodeKind(commit) === "chat" && userMessage) {
    return compactText(userMessage, 64);
  }
  const editorSummary = metadataText(commit, "board_document_editor_summary");
  if (historyNodeKind(commit) === "document" && editorSummary) {
    return compactText(editorSummary, 64);
  }
  return commit.label;
}

export function historyNodeSummary(commit: CommitRecord) {
  const explicit = metadataText(commit, "history_node_summary");
  if (explicit) {
    return explicit;
  }
  const assistantMessage = metadataText(commit, "assistant_message");
  if (historyNodeKind(commit) === "chat" && assistantMessage) {
    return compactText(assistantMessage, 160);
  }
  const editorSummary = metadataText(commit, "board_document_editor_summary");
  if (historyNodeKind(commit) === "document" && editorSummary) {
    return compactText(editorSummary, 160);
  }
  return compactText(commit.message, 160);
}

export function historyNodeKindLabel(kind: HistoryNodeKind) {
  if (kind === "chat") {
    return "Chat";
  }
  if (kind === "document") {
    return "Document";
  }
  if (kind === "restore") {
    return "Restore";
  }
  if (kind === "merge") {
    return "Merge";
  }
  return "System";
}

function historyNodeContent(commit: CommitRecord) {
  const userMessage = metadataText(commit, "user_message");
  const assistantMessage = metadataText(commit, "assistant_message");
  if (historyNodeKind(commit) === "chat" && (userMessage || assistantMessage)) {
    return [
      userMessage ? `学习者：${userMessage}` : "",
      assistantMessage ? `OpenClass：${assistantMessage}` : "",
    ].filter(Boolean).join("\n\n");
  }
  return commit.snapshot.content_text.trim() || historyNodeSummary(commit) || commit.message.trim();
}

function boundedText(value: string, maxLength: number) {
  const normalized = value.trim();
  return normalized.length <= maxLength ? normalized : `${normalized.slice(0, maxLength).trimEnd()}…`;
}

export function historyNodeSelection(lesson: Lesson, commit: CommitRecord): SelectionRef {
  const title = historyNodeTitle(commit);
  const excerpt = boundedText(
    [
      `历史节点：${title}`,
      `类型：${historyNodeKindLabel(historyNodeKind(commit))}`,
      `分支：${commit.branch_name}`,
      "",
      historyNodeContent(commit),
    ].join("\n"),
    12_000
  );
  return {
    kind: "chat",
    excerpt,
    lesson_id: lesson.id,
    document_id: commit.snapshot.id,
    segment_id: commit.id,
    heading_path: [lesson.title, title],
    before_text: `历史节点 ${commit.id}`,
    after_text: `创建于 ${commit.created_at}`,
  };
}

function answerPrefillValue(value: string) {
  // Answer decodes the query once through URLSearchParams and once in its prefill parser.
  return encodeURIComponent(value.replaceAll("%", "%25"));
}

function boundedCommunityDraft(prefix: string, content: string, suffix: string) {
  const maxEncodedLength = 6_800;
  if (answerPrefillValue(`${prefix}${content}${suffix}`).length <= maxEncodedLength) {
    return `${prefix}${content}${suffix}`;
  }
  const truncatedSuffix = "\n\n> 节点内容较长，草稿中展示了可供讨论的节选。";
  let lower = 0;
  let upper = content.length;
  while (lower < upper) {
    const middle = Math.ceil((lower + upper) / 2);
    const candidate = `${prefix}${content.slice(0, middle).trimEnd()}${truncatedSuffix}`;
    if (answerPrefillValue(candidate).length <= maxEncodedLength) {
      lower = middle;
    } else {
      upper = middle - 1;
    }
  }
  return `${prefix}${content.slice(0, lower).trimEnd()}${truncatedSuffix}`;
}

export function historyNodeCommunityShareHref(lesson: Lesson, commit: CommitRecord) {
  const nodeTitle = historyNodeTitle(commit);
  const postTitle = compactText(`${lesson.title} · ${nodeTitle}`, 140);
  const prefix = [
    "---",
    `title: ${JSON.stringify(postTitle)}`,
    "---",
    "",
    "> OpenClass 课堂历史节点",
    `> 课程：${lesson.title}`,
    `> 节点：${nodeTitle}`,
    `> 类型：${historyNodeKindLabel(historyNodeKind(commit))}`,
    `> 分支：${commit.branch_name}`,
    `> 时间：${commit.created_at}`,
    "",
    "## 节点内容",
    "",
  ].join("\n");
  const suffix = "\n\n---\n\n请围绕这个课堂节点补充你的问题、理解或建议。";
  const prefill = boundedCommunityDraft(prefix, historyNodeContent(commit), suffix);
  return `/community?prefill=${answerPrefillValue(prefill)}`;
}

export function buildHistoryGraphRows(
  lesson: Lesson,
  previewCommitId: string | null
): { lanes: HistoryGraphLane[]; rows: HistoryGraphRow[] } {
  const branches = Object.values(lesson.history_graph.branches).sort((left, right) => {
    if (left.name === "main") {
      return -1;
    }
    if (right.name === "main") {
      return 1;
    }
    const createdDelta = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
    if (createdDelta !== 0) {
      return createdDelta;
    }
    return left.name.localeCompare(right.name, "zh-CN", { numeric: true });
  });
  const currentBranchName = lesson.history_graph.current_branch;
  const lanes = branches.map((branch, index) => ({
    branchName: branch.name,
    index,
    color: LANE_COLORS[index % LANE_COLORS.length],
    isCurrent: branch.name === currentBranchName,
  }));
  const fallbackLane = lanes[0] ?? {
    branchName: "main",
    index: 0,
    color: LANE_COLORS[0],
    isCurrent: true,
  };
  const laneByBranchName = new Map(lanes.map((lane) => [lane.branchName, lane]));
  const commitIndexById = new Map(lesson.history_graph.commits.map((commit, index) => [commit.id, index]));
  const commitById = new Map(lesson.history_graph.commits.map((commit) => [commit.id, commit]));
  const branchRanges = new Map<string, { first: number; last: number }>();

  branches.forEach((branch) => {
    const commitIndexes = lesson.history_graph.commits
      .map((commit, index) => (commit.branch_name === branch.name ? index : null))
      .filter((index): index is number => index !== null);
    const baseIndex = commitIndexById.get(branch.base_commit_id);
    const headIndex = commitIndexById.get(branch.head_commit_id);
    const allIndexes = [
      ...commitIndexes,
      ...(typeof baseIndex === "number" ? [baseIndex] : []),
      ...(typeof headIndex === "number" ? [headIndex] : []),
    ];
    if (!allIndexes.length) {
      return;
    }
    branchRanges.set(branch.name, {
      first: Math.min(...allIndexes),
      last: Math.max(...allIndexes),
    });
  });

  const rows = lesson.history_graph.commits.map((commit, index) => {
    const lane = laneByBranchName.get(commit.branch_name) ?? fallbackLane;
    const nodeKind = historyNodeKind(commit);
    const continuationLaneIndexes = lanes
      .filter((candidate) => {
        const range = branchRanges.get(candidate.branchName);
        return Boolean(range && index >= range.first && index <= range.last);
      })
      .map((candidate) => candidate.index);
    const branchConnectors = branches
      .filter((branch) => branch.base_commit_id === commit.id && branch.name !== commit.branch_name)
      .flatMap((branch) => {
        const toLane = laneByBranchName.get(branch.name);
        if (!toLane) {
          return [];
        }
        return [
          {
            fromLane: lane.index,
            toLane: toLane.index,
            color: toLane.color,
          },
        ];
      });
    const sourceParent = commit.parent_ids[1] ? commitById.get(commit.parent_ids[1]) : null;
    const sourceLane = sourceParent ? laneByBranchName.get(sourceParent.branch_name) : null;
    const connectors = [
      ...branchConnectors,
      ...(sourceLane && sourceLane.index !== lane.index
        ? [{ fromLane: sourceLane.index, toLane: lane.index, color: sourceLane.color }]
        : []),
    ];
    if (sourceLane && !continuationLaneIndexes.includes(sourceLane.index)) {
      continuationLaneIndexes.push(sourceLane.index);
    }

    return {
      commit,
      lane,
      nodeKind,
      title: historyNodeTitle(commit),
      summary: historyNodeSummary(commit),
      active: commit.id === previewCommitId,
      head: lesson.history_graph.branches[commit.branch_name]?.head_commit_id === commit.id,
      continuationLaneIndexes,
      connectors,
    };
  });

  return { lanes, rows: [...rows].reverse() };
}
