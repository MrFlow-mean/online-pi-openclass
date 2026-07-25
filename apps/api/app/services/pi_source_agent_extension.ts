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
const MAX_ARCHIVE_ENTRIES = 20_000;
const MAX_ARCHIVE_ENTRY_BYTES = 160_000;
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
if (inspectionScope !== "directory_only" && inspectionScope !== "source") {
  throw new Error("OpenClass source runtime received an unsupported inspection scope");
}
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

let catalogMutationQueue: Promise<void> = Promise.resolve();

async function withCatalogMutation<T>(operation: () => Promise<T>): Promise<T> {
  const result = catalogMutationQueue.then(operation, operation);
  catalogMutationQueue = result.then(() => undefined, () => undefined);
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

let archiveEntries: Set<string> | null = null;

async function loadArchiveEntries(): Promise<Set<string>> {
  if (archiveEntries) return archiveEntries;
  await verifiedSourcePath();
  const { stdout } = await runTool("/usr/bin/unzip", ["-Z1", sourcePath], 4 * 1024 * 1024);
  const entries = stdout.split(/\r?\n/).filter(Boolean);
  if (entries.length > MAX_ARCHIVE_ENTRIES) {
    throw new Error("The source archive contains too many entries for directory inspection");
  }
  archiveEntries = new Set(entries);
  return archiveEntries;
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
    return { started: true, pdf, nodes: nodes as Array<Record<string, unknown>> };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { started: false, pdf: null, nodes: [] };
    }
    throw error;
  }
}

function validateCheckpointNodes(
  existing: Array<Record<string, unknown>>,
  additions: Array<Record<string, unknown>>,
): void {
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

export default function openClassPiSourceTools(pi: ExtensionAPI) {
  pi.registerTool({
    name: "source_info",
    label: "Source information",
    description: "Return metadata and SHA-256 for the sole staged OpenClass source.",
    parameters: Type.Object({}),
    async execute() {
      await verifiedSourcePath();
      const sourceStat = await stat(sourcePath);
      const suffixMatch = basename(sourcePath).toLowerCase().match(/\.[^.]+$/);
      const suffix = suffixMatch?.[0] ?? "";
      let pdfInfo = "";
      if (suffix === ".pdf") {
        pdfInfo = boundedText((await runTool(toolPath("pdfinfo"), [sourcePath])).stdout, 16_000);
      }
      return textResult(JSON.stringify({
        file_name: basename(sourcePath),
        suffix,
        byte_count: sourceStat.size,
        sha256: await sourceSha256(),
        pdf_info: pdfInfo,
      }));
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
        "-f", String(params.first_page), "-l", String(params.last_page),
        "-layout", "-enc", "UTF-8", sourcePath, "-",
      ]);
      return textResult(boundedText(stdout), { first_page: params.first_page, last_page: params.last_page });
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
        await runTool(toolPath("pdftoppm"), [
          "-f", String(params.page), "-l", String(params.page), "-singlefile",
          "-scale-to", "1800", "-png", sourcePath, prefix,
        ], 16 * 1024 * 1024);
        const image = await readFile(imagePath);
        if (!image.length || image.length > 12 * 1024 * 1024) {
          throw new Error("Rendered PDF page is empty or exceeds the OpenClass image limit");
        }
        return {
          content: [
            { type: "text" as const, text: `Rendered physical PDF page ${params.page}.` },
            { type: "image" as const, data: image.toString("base64"), mimeType: "image/png" },
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
    description: "List entries in the sole staged EPUB, DOCX, PPTX, or XLSX source without extracting it.",
    parameters: Type.Object({}),
    async execute() {
      const entries = [...(await loadArchiveEntries())];
      return textResult(boundedText(entries.join("\n")), { entry_count: entries.length });
    },
  });

  pi.registerTool({
    name: "archive_read",
    label: "Read one source archive entry",
    description: "Read one exact archive entry, bounded for safe navigation inspection.",
    parameters: Type.Object({ entry: Type.String({ minLength: 1, maxLength: 2_048 }) }),
    async execute(_id, params) {
      const entries = await loadArchiveEntries();
      if (!entries.has(params.entry)) throw new Error("The requested archive entry does not exist");
      const { stdout } = await runTool("/usr/bin/unzip", ["-p", sourcePath, params.entry], MAX_ARCHIVE_ENTRY_BYTES * 4);
      return textResult(boundedText(stdout, MAX_ARCHIVE_ENTRY_BYTES), { entry: params.entry });
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
      const reader = createInterface({ input: createReadStream(sourcePath, { encoding: "utf8" }), crlfDelay: Infinity });
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
      return textResult(JSON.stringify({
        started: state.started,
        node_count: state.nodes.length,
        last_keys: state.nodes.slice(-8).map((node) => node.key),
        pdf: state.pdf,
      }));
    },
  });

  pi.registerTool({
    name: "catalog_start",
    label: "Start catalog checkpoint",
    description: "Start a resumable catalog. Pass the validated PDF task object, or null for a non-PDF source.",
    parameters: Type.Object({ pdf_json: Type.String({ minLength: 4, maxLength: 16_000 }) }),
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
    description: "Append 1-100 complete directory node objects to the resumable catalog in parent-first order.",
    parameters: Type.Object({ nodes_json: Type.String({ minLength: 4, maxLength: 4 * 1024 * 1024 }) }),
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
        return textResult(JSON.stringify({
          appended: typedAdditions.length,
          node_count: nodes.length,
          last_key: nodes.at(-1)?.key ?? null,
        }));
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
        const artifact = inspectionScope === "directory_only"
          ? { complete: true, pdf: state.pdf, nodes: state.nodes }
          : { complete: true, nodes: state.nodes };
        const bytes = await atomicJsonWrite(catalogPath, artifact);
        const receipt = {
          artifact_path: "scratch/catalog.json",
          sha256: createHash("sha256").update(bytes).digest("hex"),
          byte_count: bytes.length,
          node_count: state.nodes.length,
        };
        return textResult(JSON.stringify(receipt), receipt);
      });
    },
  });
}
