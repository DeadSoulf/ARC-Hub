"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  user: null, role: "", csrf: "", devices: [], selected: null,
  control: false, adminUsers: [], catalog: [], page: "overview",
  screenSize: null, tempCredential: null
};
const ROLE_SCOPES = {
  viewer: ["status"],
  operator: ["status", "inspect", "files.read", "screen.read", "apps.launch", "input.control"],
  admin: ["status", "inspect", "files.read", "screen.read", "apps.launch", "input.control", "files.write", "shell.exec"]
};

const text = (tag, value, className = "") => {
  const e = document.createElement(tag);
  e.textContent = String(value == null ? "" : value);
  if (className) e.className = className;
  return e;
};

function notify(message, bad = false) {
  const target = $("toast");
  target.textContent = String(message);
  target.className = bad ? "toast bad" : "toast";
  target.hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => target.hidden = true, 4500);
}

async function api(path, method = "GET", payload = null) {
  const opts = {
    method, credentials: "same-origin",
    headers: {"Accept": "application/json"}
  };
  if (method !== "GET") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify({
      ...(payload || {}),
      ...(path === "/web/api/login" ? {} : {csrf: state.csrf})
    });
  }
  let response;
  try {
    response = await fetch(path, opts);
  } catch {
    throw new Error("Нет соединения с ARC Hub");
  }
  let data;
  try { data = await response.json(); }
  catch { throw new Error("Некорректный ответ сервера"); }
  if (!response.ok) {
    if (response.status === 401 && path !== "/web/api/login") showLogin();
    throw new Error(data.error || ("HTTP " + response.status));
  }
  return data;
}

function showLogin() {
  if (updatePoller) clearInterval(updatePoller);
  updatePoller = null;
  if (sessionStorage.getItem("arcUpdateInProgress") === "1")
    $("loginError").textContent = "ARC Hub перезапустился. Войдите снова, чтобы продолжить просмотр обновления.";
  state.csrf = "";
  state.user = null;
  state.selected = null;
  state.control = false;
  state.tempCredential = null;
  $("loginToken").value = "";
  $("app").hidden = true;
  $("auth").hidden = false;
}

async function bootstrap() {
  try {
    const me = await api("/web/api/me");
    state.user = me.user;
    state.role = me.role;
    state.csrf = me.csrf;
    $("auth").hidden = true;
    $("app").hidden = false;
    $("currentUser").textContent = state.user;
    $("currentRole").textContent = state.role.toUpperCase();
    $("avatar").textContent = state.user[0].toUpperCase();
    document.querySelectorAll(".admin-nav").forEach(e => e.hidden = state.role !== "admin");
    $("newRole").querySelector('option[value="admin"]').hidden = state.user !== "owner";
    $("updateServer").hidden = state.user !== "owner";
    $("createPairCode").hidden = state.user !== "owner";
    $("removeDevice").hidden = state.user !== "owner";
    $("hubAddress").textContent = window.location.origin;
    if (updatePoller) clearInterval(updatePoller);
    updatePoller = null;
    if (state.user === "owner") {
      await refreshServerUpdate();
      updatePoller = setInterval(refreshServerUpdate, 2200);
    }
    await refreshDevices();
    if (state.role === "admin") await refreshUsers();
  } catch {
    showLogin();
  }
}

function showPage(name) {
  if ((name === "users" || name === "audit") && state.role !== "admin") return;
  state.page = name;
  document.querySelectorAll(".page").forEach(section => section.hidden = section.id !== name);
  document.querySelectorAll(".nav-item").forEach(item =>
    item.classList.toggle("active", item.dataset.page === name));
  $("pageTitle").textContent = {
    overview: "Обзор", devices: "Компьютеры",
    users: "Пользователи", audit: "Журнал действий"
  }[name] || "ARC";
  if (name === "audit") refreshAudit();
  if (name === "users") refreshUsers();
}

