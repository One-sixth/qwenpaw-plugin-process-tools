/**
 * process-tools 前端伴生组件：及时自动刷新（0.3.2 · run 身份键版）。
 *
 * 两信号一与，字面实现「后端在活跃、而本页不知情 → 刷新」：
 *  1. 后端活动：每 3s 轮询 GET /api/process-tools/chat-status
 *     （task_tracker 纯内存查询本 chat 是否 running，附 run 身份键
 *     run_at=workspace 最近 run 启动时刻，同一 run 稳定、新 run 必变）。
 *  2. 前端知情：本页发送按钮是否处于 loading 态——
 *     class `qwenpaw-sender-actions-btn-loading-button` 从发出消息起
 *     覆盖整个 run（含等待吐字的思考空窗），是「页面正在展示生成」
 *     的精确 UI 信号（0.3.1 的 MutationObserver DOM 动静判据在等待
 *     吐字期误判活跃页，被用户实测否决）。
 *
 * 规则（每个 run 至多一刷，硬上限）：
 *  · running ∧ 本页非 loading ∧ 本 run 未刷过 → reload：SPA 冷启动
 *    进入 running 会话会原生 reconnect 接上进行中的 SSE（直播）。
 *  · run 结束（idle）且本 run 刷过但从没见 loading（reconnect 疑似
 *    未接管）→ 补一刷看结果，随后该 run 不再有任何刷新。
 *  · 活跃页（loading 在屏）永不刷新——正常对话零打扰。
 * 三个 run 级标记都存 sessionStorage（跨 reload 存活，同标签页视角），
 * 另有 15s 最小 reload 间隔兜底防意外风暴。只管理 /chat/<UUID> 页面。
 * 输入框草稿由宿主 localStorage 自动存取，刷新不丢。
 */
(function () {
  "use strict";
  if (window.__qptLiveRefreshInstalled) return; // SPA 重复加载防御
  window.__qptLiveRefreshInstalled = true;

  var INTERVAL_MS = 3000;
  var MIN_RELOAD_GAP_MS = 15000;
  var LOADING_CLS = "qwenpaw-sender-actions-btn-loading-button";
  var K = {
    reload: "qptRe...",
    busy: "qptBusyRun",
    fb: "qptFallbackRun",
    last: "qptLastReloadAt",
  };
  var TAG = "[process-tools/live-refresh]";
  var st = { chat: null, warned: false };

  function ss(k) {
    try { return sessionStorage.getItem(k) || ""; } catch (_) { return ""; }
  }
  function ssSet(k, v) {
    try { sessionStorage.setItem(k, v); } catch (_) {}
  }

  function chatIdFromUrl() {
    var m = (location.pathname || "").match(/^\/chat\/([0-9a-zA-Z-]+)/);
    return m ? m[1] : null;
  }

  function getApiUrl(path) {
    try {
      if (window.QwenPaw && window.QwenPaw.host && window.QwenPaw.host.getApiUrl) {
        return window.QwenPaw.host.getApiUrl(path);
      }
    } catch (_) {}
    return window.location.origin + "/api" + path;
  }

  function getApiToken() {
    try {
      if (window.QwenPaw && window.QwenPaw.host && window.QwenPaw.host.getApiToken) {
        return window.QwenPaw.host.getApiToken();
      }
    } catch (_) {}
    return "";
  }

  function pageShowsGenerating() {
    try { return !!document.querySelector("." + LOADING_CLS); } catch (_) { return false; }
  }

  function gapOk() {
    var last = Number(ss(K.last) || 0);
    return Date.now() - last > MIN_RELOAD_GAP_MS;
  }

  function doReload() {
    ssSet(K.last, String(Date.now()));
    location.reload();
  }

  async function probe(chatId) {
    // 注意：不能用 host.fetch —— 它内部会再拼 /api 前缀，与 getApiUrl()
    // 返回的完整 URL 冲突（session-tools 踩坑），故原生 fetch + 手动带头。
    var url = getApiUrl("/process-tools/chat-status?chat_id=" + encodeURIComponent(chatId));
    var headers = {};
    var token = getApiToken();
    if (token) headers["Authorization"] = "Bearer " + token;
    var host = window.QwenPaw && window.QwenPaw.host;
    if (host && typeof host.getSelectedAgentId === "function") {
      var aid = host.getSelectedAgentId();
      if (aid) headers["X-Agent-Id"] = aid;
    }
    var resp = await fetch(url, { headers: headers });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return await resp.json(); // {status, run_at}
  }

  async function tick() {
    try {
      var chat = chatIdFromUrl();
      if (!chat) return;
      if (st.chat !== chat) {
        // 切换会话：清 run 级标记（不同 chat 的 run 身份互不相干）
        st.chat = chat;
        ssSet(K.reload, ""); ssSet(K.busy, ""); ssSet(K.fb, "");
      }
      var s = await probe(chat);
      var running = s.status === "running";
      var busy = pageShowsGenerating();
      // run 身份键：优先 tracker 的 last_run_at；缺失退回时间桶（30s）
      var runKey = s.run_at ? String(Math.floor(s.run_at)) : ("t" + Math.floor(Date.now() / 30000));
      if (running && busy) ssSet(K.busy, runKey);       // 本页已在展示生成
      if (running && !busy && ss(K.reload) !== runKey && gapOk()) {
        ssSet(K.reload, runKey);
        doReload();                                // 直播接管一刷
        return;
      }
      if (!running && ss(K.reload) === runKey && runKey !== "" &&
          ss(K.busy) !== runKey && ss(K.fb) !== runKey && gapOk()) {
        ssSet(K.fb, runKey);
        doReload();                                // 落沿补看结果一刷
      }
    } catch (e) {
      if (!st.warned) {
        st.warned = true; // 只提醒一次，轮询继续（端点恢复后仍能刷新）
        console.warn(TAG, "chat-status 轮询失败（已静默重试）:", e);
      }
    }
  }

  // 标签页从后台回到前台时立刻查一次，不等下个周期
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) tick();
  });

  setInterval(tick, INTERVAL_MS);
  tick();
})();
