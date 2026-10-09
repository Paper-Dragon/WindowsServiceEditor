(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  // 全局状态管理
  const state = {
    services: [],
    filtered: [],
    current: null,
    loading: false,
    busy: false,
    theme: localStorage.getItem("svc-theme") || "system",
    stateFilter: "all",
    typeFilter: "win32",
    focusIndex: -1,
    activeTab: "info",
    detail: null,

    // 竞态序列令牌与版本号 (Race Condition Prevention Tokens)
    listRequestId: 0,
    selectRequestId: 0,
    pollRequestId: 0,
    actionInFlight: false,

    // 事件日志
    logsRequestId: 0,
    logsData: [],
    logsLoading: false,

    // 注册方式：native | wrap
    addMode: "native",

    // 监控
    monitorConfig: null,
    monitorEvents: [],
    trayAgent: null,

    // 更新
    updateInfo: null,
    updatePolling: false,
    appVersion: "0.9.0",
    frozen: false,
  };

  let searchTimer = null;
  let refreshTimer = null;

  const el = {
    search: $("#search"),
    list: $("#service-list"),
    count: $("#count-label"),
    status: $("#status-text"),
    quickStat: $("#quick-stat"),
    adminBadge: $("#admin-badge"),
    quickActions: $("#quick-actions"),
    listLoading: $("#list-loading"),
    listEmptyHint: $("#list-empty-hint"),
    detailLoading: $("#detail-loading"),
    emptyState: $("#empty-state"),
    infoContent: $("#info-content"),
    infoName: $("#info-name"),
    infoRawName: $("#info-raw-name"),
    infoDisplaySub: $("#info-display-sub"),
    stateBadge: $("#state-badge"),
    infoStart: $("#info-start"),
    infoDisplay: $("#info-display"),
    infoDesc: $("#info-desc"),
    infoPath: $("#info-path"),
    infoAccount: $("#info-account"),
    infoWorkdir: $("#info-workdir"),
    infoFailure: $("#info-failure"),
    infoEnv: $("#info-env"),
    infoPid: $("#info-pid"),
    infoPriority: $("#info-priority"),
    infoPrioritySelect: $("#info-priority-select"),
    infoDeps: $("#info-deps"),
    regKeyPath: $("#reg-key-path"),
    editStart: $("#edit-start"),
    editDisplay: $("#edit-display"),
    editDesc: $("#edit-desc"),
    editDelayedAuto: $("#edit-delayed-auto"),
    editDeps: $("#edit-deps"),
    addName: $("#add-name"),
    logsServiceName: $("#logs-service-name"),
    logsCount: $("#logs-count"),
    logsLoading: $("#logs-loading"),
    logsEmpty: $("#logs-empty"),
    logsTableWrap: $("#logs-table-wrap"),
    logsBody: $("#logs-body"),
    logsLevelFilter: $("#logs-level-filter"),
    addDisplay: $("#add-display"),
    addDesc: $("#add-desc"),
    addStart: $("#add-start"),
    deleteDialog: $("#delete-dialog"),
    deleteTarget: $("#delete-target"),
    deleteForm: $("#delete-form"),
    toastHost: $("#toast-host"),
    busy: $("#busy"),
    busyText: $("#busy-text"),
  };

  function toast(message, level = "info") {
    const node = document.createElement("div");
    node.className = `toast-item ${level}`;
    node.textContent = message;
    el.toastHost.appendChild(node);
    setTimeout(() => {
      node.style.opacity = "0";
      node.style.transform = "translateY(-4px)";
      node.style.transition = "all 0.15s ease";
      setTimeout(() => node.remove(), 160);
    }, 2400);
  }

  function setStatus(text) {
    el.status.textContent = text || "就绪";
  }

  function setBusy(on, text = "处理中…") {
    state.busy = on;
    document.body.classList.toggle("is-busy", on);
    el.busy.classList.toggle("hidden", !on);
    el.busy.setAttribute("aria-hidden", on ? "false" : "true");
    el.busyText.textContent = text;
  }

  function stateTone(label) {
    if (label === "运行中") return "running";
    if (label === "已停止") return "stopped";
    if (["启动中", "停止中", "继续中"].includes(label)) return "pending";
    if (["已暂停", "暂停中"].includes(label)) return "paused";
    return "idle";
  }

  async function api(method, ...args) {
    if (!window.pywebview?.api?.[method]) {
      throw new Error("API 未就绪，请稍候重试");
    }
    return window.pywebview.api[method](...args);
  }

  function waitApiReady() {
    return new Promise((resolve) => {
      if (window.pywebview?.api) {
        resolve();
        return;
      }
      window.addEventListener("pywebviewready", () => resolve(), { once: true });
      const t = setInterval(() => {
        if (window.pywebview?.api) {
          clearInterval(t);
          resolve();
        }
      }, 40);
      setTimeout(() => clearInterval(t), 8000);
    });
  }

  function applyTheme(theme) {
    state.theme = theme;
    localStorage.setItem("svc-theme", theme);
    let resolved = theme;
    if (theme === "system") {
      resolved = window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
    }
    document.documentElement.setAttribute("data-theme", resolved);
    $$(".theme-toggle button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.theme === theme);
    });
  }

  function switchTab(name) {
    state.activeTab = name;
    $$(".pill-tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
    if (name === "logs" && state.current) {
      loadEventLogs(state.current);
    }
    if (name === "monitor") {
      loadMonitorPanel();
    }
  }

  function showEmpty(show) {
    el.emptyState.classList.toggle("hidden", !show);
    el.infoContent.classList.toggle("hidden", show);
    el.quickActions.classList.toggle("hidden", show);
    if (show) el.detailLoading.classList.add("hidden");
  }

  function clearSelection() {
    state.current = null;
    state.focusIndex = -1;
    el.detailLoading.classList.add("hidden");
    showEmpty(true);
    renderList();
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function updateServiceInList(info) {
    const idx = state.services.findIndex((s) => s.name === info.name);
    if (idx >= 0) {
      state.services[idx] = {
        ...state.services[idx],
        state: info.state,
        display_name: info.display_name || state.services[idx].display_name,
        start: info.start ?? state.services[idx].start,
        start_label: info.start_label || state.services[idx].start_label,
      };
    }
  }

  function renderList() {
    const kw = el.search.value.trim().toLowerCase();
    state.filtered = state.services.filter((s) => {
      if (!kw) return true;
      return (
        s.name.toLowerCase().includes(kw) ||
        (s.display_name || "").toLowerCase().includes(kw)
      );
    });

    el.list.innerHTML = "";
    const frag = document.createDocumentFragment();

    state.filtered.forEach((svc, i) => {
      const item = document.createElement("div");
      const selected = state.current === svc.name;
      item.className = "svc-item" + (selected ? " selected" : "");
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", selected ? "true" : "false");
      item.dataset.name = svc.name;
      item.dataset.index = String(i);
      item.tabIndex = selected ? 0 : -1;

      const tone = stateTone(svc.state);
      const friendly = svc.display_name && svc.display_name !== svc.name
        ? escapeHtml(svc.display_name)
        : "";

      item.innerHTML = `
        <div class="svc-item-main">
          <span class="status-dot status-${tone}"></span>
          <div class="svc-titles">
            <span class="svc-name">${escapeHtml(svc.name)}</span>
            ${friendly ? `<span class="svc-desc-line">${friendly}</span>` : ""}
          </div>
        </div>
        <span class="svc-state-label state-label-${tone}">${escapeHtml(svc.state)}</span>
      `;

      item.addEventListener("click", () => selectService(svc.name));
      frag.appendChild(item);
    });

    el.list.appendChild(frag);
    el.count.textContent = `共 ${state.filtered.length} 个服务`;
    el.quickStat.textContent = `${state.filtered.length} 个服务`;

    // 有数据但过滤后为空 → 显示无匹配提示
    const noMatch = state.services.length > 0 && state.filtered.length === 0;
    el.listEmptyHint.classList.toggle("hidden", !noMatch);

    if (state.current) {
      const esc = window.CSS?.escape
        ? CSS.escape(state.current)
        : state.current.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      const node = el.list.querySelector(`.svc-item[data-name="${esc}"]`);
      if (node) {
        state.focusIndex = Number(node.dataset.index);
        node.scrollIntoView({ block: "nearest" });
      }
    }
  }

  function failOptions(prefix, index, label) {
    return `
      <label class="form-label" for="${prefix}-fail-${index}">${label}</label>
      <select id="${prefix}-fail-${index}" class="form-input">
        <option value="none">无操作</option>
        <option value="restart">重启服务</option>
        <option value="reboot">重启计算机</option>
      </select>
      <input id="${prefix}-fail-delay-${index}" type="number" min="0" max="604800" step="1" class="form-input" value="0" />
    `;
  }

  function runtimeMarkup(prefix, passwordHint) {
    return `
      <fieldset class="form-section">
        <legend class="section-title">执行</legend>
        <div class="form-cell">
          <label class="form-label" for="${prefix}-exe">可执行文件${prefix === "add" ? " *" : ""}</label>
          <div class="input-with-btn">
            <input id="${prefix}-exe" type="text" class="form-input mono" spellcheck="false" />
            <button type="button" class="btn btn-browse" data-browse="${prefix}-exe">浏览…</button>
          </div>
        </div>
        <div class="form-cell">
          <label class="form-label" for="${prefix}-args">参数</label>
          <input id="${prefix}-args" type="text" class="form-input mono" spellcheck="false" />
        </div>
        <div class="form-cell">
          <label class="form-label" for="${prefix}-workdir">工作目录 (AppDirectory)</label>
          <div class="input-with-btn">
            <input id="${prefix}-workdir" type="text" class="form-input mono" spellcheck="false" placeholder="留空表示不设置" />
            <button type="button" class="btn btn-browse" data-browse-dir="${prefix}-workdir">浏览…</button>
          </div>
        </div>
      </fieldset>
      <fieldset class="form-section" id="${prefix}-account-section">
        <legend class="section-title">登录账户</legend>
        <p class="section-note hidden" id="${prefix}-account-note">驱动服务不能修改登录账户。</p>
        <div class="form-cell">
          <label class="form-label" for="${prefix}-account">运行身份</label>
          <select id="${prefix}-account" class="form-input">
            <option value="local">Local System</option>
            <option value="service">Local Service</option>
            <option value="network">Network Service</option>
            <option value="custom">自定义账户</option>
          </select>
        </div>
        <div id="${prefix}-account-custom" class="account-custom hidden">
          <div class="form-cell">
            <label class="form-label" for="${prefix}-user">用户名</label>
            <input id="${prefix}-user" type="text" class="form-input mono" spellcheck="false" placeholder="DOMAIN\\user 或 .\\user" autocomplete="off" />
          </div>
          <div class="form-cell">
            <label class="form-label" for="${prefix}-password">密码</label>
            <input id="${prefix}-password" type="password" class="form-input" spellcheck="false" placeholder="${passwordHint}" autocomplete="new-password" />
          </div>
          <label class="check-line" for="${prefix}-grant">
            <input id="${prefix}-grant" type="checkbox" />
            授予“作为服务登录”权限
          </label>
        </div>
      </fieldset>
      <fieldset class="form-section" id="${prefix}-failure">
        <legend class="section-title">失败恢复</legend>
        <p class="section-note hidden" id="${prefix}-failure-note">无法读取失败恢复，本次保存不会改动它。</p>
        <div class="failure-head"><span></span><span>操作</span><span>延迟（秒）</span></div>
        <div class="failure-row">${failOptions(prefix, 1, "第一次")}</div>
        <div class="failure-row">${failOptions(prefix, 2, "第二次")}</div>
        <div class="failure-row">${failOptions(prefix, 3, "后续")}</div>
        <div class="reset-row">
          <label class="form-label" for="${prefix}-reset">重置周期（秒）</label>
          <input id="${prefix}-reset" type="number" min="0" step="1" class="form-input" value="86400" />
          <label class="check-line" for="${prefix}-reset-never">
            <input id="${prefix}-reset-never" type="checkbox" checked />
            不重置
          </label>
        </div>
      </fieldset>
      <fieldset class="form-section">
        <legend class="section-title">环境变量</legend>
        <div id="${prefix}-env" class="env-list"></div>
        <div class="env-add">
          <input id="${prefix}-env-name" type="text" class="form-input mono" placeholder="变量名" spellcheck="false" />
          <input id="${prefix}-env-value" type="text" class="form-input mono" placeholder="变量值" spellcheck="false" />
          <button type="button" class="btn btn-browse" data-env-add="${prefix}">添加</button>
        </div>
      </fieldset>
    `;
  }

  function mountRuntime() {
    const editHost = $("#edit-runtime");
    const addHost = $("#add-runtime");
    if (!editHost || !addHost || editHost.childElementCount) return;
    editHost.innerHTML = runtimeMarkup("edit", "留空则不修改已有密码");
    addHost.innerHTML = runtimeMarkup("add", "自定义账户时必填");
  }

  function syncAccount(prefix) {
    const custom = $(`#${prefix}-account`).value === "custom";
    $(`#${prefix}-account-custom`).classList.toggle("hidden", !custom);
  }

  function syncReset(prefix) {
    $(`#${prefix}-reset`).disabled = $(`#${prefix}-reset-never`).checked;
  }

  function renderEnv(prefix, items) {
    const host = $(`#${prefix}-env`);
    host.innerHTML = "";
    if (!items.length) {
      host.innerHTML = `<div class="env-empty">未设置环境变量</div>`;
      return;
    }
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "env-row";
      row.innerHTML = `
        <input class="form-input mono" spellcheck="false" value="${escapeHtml(item.name)}" />
        <input class="form-input mono" spellcheck="false" value="${escapeHtml(item.value)}" />
        <button type="button" class="btn btn-browse" data-env-remove="1">移除</button>
      `;
      host.appendChild(row);
    });
  }

  function readEnv(prefix) {
    const items = $$(".env-row", $(`#${prefix}-env`)).map((row) => {
      const inputs = row.querySelectorAll("input");
      return { name: inputs[0].value, value: inputs[1].value };
    });
    const pending = $(`#${prefix}-env-name`).value.trim();
    if (pending) items.push({ name: pending, value: $(`#${prefix}-env-value`).value });
    return items;
  }

  function addEnv(prefix) {
    const name = $(`#${prefix}-env-name`).value.trim();
    const value = $(`#${prefix}-env-value`).value;
    if (!name) {
      toast("变量名不能为空", "warning");
      return;
    }
    if (name.includes("=")) {
      toast("变量名不能包含等号", "warning");
      return;
    }
    const existing = $$(".env-row", $(`#${prefix}-env`)).map((row) => {
      const inputs = row.querySelectorAll("input");
      return { name: inputs[0].value, value: inputs[1].value };
    });
    if (existing.some((item) => item.name.toLowerCase() === name.toLowerCase())) {
      toast("变量名已存在", "warning");
      return;
    }
    existing.push({ name, value });
    renderEnv(prefix, existing);
    $(`#${prefix}-env-name`).value = "";
    $(`#${prefix}-env-value`).value = "";
    $(`#${prefix}-env-name`).focus();
  }

  function fillFailure(prefix, failure) {
    const actions = failure?.actions || [];
    for (let i = 1; i <= 3; i += 1) {
      const action = actions[i - 1] || { type: "none", delay_sec: 0 };
      $(`#${prefix}-fail-${i}`).value = ["none", "restart", "reboot"].includes(action.type) ? action.type : "none";
      $(`#${prefix}-fail-delay-${i}`).value = String(action.delay_sec || 0);
    }
    $(`#${prefix}-reset-never`).checked = !!failure?.never_reset;
    $(`#${prefix}-reset`).value = String(failure?.reset_seconds ?? 86400);
    syncReset(prefix);
  }

  function emptyFailure() {
    return {
      never_reset: true,
      reset_seconds: 86400,
      actions: [
        { type: "none", delay_sec: 0 },
        { type: "none", delay_sec: 0 },
        { type: "none", delay_sec: 0 },
      ],
    };
  }

  function resetAddForm() {
    el.addName.value = "";
    el.addDisplay.value = "";
    el.addDesc.value = "";
    el.addStart.value = "2";
    $("#add-delayed-auto").checked = false;
    $("#add-deps").value = "";
    $("#add-exe").value = "";
    $("#add-args").value = "";
    $("#add-workdir").value = "";
    $("#add-account").value = "local";
    $("#add-user").value = "";
    $("#add-password").value = "";
    $("#add-grant").checked = false;
    syncAccount("add");
    fillFailure("add", emptyFailure());
    renderEnv("add", []);
  }

  function autofillFromExe(path) {
    const base = path.split(/[/\\]/).pop() || "";
    const stem = base.replace(/\.[^.]+$/, "") || base;
    if (!el.addName.value.trim()) el.addName.value = stem;
    if (!el.addDisplay.value.trim()) el.addDisplay.value = stem;
    if (!$("#add-workdir").value.trim()) {
      const idx = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
      if (idx > 0) $("#add-workdir").value = path.slice(0, idx);
    }
  }

  function collectPayload(prefix) {
    const actions = [1, 2, 3].map((index) => ({
      type: $(`#${prefix}-fail-${index}`).value,
      delay_sec: Number($(`#${prefix}-fail-delay-${index}`).value || 0),
    }));
    const delayedAutoEl = $(`#${prefix}-delayed-auto`);
    const depsEl = $(`#${prefix}-deps`);
    const payload = {
      start: Number($(`#${prefix}-start`).value),
      delayed_auto: delayedAutoEl ? delayedAutoEl.checked : false,
      dependencies: depsEl ? depsEl.value.split(",").map((s) => s.trim()).filter(Boolean) : [],
      display_name: $(`#${prefix}-display`).value,
      description: $(`#${prefix}-desc`).value,
      executable: $(`#${prefix}-exe`).value,
      arguments: $(`#${prefix}-args`).value,
      working_directory: $(`#${prefix}-workdir`).value,
      account_type: $(`#${prefix}-account`).value,
      username: $(`#${prefix}-user`).value,
      password: $(`#${prefix}-password`).value,
      grant_logon: $(`#${prefix}-grant`).checked,
      environments: readEnv(prefix),
      failure: {
        never_reset: $(`#${prefix}-reset-never`).checked,
        reset_seconds: Number($(`#${prefix}-reset`).value || 0),
        actions,
        extra_actions: prefix === "edit" ? state.detail?.failure?.extra_actions || [] : [],
      },
    };
    if (prefix === "add") {
      payload.name = el.addName.value;
      payload.apply_account = true;
      payload.apply_failure = true;
    } else {
      const driver = state.detail?.kind === "driver";
      payload.apply_account = !driver;
      payload.apply_failure = !driver && state.detail?.failure?.known !== false;
    }
    return payload;
  }

  function accountText(info) {
    if (info.account_type === "custom") return info.account_name || "自定义账户";
    const map = {
      local: "Local System",
      service: "Local Service",
      network: "Network Service",
    };
    return map[info.account_type] || info.account_name || "Local System";
  }

  function failureText(failure) {
    if (!failure?.known) return "无法读取";
    const labels = { none: "无操作", restart: "重启服务", reboot: "重启计算机" };
    const parts = (failure.actions || []).slice(0, 3).map((action) => {
      const label = labels[action.type] || "无操作";
      if (!action.type || action.type === "none") return label;
      return `${label}（${action.delay_sec || 0} 秒）`;
    });
    while (parts.length < 3) parts.push("无操作");
    const reset = failure.never_reset ? "不重置计数" : `${failure.reset_seconds || 0} 秒后重置`;
    return `${parts.join(" / ")} · ${reset}`;
  }

  function editingNow() {
    if (state.activeTab === "edit" || state.activeTab === "add") return true;
    const active = document.activeElement;
    return !!(active && active.closest && active.closest("#panel-edit, #panel-add"));
  }

  function fillEditForm(info) {
    if ([0, 1, 2, 3, 4].includes(info.start)) el.editStart.value = String(info.start);
    el.editDisplay.value = info.display_name || "";
    el.editDesc.value = info.description || "";
    $("#edit-exe").value = info.executable || "";
    $("#edit-args").value = info.arguments || "";
    $("#edit-workdir").value = info.working_directory || "";
    $("#edit-account").value = info.account_type || "local";
    $("#edit-user").value = info.account_type === "custom" ? info.account_name || "" : "";
    $("#edit-password").value = "";
    $("#edit-grant").checked = false;
    el.editDelayedAuto.checked = !!info.delayed_auto;
    el.editDeps.value = (info.dependencies || []).join(", ");
    syncAccount("edit");
    fillFailure("edit", info.failure);
    renderEnv("edit", info.environments || []);
    const driver = info.kind === "driver";
    $("#edit-account-section").disabled = driver;
    $("#edit-failure").disabled = driver || info.failure?.known === false;
    $("#edit-account-note").classList.toggle("hidden", !driver);
    const failureNote = $("#edit-failure-note");
    if (driver) {
      failureNote.textContent = "驱动服务不能修改失败恢复。";
      failureNote.classList.remove("hidden");
    } else if (info.failure?.known === false) {
      failureNote.textContent = "无法读取失败恢复，本次保存不会改动它。";
      failureNote.classList.remove("hidden");
    } else {
      failureNote.classList.add("hidden");
    }
  }

  function fillInfo(info, isBackgroundRefresh = false) {
    if (!isBackgroundRefresh || !editingNow()) state.detail = info;
    showEmpty(false);
    el.infoName.textContent = info.name || "—";
    el.infoRawName.textContent = info.name || "—";
    el.infoDisplaySub.textContent = info.display_name || "无独立显示名称";
    el.regKeyPath.textContent = `HKLM\\SYSTEM\\CurrentControlSet\\Services\\${info.name || ""}`;

    const tone = stateTone(info.state);
    el.stateBadge.className = `badge-status ${tone}`;
    el.stateBadge.innerHTML = `<span class="status-pip"></span><span class="state-text">${escapeHtml(info.state || "—")}</span>`;

    el.infoStart.textContent =
      info.start >= 0 ? `${info.start_label} (DWORD: ${info.start})` : "—";
    el.infoDisplay.textContent = info.display_name || "—";
    el.infoDesc.textContent = info.description || "— (未提供描述)";
    el.infoPath.textContent = info.image_path || "—";
    if (info.is_wrapped && info.wrapper?.application) {
      el.infoPath.textContent = `${info.wrapper.application}${info.wrapper.arguments ? " " + info.wrapper.arguments : ""}`;
    }
    el.infoAccount.textContent = accountText(info);
    el.infoWorkdir.textContent = info.working_directory || "—";
    el.infoFailure.textContent = failureText(info.failure);
    el.infoEnv.textContent = info.environments?.length
      ? info.environments.map((item) => item.name).join(", ")
      : "—";
    el.infoPid.textContent = info.pid ? String(info.pid) : "—";
    // 进程优先级：运行中时显示下拉选择器，否则显示文本
    if (info.pid && info.priority) {
      el.infoPriority.textContent = "";
      el.infoPrioritySelect.classList.remove("hidden");
      el.infoPrioritySelect.value = info.priority;
    } else {
      el.infoPriority.textContent = info.pid ? (info.priority_label || "—") : "—";
      el.infoPrioritySelect.classList.add("hidden");
    }
    el.infoDeps.textContent = info.dependencies?.length
      ? info.dependencies.join(", ")
      : "—";

    const wrapRow = $("#info-wrapper-row");
    const wrapVal = $("#info-wrapper");
    if (info.is_wrapped && info.wrapper) {
      wrapRow?.classList.remove("hidden");
      const w = info.wrapper;
      wrapVal.textContent = `${w.application || "—"}${w.arguments ? " " + w.arguments : ""} · 退出后 ${w.on_exit || "restart"}`;
    } else {
      wrapRow?.classList.add("hidden");
      if (wrapVal) wrapVal.textContent = "—";
    }

    if (isBackgroundRefresh && editingNow()) return;
    fillEditForm(info);
  }

  // 加载服务列表（带版本序号令牌 requestId，杜绝慢请求覆盖快请求）
  async function loadServices(keepSelection = true) {
    const thisRequestId = ++state.listRequestId;
    state.loading = true;
    $("#btn-refresh")?.classList.add("spinning");
    setStatus("正在枚举 Windows 服务...");

    // 列表为空时显示加载占位
    if (!state.services.length) {
      el.listLoading.classList.remove("hidden");
      el.list.classList.add("hidden");
    }

    try {
      const kw = el.search.value.trim();
      const res = await api(
        "list_services",
        kw,
        state.stateFilter,
        state.typeFilter
      );

      // 竞态判断：如果期间发起了更新的列表请求，直接丢弃本过期响应
      if (thisRequestId !== state.listRequestId) {
        return;
      }

      if (!res.ok) throw new Error(res.message);
      state.services = res.data.services || [];
      renderList();
      setStatus(`已枚举 ${state.services.length} 个服务`);

      if (keepSelection && state.current) {
        const still = state.services.find((s) => s.name === state.current);
        if (still) await selectService(state.current, false, false);
        else clearSelection();
      } else if (!state.current) {
        showEmpty(true);
      }
    } catch (err) {
      if (thisRequestId === state.listRequestId) {
        toast(err.message || String(err), "error");
        setStatus("枚举失败");
      }
    } finally {
      if (thisRequestId === state.listRequestId) {
        state.loading = false;
        $("#btn-refresh")?.classList.remove("spinning");
        el.listLoading.classList.add("hidden");
        el.list.classList.remove("hidden");
      }
    }
  }

  function scheduleLoad() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadServices(true), 150);
  }

  // 选择服务（带版本序号令牌，快速连选时杜绝后点先回、先点后回导致展示错乱）
  // resetTab: 仅在用户主动在列表点击/切选时切换到概览 tab，刷新列表保持选择时不重置当前 tab
  async function selectService(name, updateStatus = true, resetTab = true) {
    if (!name) return;
    const thisSelectId = ++state.selectRequestId;
    state.current = name;
    renderList();

    // 首次选择或切换到不同服务时，显示详情加载占位
    const switching = !state.detail || state.detail.name !== name;
    if (switching && resetTab) {
      el.emptyState.classList.add("hidden");
      el.infoContent.classList.add("hidden");
      el.detailLoading.classList.remove("hidden");
    }

    try {
      const res = await api("get_service", name);
      if (thisSelectId !== state.selectRequestId) {
        return;
      }
      if (!res.ok) throw new Error(res.message);
      el.detailLoading.classList.add("hidden");
      fillInfo(res.data, !resetTab);
      updateServiceInList(res.data);
      renderList();
      if (updateStatus) {
        setStatus(`已就绪：${name}`);
        if (resetTab) {
          switchTab("info");
        }
      }
    } catch (err) {
      if (thisSelectId === state.selectRequestId) {
        el.detailLoading.classList.add("hidden");
        showEmpty(true);
        toast(err.message || String(err), "error");
      }
    }
  }

  async function withBusy(text, fn) {
    if (state.busy || state.actionInFlight) return;
    state.actionInFlight = true;
    setBusy(true, text);
    try {
      await fn();
    } finally {
      state.actionInFlight = false;
      setBusy(false);
    }
  }

  async function control(action) {
    if (!state.current) {
      toast("请先选定一个服务条目", "warning");
      return;
    }
    const targetService = state.current;
    const labels = {
      start: "启动",
      stop: "停止",
      restart: "重启",
      pause: "暂停",
      continue: "继续",
    };
    await withBusy(`正在${labels[action]} ${targetService}…`, async () => {
      setStatus(`正在${labels[action]}服务 ${targetService}…`);
      try {
        const res = await api("control_service", targetService, action);
        if (!res.ok) {
          toast(res.message, "error");
          setStatus("操作未成功完成");
          return;
        }
        toast(res.message || "指令下发成功", "success");
        // 确保仍旧处于当前服务上下文才更新面板
        if (state.current === targetService) {
          fillInfo(res.data, false);
        }
        updateServiceInList(res.data);
        renderList();
        setStatus(res.message || "就绪");
      } catch (err) {
        toast(err.message || String(err), "error");
        setStatus("执行异常");
      }
    });
  }

  async function saveConfig() {
    if (!state.current) {
      toast("请先选定一个服务条目", "warning");
      return;
    }
    const targetService = state.current;
    await withBusy("写入服务配置…", async () => {
      try {
        const res = await api("save_config", targetService, collectPayload("edit"));
        if (!res.ok) {
          toast(res.message, "error");
          return;
        }
        toast(res.message, "success");
        if (state.current === targetService) {
          fillInfo(res.data, false);
        }
        updateServiceInList(res.data);
        renderList();
        setStatus(res.message);
      } catch (err) {
        toast(err.message || String(err), "error");
      }
    });
  }

  async function addService() {
    if (state.addMode === "wrap") {
      await addWrappedService();
      return;
    }
    await withBusy("注册新系统服务…", async () => {
      try {
        const created = el.addName.value.trim();
        const res = await api("add_service", collectPayload("add"));
        if (!res.ok) {
          toast(res.message, "error");
          return;
        }
        toast(res.message, "success");
        resetAddForm();
        await loadServices(false);
        if (created) {
          state.current = created;
          await selectService(created);
        }
      } catch (err) {
        toast(err.message || String(err), "error");
      }
    });
  }

  async function addWrappedService() {
    await withBusy("将程序注册为 Windows 服务…", async () => {
      try {
        const created = $("#wrap-name").value.trim();
        const payload = {
          name: created,
          display_name: $("#wrap-display").value.trim(),
          description: $("#wrap-desc").value.trim(),
          start: Number($("#wrap-start").value || 2),
          delayed_auto: $("#wrap-delayed-auto").checked,
          application: $("#wrap-exe").value.trim(),
          arguments: $("#wrap-args").value,
          working_directory: $("#wrap-workdir").value.trim(),
          on_exit: $("#wrap-on-exit").value,
          restart_delay_ms: Number($("#wrap-restart-delay").value || 0),
          max_restarts: Number($("#wrap-max-restarts").value || 5),
          throttle_seconds: Number($("#wrap-throttle").value || 60),
        };
        const res = await api("add_wrapped_service", payload);
        if (!res.ok) {
          toast(res.message, "error");
          return;
        }
        toast(res.message, "success");
        resetWrapForm();
        await loadServices(false);
        if (created) {
          state.current = created;
          await selectService(created);
          switchTab("info");
        }
      } catch (err) {
        toast(err.message || String(err), "error");
      }
    });
  }

  function resetWrapForm() {
    $("#wrap-name").value = "";
    $("#wrap-display").value = "";
    $("#wrap-desc").value = "";
    $("#wrap-start").value = "2";
    $("#wrap-exe").value = "";
    $("#wrap-args").value = "";
    $("#wrap-workdir").value = "";
    $("#wrap-on-exit").value = "restart";
    $("#wrap-restart-delay").value = "3000";
    $("#wrap-max-restarts").value = "5";
    $("#wrap-throttle").value = "60";
    $("#wrap-delayed-auto").checked = false;
  }

  function setAddMode(mode) {
    state.addMode = mode === "wrap" ? "wrap" : "native";
    $$("[data-add-mode]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.addMode === state.addMode);
    });
    $("#add-native-block")?.classList.toggle("hidden", state.addMode !== "native");
    $("#add-wrap-block")?.classList.toggle("hidden", state.addMode !== "wrap");
    const hint = $("#add-mode-hint");
    const tip = $("#add-form-tip");
    const btn = $("#btn-add");
    if (state.addMode === "wrap") {
      if (hint) hint.textContent = "把任意程序包装成服务（崩溃可自动重启）";
      if (tip) tip.textContent = "宿主由本程序提供。目标程序无需本身支持服务协议。日志位于 %ProgramData%\\WindowsServiceEditor\\logs\\。";
      if (btn) btn.textContent = "创建程序服务";
    } else {
      if (hint) hint.textContent = "注册已符合服务规范的可执行文件";
      if (tip) tip.textContent = "选择可执行文件后，会在服务名和显示名为空时用文件名自动填写。工作目录默认取该文件所在目录，可清空。";
      if (btn) btn.textContent = "注册服务";
    }
  }

  function autofillWrapFromExe(path) {
    const base = path.split(/[/\\]/).pop() || "";
    const stem = base.replace(/\.[^.]+$/, "") || base;
    if (!$("#wrap-name").value.trim()) $("#wrap-name").value = stem;
    if (!$("#wrap-display").value.trim()) $("#wrap-display").value = stem;
    if (!$("#wrap-workdir").value.trim()) {
      const idx = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
      if (idx > 0) $("#wrap-workdir").value = path.slice(0, idx);
    }
  }

  function openDeleteDialog() {
    if (!state.current) {
      toast("请先选定一个服务条目", "warning");
      return;
    }
    el.deleteTarget.textContent = state.current;
    el.deleteDialog.showModal();
  }

  async function confirmDelete() {
    if (!state.current) return;
    const targetService = state.current;
    await withBusy("正在从 SCM 注销服务…", async () => {
      try {
        const res = await api("delete_service", targetService);
        if (!res.ok) {
          toast(res.message, "error");
          return;
        }
        toast(res.message, "success");
        clearSelection();
        await loadServices(false);
      } catch (err) {
        toast(err.message || String(err), "error");
      }
    });
  }

  async function browseFor(inputId) {
    try {
      const res = await api("pick_file");
      if (!res.ok) {
        toast(res.message, "error");
        return;
      }
      if (res.data?.path) {
        $(`#${inputId}`).value = res.data.path;
        if (inputId === "add-exe") autofillFromExe(res.data.path);
        if (inputId === "wrap-exe") autofillWrapFromExe(res.data.path);
      }
    } catch (err) {
      toast(err.message || String(err), "error");
    }
  }

  async function browseDir(inputId) {
    try {
      const res = await api("pick_folder");
      if (!res.ok) {
        toast(res.message, "error");
        return;
      }
      if (res.data?.path) $(`#${inputId}`).value = res.data.path;
    } catch (err) {
      toast(err.message || String(err), "error");
    }
  }

  // ---- 事件日志 ----

  async function loadEventLogs(name) {
    if (!name || state.logsLoading) return;
    const thisId = ++state.logsRequestId;
    state.logsLoading = true;

    el.logsServiceName.textContent = name;
    el.logsLoading.classList.remove("hidden");
    el.logsEmpty.classList.add("hidden");
    el.logsTableWrap.classList.add("hidden");

    try {
      const res = await api("get_event_logs", name, 200);
      if (thisId !== state.logsRequestId) return;
      if (!res.ok) throw new Error(res.message);
      state.logsData = res.data.logs || [];
      renderLogs();
    } catch (err) {
      if (thisId === state.logsRequestId) {
        toast(err.message || String(err), "error");
        el.logsEmpty.classList.remove("hidden");
      }
    } finally {
      if (thisId === state.logsRequestId) {
        state.logsLoading = false;
        el.logsLoading.classList.add("hidden");
      }
    }
  }

  function renderLogs() {
    const filter = el.logsLevelFilter.value;
    let logs = state.logsData;
    if (filter !== "all") {
      const filterMap = {
        error: ["错误", "严重"],
        warning: ["警告"],
        info: ["信息", "详细"],
      };
      const allowed = filterMap[filter] || [];
      logs = logs.filter((e) => allowed.includes(e.level));
    }

    el.logsBody.innerHTML = "";
    if (!logs.length) {
      el.logsEmpty.classList.remove("hidden");
      el.logsTableWrap.classList.add("hidden");
      el.logsCount.textContent = "0 条记录";
      return;
    }

    el.logsEmpty.classList.add("hidden");
    el.logsTableWrap.classList.remove("hidden");
    el.logsCount.textContent = `${logs.length} 条记录`;

    const frag = document.createDocumentFragment();
    const levelClass = {
      "严重": "log-level-critical",
      "错误": "log-level-error",
      "警告": "log-level-warning",
      "信息": "log-level-info",
      "详细": "log-level-verbose",
    };

    for (const entry of logs) {
      const tr = document.createElement("tr");
      const cls = levelClass[entry.level] || "log-level-info";
      tr.innerHTML = `
        <td class="cell-time">${escapeHtml(entry.time)}</td>
        <td><span class="log-level ${cls}">${escapeHtml(entry.level)}</span></td>
        <td class="cell-eid">${entry.event_id}</td>
        <td class="cell-source" title="${escapeHtml(entry.source)}">${escapeHtml(entry.source)}</td>
        <td class="cell-msg">${escapeHtml(entry.message)}</td>
      `;
      frag.appendChild(tr);
    }
    el.logsBody.appendChild(frag);
  }

  async function copyPath() {
    const text = el.infoPath.textContent || "";
    if (!text || text === "—") {
      toast("无可复制路径", "warning");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      toast("可执行路径已复制", "success");
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      toast("可执行路径已复制", "success");
    }
  }

  function moveSelection(delta) {
    if (!state.filtered.length) return;
    let idx = state.focusIndex;
    if (idx < 0) {
      idx = state.filtered.findIndex((s) => s.name === state.current);
    }
    if (idx < 0) idx = 0;
    else idx = Math.max(0, Math.min(state.filtered.length - 1, idx + delta));
    state.focusIndex = idx;
    selectService(state.filtered[idx].name);
  }

  function syncFilterChips() {
    $$("[data-state-filter]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.stateFilter === state.stateFilter);
    });
    $$("[data-type-filter]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.typeFilter === state.typeFilter);
    });
  }

  // ---- 监控告警 ----

  function readMonitorForm() {
    const watched = [];
    $$("#mon-watch-list .mon-watch-item").forEach((row) => {
      const name = row.dataset.name;
      if (!name) return;
      watched.push({
        name,
        auto_restart: !!row.querySelector(".mon-auto-restart")?.checked,
        alert_on_stop: !!row.querySelector(".mon-alert-stop")?.checked,
        alert_on_start: !!row.querySelector(".mon-alert-start")?.checked,
      });
    });
    return {
      enabled: $("#mon-enabled").checked,
      interval_sec: Number($("#mon-interval").value || 5),
      system_toast: $("#mon-toast").checked,
      watched,
    };
  }

  function fillMonitorForm(config) {
    state.monitorConfig = config;
    $("#mon-enabled").checked = !!config.enabled;
    $("#mon-interval").value = String(config.interval_sec || 5);
    $("#mon-toast").checked = config.system_toast !== false;
    renderWatchList(config.watched || []);
  }

  function renderWatchList(items) {
    const host = $("#mon-watch-list");
    const empty = $("#mon-watch-empty");
    const count = $("#mon-watch-count");
    host.innerHTML = "";
    if (!items.length) {
      host.classList.add("hidden");
      empty.classList.remove("hidden");
      count.textContent = "0 项";
      return;
    }
    empty.classList.add("hidden");
    host.classList.remove("hidden");
    count.textContent = `${items.length} 项`;
    const frag = document.createDocumentFragment();
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "mon-watch-item";
      row.dataset.name = item.name;
      row.innerHTML = `
        <div class="mon-watch-main">
          <span class="mon-watch-name mono">${escapeHtml(item.name)}</span>
          <button type="button" class="icon-text-btn mon-remove" title="移除">移除</button>
        </div>
        <div class="mon-watch-opts">
          <label class="form-label-check"><input type="checkbox" class="mon-alert-stop" ${item.alert_on_stop !== false ? "checked" : ""}/><span>停止告警</span></label>
          <label class="form-label-check"><input type="checkbox" class="mon-alert-start" ${item.alert_on_start ? "checked" : ""}/><span>启动告警</span></label>
          <label class="form-label-check"><input type="checkbox" class="mon-auto-restart" ${item.auto_restart ? "checked" : ""}/><span>自动拉起</span></label>
        </div>
      `;
      row.querySelector(".mon-remove")?.addEventListener("click", () => {
        const next = readMonitorForm().watched.filter((w) => w.name !== item.name);
        renderWatchList(next);
      });
      frag.appendChild(row);
    });
    host.appendChild(frag);
  }

  function renderMonitorEvents(events) {
    state.monitorEvents = events || [];
    const host = $("#mon-events-list");
    const empty = $("#mon-events-empty");
    host.innerHTML = "";
    if (!state.monitorEvents.length) {
      host.classList.add("hidden");
      empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden");
    host.classList.remove("hidden");
    const frag = document.createDocumentFragment();
    state.monitorEvents.forEach((ev) => {
      const row = document.createElement("div");
      row.className = `mon-event-item level-${ev.level || "info"}`;
      const ts = ev.ts ? new Date(ev.ts * 1000).toLocaleString() : "";
      row.innerHTML = `
        <div class="mon-event-top">
          <span class="mon-event-title">${escapeHtml(ev.title || ev.name || "告警")}</span>
          <span class="mon-event-time">${escapeHtml(ts)}</span>
        </div>
        <div class="mon-event-msg">${escapeHtml(ev.message || "")}</div>
      `;
      frag.appendChild(row);
    });
    host.appendChild(frag);
  }

  function renderTrayAgentStatus(status) {
    state.trayAgent = status || null;
    const running = !!status?.running;
    const dot = $("#mon-agent-dot");
    const label = $("#mon-agent-label");
    const btn = $("#btn-mon-tray-toggle");
    const auto = $("#mon-autostart");
    if (dot) {
      dot.className = `status-dot ${running ? "status-running" : "status-stopped"}`;
    }
    if (label) {
      label.textContent = running
        ? `托盘代理运行中${status.pid ? ` (PID ${status.pid})` : ""}`
        : "托盘代理：未运行（仅主窗口内监控）";
    }
    if (btn) {
      btn.textContent = running ? "停止托盘常驻" : "启动托盘常驻";
    }
    if (auto) {
      auto.checked = !!status?.autostart;
    }
  }

  async function refreshTrayAgentStatus() {
    try {
      const res = await api("get_tray_agent_status");
      if (res.ok) renderTrayAgentStatus(res.data);
    } catch (_) {
      /* ignore */
    }
  }

  async function toggleTrayAgent() {
    const running = !!state.trayAgent?.running;
    try {
      const res = await api(running ? "stop_tray_agent" : "start_tray_agent");
      if (!res.ok) {
        toast(res.message, "error");
        return;
      }
      renderTrayAgentStatus(res.data);
      toast(res.message || "完成", "success");
    } catch (err) {
      toast(err.message || String(err), "error");
    }
  }

  async function toggleAutostart() {
    const enabled = $("#mon-autostart").checked;
    try {
      const res = await api("set_monitor_autostart", enabled);
      if (!res.ok) {
        toast(res.message, "error");
        $("#mon-autostart").checked = !enabled;
        return;
      }
      renderTrayAgentStatus(res.data);
      toast(res.message || "完成", "success");
    } catch (err) {
      $("#mon-autostart").checked = !enabled;
      toast(err.message || String(err), "error");
    }
  }

  async function loadMonitorPanel() {
    try {
      const [cfgRes, evRes] = await Promise.all([
        api("get_monitor_config"),
        api("get_monitor_events", 50),
        refreshTrayAgentStatus(),
      ]);
      if (cfgRes.ok) fillMonitorForm(cfgRes.data);
      if (evRes.ok) renderMonitorEvents(evRes.data.events || []);
    } catch (err) {
      toast(err.message || String(err), "error");
    }
  }

  async function saveMonitorConfig() {
    try {
      const res = await api("save_monitor_config", readMonitorForm());
      if (!res.ok) {
        toast(res.message, "error");
        return;
      }
      fillMonitorForm(res.data);
      toast(res.message || "已保存", "success");
    } catch (err) {
      toast(err.message || String(err), "error");
    }
  }

  async function watchCurrentService() {
    if (!state.current) {
      toast("请先选定一个服务", "warning");
      return;
    }
    const cfg = readMonitorForm();
    if (cfg.watched.some((w) => w.name.toLowerCase() === state.current.toLowerCase())) {
      toast("该服务已在监控列表中", "info");
      switchTab("monitor");
      return;
    }
    cfg.watched.push({
      name: state.current,
      auto_restart: false,
      alert_on_stop: true,
      alert_on_start: false,
    });
    cfg.enabled = true;
    try {
      const res = await api("save_monitor_config", cfg);
      if (!res.ok) {
        toast(res.message, "error");
        return;
      }
      fillMonitorForm(res.data);
      toast(`已监控 ${state.current}`, "success");
      switchTab("monitor");
    } catch (err) {
      toast(err.message || String(err), "error");
    }
  }

  // ---- 自动更新 ----

  function showUpdateDialog() {
    const dlg = $("#update-dialog");
    if (dlg && !dlg.open) dlg.showModal();
  }

  function closeUpdateDialog() {
    const dlg = $("#update-dialog");
    if (dlg?.open) dlg.close();
  }

  function fillUpdateDialog(info, message) {
    state.updateInfo = info || null;
    const available = !!info?.update_available;
    const status = info?.status || (available ? "update_available" : "up_to_date");
    const titles = {
      update_available: `发现新版本 ${info?.latest_version || ""}`.trim(),
      ahead: "当前已领先发布版",
      up_to_date: "已是最新版本",
      unknown: "检查更新",
    };
    $("#update-dialog-title").textContent = titles[status] || "检查更新";

    let summary = message || info?.message || "";
    if (!summary) {
      if (available) {
        summary = `当前 ${info.current_version} → 最新 ${info.latest_version}`;
      } else if (status === "ahead") {
        summary = `本地 ${info.current_version}，GitHub 已发布 ${info.latest_version}`;
      } else {
        summary = `当前版本 ${info?.current_version || state.appVersion}`;
      }
    }
    $("#update-dialog-summary").textContent = summary;

    const notes = $("#update-dialog-notes");
    if (available && info?.body) {
      notes.textContent = info.body;
      notes.classList.remove("hidden");
    } else {
      notes.textContent = "";
      notes.classList.add("hidden");
    }
    const applyBtn = $("#btn-update-apply");
    if (available) {
      applyBtn.classList.remove("hidden");
      applyBtn.disabled = false;
      applyBtn.textContent = info.can_apply ? "立即更新" : "前往下载";
    } else {
      applyBtn.classList.add("hidden");
    }
    $("#update-dot")?.classList.toggle("hidden", !available);
  }

  async function checkForUpdate({ silent = false } = {}) {
    if (!silent) {
      showUpdateDialog();
      $("#update-dialog-title").textContent = "检查更新";
      $("#update-dialog-summary").textContent = "正在查询 GitHub Releases…";
      $("#update-dialog-notes").classList.add("hidden");
      $("#btn-update-apply").classList.add("hidden");
      $("#update-progress-wrap").classList.add("hidden");
    }
    try {
      const res = await api("check_update");
      if (!res.ok) {
        if (!silent) {
          $("#update-dialog-summary").textContent = res.message || "检查失败";
          toast(res.message, "error");
        }
        return;
      }
      fillUpdateDialog(res.data, res.message);
      if (!silent) showUpdateDialog();
      else if (res.data?.update_available) {
        $("#update-dot")?.classList.remove("hidden");
        setStatus(`发现新版本 ${res.data.latest_version}`);
      }
    } catch (err) {
      if (!silent) {
        $("#update-dialog-summary").textContent = err.message || String(err);
        toast(err.message || String(err), "error");
      }
    }
  }

  async function pollUpdateProgress() {
    if (state.updatePolling) return;
    state.updatePolling = true;
    const wrap = $("#update-progress-wrap");
    const fill = $("#update-progress-fill");
    const text = $("#update-progress-text");
    wrap.classList.remove("hidden");
    try {
      while (state.updatePolling) {
        const res = await api("get_update_progress");
        if (!res.ok) break;
        const p = res.data || {};
        fill.style.width = `${p.percent || 0}%`;
        text.textContent = p.total
          ? `${p.percent || 0}%`
          : p.state === "downloading"
            ? "下载中…"
            : `${p.percent || 0}%`;
        $("#update-dialog-summary").textContent = p.message || p.state || "";
        if (p.state === "error") {
          toast(p.error || "升级失败", "error");
          $("#btn-update-apply").disabled = false;
          break;
        }
        if (p.state === "restarting") {
          $("#update-dialog-summary").textContent = "即将退出并完成更新…";
          break;
        }
        if (p.state === "done") {
          toast(p.message || "请按安装向导完成升级", "success");
          break;
        }
        await new Promise((r) => setTimeout(r, 300));
      }
    } finally {
      state.updatePolling = false;
    }
  }

  async function applyUpdate() {
    const info = state.updateInfo;
    if (!info?.update_available) return;
    if (!info.can_apply) {
      try {
        await api("open_releases");
      } catch (_) {
        /* ignore */
      }
      return;
    }
    $("#btn-update-apply").disabled = true;
    try {
      const res = await api("apply_update");
      if (!res.ok) {
        toast(res.message, "error");
        $("#btn-update-apply").disabled = false;
        return;
      }
      pollUpdateProgress();
    } catch (err) {
      toast(err.message || String(err), "error");
      $("#btn-update-apply").disabled = false;
    }
  }

  function bindEvents() {
    $$(".pill-tab").forEach((tab) => {
      tab.addEventListener("click", () => switchTab(tab.dataset.tab));
    });

    $$(".theme-toggle button").forEach((btn) => {
      btn.addEventListener("click", () => applyTheme(btn.dataset.theme));
    });

    el.search.addEventListener("input", () => {
      renderList();
      scheduleLoad();
    });
    $("#btn-refresh").addEventListener("click", () => loadServices(true));

    $$("[data-state-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (state.stateFilter === btn.dataset.stateFilter) return;
        state.stateFilter = btn.dataset.stateFilter;
        syncFilterChips();
        loadServices(true);
      });
    });
    $$("[data-type-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (state.typeFilter === btn.dataset.typeFilter) return;
        state.typeFilter = btn.dataset.typeFilter;
        syncFilterChips();
        loadServices(true);
      });
    });

    $$(".action-btn[data-action]").forEach((btn) => {
      btn.addEventListener("click", () => control(btn.dataset.action));
    });

    $("#btn-delete").addEventListener("click", openDeleteDialog);
    $("#btn-save").addEventListener("click", saveConfig);
    $("#btn-add").addEventListener("click", addService);
    $("#btn-copy-path").addEventListener("click", copyPath);

    $$("[data-add-mode]").forEach((btn) => {
      btn.addEventListener("click", () => setAddMode(btn.dataset.addMode));
    });

    $("#btn-mon-save")?.addEventListener("click", saveMonitorConfig);
    $("#btn-mon-watch-current")?.addEventListener("click", watchCurrentService);
    $("#btn-mon-tray-toggle")?.addEventListener("click", toggleTrayAgent);
    $("#mon-autostart")?.addEventListener("change", toggleAutostart);
    $("#btn-check-update")?.addEventListener("click", () => checkForUpdate({ silent: false }));
    $("#btn-update-close")?.addEventListener("click", closeUpdateDialog);
    $("#btn-update-apply")?.addEventListener("click", applyUpdate);
    $("#btn-update-releases")?.addEventListener("click", async () => {
      try {
        await api("open_releases");
      } catch (err) {
        toast(err.message || String(err), "error");
      }
    });
    $("#btn-mon-events-refresh")?.addEventListener("click", async () => {
      try {
        const res = await api("get_monitor_events", 50);
        if (res.ok) renderMonitorEvents(res.data.events || []);
      } catch (err) {
        toast(err.message || String(err), "error");
      }
    });

    window.__svcMonitorAlert = (payload) => {
      const data = typeof payload === "string" ? { message: payload } : payload || {};
      const level = data.level === "error" ? "error" : data.level === "success" ? "success" : "warning";
      toast(`${data.title || "服务告警"}：${data.message || ""}`, level);
      if (state.activeTab === "monitor") {
        api("get_monitor_events", 50).then((res) => {
          if (res.ok) renderMonitorEvents(res.data.events || []);
        }).catch(() => {});
      }
    };

    el.infoPrioritySelect.addEventListener("change", async () => {
      if (!state.current) return;
      const priority = el.infoPrioritySelect.value;
      try {
        const res = await api("set_priority", state.current, priority);
        if (!res.ok) {
          toast(res.message, "error");
          return;
        }
        toast(res.message, "success");
      } catch (err) {
        toast(err.message || String(err), "error");
      }
    });

    $("#btn-logs-refresh").addEventListener("click", () => {
      if (state.current) loadEventLogs(state.current);
    });
    el.logsLevelFilter.addEventListener("change", () => renderLogs());

    $$("[data-browse]").forEach((btn) => {
      btn.addEventListener("click", () => browseFor(btn.dataset.browse));
    });
    $$("[data-browse-dir]").forEach((btn) => {
      btn.addEventListener("click", () => browseDir(btn.dataset.browseDir));
    });
    $$("[data-env-add]").forEach((btn) => {
      btn.addEventListener("click", () => addEnv(btn.dataset.envAdd));
    });
    ["edit", "add"].forEach((prefix) => {
      $(`#${prefix}-account`).addEventListener("change", () => syncAccount(prefix));
      $(`#${prefix}-reset-never`).addEventListener("change", () => syncReset(prefix));
      [`#${prefix}-env-name`, `#${prefix}-env-value`].forEach((sel) => {
        $(sel).addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            addEnv(prefix);
          }
        });
      });
      $(`#${prefix}-env`).addEventListener("click", (e) => {
        const button = e.target.closest("[data-env-remove]");
        if (!button) return;
        const row = button.closest(".env-row");
        row?.remove();
        if (!$(`#${prefix}-env .env-row`)) renderEnv(prefix, []);
      });
    });

    el.deleteForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const submitter = e.submitter;
      const value = submitter?.value || "cancel";
      el.deleteDialog.close();
      if (value === "confirm") {
        confirmDelete();
      }
    });

    document.addEventListener("keydown", (e) => {
      if (el.deleteDialog.open) return;
      const tag = (e.target && e.target.tagName) || "";
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(tag);

      if (e.key === "/" && !typing) {
        e.preventDefault();
        el.search.focus();
        el.search.select();
        return;
      }
      if (e.key === "F5") {
        e.preventDefault();
        loadServices(true);
        return;
      }
      if (e.ctrlKey && e.key.toLowerCase() === "f") {
        e.preventDefault();
        el.search.focus();
        el.search.select();
        return;
      }
      if (!typing && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        moveSelection(e.key === "ArrowDown" ? 1 : -1);
        return;
      }
      if (!typing && e.key === "Enter" && state.current) {
        switchTab("info");
      }
    });

    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (state.theme === "system") applyTheme("system");
    });

    // 定时轮询（带严格服务名称校验与在途互斥校验）
    refreshTimer = setInterval(async () => {
      if (state.busy || state.loading || state.actionInFlight || !state.current || document.hidden) {
        return;
      }
      const pollService = state.current;
      const thisPollId = ++state.pollRequestId;
      try {
        const res = await api("get_service", pollService);
        // 如果轮询返回时，用户已切换到其他服务或有新的选择，则忽略此次结果
        if (thisPollId !== state.pollRequestId || state.current !== pollService) {
          return;
        }
        if (res.ok) {
          fillInfo(res.data, true);
          updateServiceInList(res.data);
          renderList();
        }
      } catch (_) {
        /* ignore */
      }
    }, 5000);
  }

  async function init() {
    mountRuntime();
    resetAddForm();
    resetWrapForm();
    setAddMode("native");
    applyTheme(state.theme);
    syncFilterChips();
    bindEvents();
    showEmpty(true);
    el.list.classList.add("hidden");
    el.listLoading.classList.remove("hidden");
    setStatus("正在连接服务核心…");
    await waitApiReady();
    setStatus("已连接，正在枚举服务…");

    try {
      const meta = await api("get_meta");
      if (meta.ok) {
        if (!meta.data.is_admin) el.adminBadge.classList.remove("hidden");
        state.appVersion = meta.data.version || state.appVersion;
        state.frozen = !!meta.data.frozen;
        const verEl = $("#app-version");
        if (verEl) verEl.textContent = `v${state.appVersion}`;
      }
    } catch (_) {
      /* ignore */
    }

    await loadServices(false);
    try {
      const cfg = await api("get_monitor_config");
      if (cfg.ok) fillMonitorForm(cfg.data);
    } catch (_) {
      /* ignore */
    }

    // 启动后静默检查更新（失败忽略）
    setTimeout(() => checkForUpdate({ silent: true }), 2500);
  }

  init();
})();