function renderTile(device) {
  const card = text("button", "", "device-tile");
  const icon = text("div", "▣", "device-icon");
  const title = text("b", device.name || device.device_id);
  const desc = text("small", device.device_id.substring(0, 18) + "…");
  const online = text("span", device.online ? "Online" : "Offline",
                       "device-state " + (device.online ? "" : "offline"));
  card.append(icon, title, desc, online);
  card.addEventListener("click", () => {
    state.selected = device.device_id;
    state.control = false;
    state.screenSize = null;
    showPage("devices");
    renderDevices();
  });
  return card;
}

async function refreshDevices() {
  const data = await api("/web/api/devices");
  state.devices = data.devices || [];
  if (state.selected && !state.devices.some(d => d.device_id === state.selected)) {
    state.selected = null;
    state.control = false;
  }
  if (!state.selected && state.devices.length) state.selected = state.devices[0].device_id;
  $("totalDevices").textContent = state.devices.length;
  $("onlineDevices").textContent = state.devices.filter(d => d.online).length;
  $("busyDevices").textContent = state.devices.filter(d => d.control_busy).length;
  $("listCount").textContent = state.devices.length;
  const overview = $("overviewDevices");
  const list = $("deviceList");
  overview.replaceChildren();
  list.replaceChildren();
  if (!state.devices.length) {
    overview.append(text("p", "Для вашей учётной записи пока не назначены компьютеры.", "muted"));
    list.append(text("p", "Нет доступных устройств", "muted"));
  }
  for (const d of state.devices) {
    overview.append(renderTile(d));
    const btn = text("button", "", "list-item" + (d.device_id === state.selected ? " active" : ""));
    btn.append(text("b", d.name || d.device_id), text("small", d.online ? "● В сети" : "○ Не в сети"));
    btn.addEventListener("click", () => {
      state.selected = d.device_id;
      state.control = false;
      state.screenSize = null;
      renderDevices();
    });
    list.append(btn);
  }
  renderDetails();
}

function device() {
  return state.devices.find(d => d.device_id === state.selected) || null;
}
function permitted(scope) {
  const d = device();
  return !!d && d.permissions.includes(scope);
}

function renderDevices() {
  const list = $("deviceList");
  Array.from(list.children).forEach((child, i) =>
    child.classList.toggle("active", state.devices[i]?.device_id === state.selected));
  renderDetails();
}

function renderDetails() {
  const d = device();
  $("selectedName").textContent = d ? d.name : "Выберите компьютер";
  $("selectedId").textContent = d?.device_id || "";
  $("selectedState").textContent = d ? (d.online ? "● Online" : "○ Offline") : "—";
  $("selectedState").className = "pill " + (d?.online ? "green" : "");
  const scopes = $("scopeList");
  scopes.replaceChildren();
  for (const scope of d?.permissions || []) scopes.append(text("span", scope, "scope"));
  $("takeControl").disabled = !d?.online || !(d.permissions.some(s =>
    ["apps.launch", "input.control", "files.write", "shell.exec"].includes(s)));
  $("releaseControl").disabled = !state.control;
  $("removeDevice").disabled = !d || state.user !== "owner";
  document.querySelectorAll("[data-tool]").forEach(b => {
    const s = b.dataset.tool === "computer_status" ? "status" : "inspect";
    b.disabled = !d?.online || !permitted(s);
  });
  $("listFiles").disabled = $("readFile").disabled = !d?.online || !permitted("files.read");
  $("captureScreen").disabled = !d?.online || !permitted("screen.read");
  $("sendText").disabled = !d?.online || !permitted("input.control") || !state.control;
  $("runShell").disabled = !d?.online || !permitted("shell.exec") || !state.control || state.role !== "admin";
  if (!d) $("diagnosticOutput").textContent = "Выберите компьютер";
}

function showTab(tab) {
  document.querySelectorAll(".tab").forEach(b =>
    b.classList.toggle("selected", b.dataset.tab === tab));
  document.querySelectorAll(".tab-panel").forEach(panel => panel.hidden = panel.id !== tab);
}

