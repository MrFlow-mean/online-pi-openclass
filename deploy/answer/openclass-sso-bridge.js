(function syncOpenClassSessionToAnswer() {
  var config = window.__OPENCLASS_COMMUNITY_BRIDGE__;
  if (!config || !config.entryUrl || window.location.pathname.indexOf("/users/auth-landing") !== -1) {
    return;
  }

  var entryUrl;
  try {
    entryUrl = new URL(config.entryUrl, window.location.href);
  } catch (_error) {
    return;
  }
  if (entryUrl.origin !== window.location.origin) {
    return;
  }

  var answerToken = window.localStorage.getItem("_a_ltk_");
  var attemptKey = "openclass.community.sso-attempted-at";
  var attemptCooldownMs = 15000;

  function openClassHeaders() {
    var openClassToken = window.localStorage.getItem("openclass.auth.token");
    return openClassToken ? { Authorization: "Bearer " + openClassToken } : {};
  }

  function resumeOpenClassSession() {
    var lastAttempt = Number(window.sessionStorage.getItem(attemptKey)) || 0;
    if (Date.now() - lastAttempt < attemptCooldownMs) {
      return;
    }
    window.fetch(new URL("/api/auth/me", entryUrl.origin), {
      credentials: "same-origin",
      headers: openClassHeaders(),
    }).then(function readOpenClassUser(response) {
      return response.ok ? response.json() : null;
    }).then(function enterCommunity(user) {
      if (!user || (user.role !== "user" && user.role !== "admin")) {
        return;
      }
      window.sessionStorage.setItem(attemptKey, String(Date.now()));
      window.location.replace(entryUrl.toString());
    }).catch(function ignoreUnavailableOpenClassSession() {});
  }

  if (!answerToken) {
    resumeOpenClassSession();
    return;
  }

  window.fetch("/answer/api/v1/user/info", {
    headers: { Authorization: answerToken },
  }).then(function readAnswerUser(response) {
    return response.ok ? response.json() : null;
  }).then(function reconcileAnswerUser(payload) {
    if (payload && payload.data) {
      config.currentAnswerUsername = payload.data.username || "";
      window.dispatchEvent(new Event("openclass:answer-user"));
      return;
    }
    resumeOpenClassSession();
  }).catch(function ignoreUnavailableAnswerSession() {});
})();

(function installExternalAvatarFallback() {
  var palette = ["#dce9e4", "#e8e1d4", "#dfe5ef", "#eadde3", "#e3e0ef"];

  function isExternalAvatar(image) {
    if (!image.classList.contains("rounded-circle")) {
      return false;
    }
    var source = image.getAttribute("src") || image.dataset.src || "";
    if (!source || source.indexOf("data:") === 0 || source.indexOf("blob:") === 0) {
      return false;
    }
    try {
      return new URL(source, window.location.href).origin !== window.location.origin;
    } catch (_error) {
      return false;
    }
  }

  function initials(label) {
    var characters = Array.from((label || "U").trim());
    return (characters.slice(0, 2).join("") || "U").toUpperCase();
  }

  function avatarDataUrl(label, requestedSize) {
    var size = Math.max(32, Number(requestedSize) || 96);
    var hash = Array.from(label || "U").reduce(function combine(value, character) {
      return ((value * 31) + character.codePointAt(0)) >>> 0;
    }, 0);
    var canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    var context = canvas.getContext("2d");
    if (!context) {
      return "";
    }
    context.fillStyle = palette[hash % palette.length];
    context.fillRect(0, 0, size, size);
    context.fillStyle = "#3f3b35";
    context.font = "600 " + Math.round(size * 0.36) + "px system-ui, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(initials(label), size / 2, size / 2 + 1);
    return canvas.toDataURL("image/png");
  }

  function userIdFromAvatar(image) {
    var link = image.closest("a[href]");
    if (link) {
      try {
        var linkMatch = new URL(link.href, window.location.href).pathname.match(/\/users\/([^/]+)\/?$/);
        if (linkMatch) {
          return decodeURIComponent(linkMatch[1]);
        }
      } catch (_error) {
        // Continue with page or logged-user context.
      }
    }
    var pageMatch = window.location.pathname.match(/\/users\/([^/]+)\/?$/);
    if (pageMatch && !image.closest("#header")) {
      return decodeURIComponent(pageMatch[1]);
    }
    var config = window.__OPENCLASS_COMMUNITY_BRIDGE__;
    if (image.closest("#header") && config && config.currentAnswerUsername) {
      return config.currentAnswerUsername;
    }
    return "";
  }

  function synchronizedAvatarUrl(image) {
    var config = window.__OPENCLASS_COMMUNITY_BRIDGE__;
    var userId = userIdFromAvatar(image);
    if (!config || !config.avatarBaseUrl || !userId) {
      return "";
    }
    try {
      var avatarUrl = new URL(config.avatarBaseUrl + encodeURIComponent(userId), window.location.href);
      return avatarUrl.origin === window.location.origin ? avatarUrl.toString() : "";
    } catch (_error) {
      return "";
    }
  }

  function replaceAvatar(image) {
    if (!(image instanceof HTMLImageElement)) {
      return;
    }
    var synchronizedAvatar = synchronizedAvatarUrl(image);
    if (image.dataset.openclassAvatarFallback === "true" && synchronizedAvatar) {
      image.dataset.openclassAvatarSynced = "true";
      image.removeAttribute("data-openclass-avatar-fallback");
      image.src = synchronizedAvatar;
      return;
    }
    if (!isExternalAvatar(image)) {
      return;
    }
    if (synchronizedAvatar) {
      image.dataset.openclassAvatarSynced = "true";
      image.removeAttribute("data-src");
      image.classList.remove("broken");
      image.src = synchronizedAvatar;
      return;
    }
    var fallback = avatarDataUrl(image.alt, image.getAttribute("width") || image.width);
    if (!fallback) {
      return;
    }
    image.dataset.openclassAvatarFallback = "true";
    image.removeAttribute("data-src");
    image.classList.remove("broken");
    image.src = fallback;
  }

  function scanAvatars(root) {
    if (root instanceof HTMLImageElement) {
      replaceAvatar(root);
    }
    if (root instanceof Element || root instanceof Document) {
      root.querySelectorAll("img.rounded-circle").forEach(replaceAvatar);
    }
  }

  var observer = new MutationObserver(function handleAvatarMutations(mutations) {
    mutations.forEach(function inspectMutation(mutation) {
      if (mutation.type === "attributes") {
        replaceAvatar(mutation.target);
        return;
      }
      mutation.addedNodes.forEach(scanAvatars);
    });
  });
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["src", "data-src"],
    childList: true,
    subtree: true,
  });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function scanLoadedAvatars() {
      scanAvatars(document);
    }, { once: true });
  } else {
    scanAvatars(document);
  }
  window.addEventListener("openclass:answer-user", function rescanCurrentUserAvatar() {
    scanAvatars(document);
  });
})();

