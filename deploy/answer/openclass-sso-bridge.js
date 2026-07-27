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
    if (!payload || !payload.data) {
      resumeOpenClassSession();
    }
  }).catch(function ignoreUnavailableAnswerSession() {});
})();