function prettyResult(result) {
  const content = result?.content || [];
  if (result?.isError) throw new Error(content[0]?.text || "Ошибка выполнения");
  const block = content.find(c => c.type === "text");
  if (block) {
    try { return JSON.stringify(JSON.parse(block.text), null, 2); }
    catch { return block.text; }
  }
  return JSON.stringify(result || {}, null, 2);
}

async function execute(tool, args = {}) {
  const d = device();
  if (!d || !d.online) throw new Error("Компьютер не в сети");
  const result = await api("/web/api/call", "POST", {
    device_id: d.device_id, tool, arguments: args
  });
  return result.result;
}

async function runDiagnostic(tool) {
  $("diagnosticOutput").textContent = "Загрузка данных…";
  try {
    const result = await execute(tool);
    $("diagnosticOutput").textContent = prettyResult(result);
    if (tool === "computer_status") {
      const raw = result.content?.find(c => c.type === "text")?.text;
      if (raw) {
        try { state.screenSize = JSON.parse(raw).screen || null; }
        catch { state.screenSize = null; }
      }
    }
  } catch (e) { $("diagnosticOutput").textContent = e.message; notify(e.message, true); }
}

async function fileAction(tool) {
  const path = $("filePath").value.trim();
  if (!path) return notify("Укажите путь", true);
  $("fileOutput").textContent = "Загрузка…";
  try { $("fileOutput").textContent = prettyResult(await execute(tool, {path})); }
  catch (e) { $("fileOutput").textContent = e.message; notify(e.message, true); }
}

async function capture() {
  try {
    const response = await execute("capture_screen");
    const img = response?.content?.find(c => c.type === "image" && c.mimeType === "image/png");
    if (!img?.data) throw new Error("Агент не вернул изображение PNG");
    $("remoteScreen").src = "data:image/png;base64," + img.data;
    $("remoteScreen").hidden = false;
    $("screenPlaceholder").hidden = true;
  } catch (e) { notify(e.message, true); }
}

async function takeControl(action) {
  if (!device()) return;
  try {
    await api("/web/api/control", "POST", {device_id: state.selected, action});
    state.control = action === "take";
    notify(state.control ? "Сеанс управления получен на 90 секунд" : "Сеанс управления освобождён");
    await refreshDevices();
  } catch (e) { notify(e.message, true); }
}

function cell(row, value) { row.append(text("td", value)); }
async function refreshUsers() {
  if (state.role !== "admin") return;
  try {
    const result = await api("/web/api/users");
    state.adminUsers = result.users || [];
    state.catalog = result.catalog || [];
    renderUsers();
  } catch (e) { notify(e.message, true); }
}

function renderUsers() {
  const table = $("usersTable");
  table.replaceChildren();
  for (const u of state.adminUsers) {
    const row = document.createElement("tr");
    cell(row, u.username);
    cell(row, u.role);
    cell(row, u.enabled ? "Активен" : "Отключён");
    cell(row, Object.keys(u.grants || {}).length);
    const actions = document.createElement("td");
    if (u.username !== "owner" && u.username !== state.user) {
      const btn = text("button", u.enabled ? "Отключить" : "Включить", "subtle");
      btn.addEventListener("click", async () => {
        if (!confirm("Изменить доступ пользователя " + u.username + "?")) return;
        try {
          await api("/web/api/users/enabled", "POST", {
            username: u.username, enabled: !u.enabled
          });
          notify("Статус пользователя изменён");
          refreshUsers();
        } catch (e) { notify(e.message, true); }
      });
      actions.append(btn);
    } else actions.textContent = "—";
    row.append(actions);
    table.append(row);
  }
  const users = $("grantUser"), devices = $("grantDevice");
  const previousUser = users.value, previousDevice = devices.value;
  users.replaceChildren();
  devices.replaceChildren();
  for (const u of state.adminUsers) users.add(new Option(u.username + " (" + u.role + ")", u.username));
  for (const d of state.catalog) devices.add(new Option(d.name, d.device_id));
  if (state.adminUsers.some(u => u.username === previousUser)) users.value = previousUser;
  if (state.catalog.some(d => d.device_id === previousDevice)) devices.value = previousDevice;
  renderGrantScopes();
}

