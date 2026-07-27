(function installRidocCommunityAttachments() {
  var config = window.__OPENCLASS_COMMUNITY_BRIDGE__;
  if (!config || !config.entryUrl) {
    return;
  }

  var RIDOC_MEDIA_TYPE = "application/vnd.openclass.ridoc+zip";
  var RIDOC_MARKER = "openclass-ridoc=";
  var MAX_RIDOC_BYTES = 256 * 1024 * 1024;
  var MAX_MANIFEST_BYTES = 2 * 1024 * 1024;
  var MAX_CENTRAL_DIRECTORY_BYTES = 32 * 1024 * 1024;
  var REQUIRED_PATHS = [
    "manifest.json",
    "history/graph.json",
    "evidence/index.json",
    "integrity/checksums.json",
  ];

  var style = document.createElement("style");
  style.textContent = [
    ".openclass-ridoc-receiver{margin:14px 0 18px;padding:0;border:0}",
    ".openclass-ridoc-dropzone{display:flex;align-items:center;gap:14px;padding:16px 18px;border:1px dashed #7dd3fc;border-radius:14px;background:#f0f9ff;color:#0c4a6e;cursor:pointer;transition:border-color .15s ease,background .15s ease}",
    ".openclass-ridoc-dropzone:hover,.openclass-ridoc-dropzone[data-dragging='true']{border-color:#0284c7;background:#e0f2fe}",
    ".openclass-ridoc-dropzone[data-busy='true']{cursor:progress;opacity:.72}",
    ".openclass-ridoc-dropzone-icon{display:flex;width:38px;height:38px;flex:0 0 auto;align-items:center;justify-content:center;border-radius:11px;background:#fff;color:#0369a1;font-size:19px;font-weight:800;box-shadow:0 1px 2px rgba(12,74,110,.08)}",
    ".openclass-ridoc-dropzone-title{display:block;font-size:14px;font-weight:700;color:#0c4a6e}",
    ".openclass-ridoc-dropzone-help{display:block;margin-top:3px;font-size:12px;line-height:1.5;color:#0369a1}",
    ".openclass-ridoc-status{min-height:20px;margin:7px 2px 0;font-size:12px;color:#0369a1}",
    ".openclass-ridoc-status[data-error='true']{color:#b91c1c}",
    "blockquote.openclass-ridoc-card{position:relative;margin:16px 0;padding:20px;border:1px solid #bae6fd;border-radius:16px;background:linear-gradient(135deg,#f0f9ff 0%,#fff 72%);color:#0f172a;cursor:pointer;box-shadow:0 8px 24px rgba(14,116,144,.08);transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease}",
    "blockquote.openclass-ridoc-card:hover{border-color:#38bdf8;box-shadow:0 12px 30px rgba(14,116,144,.13);transform:translateY(-1px)}",
    "blockquote.openclass-ridoc-card:focus-visible{outline:3px solid rgba(14,165,233,.28);outline-offset:2px}",
    ".openclass-ridoc-card-kicker{font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:#0284c7}",
    ".openclass-ridoc-card-title{margin:7px 0 0;font-size:18px;font-weight:750;line-height:1.35;color:#0f172a}",
    ".openclass-ridoc-card-summary{margin:9px 0 0;font-size:13px;line-height:1.65;color:#475569}",
    ".openclass-ridoc-card-meta{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}",
    ".openclass-ridoc-card-pill{display:inline-flex;align-items:center;border-radius:999px;background:#e0f2fe;padding:4px 8px;font-size:11px;font-weight:650;color:#075985}",
    ".openclass-ridoc-card-action{display:inline-flex;margin-top:16px;font-size:12px;font-weight:750;color:#0369a1}",
    "blockquote.openclass-ridoc-card p{margin:0}",
    "blockquote.openclass-ridoc-card a{color:inherit;text-decoration:none}",
  ].join("");
  document.head.appendChild(style);

  function cleanText(value, maximumLength) {
    return String(value || "")
      .replace(/[\u0000-\u001f\u007f]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, maximumLength);
  }

  function bytesFromBase64Url(value) {
    var base64 = value.replace(/-/g, "+").replace(/_/g, "/");
    while (base64.length % 4) {
      base64 += "=";
    }
    var binary = window.atob(base64);
    var bytes = new Uint8Array(binary.length);
    for (var index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  }

  function base64UrlFromText(value) {
    var bytes = new TextEncoder().encode(value);
    var binary = "";
    for (var offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + 0x8000));
    }
    return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function findSignature(bytes, signature) {
    for (var index = bytes.length - 4; index >= 0; index -= 1) {
      if (
        bytes[index] === (signature & 0xff)
        && bytes[index + 1] === ((signature >>> 8) & 0xff)
        && bytes[index + 2] === ((signature >>> 16) & 0xff)
        && bytes[index + 3] === ((signature >>> 24) & 0xff)
      ) {
        return index;
      }
    }
    return -1;
  }

  async function zipEntries(file) {
    var tailSize = Math.min(file.size, 65557);
    var tail = new Uint8Array(await file.slice(file.size - tailSize).arrayBuffer());
    var endOffset = findSignature(tail, 0x06054b50);
    if (endOffset < 0 || endOffset + 22 > tail.length) {
      throw new Error("文件不是有效的 RIDOC ZIP 归档");
    }
    var end = new DataView(tail.buffer, tail.byteOffset + endOffset, tail.length - endOffset);
    var entryCount = end.getUint16(10, true);
    var centralSize = end.getUint32(12, true);
    var centralOffset = end.getUint32(16, true);
    if (
      !entryCount
      || entryCount > 4096
      || centralSize > MAX_CENTRAL_DIRECTORY_BYTES
      || centralOffset + centralSize > file.size
    ) {
      throw new Error("RIDOC 目录结构无效或过大");
    }
    var centralBytes = new Uint8Array(
      await file.slice(centralOffset, centralOffset + centralSize).arrayBuffer()
    );
    var view = new DataView(centralBytes.buffer, centralBytes.byteOffset, centralBytes.byteLength);
    var decoder = new TextDecoder("utf-8", { fatal: true });
    var entries = {};
    var cursor = 0;
    for (var entryIndex = 0; entryIndex < entryCount; entryIndex += 1) {
      if (cursor + 46 > centralBytes.length || view.getUint32(cursor, true) !== 0x02014b50) {
        throw new Error("RIDOC 中央目录损坏");
      }
      var method = view.getUint16(cursor + 10, true);
      var compressedSize = view.getUint32(cursor + 20, true);
      var uncompressedSize = view.getUint32(cursor + 24, true);
      var nameLength = view.getUint16(cursor + 28, true);
      var extraLength = view.getUint16(cursor + 30, true);
      var commentLength = view.getUint16(cursor + 32, true);
      var localOffset = view.getUint32(cursor + 42, true);
      var nextCursor = cursor + 46 + nameLength + extraLength + commentLength;
      if (nextCursor > centralBytes.length) {
        throw new Error("RIDOC 文件条目越界");
      }
      var name = decoder.decode(centralBytes.subarray(cursor + 46, cursor + 46 + nameLength));
      entries[name] = {
        method: method,
        compressedSize: compressedSize,
        uncompressedSize: uncompressedSize,
        localOffset: localOffset,
      };
      cursor = nextCursor;
    }
    return entries;
  }

  async function readZipEntry(file, entry) {
    if (!entry || entry.uncompressedSize > MAX_MANIFEST_BYTES) {
      throw new Error("RIDOC 课程清单缺失或过大");
    }
    var localHeader = new Uint8Array(await file.slice(entry.localOffset, entry.localOffset + 30).arrayBuffer());
    if (localHeader.length !== 30 || new DataView(localHeader.buffer).getUint32(0, true) !== 0x04034b50) {
      throw new Error("RIDOC 课程清单入口无效");
    }
    var localView = new DataView(localHeader.buffer);
    var nameLength = localView.getUint16(26, true);
    var extraLength = localView.getUint16(28, true);
    var dataOffset = entry.localOffset + 30 + nameLength + extraLength;
    var compressed = new Uint8Array(
      await file.slice(dataOffset, dataOffset + entry.compressedSize).arrayBuffer()
    );
    if (entry.method === 0) {
      return compressed;
    }
    if (entry.method !== 8 || typeof DecompressionStream === "undefined") {
      throw new Error("当前浏览器无法读取这个 RIDOC 压缩方式");
    }
    var stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  async function readRidocMetadata(file) {
    if (!file || !/\.ridoc$/i.test(file.name || "")) {
      throw new Error("请选择 .ridoc 课程文件");
    }
    if (!file.size || file.size > MAX_RIDOC_BYTES) {
      throw new Error("RIDOC 文件大小必须在 256 MiB 以内");
    }
    var entries = await zipEntries(file);
    REQUIRED_PATHS.forEach(function requirePath(path) {
      if (!entries[path]) {
        throw new Error("RIDOC 缺少必要文件：" + path);
      }
    });
    var manifestBytes = await readZipEntry(file, entries["manifest.json"]);
    var manifest;
    try {
      manifest = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(manifestBytes));
    } catch (_error) {
      throw new Error("RIDOC 课程清单不是有效 JSON");
    }
    if (
      !manifest
      || manifest.spec_version !== "1.0"
      || manifest.profile !== "learning.lesson"
      || manifest.media_type !== RIDOC_MEDIA_TYPE
    ) {
      throw new Error("RIDOC 课程格式或版本不受支持");
    }
    var lesson = manifest.lesson && typeof manifest.lesson === "object" ? manifest.lesson : {};
    var title = cleanText(lesson.title, 160);
    if (!title) {
      throw new Error("RIDOC 课程清单缺少课程名称");
    }
    var capabilities = manifest.capabilities && typeof manifest.capabilities === "object"
      ? manifest.capabilities
      : {};
    var capabilityLabels = [];
    if (capabilities.playback) capabilityLabels.push("可播放");
    if (capabilities.continue) capabilityLabels.push("可继续");
    if (capabilities.fork) capabilityLabels.push("可分叉");
    return {
      version: 1,
      title: title,
      summary: cleanText(lesson.summary, 360),
      fileName: cleanText(file.name, 180),
      sizeBytes: file.size,
      capabilities: capabilityLabels,
    };
  }

  function attachmentUrl(value, metadata) {
    var destination = new URL(value, window.location.href);
    if (destination.protocol !== "http:" && destination.protocol !== "https:") {
      throw new Error("附件地址无效");
    }
    destination.hash = RIDOC_MARKER + base64UrlFromText(JSON.stringify(metadata));
    return destination.toString();
  }

  function insertAttachmentMarkdown(textarea, destination) {
    var markdown = "> [OpenClass RIDOC 课程文件](" + destination + ")";
    var current = textarea.value || "";
    var next = current + (current.trim() ? "\n\n" : "") + markdown;
    var setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
    setter.call(textarea, next);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function uploadRidoc(file, textarea, receiver) {
    var input = receiver.querySelector("input[type='file']");
    var dropzone = receiver.querySelector(".openclass-ridoc-dropzone");
    var status = receiver.querySelector(".openclass-ridoc-status");
    input.disabled = true;
    dropzone.dataset.busy = "true";
    status.dataset.error = "false";
    status.textContent = "正在读取课程简介并上传…";
    try {
      var metadata = await readRidocMetadata(file);
      var answerToken = window.localStorage.getItem("_a_ltk_");
      if (!answerToken) {
        throw new Error("请先登录社区再添加课程文件");
      }
      var payload = new FormData();
      payload.append("source", "post_attachment");
      payload.append("file", file, file.name);
      var response = await window.fetch("/answer/api/v1/file", {
        method: "POST",
        headers: { Authorization: answerToken },
        body: payload,
      });
      var result = await response.json().catch(function emptyResult() { return null; });
      if (!response.ok || !result || typeof result.data !== "string" || !result.data) {
        var detail = result && (result.msg || result.message);
        throw new Error(cleanText(detail, 180) || "课程文件上传失败");
      }
      insertAttachmentMarkdown(textarea, attachmentUrl(result.data, metadata));
      status.textContent = "已添加：" + metadata.title;
      input.value = "";
    } catch (error) {
      status.dataset.error = "true";
      status.textContent = error instanceof Error ? error.message : "课程文件上传失败";
    } finally {
      input.disabled = false;
      dropzone.dataset.busy = "false";
      dropzone.dataset.dragging = "false";
    }
  }

  function createReceiver(textarea) {
    var receiver = document.createElement("section");
    receiver.className = "openclass-ridoc-receiver";
    receiver.dataset.openclassRidocReceiver = "true";
    var dropzone = document.createElement("label");
    dropzone.className = "openclass-ridoc-dropzone";
    dropzone.innerHTML = [
      '<span class="openclass-ridoc-dropzone-icon" aria-hidden="true">＋</span>',
      '<span><span class="openclass-ridoc-dropzone-title">添加 RIDOC 课程文件</span>',
      '<span class="openclass-ridoc-dropzone-help">点击选择或拖入 .ridoc；发布后显示课程简介卡片</span></span>',
    ].join("");
    var input = document.createElement("input");
    input.type = "file";
    input.accept = ".ridoc," + RIDOC_MEDIA_TYPE;
    input.hidden = true;
    input.setAttribute("aria-label", "添加 RIDOC 课程文件");
    dropzone.appendChild(input);
    var status = document.createElement("p");
    status.className = "openclass-ridoc-status";
    status.setAttribute("aria-live", "polite");
    receiver.appendChild(dropzone);
    receiver.appendChild(status);

    input.addEventListener("change", function receiveSelectedFile() {
      if (input.files && input.files[0]) {
        uploadRidoc(input.files[0], textarea, receiver);
      }
    });
    ["dragenter", "dragover"].forEach(function bindDragState(eventName) {
      dropzone.addEventListener(eventName, function markDragging(event) {
        event.preventDefault();
        dropzone.dataset.dragging = "true";
      });
    });
    ["dragleave", "drop"].forEach(function bindDragEnd(eventName) {
      dropzone.addEventListener(eventName, function clearDragging(event) {
        event.preventDefault();
        dropzone.dataset.dragging = "false";
      });
    });
    dropzone.addEventListener("drop", function receiveDroppedFile(event) {
      var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) {
        uploadRidoc(file, textarea, receiver);
      }
    });
    return receiver;
  }

  function installReceiver() {
    if (
      !/(?:^|\/)questions\/add\/?$/.test(window.location.pathname)
      && !/(?:^|\/)questions\/[^/]+\/edit\/?$/.test(window.location.pathname)
    ) {
      return;
    }
    if (document.querySelector("[data-openclass-ridoc-receiver='true']")) {
      return;
    }
    var textarea = document.querySelector("textarea");
    if (!textarea || textarea.dataset.openclassRidocEnabled === "true") {
      return;
    }
    var host = textarea.closest(".mb-3") || textarea.parentElement;
    if (!host || !host.parentElement) {
      return;
    }
    textarea.dataset.openclassRidocEnabled = "true";
    host.insertAdjacentElement("afterend", createReceiver(textarea));
  }

  function metadataFromAnchor(anchor) {
    try {
      var destination = new URL(anchor.href, window.location.href);
      if (
        (destination.protocol !== "http:" && destination.protocol !== "https:")
        || destination.hash.indexOf("#" + RIDOC_MARKER) !== 0
      ) {
        return null;
      }
      var encoded = destination.hash.slice(RIDOC_MARKER.length + 1);
      var metadata = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytesFromBase64Url(encoded)));
      if (!metadata || metadata.version !== 1 || !cleanText(metadata.title, 160)) {
        return null;
      }
      destination.hash = "";
      return {
        destination: destination,
        title: cleanText(metadata.title, 160),
        summary: cleanText(metadata.summary, 360),
        fileName: cleanText(metadata.fileName, 180),
        sizeBytes: Number(metadata.sizeBytes) || 0,
        capabilities: Array.isArray(metadata.capabilities)
          ? metadata.capabilities.map(function cleanCapability(value) { return cleanText(value, 24); }).filter(Boolean).slice(0, 6)
          : [],
      };
    } catch (_error) {
      return null;
    }
  }

  function formatBytes(value) {
    if (!value || value < 1024) return value + " B";
    if (value < 1024 * 1024) return Math.round(value / 1024) + " KB";
    return (value / (1024 * 1024)).toFixed(value < 10 * 1024 * 1024 ? 1 : 0) + " MB";
  }

  function cardText(className, text) {
    var element = document.createElement("div");
    element.className = className;
    element.textContent = text;
    return element;
  }

  function enhanceRidocCard(anchor) {
    if (!(anchor instanceof HTMLAnchorElement)) {
      return;
    }
    var metadata = metadataFromAnchor(anchor);
    var card = anchor.closest("blockquote");
    if (!metadata || !card || card.dataset.openclassRidocCard === "true") {
      return;
    }
    card.dataset.openclassRidocCard = "true";
    card.classList.add("openclass-ridoc-card");
    card.setAttribute("role", "link");
    card.setAttribute("tabindex", "0");
    card.setAttribute("aria-label", "下载 RIDOC 课程：" + metadata.title);
    card.replaceChildren();
    card.appendChild(cardText("openclass-ridoc-card-kicker", "OpenClass · RIDOC 课程"));
    card.appendChild(cardText("openclass-ridoc-card-title", metadata.title));
    if (metadata.summary) {
      card.appendChild(cardText("openclass-ridoc-card-summary", metadata.summary));
    }
    var meta = document.createElement("div");
    meta.className = "openclass-ridoc-card-meta";
    [metadata.fileName, metadata.sizeBytes ? formatBytes(metadata.sizeBytes) : ""]
      .concat(metadata.capabilities)
      .filter(Boolean)
      .forEach(function addPill(label) {
        meta.appendChild(cardText("openclass-ridoc-card-pill", label));
      });
    card.appendChild(meta);
    var action = document.createElement("a");
    action.className = "openclass-ridoc-card-action";
    action.href = metadata.destination.toString();
    action.setAttribute("download", metadata.fileName || "course.ridoc");
    action.textContent = "下载课程文件 →";
    card.appendChild(action);

    function openAttachment(event) {
      event.preventDefault();
      window.location.assign(metadata.destination.toString());
    }
    card.addEventListener("click", openAttachment);
    card.addEventListener("keydown", function openAttachmentFromKeyboard(event) {
      if (event.key === "Enter" || event.key === " ") {
        openAttachment(event);
      }
    });
  }

  function scan(root) {
    installReceiver();
    if (root instanceof HTMLAnchorElement) {
      enhanceRidocCard(root);
    }
    if (root instanceof Element || root instanceof Document) {
      root.querySelectorAll("blockquote a[href*='#" + RIDOC_MARKER + "']").forEach(enhanceRidocCard);
    }
  }

  var observer = new MutationObserver(function handleMutations(mutations) {
    mutations.forEach(function inspectMutation(mutation) {
      mutation.addedNodes.forEach(scan);
    });
    installReceiver();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function scanLoadedPage() {
      scan(document);
    }, { once: true });
  } else {
    scan(document);
  }
})();