(function installHistoryReferenceCards() {
  var config = window.__OPENCLASS_COMMUNITY_BRIDGE__;
  if (!config || !config.entryUrl) {
    return;
  }

  var openClassOrigin;
  try {
    openClassOrigin = new URL(config.entryUrl, window.location.href).origin;
  } catch (_error) {
    return;
  }

  var style = document.createElement("style");
  style.textContent = [
    "blockquote.openclass-history-reference{position:relative;margin:16px 0;padding:18px 20px 16px;border:1px solid #bae6fd;border-radius:14px;background:#f0f9ff;color:#0c4a6e;cursor:pointer;transition:border-color .15s ease,background .15s ease,transform .15s ease}",
    "blockquote.openclass-history-reference:before{content:'OpenClass · 历史节点引用';display:block;margin-bottom:7px;font-size:11px;font-weight:700;letter-spacing:.08em;color:#0369a1}",
    "blockquote.openclass-history-reference:hover{border-color:#38bdf8;background:#e0f2fe;transform:translateY(-1px)}",
    "blockquote.openclass-history-reference:focus-visible{outline:3px solid rgba(14,165,233,.3);outline-offset:2px}",
    "blockquote.openclass-history-reference p{margin:0}",
    "blockquote.openclass-history-reference a{display:block;color:#0c4a6e;font-weight:650;text-decoration:none}",
  ].join("");
  document.head.appendChild(style);

  function historyReferenceUrl(anchor) {
    try {
      var destination = new URL(anchor.href, window.location.href);
      if (
        destination.origin !== openClassOrigin
        || !/^\/courses\/shared\/lesson\/[^/]+$/.test(destination.pathname)
        || !destination.searchParams.get("history_node")
      ) {
        return null;
      }
      return destination;
    } catch (_error) {
      return null;
    }
  }

  function enhanceReference(anchor) {
    if (!(anchor instanceof HTMLAnchorElement)) {
      return;
    }
    var destination = historyReferenceUrl(anchor);
    var card = anchor.closest("blockquote");
    if (!destination || !card || card.dataset.openclassHistoryReference === "true") {
      return;
    }
    card.dataset.openclassHistoryReference = "true";
    card.classList.add("openclass-history-reference");
    card.setAttribute("role", "link");
    card.setAttribute("tabindex", "0");
    card.setAttribute("aria-label", "打开引用的课堂历史节点");
    anchor.setAttribute("tabindex", "-1");
    card.addEventListener("click", function openReference(event) {
      event.preventDefault();
      window.location.assign(destination.toString());
    });
    card.addEventListener("keydown", function openReferenceFromKeyboard(event) {
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      event.preventDefault();
      window.location.assign(destination.toString());
    });
  }

  function scanReferences(root) {
    if (root instanceof HTMLAnchorElement) {
      enhanceReference(root);
    }
    if (root instanceof Element || root instanceof Document) {
      root.querySelectorAll("blockquote a[href]").forEach(enhanceReference);
    }
  }

  var observer = new MutationObserver(function handleReferenceMutations(mutations) {
    mutations.forEach(function inspectMutation(mutation) {
      mutation.addedNodes.forEach(scanReferences);
    });
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function scanLoadedReferences() {
      scanReferences(document);
    }, { once: true });
  } else {
    scanReferences(document);
  }
})();