function renderGrantScopes() {
  const user = state.adminUsers.find(u => u.username === $("grantUser").value);
  const d = state.catalog.find(x => x.device_id === $("grantDevice").value);
  const container = $("grantScopes");
  container.replaceChildren();
  if (!user || !d) return;
  const approved = new Set(user.grants?.[d.device_id] || []);
  const available = d.allowed_scopes.filter(s => (ROLE_SCOPES[user.role] || []).includes(s));
  for (const scope of available) {
    const label = text("label", "", "check");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "scopes";
    input.value = scope;
    input.checked = approved.has(scope);
    label.append(input, text("span", scope));
    container.append(label);
  }
  if (!available.length) container.append(text("p", "Нет доступных полномочий", "muted"));
}

async function refreshAudit() {
  if (state.role !== "admin") return;
  try {
    const {entries} = await api("/web/api/audit");
    const table = $("auditTable");
    table.replaceChildren();
    for (const e of (entries || []).reverse()) {
      const row = document.createElement("tr");
      for (const key of ["timestamp", "user", "device", "tool", "outcome"]) cell(row, e[key] || "—");
      table.append(row);
    }
  } catch (e) { notify(e.message, true); }
}

$("loginForm").addEventListener("submit", async e => {
  e.preventDefault();
  $("loginError").textContent = "";
  try {
    await api("/web/api/login", "POST", {
      user: $("loginUser").value.trim(), token: $("loginToken").value
    });
    await bootstrap();
  } catch (err) { $("loginError").textContent = err.message; }
});
$("logout").addEventListener("click", async () => {
  try { await api("/web/api/logout", "POST"); }
  catch {}
  showLogin();
});
document.querySelectorAll(".nav-item").forEach(b => b.addEventListener("click", () => showPage(b.dataset.page)));
document.querySelectorAll(".tab").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
document.querySelectorAll("[data-tool]").forEach(b =>
  b.addEventListener("click", () => runDiagnostic(b.dataset.tool)));
