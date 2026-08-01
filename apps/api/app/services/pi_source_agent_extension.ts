import { createHash, randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { createReadStream } from "node:fs";
import { chmod, lstat, readFile, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { promisify } from "node:util";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const execFileAsync = promisify(execFile);
const MAX_TEXT_OUTPUT = 120_000;
const MAX_ARCHIVE_ENTRIES = 50_001;
const MAX_ARCHIVE_LIST_PAGE_ENTRIES = 2_000;
const MAX_ARCHIVE_ENTRY_CHARACTERS = 120_000;
const MAX_ARCHIVE_ENTRY_BUFFER_BYTES = 16 * 1024 * 1024;
const MAX_CATALOG_BYTES = 16 * 1024 * 1024;
const MAX_TEXT_LINES_PER_CALL = 500;
const MAX_PDF_PAGE_SPAN = 32;

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Missing required OpenClass source runtime setting: ${name}`);
  return value;
}

const workspace = resolve(process.cwd());
const sourcePath = resolve(workspace, requiredEnvironment("OPENCLASS_PI_SOURCE_FILE"));
const scratchPath = resolve(workspace, requiredEnvironment("OPENCLASS_PI_SOURCE_SCRATCH"));
const toolboxBin = resolve(requiredEnvironment("OPENCLASS_PI_SOURCE_TOOLBOX_BIN"));
const inspectionScope = requiredEnvironment("OPENCLASS_PI_SOURCE_INSPECTION_SCOPE");
if (inspectionScope !== "directory_only" && inspectionScope !== "source" && inspectionScope !== "repository") {
  throw new Error("OpenClass source runtime received an unsupported inspection scope");
}
const archivePrefix = (process.env.OPENCLASS_PI_SOURCE_ARCHIVE_PREFIX ?? "").trim().replace(/^\/+|\/+$/g, "");
if (
  inspectionScope === "repository" &&
  (!archivePrefix || archivePrefix.includes("\\") || archivePrefix.includes("\0") || archivePrefix.split("/").length !== 1 || archivePrefix === "..")
) {
  throw new Error("OpenClass repository runtime received an invalid archive root prefix");
}
const repositoryReadablePathsFile = inspectionScope === "repository"
  ? requiredEnvironment("OPENCLASS_PI_REPOSITORY_READABLE_PATHS")
  : "";
const catalogPath = join(scratchPath, "catalog.json");
const catalogHeaderPath = join(scratchPath, "catalog-header.json");
const catalogNodesPath = join(scratchPath, "catalog-nodes.json");

function assertWorkspacePath(path: string, expectedParent: string): void {
  if (dirname(path) !== expectedParent) {
    throw new Error("OpenClass source runtime rejected a path outside its isolated workspace");
  }
}

assertWorkspacePath(sourcePath, workspace);
assertWorkspacePath(catalogPath, scratchPath);
assertWorkspacePath(catalogHeaderPath, scratchPath);
assertWorkspacePath(catalogNodesPath, scratchPath);
const repositoryReadablePathsPath = repositoryReadablePathsFile
  ? resolve(workspace, repositoryReadablePathsFile)
  : "";
if (repositoryReadablePathsPath) assertWorkspacePath(repositoryReadablePathsPath, workspace);

let catalogMutationQueue: Promise<void> = Promise.resolve();

async function withCatalogMutation<T>(operation: () => Promise<T>): Promise<T> {
  const result = catalogMutationQueue.then(operation, operation);
  catalogMutationQueue = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

function textResult(text: string, details: Record<string, unknown> = {}) {
  return { content: [{ type: "text" as const, text }], details };
}

function boundedText(value: string, limit = MAX_TEXT_OUTPUT): string {
  return value.length <= limit ? value : `${value.slice(0, limit)}\n[output truncated by OpenClass]`;
}

async function sourceSha256(): Promise<string> {
  const digest = createHash("sha256");
  for await (const chunk of createReadStream(sourcePath)) digest.update(chunk);
  return digest.digest("hex");
}

async function verifiedSourcePath(): Promise<string> {
  const sourceStat = await lstat(sourcePath);
  if (!sourceStat.isFile() || sourceStat.isSymbolicLink()) {
    throw new Error("The staged OpenClass source is not a regular file");
  }
  const resolved = await realpath(sourcePath);
  if (resolved !== sourcePath) throw new Error("The staged OpenClass source changed identity");
  return resolved;
}

function toolPath(name: "pdfinfo" | "pdftotext" | "pdftoppm"): string {
  return join(toolboxBin, name);
}

async function runTool(executable: string, args: string[], maxBuffer = MAX_TEXT_OUTPUT * 4) {
  const result = await execFileAsync(executable, args, {
    cwd: workspace,
    encoding: "utf8",
    maxBuffer,
    timeout: 60_000,
    env: { PATH: process.env.PATH ?? "/usr/bin:/bin", LANG: "en_US.UTF-8" },
  });
  return { stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
}

type ArchiveIndex = {
  visibleEntries: string[];
  rawByVisibleEntry: Map<string, string>;
};

let archiveIndex: ArchiveIndex | null = null;
let repositoryReadablePaths: Set<string> | null = null;

async function loadRepositoryReadablePaths(): Promise<Set<string>> {
  if (inspectionScope !== "repository" || !repositoryReadablePathsPath) {
    throw new Error("Repository file reading is unavailable for this source task");
  }
  if (repositoryReadablePaths) return repositoryReadablePaths;
  const raw = JSON.parse(await readFile(repositoryReadablePathsPath, "utf8")) as unknown;
  if (
    !Array.isArray(raw) ||
    !raw.length ||
    raw.length > MAX_ARCHIVE_ENTRIES ||
    raw.some((entry) => typeof entry !== "string" || !entry || entry.includes("\\") || entry.includes("\0") || entry.split("/").includes(".."))
  ) {
    throw new Error("The OpenClass repository-readable path manifest is invalid");
  }
  repositoryReadablePaths = new Set(raw as string[]);
  if (repositoryReadablePaths.size !== raw.length) {
    throw new Error("The OpenClass repository-readable path manifest contains duplicates");
  }
  return repositoryReadablePaths;
}

async function loadArchiveEntries(): Promise<ArchiveIndex> {
  if (archiveIndex) return archiveIndex;
  await verifiedSourcePath();
  const { stdout } = await runTool("/usr/bin/unzip", ["-Z1", sourcePath], 4 * 1024 * 1024);
  const rawEntries = stdout.split(/\r?\n/).filter(Boolean);
  if (rawEntries.length > MAX_ARCHIVE_ENTRIES) {
    throw new Error("The source archive contains too many entries for directory inspection");
  }
  const rawByVisibleEntry = new Map<string, string>();
  const repositoryPrefix = `${archivePrefix}/`;
  for (const rawEntry of rawEntries) {
    const visibleEntry = inspectionScope === "repository"
      ? rawEntry.startsWith(repositoryPrefix)
        ? rawEntry.slice(repositoryPrefix.length)
        : ""
      : rawEntry;
    if (!visibleEntry) continue;
    if (rawByVisibleEntry.has(visibleEntry)) {
      throw new Error("The source archive contains duplicate visible entries");
    }
    rawByVisibleEntry.set(visibleEntry, rawEntry);
  }
  archiveIndex = {
    visibleEntries: [...rawByVisibleEntry.keys()],
    rawByVisibleEntry,
  };
  return archiveIndex;
}

function normalizedArchivePrefix(prefix: string | undefined): string {
  const normalized = (prefix ?? "").trim().replace(/^\/+|\/+$/g, "");
  if (normalized.includes("\\") || normalized.includes("\0") || normalized.split("/").includes("..")) {
    throw new Error("archive_list received an unsafe repository prefix");
  }
  return normalized;
}

function filteredArchiveEntries(entries: string[], prefix: string, recursive: boolean): string[] {
  const base = prefix ? `${prefix}/` : "";
  const selected = new Set<string>();
  for (const entry of entries) {
    if (prefix && entry !== prefix && !entry.startsWith(base)) continue;
    if (recursive || inspectionScope !== "repository") {
      selected.add(entry);
      continue;
    }
    if (entry === prefix) {
      selected.add(entry);
      continue;
    }
    const remainder = entry.slice(base.length);
    if (!remainder) continue;
    const separator = remainder.indexOf("/");
    selected.add(separator >= 0 ? `${base}${remainder.slice(0, separator)}/` : `${base}${remainder}`);
  }
  return [...selected].sort((left, right) => left.localeCompare(right));
}

async function atomicJsonWrite(path: string, value: unknown): Promise<Buffer> {
  const bytes = Buffer.from(JSON.stringify(value), "utf8");
  if (bytes.length < 2 || bytes.length > MAX_CATALOG_BYTES) {
    throw new Error("The catalog checkpoint is outside the OpenClass size limit");
  }
  const temporaryPath = join(scratchPath, `.${basename(path)}-${randomUUID()}.tmp`);
  await writeFile(temporaryPath, bytes, { flag: "wx", mode: 0o600 });
  await chmod(temporaryPath, 0o600);
  await rename(temporaryPath, path);
  return bytes;
}

async function readJsonFile(path: string): Promise<unknown> {
  return JSON.parse(await readFile(path, "utf8")) as unknown;
}

async function checkpointState(): Promise<{
  started: boolean;
  pdf: unknown;
  nodes: Array<Record<string, unknown>>;
}> {
  try {
    const pdf = await readJsonFile(catalogHeaderPath);
    const nodes = await readJsonFile(catalogNodesPath);
    if (!Array.isArray(nodes) || nodes.some((node) => !node || typeof node !== "object" || Array.isArray(node))) {
      throw new Error("The OpenClass catalog node checkpoint is invalid");
    }
    return {
      started: true,
      pdf,
      nodes: nodes as Array<Record<string, unknown>>,
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { started: false, pdf: null, nodes: [] };
    }
    throw error;
  }
}

function validateCheckpointNodes(existing: Array<Record<string, unknown>>, additions: Array<Record<string, unknown>>): void {
  if (!additions.length || additions.length > 100) {
    throw new Error("catalog_append requires between 1 and 100 directory nodes");
  }
  const levels = new Map<string, number>();
  for (const node of existing) {
    if (typeof node.key === "string" && Number.isInteger(node.level)) {
      levels.set(node.key, node.level as number);
    }
  }
  for (const node of additions) {
    const key = node.key;
    const parentKey = node.parent_key;
    const level = node.level;
    if (typeof key !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(key) || levels.has(key)) {
      throw new Error("A checkpoint node has an invalid or duplicate key");
    }
    if (typeof node.title !== "string" || !node.title.trim() || !Number.isInteger(level) || (level as number) < 1) {
      throw new Error("A checkpoint node has an invalid title or level");
    }
    if (parentKey === null) {
      if (level !== 1) throw new Error("A root checkpoint node must use level 1");
    } else {
      if (typeof parentKey !== "string" || levels.get(parentKey) !== (level as number) - 1) {
        throw new Error("Checkpoint nodes must use parent-first contiguous levels");
      }
    }
    levels.set(key, level as number);
  }

}

export function parentConsistentPreorder(nodes: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const children = new Map<string | null, Array<Record<string, unknown>>>();
  for (const node of nodes) {
    const parentKey = node.parent_key as string | null;
    const siblings = children.get(parentKey) ?? [];
    siblings.push(node);
    children.set(parentKey, siblings);
  }
  const ordered: Array<Record<string, unknown>> = [];
  const visit = (node: Record<string, unknown>): void => {
    ordered.push(node);
    for (const child of children.get(node.key as string) ?? []) {
      visit(child);
    }
  };
  for (const root of children.get(null) ?? []) {
    visit(root);
  }
  if (ordered.length !== nodes.length) {
    throw new Error("The Pi-authored directory graph contains an unreachable node");
  }
  return ordered;
}

export default function openClassPiSourceTools(pi: ExtensionAPI) {
  pi.registerTool({
    name: "source_info",
    label: "Source information",
    description: "Return metadata and SHA-256 for the sole staged OpenClass source.",
    parameters: Type.Object({}),
    async execute() {
      await verifiedSourcePath();
      const sourceStat = await stat(sourcePath);
      const suffixMatch = basename(sourcePath)
        .toLowerCase()
        .match(/\.[^.]+$/);
      const suffix = suffixMatch?.[0] ?? "";
      let pdfInfo = "";
      if (suffix === ".pdf") {
        pdfInfo = boundedText((await runTool(toolPath("pdfinfo"), [sourcePath])).stdout, 16_000);
      }
      return textResult(
        JSON.stringify({
          file_name: basename(sourcePath),
          suffix,
          byte_count: sourceStat.size,
          sha256: await sourceSha256(),
          pdf_info: pdfInfo,
          inspection_scope: inspectionScope,
          archive_prefix: archivePrefix || null,
        }),
      );
    },
  });

  pi.registerTool({
    name: "pdf_text",
    label: "Read bounded PDF pages",
    description: `Read layout-preserving text from ${MAX_PDF_PAGE_SPAN} or fewer one-based PDF pages.`,
    parameters: Type.Object({
      first_page: Type.Integer({ minimum: 1 }),
      last_page: Type.Integer({ minimum: 1 }),
    }),
    async execute(_id, params) {
      await verifiedSourcePath();
      if (params.last_page < params.first_page || params.last_page - params.first_page + 1 > MAX_PDF_PAGE_SPAN) {
        throw new Error(`PDF inspection must cover between 1 and ${MAX_PDF_PAGE_SPAN} pages`);
      }
      const { stdout } = await runTool(toolPath("pdftotext"), [
        "-f",
        String(params.first_page),
        "-l",
        String(params.last_page),
        "-layout",
        "-enc",
        "UTF-8",
        sourcePath,
        "-",
      ]);
      return textResult(boundedText(stdout), {
        first_page: params.first_page,
        last_page: params.last_page,
      });
    },
  });

  pi.registerTool({
    name: "pdf_page_image",
    label: "Render one PDF page",
    description: "Render one one-based PDF page as a PNG for visual directory/OCR inspection.",
    parameters: Type.Object({ page: Type.Integer({ minimum: 1 }) }),
    async execute(_id, params) {
      await verifiedSourcePath();
      const prefix = join(scratchPath, `page-${params.page}-${randomUUID()}`);
      const imagePath = `${prefix}.png`;
      try {
        await runTool(
          toolPath("pdftoppm"),
          ["-f", String(params.page), "-l", String(params.page), "-singlefile", "-scale-to", "1800", "-png", sourcePath, prefix],
          16 * 1024 * 1024,
        );
        const image = await readFile(imagePath);
        if (!image.length || image.length > 12 * 1024 * 1024) {
          throw new Error("Rendered PDF page is empty or exceeds the OpenClass image limit");
        }
        return {
          content: [
            {
              type: "text" as const,
              text: `Rendered physical PDF page ${params.page}.`,
            },
            {
              type: "image" as const,
              data: image.toString("base64"),
              mimeType: "image/png",
            },
          ],
          details: { page: params.page },
        };
      } finally {
        await rm(imagePath, { force: true });
      }
    },
  });

  pi.registerTool({
    name: "archive_list",
    label: "List source archive entries",
    description: "List a bounded page of entries in the sole staged source archive without extracting it. Repository inspection supports non-recursive prefix navigation.",
    parameters: Type.Object({
      prefix: Type.Optional(Type.String({ maxLength: 2_048 })),
      recursive: Type.Optional(Type.Boolean()),
      start_index: Type.Optional(Type.Integer({ minimum: 0, maximum: MAX_ARCHIVE_ENTRIES })),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: MAX_ARCHIVE_LIST_PAGE_ENTRIES })),
    }),
    async execute(_id, params) {
      const index = await loadArchiveEntries();
      const prefix = normalizedArchivePrefix(params.prefix);
      const recursive = params.recursive ?? inspectionScope !== "repository";
      const entries = filteredArchiveEntries(index.visibleEntries, prefix, recursive);
      const startIndex = params.start_index ?? 0;
      const limit = params.limit ?? 500;
      if (startIndex > entries.length) {
        throw new Error("archive_list start_index falls beyond the filtered archive entries");
      }
      const page: string[] = [];
      let endIndex = startIndex;
      let characterCount = 0;
      while (endIndex < entries.length && page.length < limit) {
        const entry = entries[endIndex];
        const nextCharacters = entry.length + (page.length ? 1 : 0);
        if (page.length && characterCount + nextCharacters > MAX_TEXT_OUTPUT) break;
        page.push(entry);
        characterCount += nextCharacters;
        endIndex += 1;
      }
      const complete = endIndex >= entries.length;
      return textResult(page.join("\n"), {
        prefix,
        recursive,
        total_entry_count: index.visibleEntries.length,
        filtered_entry_count: entries.length,
        start_index: startIndex,
        end_index: endIndex,
        complete,
        next_start_index: complete ? null : endIndex,
      });
    },
  });

  pi.registerTool({
    name: "archive_read",
    label: "Read one source archive entry",
    description: "Read one bounded character segment from an exact archive entry. Continue from next_start_character until complete=true.",
    parameters: Type.Object({
      entry: Type.String({ minLength: 1, maxLength: 2_048 }),
      start_character: Type.Optional(Type.Integer({ minimum: 0, maximum: MAX_ARCHIVE_ENTRY_BUFFER_BYTES })),
    }),
    async execute(_id, params) {
      const index = await loadArchiveEntries();
      const rawEntry = index.rawByVisibleEntry.get(params.entry);
      if (!rawEntry) throw new Error("The requested archive entry does not exist");
      if (inspectionScope === "repository" && !(await loadRepositoryReadablePaths()).has(params.entry)) {
        throw new Error("The requested repository entry is not a verified readable regular file");
      }
      const { stdout } = await runTool("/usr/bin/unzip", ["-p", sourcePath, rawEntry], MAX_ARCHIVE_ENTRY_BUFFER_BYTES);
      const startCharacter = params.start_character ?? 0;
      if (startCharacter > stdout.length) {
        throw new Error("archive_read start_character falls beyond the decoded archive entry");
      }
      const endCharacter = Math.min(stdout.length, startCharacter + MAX_ARCHIVE_ENTRY_CHARACTERS);
      const complete = endCharacter >= stdout.length;
      return textResult(stdout.slice(startCharacter, endCharacter), {
        entry: params.entry,
        start_character: startCharacter,
        end_character: endCharacter,
        total_character_count: stdout.length,
        complete,
        next_start_character: complete ? null : endCharacter,
      });
    },
  });

  pi.registerTool({
    name: "repository_read",
    label: "Read verified repository lines",
    description: `Read at most ${MAX_TEXT_LINES_PER_CALL} line-numbered lines from one verified readable regular repository file.`,
    parameters: Type.Object({
      entry: Type.String({ minLength: 1, maxLength: 2_048 }),
      start_line: Type.Integer({ minimum: 1, maximum: 10_000_000 }),
      end_line: Type.Integer({ minimum: 1, maximum: 10_000_000 }),
    }),
    async execute(_id, params) {
      if (inspectionScope !== "repository") {
        throw new Error("repository_read is available only for repository inspection");
      }
      if (params.end_line < params.start_line || params.end_line - params.start_line + 1 > MAX_TEXT_LINES_PER_CALL) {
        throw new Error(`Repository inspection must cover between 1 and ${MAX_TEXT_LINES_PER_CALL} lines`);
      }
      const readable = await loadRepositoryReadablePaths();
      if (!readable.has(params.entry)) {
        throw new Error("The requested repository entry is not a verified readable regular file");
      }
      const index = await loadArchiveEntries();
      const rawEntry = index.rawByVisibleEntry.get(params.entry);
      if (!rawEntry) throw new Error("The requested repository entry does not exist");
      const { stdout } = await runTool("/usr/bin/unzip", ["-p", sourcePath, rawEntry], MAX_ARCHIVE_ENTRY_BUFFER_BYTES);
      const lines = stdout.split(/\r\n|\n|\r/);
      if (lines.at(-1) === "") lines.pop();
      if (!lines.length) lines.push("");
      if (params.start_line > lines.length) {
        throw new Error("repository_read start_line falls beyond the verified file boundary");
      }
      const actualEnd = Math.min(params.end_line, lines.length);
      const selected = lines
        .slice(params.start_line - 1, actualEnd)
        .map((line, index) => `${params.start_line + index}: ${line}`)
        .join("\n");
      return textResult(boundedText(selected), {
        entry: params.entry,
        start_line: params.start_line,
        end_line: actualEnd,
        total_line_count: lines.length,
      });
    },
  });

  pi.registerTool({
    name: "text_read",
    label: "Read bounded source lines",
    description: `Read at most ${MAX_TEXT_LINES_PER_CALL} lines from a plain-text source.`,
    parameters: Type.Object({
      start_line: Type.Integer({ minimum: 1 }),
      end_line: Type.Integer({ minimum: 1 }),
    }),
    async execute(_id, params) {
      await verifiedSourcePath();
      if (params.end_line < params.start_line || params.end_line - params.start_line + 1 > MAX_TEXT_LINES_PER_CALL) {
        throw new Error(`Text inspection must cover between 1 and ${MAX_TEXT_LINES_PER_CALL} lines`);
      }
      const lines: string[] = [];
      let lineNumber = 0;
      const reader = createInterface({
        input: createReadStream(sourcePath, { encoding: "utf8" }),
        crlfDelay: Infinity,
      });
      for await (const line of reader) {
        lineNumber += 1;
        if (lineNumber >= params.start_line) lines.push(line);
        if (lineNumber >= params.end_line) break;
      }
      return textResult(boundedText(lines.join("\n")), {
        start_line: params.start_line,
        end_line: Math.min(params.end_line, lineNumber),
      });
    },
  });

  pi.registerTool({
    name: "catalog_status",
    label: "Read catalog checkpoint status",
    description: "Report whether a resumable catalog checkpoint exists and which nodes are already saved.",
    parameters: Type.Object({}),
    async execute() {
      const state = await checkpointState();
      return textResult(
        JSON.stringify({
          started: state.started,
          node_count: state.nodes.length,
          last_keys: state.nodes.slice(-8).map((node) => node.key),
          last_nodes: state.nodes.slice(-8).map((node) => ({
            key: node.key,
            title: node.title,
            level: node.level,
            source_locator: node.source_locator,
          })),
          pdf: state.pdf,
        }),
      );
    },
  });

  pi.registerTool({
    name: "catalog_start",
    label: "Start catalog checkpoint",
    description: "Start a resumable catalog. Pass the validated PDF task object, or null for a non-PDF source.",
    parameters: Type.Object({
      pdf_json: Type.String({ minLength: 4, maxLength: 16_000 }),
    }),
    async execute(_id, params) {
      return withCatalogMutation(async () => {
        const state = await checkpointState();
        if (state.started && state.nodes.length) {
          throw new Error("A non-empty catalog checkpoint already exists; resume it instead of restarting");
        }
        const pdf = JSON.parse(params.pdf_json) as unknown;
        if (pdf !== null && (!pdf || typeof pdf !== "object" || Array.isArray(pdf))) {
          throw new Error("pdf_json must contain one PDF task object or null");
        }
        await atomicJsonWrite(catalogHeaderPath, pdf);
        await atomicJsonWrite(catalogNodesPath, []);
        return textResult(JSON.stringify({ started: true, node_count: 0 }));
      });
    },
  });

  pi.registerTool({
    name: "catalog_append",
    label: "Append catalog checkpoint nodes",
    description: "Append 1-100 complete Pi-authored directory nodes in parent-before-child order. Final submission mechanically orders the unchanged parent graph.",
    parameters: Type.Object({
      nodes_json: Type.String({ minLength: 4, maxLength: 4 * 1024 * 1024 }),
    }),
    async execute(_id, params) {
      return withCatalogMutation(async () => {
        const state = await checkpointState();
        if (!state.started) throw new Error("Call catalog_start before appending nodes");
        const additions = JSON.parse(params.nodes_json) as unknown;
        if (!Array.isArray(additions) || additions.some((node) => !node || typeof node !== "object" || Array.isArray(node))) {
          throw new Error("nodes_json must contain one JSON array of directory node objects");
        }
        const typedAdditions = additions as Array<Record<string, unknown>>;
        validateCheckpointNodes(state.nodes, typedAdditions);
        const nodes = [...state.nodes, ...typedAdditions];
        await atomicJsonWrite(catalogNodesPath, nodes);
        return textResult(
          JSON.stringify({
            appended: typedAdditions.length,
            node_count: nodes.length,
            last_key: nodes.at(-1)?.key ?? null,
          }),
        );
      });
    },
  });

  pi.registerTool({
    name: "write_catalog",
    label: "Submit source directory catalog",
    description: "Assemble the resumable checkpoint and atomically write the final OpenClass directory catalog artifact.",
    parameters: Type.Object({}),
    async execute() {
      return withCatalogMutation(async () => {
        const state = await checkpointState();
        if (!state.started || !state.nodes.length) {
          throw new Error("A non-empty catalog checkpoint is required before final submission");
        }
        const orderedNodes = parentConsistentPreorder(state.nodes);
        const artifact = inspectionScope === "directory_only" ? { complete: true, pdf: state.pdf, nodes: orderedNodes } : { complete: true, nodes: orderedNodes };
        const bytes = await atomicJsonWrite(catalogPath, artifact);
        const receipt = {
          artifact_path: "scratch/catalog.json",
          sha256: createHash("sha256").update(bytes).digest("hex"),
          byte_count: bytes.length,
          node_count: orderedNodes.length,
        };
        return textResult(JSON.stringify(receipt), receipt);
      });
    },
  });
}
