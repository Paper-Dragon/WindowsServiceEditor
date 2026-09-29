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
    theme: localStorage.getItem("svc-theme") || "dark",
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
    regKeyPath: $("#reg-key-path"),
    editStart: $("#edit-start"),
    editDisplay: $("#edit-display"),
    editDesc: $("#edit-desc"),
    addName: $("#add-name"),
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
    const payload = {
      start: Number($(`#${prefix}-start`).value),
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
    el.infoAccount.textContent = accountText(info);
    el.infoWorkdir.textContent = info.working_directory || "—";
    el.infoFailure.textContent = failureText(info.failure);
    el.infoEnv.textContent = info.environments?.length
      ? info.environments.map((item) => item.name).join(", ")
      : "—";

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
      if (meta.ok && !meta.data.is_admin) {
        el.adminBadge.classList.remove("hidden");
      }
    } catch (_) {
      /* ignore */
    }

    await loadServices(false);
  }

  init();
})();