$("overviewOpenDevices").addEventListener("click", () => showPage("devices"));
$("refresh").addEventListener("click", () => refreshDevices().catch(e => notify(e.message, true)));
$("refreshAudit").addEventListener("click", refreshAudit);
$("listFiles").addEventListener("click", () => fileAction("list_directory"));
$("readFile").addEventListener("click", () => fileAction("read_text_file"));
$("takeControl").addEventListener("click", () => takeControl("take"));
$("releaseControl").addEventListener("click", () => takeControl("release"));
$("removeDevice").addEventListener("click", async () => {
  if (state.user !== "owner") return;
  const selected = device();
  if (!selected) return notify("Сначала выберите компьютер", true);
  const answer = prompt(
    "Удалить устройство «" + selected.name + "» из ARC Hub?\n" +
    "Будут отозваны ключ устройства и все права пользователей.\n" +
    "Файлы на компьютере не удаляются.\n\n" +
    "Для подтверждения введите точное имя компьютера:", ""
  );
  if (answer === null) return;
  if (answer !== selected.name) return notify("Имя не совпадает. Удаление отменено.", true);
  if (!confirm("Последнее подтверждение: отозвать подключение «" + selected.name +
               "» и удалить его из ARC Hub?")) return;
  try {
    await api("/web/api/devices/remove", "POST", {
      device_id: selected.device_id, confirm_name: selected.name
    });
    state.selected = null;
    state.control = false;
    notify("Устройство удалено из ARC Hub. Для нового подключения сбросьте привязку в ARC Agent.");
    await refreshDevices();
    if (state.role === "admin") await refreshUsers();
  } catch (err) { notify(err.message, true); }
});
$("captureScreen").addEventListener("click", capture);
$("remoteScreen").addEventListener("click", async e => {
  if (!state.control || !permitted("input.control")) return notify("Сначала получите управление", true);
  const img = e.currentTarget, r = img.getBoundingClientRect();
  const scr = state.screenSize;
  if (!scr?.width || !scr?.height) {
    await runDiagnostic("computer_status");
    if (!state.screenSize) return notify("Сначала получите размеры экрана в Диагностике", true);
  }
  const x = Math.max(0, Math.min(state.screenSize.width - 1,
    Math.floor((e.clientX - r.left) / r.width * state.screenSize.width)));
  const y = Math.max(0, Math.min(state.screenSize.height - 1,
    Math.floor((e.clientY - r.top) / r.height * state.screenSize.height)));
  try { await execute("click_mouse", {x, y, button: "left"}); notify("Щелчок отправлен: " + x + ", " + y); }
  catch (err) { notify(err.message, true); }
});
$("sendText").addEventListener("click", async () => {
  const value = $("typedText").value;
  if (!value) return;
  try { await execute("type_text", {text: value}); notify("Текст отправлен"); }
  catch (err) { notify(err.message, true); }
});
$("runShell").addEventListener("click", async () => {
  const command = $("shellCommand").value.trim();
  if (!command) return notify("Введите команду", true);
  if (!confirm("Выполнить эту команду PowerShell на " + (device()?.name || "ПК") + "?\n\n" + command.substring(0, 350))) return;
  $("shellOutput").textContent = "Выполнение…";
  try { $("shellOutput").textContent = prettyResult(await execute("run_command", {command, timeout_seconds: 20})); }
  catch (err) { $("shellOutput").textContent = err.message; notify(err.message, true); }
});
$("createUserForm").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const result = await api("/web/api/users/create", "POST", {
      username: $("newUsername").value.trim(), role: $("newRole").value
    });
    state.tempCredential = result.token;
    $("credentialToken").textContent = result.token;
    $("credentialOutput").hidden = false;
    notify("Пользователь создан. Сохраните токен сейчас.");
    await refreshUsers();
  } catch (err) { notify(err.message, true); }
});
$("copyCredential").addEventListener("click", async () => {
  if (state.tempCredential) {
    try { await navigator.clipboard.writeText(state.tempCredential); notify("Токен скопирован"); }
    catch { notify("Скопируйте токен вручную", true); }
  }
});
$("grantUser").addEventListener("change", renderGrantScopes);
$("grantDevice").addEventListener("change", renderGrantScopes);
$("grantForm").addEventListener("submit", async e => {
  e.preventDefault();
  const scopes = Array.from(document.querySelectorAll('#grantScopes input:checked')).map(x => x.value);
  try {
    await api("/web/api/users/grant", "POST", {
      username: $("grantUser").value, device_id: $("grantDevice").value, scopes
    });
    notify("Права пользователя сохранены");
    await refreshUsers();
  } catch (err) { notify(err.message, true); }
});
bootstrap();

const ARC_UPDATE_STAGES = [
  ["prepare", "Подготовка"], ["download", "Загрузка GitHub"],
  ["test", "Автоматические тесты"], ["backup", "Резервная копия"],
  ["install", "Установка файлов"], ["restart", "Перезапуск ARC Hub"],
  ["health", "Проверка работоспособности"],
  ["service", "Обновление служб"], ["complete", "Завершение"]
];
let updatePoller = null;
let updatePollBusy = false;

function renderServerUpdate(status) {
  const stateValue = status.state || "idle";
  const panel = $("serverUpdateProgress");
  const hasHistory = status.updated_unix != null || ["queued", "running", "success", "failed"].includes(stateValue);
  panel.hidden = !hasHistory;
  if (!hasHistory) return;
  const percent = Math.max(0, Math.min(100, Number(status.percent) || 0));
  const failed = stateValue === "failed";
  panel.classList.toggle("update-failed", failed);
  panel.classList.toggle("update-success", stateValue === "success");
  $("serverUpdatePercent").textContent = percent + "%";
  $("serverUpdateBar").value = percent;
  $("serverUpdateTitle").textContent =
    ({queued:"В очереди", running:"Обновление выполняется", success:"Обновление завершено",
      failed:"Ошибка обновления"})[stateValue] || "Состояние обновления";
  $("serverUpdateState").textContent = status.detail || "Ожидание статуса";
  $("serverUpdateTime").textContent =
    Number.isFinite(status.updated_unix) && status.updated_unix > 0
      ? "Обновлено: " + new Date(status.updated_unix * 1000).toLocaleTimeString("ru-RU")
      : "";
  const stages = $("serverUpdateStages");
  stages.replaceChildren();
  const stageIndex = ARC_UPDATE_STAGES.findIndex(x => x[0] === status.step);
  for (const [index, item] of ARC_UPDATE_STAGES.entries()) {
    const step = document.createElement("li");
    step.textContent = item[1];
    if (stateValue === "success" || (stageIndex > index)) step.className = "done";
    if (stageIndex === index && stateValue === "running") step.className = "current";
    if (failed && (stageIndex === index || index === stageIndex - 1)) step.className = "error-step";
    stages.append(step);
  }
  const help = $("serverUpdateHelp");
  help.textContent = stateValue === "failed"
    ? "Проверьте: sudo journalctl -u arc-update.service -n 60 --no-pager"
    : stateValue === "success"
      ? "Сервер обновлён. Если сессия прервалась, войдите заново."
      : "Этапы обновляются автоматически. При перезапуске ARC Hub вход может потребоваться снова.";
  if (stateValue === "success" || failed) sessionStorage.removeItem("arcUpdateInProgress");
}

async function refreshServerUpdate() {
  if (state.user !== "owner" || updatePollBusy) return;
  updatePollBusy = true;
  try {
    const status = await api("/web/api/server");
    renderServerUpdate(status);
    $("updateServer").disabled = !status.enabled || ["queued", "running"].includes(status.state);
    $("updateServer").title = status.enabled
      ? "Запустить обновление из GitHub с резервной копией"
      : "Служба arc-update.path ещё не установлена на VDS";
  } catch (err) {
    // During systemd restart the HTTP listener temporarily disappears.
    // Keep the last known progress rather than erasing it.
    $("serverUpdateState").textContent = "Ожидание восстановления связи с ARC Hub…";
    $("updateServer").disabled = true;
  } finally {
    updatePollBusy = false;
  }
}

$("updateServer").addEventListener("click", async () => {
  if (state.user !== "owner") return;
  if (!confirm("Обновить ARC Hub на VDS из ветки main GitHub?\n\n" +
    "Служба будет перезапущена. Текущие сессии прервутся; будет создана резервная копия.")) return;
  try {
    const result = await api("/web/api/server/update", "POST");
    notify(result.detail || "Обновление поставлено в очередь");
    sessionStorage.setItem("arcUpdateInProgress", "1");
    $("updateServer").disabled = true;
    renderServerUpdate({state: "queued", step: "queued", percent: 0,
      detail: "Запрос отправлен системной службе ARC Hub"});
    await refreshServerUpdate();
  } catch (err) { notify(err.message, true); }
});


$("createPairCode").addEventListener("click", async () => {
  if (state.user !== "owner") return;
  try {
    const result = await api("/web/api/pairing/create", "POST");
    $("pairCodeText").textContent = result.code;
    $("pairCodePanel").hidden = false;
    notify("Одноразовый код создан. Действует 10 минут.");
  } catch (err) { notify(err.message, true); }
});
$("copyPairCode").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("pairCodeText").textContent);
    notify("Код скопирован");
  } catch { notify("Скопируйте код вручную", true); }
});
