/**
 * RIBBON live audit interface.
 *
 * Sign in, upload PDFs, follow the SSE stream, review discrepancies, and compare
 * resolutions in the metrics view. Visible text is in Portuguese; the API contract
 * (routes, JSON keys, and status values) stays in English.
 */

const $ = (id) => document.getElementById(id);

const auditForm = $("auditForm");
const startButton = $("start-button");
const notice = $("notice");
const panel = $("panel");
const tableBody = $("body-table");
const viewer = $("viewer");
const progressBar = $("progressBar");
const statusText = $("statusText");

// Keep annotated page images available when revisiting report rows.
const pages = new Map();
let counters = { processed: 0, matched: 0, mismatched: 0, total: null };
let eventSource = null;
let currentJobId = null;
let selectedPage = null;

let timerInterval = null;
let elapsedSeconds = 0;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]
  ));
}

/** Format a number with Brazilian decimal separators, e.g. 1,5. */
function decimal(value, digits = 1) {
  return value.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// API error messages are in English; show their Portuguese equivalents.
const API_MESSAGES = {
  "Invalid username or password.": "Usuário ou senha inválidos.",
  "Usernames have 3 to 32 letters, digits, dots, hyphens, or underscores.":
    "O usuário deve ter de 3 a 32 letras, números, pontos, hífens ou sublinhados.",
  "Usernames starting with 'sim-' are reserved for the simulation.":
    "Usuários que começam com 'sim-' são reservados para a simulação.",
  "Passwords need at least 8 characters.": "A senha precisa ter pelo menos 8 caracteres.",
  "This username is already taken.": "Este nome de usuário já está em uso.",
  "Sign in to continue.": "Entre para continuar.",
  "Audit not found.": "Auditoria não encontrada.",
  "No pages processed yet.": "Nenhuma página foi processada ainda.",
  "Unknown document profile.": "Modelo de documento desconhecido.",
  "The profile definition is not valid JSON.": "As regras do modelo estão incompletas.",
  "The built-in profile cannot be deleted.": "O modelo padrão não pode ser excluído.",
  "Could not read the PDF.": "Não foi possível abrir este PDF.",
  "Page not processed yet.": "Esta página ainda não foi processada.",
  "Page not found.": "Página não encontrada.",
};

function errorMessage(body, fallback) {
  if (Array.isArray(body.detail)) return "Valores inválidos. Confira os campos e tente novamente.";
  const detail = body.detail;
  if (!detail) return fallback;
  if (API_MESSAGES[detail]) return API_MESSAGES[detail];
  const notPdf = /^'(.*)' is not a PDF\.$/.exec(detail);
  if (notPdf) return `"${notPdf[1]}" não é um PDF.`;
  return detail;
}

/** Fetch from the API, returning to the sign-in screen when the session has ended. */
async function api(url, options) {
  const response = await fetch(url, options);
  if (response.status === 401) showLogin();
  return response;
}

function jsonRequest(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

/* =========================================================
   Sign-in
   ========================================================= */

async function initSession() {
  try {
    const response = await fetch("/api/v1/auth/me");
    if (response.ok) {
      const { username } = await response.json();
      showApp(username);
      return;
    }
  } catch {
    // fall through to the sign-in screen
  }
  showLogin();
}

function showLogin() {
  $("login-card").hidden = false;
  $("app-content").hidden = true;
  $("user-box").hidden = true;
}

function showApp(username) {
  $("user-name").textContent = username;
  $("user-initial").textContent = String(username || "?").charAt(0);
  $("dash-greeting").textContent = `Olá, ${username}`;
  $("dash-date").textContent = longDate(new Date());
  $("login-card").hidden = true;
  $("app-content").hidden = false;
  $("user-box").hidden = false;
  showView("dashboard");
  loadProfiles();
}

function longDate(date) {
  const text = date.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  return text.charAt(0).toUpperCase() + text.slice(1);
}

async function submitCredentials(mode) {
  const notice = $("login-notice");
  notice.textContent = "";
  const credentials = { username: $("login-username").value.trim(), password: $("login-password").value };
  try {
    const response = await fetch(`/api/v1/auth/${mode}`, jsonRequest("POST", credentials));
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(body, `Não foi possível entrar (HTTP ${response.status}).`));
    $("login-password").value = "";
    showApp(body.username);
  } catch (error) {
    notice.textContent = error.message;
  }
}

$("loginForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitCredentials("login");
});
$("register-button").addEventListener("click", () => submitCredentials("register"));

$("logout-button").addEventListener("click", async () => {
  if (eventSource) finishAudit(currentJobId, false);
  await fetch("/api/v1/auth/logout", { method: "POST" }).catch(() => {});
  panel.hidden = true;
  showLogin();
});

/* =========================================================
   Views
   ========================================================= */

const VIEWS = ["dashboard", "audit", "metrics", "profiles"];

function showView(name) {
  for (const view of VIEWS) {
    $(`view-${view}`).hidden = view !== name;
    $(`tab-${view}`).classList.toggle("active", view === name);
  }
  if (name === "metrics") loadMetrics();
  if (name === "profiles") loadProfiles();
  if (name === "dashboard") {
    loadHistory();
    loadMetrics();
  }
}

for (const view of VIEWS) {
  $(`tab-${view}`).addEventListener("click", () => showView(view));
}
$("dash-start-button").addEventListener("click", () => showView("audit"));

/* =========================================================
   Resolution
   ========================================================= */

const RESOLUTIONS = {
  "200": "≈ 3,9 megapixels por página A4. O mais rápido; dígitos pequenos ou apagados podem perder detalhe.",
  "300": "≈ 8,7 megapixels por página A4, 2,3× o trabalho de 200 DPI. Resolução recomendada pelo Tesseract para texto impresso.",
  "500": "≈ 24 megapixels por página A4, 6× o trabalho de 200 DPI. O mais lento; só ajuda se a digitalização tiver esse nível de detalhe.",
};

function updateDpiHelp() {
  $("dpi-help").textContent = RESOLUTIONS[$("dpi-select").value] || "";
}

$("dpi-select").addEventListener("change", updateDpiHelp);
updateDpiHelp();

/* =========================================================
   Live audit
   ========================================================= */

auditForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  notice.textContent = "";

  const data = new FormData(auditForm);
  const dpi = data.get("dpi") || 500;
  const profile = data.get("profile") || $("profile-select").value || "padrao";
  data.delete("dpi");
  data.delete("profile");

  startButton.disabled = true;
  startButton.textContent = "Enviando documentos…";

  try {
    const response = await api(`/api/v1/audits?dpi=${dpi}&profile=${encodeURIComponent(profile)}`, { method: "POST", body: data });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(errorMessage(error, `Não foi possível iniciar (HTTP ${response.status}).`));
    }
    const { job_id } = await response.json();
    resetPanel();
    followAudit(job_id);
  } catch (error) {
    notice.textContent = error.message;
    startButton.disabled = false;
    startButton.textContent = "Iniciar auditoria";
  }
});

const PANEL_STATES = {
  live: ["Auditoria em andamento", "Ao vivo", "live"],
  done: ["Auditoria concluída", "Concluída", "ok"],
  stopped: ["Auditoria interrompida", "Interrompida", "error"],
  history: ["Relatório da auditoria", "Histórico", ""],
};

function setPanelState(state) {
  const [title, label, className] = PANEL_STATES[state];
  $("panel-title").textContent = title;
  const tag = $("live-tag");
  tag.textContent = label;
  for (const name of ["live", "ok", "error"]) tag.classList.toggle(name, name === className);
}

function resetPanel() {
  clearInterval(timerInterval);
  elapsedSeconds = 0;
  $("ind-time").textContent = "00:00";
  $("ind-page-time").textContent = "—";
  pages.clear();
  selectedPage = null;
  tableBody.innerHTML = "";
  counters = { processed: 0, matched: 0, mismatched: 0, total: null };
  updateIndicators();
  progressBar.style.width = "0%";
  viewer.innerHTML = '<p class="empty">Aguardando a primeira página…</p>';
  $("comparison").hidden = true;
  $("evidence").hidden = true;
  $("review-box").hidden = true;
  $("correction-box").hidden = true;
  $("tag-strategy").hidden = true;
  $("strategy-dots").hidden = true;
  $("caption-viewer").hidden = true;
  $("link-csv").hidden = true;
  $("link-report").hidden = true;
  renderMissing([]);
  setFilter("all");

  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function followAudit(jobId) {
  currentJobId = jobId;
  setPanelState("live");
  statusText.textContent = "Convertendo o PDF e lendo a lista de consulta…";
  startButton.textContent = "Auditoria em andamento…";

  timerInterval = setInterval(() => {
    elapsedSeconds++;
    $("ind-time").textContent = formatClock(elapsedSeconds);
    if (counters.processed) {
      $("ind-page-time").textContent = decimal(elapsedSeconds / counters.processed);
    }
  }, 1000);

  eventSource = new EventSource(`/api/v1/audits/${jobId}/events`);

  eventSource.onmessage = (message) => {
    const event = JSON.parse(message.data);

    if (event.type === "start") {
      counters.total = event.total_pages;
      statusText.textContent = `${event.total_pages} guias encontradas · ${event.reference_count} lançamentos na consulta · ${event.dpi} DPI`;
      $("active-resolution").textContent = `${event.dpi} DPI`;
      $("active-profile").textContent = profileName(event.profile);
      updateIndicators();
    } else if (event.type === "page") {
      registerPage(event);
    } else if (event.type === "error") {
      statusText.textContent = "";
      notice.textContent = `Erro durante a auditoria: ${event.message}`;
      finishAudit(jobId, false);
    } else if (event.type === "end") {
      const seconds = event.elapsed_seconds;
      statusText.textContent = `Auditoria concluída — ${counters.processed} guias conferidas` +
        (seconds != null ? ` em ${decimal(seconds)} s.` : ".");
      if (seconds != null) {
        // Prefer the server's measurement to the browser's one-second ticks.
        $("ind-time").textContent = formatClock(seconds);
        if (counters.processed) $("ind-page-time").textContent = decimal(seconds / counters.processed);
      }
      renderMissing(event.missing || []);
      finishAudit(jobId, true);
    }
  };

  eventSource.onerror = () => {
    // Close the session explicitly when the event stream has closed.
    if (eventSource && eventSource.readyState === EventSource.CLOSED) {
      finishAudit(jobId, counters.processed > 0);
    }
  };
}

function finishAudit(jobId, hasResults) {
  clearInterval(timerInterval);
  if (eventSource) setPanelState(hasResults ? "done" : "stopped");
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  startButton.disabled = false;
  startButton.textContent = "Iniciar nova auditoria";
  if (hasResults) {
    showCsvLink(jobId);
  }
  loadHistory();
}

function showCsvLink(jobId) {
  const link = $("link-csv");
  link.href = `/api/v1/audits/${jobId}/report.csv`;
  link.hidden = false;
  const report = $("link-report");
  report.href = `/api/v1/audits/${jobId}/report.html`;
  report.hidden = false;
}

function renderMissing(entries) {
  $("missing-block").hidden = entries.length === 0;
  $("missing-list").innerHTML = entries.map((entry) =>
    `<li>Linha ${escapeHtml(entry.line)}: código <strong>${escapeHtml(entry.code)}</strong>, valor ${escapeHtml(entry.amount)}</li>`
  ).join("");
}

/* =========================================================
   History — stored audit results
   ========================================================= */

const STATUS_LABELS = {
  completed: ["Concluída", "ok"],
  processing: ["Em andamento", "live"],
  abandoned: ["Interrompida", ""],
  error: ["Falhou", "error"],
};

function formatTimestamp(iso) {
  const data = new Date(iso);
  if (isNaN(data)) return iso;
  const twoDigits = (n) => String(n).padStart(2, "0");
  return `${twoDigits(data.getDate())}/${twoDigits(data.getMonth() + 1)}/${data.getFullYear()}` +
    ` ${twoDigits(data.getHours())}:${twoDigits(data.getMinutes())}`;
}

function formatClock(seconds) {
  const rounded = Math.round(seconds);
  return `${String(Math.floor(rounded / 60)).padStart(2, "0")}:${String(rounded % 60).padStart(2, "0")}`;
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${decimal(seconds)} s`;
  return `${Math.floor(seconds / 60)} min ${String(Math.round(seconds % 60)).padStart(2, "0")} s`;
}

async function loadHistory() {
  let audits;
  try {
    const response = await api("/api/v1/audits");
    if (!response.ok) return;
    audits = await response.json();
  } catch {
    return; // history failure must not block live verification
  }

  const body = $("body-history");
  body.innerHTML = "";
  $("history-empty").hidden = audits.length > 0;
  renderDashboardAudits(audits);

  for (const audit of audits) {
    const evaluated = audit.matched + audit.mismatched;
    const rate = percent(audit.matched, evaluated);
    const [label, className] = STATUS_LABELS[audit.status] || [audit.status, ""];

    const tr = document.createElement("tr");
    tr.className = "row-history";
    tr.innerHTML = `
      <td>${formatTimestamp(audit.created_at)}</td>
      <td><span class="file-name">${escapeHtml(audit.payment_slips_file || "—")}</span>
        <span class="profile-name">${escapeHtml(profileName(audit.profile))}</span></td>
      <td><span class="badge ${className}">${escapeHtml(label)}</span></td>
      <td class="numeric">${audit.dpi ?? "—"}</td>
      <td class="numeric">${audit.processed_pages}${audit.total_pages ? ` / ${audit.total_pages}` : ""}</td>
      <td class="numeric">${audit.matched}</td>
      <td class="numeric ${audit.mismatched ? "discrepancy" : ""}">${audit.mismatched}</td>
      <td>${rateCell(audit.matched, evaluated, rate)}</td>
      <td>${formatDuration(audit.duration_seconds)}</td>
    `;
    tr.addEventListener("click", () => openAudit(audit));
    body.appendChild(tr);
  }
}

async function openAudit(audit) {
  if (eventSource) return; // preserve the active audit session

  let rows;
  let missing = [];
  try {
    const response = await api(`/api/v1/audits/${audit.job_id}/pages`);
    if (!response.ok) throw new Error("Não foi possível carregar esta auditoria.");
    rows = await response.json();
    const missingResponse = await api(`/api/v1/audits/${audit.job_id}/missing`);
    if (missingResponse.ok) missing = await missingResponse.json();
  } catch (error) {
    notice.textContent = error.message;
    return;
  }

  showView("audit");
  resetPanel();
  setPanelState("history");
  currentJobId = audit.job_id;
  counters.total = audit.total_pages;
  $("active-resolution").textContent = audit.dpi ? `${audit.dpi} DPI` : "—";
  $("active-profile").textContent = profileName(audit.profile);

  for (const row of rows) {
    // Historical rows have no images; previews are generated only during a live audit.
    const event = { page: row["Page"], row };
    const mismatch = row["Overall Status"] === "ERROR";

    counters.processed += 1;
    if (row["Overall Status"] === "OK") counters.matched += 1;
    if (mismatch) counters.mismatched += 1;

    pages.set(event.page, event);
    addRow(event, mismatch);
  }

  updateIndicators();
  renderMissing(missing);
  progressBar.style.width = "100%";
  statusText.textContent = `Auditoria de ${formatTimestamp(audit.created_at)} — ` +
    `${counters.processed} guias, reaberta do histórico.`;
  if (audit.duration_seconds != null) {
    $("ind-time").textContent = formatClock(audit.duration_seconds);
    if (counters.processed) {
      $("ind-page-time").textContent = decimal(audit.duration_seconds / counters.processed);
    }
  }

  if (rows.length) {
    showCsvLink(audit.job_id);
    showPage(rows[0]["Page"]);
  }

  // Replace the generic preview message after displaying the first historical row.
  viewer.innerHTML = '<p class="empty">As imagens com os destaques do OCR não são guardadas:<br>' +
    'elas existem apenas durante a conferência ao vivo.</p>';
  $("caption-viewer").hidden = true;
}

$("refresh-history-button").addEventListener("click", loadHistory);

function registerPage(event) {
  const row = event.row;
  const mismatch = row["Overall Status"] === "ERROR";

  counters.processed += 1;
  if (row["Overall Status"] === "OK") counters.matched += 1;
  if (mismatch) counters.mismatched += 1;
  updateIndicators();

  if (counters.total) {
    progressBar.style.width = `${(counters.processed / counters.total) * 100}%`;
    statusText.textContent = `Conferindo… página ${event.page} de ${counters.total}`;
  }

  pages.set(event.page, event);
  addRow(event, mismatch);
  showPage(event.page);
}

function rowMarkup(event) {
  const row = event.row;
  const codeMismatch = row["Code Status"] !== "OK";
  const amountMismatch = row["Amount Status"] !== "OK";
  return `
    <td>${event.page}</td>
    <td>${escapeHtml(row["Code (Reference PDF)"])}</td>
    <td class="${codeMismatch ? "discrepancy" : ""}">${escapeHtml(row["Code (OCR Slips)"])}</td>
    <td>${escapeHtml(row["Amount (Reference PDF)"])}</td>
    <td class="${amountMismatch ? "discrepancy" : ""}">${escapeHtml(row["Amount (OCR Slips)"])}</td>
    <td>${badge(row["Overall Status"])}${categoryBadge(row["Category"])}${reviewBadge(row["Review"])}${correctedBadge(row)}</td>
  `;
}

function addRow(event, mismatch) {
  const tr = document.createElement("tr");
  tr.dataset.page = event.page;
  tr.dataset.mismatch = mismatch ? "1" : "0";
  if (mismatch) tr.classList.add("mismatch");
  tr.hidden = !FILTERS[activeFilter](event.row);
  tr.innerHTML = rowMarkup(event);
  tr.addEventListener("click", () => showPage(event.page));
  event.element = tr;
  tableBody.appendChild(tr);
  updateFilterCounts();
}

// Report statuses from the API, with their badge text and style.
const STATUS_BADGES = {
  "OK": ["Conforme", "ok"],
  "ERROR": ["Divergente", "error"],
  "MISMATCH": ["Divergente", "error"],
  "END OF LIST": ["Fim da lista", ""],
};

function badge(status) {
  const [text, className] = STATUS_BADGES[status] || [status, ""];
  return `<span class="badge ${className}">${escapeHtml(text)}</span>`;
}

// Discrepancy categories and suggested operator actions.
const CATEGORIES = {
  "DUPLICATE": ["Duplicada", "severe", "Outra página já foi pareada com esta matrícula. Verifique se a guia está repetida, ou se o código foi lido como outro deste lote."],
  "LIKELY MISREAD": ["Provável erro de leitura", "misread", "O código não está na lista de consulta, mas o valor confere com a guia esperada nesta posição. O OCR provavelmente leu o código errado — confirme na imagem."],
  "UNREAD": ["Não lido", "unread", "O OCR não extraiu este dado. Confira a qualidade da digitalização ou tente outro DPI."],
  "REVIEW": ["Verificar", "", "A leitura difere do lançamento da consulta. Compare o campo destacado com a guia original."],
  // Reports created before slips were matched by code.
  "OTHER REGISTRATION": ["Outro cadastro", "severe", "O código lido pertence a outra guia deste lote — pode ser guia trocada."],
};

function categoryBadge(category) {
  const info = CATEGORIES[category];
  if (!info) return "";
  const [text, className, explanation] = info;
  return `<span class="category ${className}" title="${escapeHtml(explanation)}">${text}</span>`;
}

const REVIEWS = {
  DOCUMENT: ["Divergência real", "document"],
  OCR: ["Erro do OCR", "ocr"],
};

function reviewBadge(verdict) {
  const info = REVIEWS[verdict];
  return info ? `<span class="review-badge ${info[1]}">${info[0]}</span>` : "";
}

// Strategy names come from the engine in English.
const STRATEGY_NAMES = {
  "1. Default": "1. Padrão",
  "2. Dark": "2. Escuro",
  "3. Sharpness": "3. Nitidez",
  "4. Original": "4. Original",
  "5. Zoom 2x": "5. Zoom 2x",
  "6. High Threshold": "6. Limiar alto",
  "7. Contrast": "7. Contraste",
  "8. Thicken": "8. Engrossar",
};

function evidenceText(event) {
  const row = event.row;
  const parts = [];
  const strategies = row["Strategies Run"];
  for (const [label, field] of [["Código", "Code"], ["Valor", "Amount"]]) {
    const confidence = row[`${field} Confidence`];
    const votes = row[`${field} Votes`];
    const details = [];
    if (confidence != null) details.push(`confiança ${confidence}%`);
    if (votes != null && strategies) details.push(`${votes}/${strategies} estratégias concordam`);
    if (details.length) parts.push(`${label}: ${details.join(", ")}`);
  }
  if (row["OCR ms"] != null) {
    parts.push(`Tempo da página ${decimal((row["Render ms"] + row["OCR ms"]) / 1000)} s`);
  }
  if (row["Matched By"] === "POSITION" && row["Overall Status"] !== "END OF LIST") {
    parts.push(`Pareada pela posição com a linha ${row["Reference Line"]} da consulta`);
  } else if (row["Matched By"] === "SIMILAR CODE") {
    parts.push(`O código lido é de outra linha, já pareada; pareada com a linha ${row["Reference Line"]}, de mesmo valor e código com um dígito diferente`);
  } else if (row["Matched By"] === "AMOUNT") {
    parts.push(`Código não conferiu; pareada pelo valor com a linha ${row["Reference Line"]} da consulta`);
  } else if (row["Reference Line"] != null && row["Reference Line"] !== event.page) {
    parts.push(`Pareada com a linha ${row["Reference Line"]} da consulta`);
  }
  return parts.join(" · ");
}

function showPage(number) {
  const event = pages.get(number);
  if (!event) return;
  selectedPage = number;
  const row = event.row;

  document.querySelectorAll("#body-table tr").forEach((tr) => {
    tr.classList.toggle("selected", Number(tr.dataset.page) === number);
  });

  $("comparison").hidden = false;
  $("cmp-code-expected").textContent = row["Code (Reference PDF)"];
  $("cmp-code-read").innerHTML = diffMarkup(row["Code (Reference PDF)"], row["Code (OCR Slips)"]);
  $("cmp-code-badge").outerHTML = badge(row["Code Status"]).replace('class="badge', 'id="cmp-code-badge" class="badge');
  $("cmp-amount-expected").textContent = row["Amount (Reference PDF)"];
  $("cmp-amount-read").innerHTML = diffMarkup(row["Amount (Reference PDF)"], row["Amount (OCR Slips)"]);
  $("cmp-amount-badge").outerHTML = badge(row["Amount Status"]).replace('class="badge', 'id="cmp-amount-badge" class="badge');

  renderStrategyDots(row);
  if (event.element && event.element.scrollIntoView) event.element.scrollIntoView({ block: "nearest" });

  const evidence = evidenceText(event);
  $("evidence").textContent = evidence;
  $("evidence").hidden = !evidence;

  const explanation = $("explanation-category");
  const categoryInfo = CATEGORIES[row["Category"]];
  if (categoryInfo) {
    const [text, className, detail] = categoryInfo;
    explanation.className = `explanation-category ${className}`;
    explanation.innerHTML = `<strong>${text}:</strong> ${escapeHtml(detail)}`;
    explanation.hidden = false;
  } else {
    explanation.hidden = true;
  }

  showReview(row);
  showCorrection(row);

  const tag = $("tag-strategy");
  if (event.strategy) {
    tag.textContent = `Estratégia vencedora: ${STRATEGY_NAMES[event.strategy] || event.strategy}`;
    tag.hidden = false;
  } else {
    tag.hidden = true;
  }

  if (event.image) {
    viewer.innerHTML = `<img src="${event.image}" alt="Guia da página ${number} com os campos extraídos destacados">`;
  } else {
    viewer.innerHTML = '<p class="empty">Imagem indisponível para esta página.</p>';
  }

  const caption = $("caption-viewer");
  const notLocated = [];
  if (!event.code_found) notLocated.push("código");
  if (!event.amount_found) notLocated.push("valor");
  if (event.image && notLocated.length) {
    caption.textContent = `Não foi possível localizar: ${notLocated.join(" e ")} — o OCR não encontrou este campo na página.`;
    caption.hidden = false;
  } else {
    caption.hidden = true;
  }
}

/* =========================================================
   Review — separate real discrepancies from OCR errors
   ========================================================= */

function showReview(row) {
  const flagged = row["Overall Status"] === "ERROR";
  $("review-box").hidden = !flagged;
  if (!flagged) return;
  $("review-document").classList.toggle("active", row["Review"] === "DOCUMENT");
  $("review-ocr").classList.toggle("active", row["Review"] === "OCR");
  $("review-status").textContent = row["Review"] ? "Salvo." : "Ainda não revisada.";
}

async function setReview(verdict) {
  const event = pages.get(selectedPage);
  if (!event || !currentJobId) return;
  $("review-status").textContent = "Salvando…";
  try {
    const response = await api(
      `/api/v1/audits/${currentJobId}/pages/${selectedPage}/review`, jsonRequest("PUT", { verdict })
    );
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(body, "Não foi possível salvar a revisão."));
    event.row = body;
    if (event.element) {
      event.element.innerHTML = rowMarkup(event);
      event.element.hidden = !FILTERS[activeFilter](event.row);
    }
    showReview(event.row);
    updateFilterCounts();
  } catch (error) {
    $("review-status").textContent = error.message;
  }
}

/* =========================================================
   Manual correction — the reviewer picks what is really printed
   ========================================================= */

function isCorrected(row) {
  return Boolean(row["Corrected Code"] || row["Corrected Amount"]);
}

function correctedBadge(row) {
  if (!isCorrected(row)) return "";
  const who = row["Corrected By"] ? ` por ${row["Corrected By"]}` : "";
  return `<span class="corrected-badge" title="Corrigida${escapeHtml(who)}">Corrigida</span>`;
}

function readingCell(field, value, expected, current) {
  if (!value) return '<span class="reading-empty">não leu</span>';
  const same = value === expected ? '<span class="same">= lista</span>' : "";
  const chosen = value === current ? " chosen" : "";
  return `<button type="button" class="reading-pick${chosen}" data-field="${field}" data-value="${escapeHtml(value)}"
    title="Usar esta leitura">${escapeHtml(value)}${same}</button>`;
}

function renderReadings() {
  const row = pages.get(selectedPage)?.row;
  if (!row) return;
  const code = $("correction-code").value;
  const amount = $("correction-amount").value;
  const readings = row["Readings"] || [];
  $("readings-body").innerHTML = readings.length
    ? readings.map((reading) => `<tr>
        <td>${escapeHtml(STRATEGY_NAMES[reading.strategy] || reading.strategy)}</td>
        <td>${readingCell("code", reading.code, row["Code (Reference PDF)"], code)}</td>
        <td>${readingCell("amount", reading.amount, row["Amount (Reference PDF)"], amount)}</td>
      </tr>`).join("")
    : '<tr><td colspan="3">As leituras de cada estratégia não foram registradas nesta auditoria.</td></tr>';
}

function showCorrection(row) {
  const flagged = row["Overall Status"] === "ERROR";
  $("correction-box").hidden = !flagged;
  if (!flagged) return;
  $("correction-code").value = row["Corrected Code"] || row["Code (OCR Slips)"] || "";
  $("correction-amount").value = row["Corrected Amount"] || row["Amount (OCR Slips)"] || "";
  $("correction-clear").hidden = !isCorrected(row);
  $("correction-status").textContent = isCorrected(row)
    ? `Corrigida${row["Corrected By"] ? ` por ${row["Corrected By"]}` : ""}. O OCR tinha lido ${row["Code (OCR Slips)"]} · ${row["Amount (OCR Slips)"]}.`
    : "";
  renderReadings();
}

function applyRowUpdate(event, row) {
  event.row = row;
  if (event.element) {
    event.element.innerHTML = rowMarkup(event);
    event.element.hidden = !FILTERS[activeFilter](event.row);
  }
  showReview(event.row);
  showCorrection(event.row);
  updateFilterCounts();
}

async function saveCorrection(clear) {
  const event = pages.get(selectedPage);
  if (!event || !currentJobId) return;
  const row = event.row;
  const code = $("correction-code").value.trim();
  const amount = $("correction-amount").value.trim();
  // Only fields that differ from the OCR reading are corrections.
  const body = clear ? { code: null, amount: null } : {
    code: code && code !== row["Code (OCR Slips)"] ? code : null,
    amount: amount && amount !== row["Amount (OCR Slips)"] ? amount : null,
  };
  if (!clear && body.code === null && body.amount === null) {
    $("correction-status").textContent = "Os valores são iguais à leitura do OCR: não há o que corrigir.";
    return;
  }
  $("correction-status").textContent = "Salvando…";
  try {
    const response = await api(
      `/api/v1/audits/${currentJobId}/pages/${selectedPage}/correction`, jsonRequest("PUT", body)
    );
    const saved = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(saved, "Não foi possível salvar a correção."));
    applyRowUpdate(event, saved);
  } catch (error) {
    $("correction-status").textContent = error.message;
  }
}

$("readings-body").addEventListener("click", (click) => {
  const button = click.target && click.target.closest ? click.target.closest(".reading-pick") : null;
  if (!button) return;
  $(button.dataset.field === "code" ? "correction-code" : "correction-amount").value = button.dataset.value;
  renderReadings();
});
$("correction-save").addEventListener("click", () => saveCorrection(false));
$("correction-clear").addEventListener("click", () => saveCorrection(true));

$("review-document").addEventListener("click", () => setReview("DOCUMENT"));
$("review-ocr").addEventListener("click", () => setReview("OCR"));
$("review-clear").addEventListener("click", () => setReview(null));

function updateIndicators() {
  $("ind-processed").textContent = counters.total
    ? `${counters.processed}/${counters.total}`
    : counters.processed;
  $("ind-matched").textContent = counters.matched;
  $("ind-mismatched").textContent = counters.mismatched;
  $("ind-match-rate").textContent = percent(counters.matched, counters.matched + counters.mismatched);

  const share = counters.total ? Math.min(counters.processed / counters.total, 1) : 0;
  $("progress-ring-fill").style.strokeDashoffset = (RING_LENGTH * (1 - share)).toFixed(1);
  $("progress-percent").textContent = counters.total ? `${Math.round(share * 100)}%` : "—";
  $("progress-count").textContent = counters.total ? `${counters.processed} de ${counters.total}` : "aguardando";
  $("progress-ring").classList.toggle("done", counters.total > 0 && counters.processed >= counters.total);
}

const RING_LENGTH = 339.3; // circumference of the progress ring (r = 54)

/* =========================================================
   Report filters, navigation, and keyboard shortcuts
   ========================================================= */

const isFlagged = (row) => row["Overall Status"] === "ERROR";

// Each filter chip shows the rows its predicate accepts.
const FILTERS = {
  all: () => true,
  mismatch: isFlagged,
  pending: (row) => isFlagged(row) && !row["Review"],
  misread: (row) => row["Category"] === "LIKELY MISREAD",
  review: (row) => row["Category"] === "REVIEW",
  unread: (row) => row["Category"] === "UNREAD",
  duplicate: (row) => row["Category"] === "DUPLICATE",
};
let activeFilter = "all";

function setFilter(name) {
  activeFilter = name;
  for (const key of Object.keys(FILTERS)) $(`filter-${key}`).classList.toggle("active", key === name);
  for (const event of pages.values()) {
    if (event.element) event.element.hidden = !FILTERS[name](event.row);
  }
  updateFilterCounts();
}

function updateFilterCounts() {
  for (const [key, accepts] of Object.entries(FILTERS)) {
    let count = 0;
    for (const event of pages.values()) if (accepts(event.row)) count += 1;
    $(`count-${key}`).textContent = count;
  }
}

for (const key of Object.keys(FILTERS)) {
  $(`filter-${key}`).addEventListener("click", () => setFilter(key));
}

/** Report pages the current filter shows, in page order. */
function visiblePages() {
  return [...pages.values()]
    .filter((event) => event.element && !event.element.hidden)
    .map((event) => event.page)
    .sort((a, b) => a - b);
}

function moveSelection(step) {
  const list = visiblePages();
  if (!list.length) return;
  const index = list.indexOf(selectedPage);
  const next = index === -1 ? list[step > 0 ? 0 : list.length - 1] : list[index + step];
  if (next != null) showPage(next);
}

function nextPending() {
  const pending = [...pages.values()]
    .filter((event) => FILTERS.pending(event.row))
    .map((event) => event.page)
    .sort((a, b) => a - b);
  if (!pending.length) {
    $("review-status").textContent = "Nenhuma divergência pendente de parecer.";
    return;
  }
  showPage(pending.find((page) => page > selectedPage) ?? pending[0]);
}

$("next-pending").addEventListener("click", nextPending);

// J/K move through the visible rows; R/O record the verdict of a flagged page.
function onShortcut(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (/^(INPUT|SELECT|TEXTAREA|BUTTON|A|SUMMARY)$/.test(event.target?.tagName || "")) return;
  if (panel.hidden || $("view-audit").hidden) return;
  const reviewable = !$("review-box").hidden;
  const actions = {
    j: () => moveSelection(1),
    k: () => moveSelection(-1),
    r: () => reviewable && setReview("DOCUMENT"),
    o: () => reviewable && setReview("OCR"),
  };
  const action = actions[String(event.key).toLowerCase()];
  if (!action) return;
  event.preventDefault();
  action();
}

if (typeof document.addEventListener === "function") document.addEventListener("keydown", onShortcut);

/** Highlight the characters the OCR read differently from the reference. */
function diffMarkup(expected, read) {
  const text = String(read ?? "");
  const reference = String(expected ?? "");
  if (!text || text === reference) return escapeHtml(text || "—");
  if (text.length !== reference.length) return `<span class="diff">${escapeHtml(text)}</span>`;
  return [...text].map((character, index) => (
    character === reference[index] ? escapeHtml(character) : `<span class="diff">${escapeHtml(character)}</span>`
  )).join("");
}

/** One square per image strategy; filled squares agreed on the code. */
function renderStrategyDots(row) {
  const box = $("strategy-dots");
  const strategies = row["Strategies Run"];
  if (!strategies) {
    box.hidden = true;
    return;
  }
  const votes = Math.max(row["Code Votes"] ?? 0, 0);
  const flagged = isFlagged(row) ? " flagged" : "";
  if (strategies === 1) {
    box.innerHTML = `<span class="dots-label">Leitura</span><span class="dot agree${flagged}"></span>` +
      '<span class="dots-note">1ª leitura já pareada com a consulta; consenso não foi necessário.</span>';
  } else {
    const dots = Array.from({ length: strategies }, (_, index) => (
      `<span class="dot${index < votes ? ` agree${flagged}` : ""}" style="animation-delay: ${index * 40}ms"></span>`
    )).join("");
    box.innerHTML = `<span class="dots-label">Consenso</span>${dots}` +
      `<span class="dots-note">${votes} de ${strategies} estratégias concordaram no código</span>`;
  }
  box.hidden = false;
}

/** Conformance as a small bar plus its percentage. */
function rateCell(matched, evaluated, label) {
  if (!evaluated) return label;
  const share = (matched / evaluated) * 100;
  return `<div class="rate-cell"><div class="rate-track"><div class="rate-fill${share < 97 ? " low" : ""}"` +
    ` style="width: ${share.toFixed(1)}%"></div></div><span class="rate-value">${label}</span></div>`;
}

/* =========================================================
   Dashboard
   ========================================================= */

const WEEKDAYS = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];

/** "1 página" or "3 páginas": the count followed by the matching form. */
function plural(count, singular, pluralForm) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

/** Local calendar day of a timestamp, as YYYY-MM-DD. */
function dayKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (isNaN(date)) return String(value).slice(0, 10);
  const twoDigits = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${twoDigits(date.getMonth() + 1)}-${twoDigits(date.getDate())}`;
}

/** A round axis maximum for four gridline steps. */
function niceMaximum(value) {
  const step = value / 4;
  const magnitude = 10 ** Math.floor(Math.log10(step));
  const nice = [1, 2, 2.5, 5, 10].find((multiple) => multiple * magnitude >= step) * magnitude;
  return nice * 4;
}

function renderDashboardAudits(audits) {
  const now = new Date();
  const today = dayKey(now);
  const todays = audits.filter((audit) => dayKey(audit.created_at) === today);
  const running = todays.filter((audit) => audit.status === "processing").length;
  const matched = todays.reduce((total, audit) => total + audit.matched, 0);
  const evaluated = todays.reduce((total, audit) => total + audit.matched + audit.mismatched, 0);

  $("dash-today-audits").textContent = todays.length;
  $("dash-today-audits-note").textContent = todays.length
    ? [plural(todays.length - running, "concluída", "concluídas"), running ? `${running} em andamento` : ""]
      .filter(Boolean).join(" · ")
    : "Nenhuma auditoria hoje";
  $("dash-today-rate").textContent = percent(matched, evaluated);
  $("dash-today-rate-note").textContent = evaluated
    ? `${matched} de ${plural(evaluated, "página confere", "páginas conferem")}`
    : "Sem páginas conferidas hoje";

  // Seven calendar days, oldest first, including days without audits.
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (6 - index));
    const key = dayKey(date);
    const ofDay = audits.filter((audit) => dayKey(audit.created_at) === key);
    return {
      label: index === 6 ? "Hoje" : `${WEEKDAYS[date.getDay()]} ${String(date.getDate()).padStart(2, "0")}/${String(date.getMonth() + 1).padStart(2, "0")}`,
      pages: ofDay.reduce((total, audit) => total + audit.processed_pages, 0),
      divergences: ofDay.reduce((total, audit) => total + audit.mismatched, 0),
      today: index === 6,
    };
  });

  const totalPages = days.reduce((total, day) => total + day.pages, 0);
  const totalDivergences = days.reduce((total, day) => total + day.divergences, 0);
  $("dash-days-subtitle").textContent = totalPages
    ? `Últimos 7 dias · ${totalPages} páginas · ${percent(totalPages - totalDivergences, totalPages)} conformes`
    : "Últimos 7 dias";

  if (!totalPages) {
    $("dash-days").innerHTML = '<p class="empty">Nenhuma página conferida nos últimos 7 dias.</p>';
    return;
  }

  const top = niceMaximum(Math.max(...days.map((day) => day.pages)));
  const topDivergences = Math.max(...days.map((day) => day.divergences), 1);
  const axis = [4, 3, 2, 1, 0].map((step) => `<span>${Math.round((top * step) / 4)}</span>`).join("");
  const bars = days.map((day, index) => `
    <div class="day-column">
      ${day.pages ? `<span class="day-value">${day.pages}</span>` : ""}
      <div class="day-bar${day.today ? " today" : ""}" title="${escapeHtml(day.label)}: ${day.pages} páginas, ${day.divergences} divergências"
        style="height: ${((day.pages / top) * 100).toFixed(1)}%; animation-delay: ${index * 70}ms"></div>
    </div>`).join("");
  const divergenceBars = days.map((day, index) => `
    <div class="day-column">
      ${day.divergences ? `<span class="day-value divergence">${day.divergences}</span>` : ""}
      <div class="day-bar divergence" style="height: ${((day.divergences / topDivergences) * 70).toFixed(1)}%; animation-delay: ${index * 70}ms"></div>
    </div>`).join("");
  const labels = days.map((day) => `<span>${escapeHtml(day.label)}</span>`).join("");
  const describe = days.map((day) => `${day.label} ${day.pages}`).join(", ");

  $("dash-days").innerHTML = `
    <div class="day-chart">
      <div class="day-axis" aria-hidden="true">${axis}</div>
      <div class="day-plot">
        <div class="day-bars" role="img" aria-label="Páginas por dia: ${escapeHtml(describe)}">${bars}</div>
        <div class="day-divergences" role="img" aria-label="Divergências por dia">${divergenceBars}</div>
        <div class="day-labels" aria-hidden="true">${labels}</div>
      </div>
    </div>`;
}

function renderDashboardMetrics(metrics) {
  const sum = (key) => metrics.reduce((total, item) => total + (item[key] || 0), 0);
  const flagged = sum("flagged");
  const ocr = sum("ocr_errors");
  const documentCount = sum("document_discrepancies");
  const pending = sum("pending_review");

  // Average conversion + OCR time per page, weighted by the pages that were timed.
  const timed = metrics.filter((item) => pageMilliseconds(item) != null && item.timed_pages);
  const timedPages = timed.reduce((total, item) => total + item.timed_pages, 0);
  if (timedPages) {
    const milliseconds = timed.reduce((total, item) => total + pageMilliseconds(item) * item.timed_pages, 0) / timedPages;
    const busiest = timed.reduce((best, item) => (item.pages > best.pages ? item : best));
    $("dash-page-time").textContent = `${decimal(milliseconds / 1000)} s`;
    $("dash-page-time-note").textContent = `${plural(timedPages, "página medida", "páginas medidas")} · mais usado: ${busiest.dpi} DPI`;
  } else {
    $("dash-page-time").textContent = "—";
    $("dash-page-time-note").textContent = "Medido nas auditorias concluídas";
  }

  $("dash-pending").textContent = metrics.length ? pending : "—";
  $("dash-pending-note").textContent = flagged
    ? `${plural(flagged, "página sinalizada", "páginas sinalizadas")} · ${flagged - pending} com parecer`
    : "Nenhuma página sinalizada ainda";

  $("dash-verdicts").innerHTML = flagged ? `
    <span class="big-number">${flagged}</span> <span class="card-subtitle" style="display: inline">${flagged === 1 ? "página sinalizada" : "páginas sinalizadas"}</span>
    <div class="verdict-bar" role="img" aria-label="${ocr} erros do OCR, ${documentCount} divergências reais, ${pending} sem parecer">
      <div class="ocr" style="flex: ${ocr} 1 0"></div>
      <div class="document" style="flex: ${documentCount} 1 0"></div>
      <div class="pending" style="flex: ${pending} 1 0"></div>
    </div>
    <div class="legend-row">
      <span><i class="swatch ocr"></i>Erro do OCR <strong>${ocr}</strong></span>
      <span><i class="swatch document"></i>Divergência real <strong>${documentCount}</strong></span>
      <span><i class="swatch pending"></i>Sem parecer <strong>${pending}</strong></span>
    </div>
    <p class="info-note">Taxa de erro do OCR confirmada: ${percent(ocr, sum("pages"))} das páginas${pending ? " (limite inferior enquanto houver páginas sem parecer)" : ""}.</p>`
    : '<p class="empty">Nenhuma página sinalizada ainda.</p>';

  const longest = Math.max(...timed.map(pageMilliseconds), 1);
  $("dash-dpi").innerHTML = timed.map((item) => `
    <div class="bar-row">
      <span class="bar-label">${item.dpi} DPI</span>
      <div class="bar-track"><div class="bar-fill time" style="width: ${(pageMilliseconds(item) / longest) * 100}%"></div></div>
      <span class="bar-value">${seconds(pageMilliseconds(item))}</span>
    </div>`).join("") || '<p class="empty">Sem medições ainda.</p>';
}

/* =========================================================
   Metrics by resolution
   ========================================================= */

function percent(part, total) {
  return total ? `${decimal((part / total) * 100)}%` : "—";
}

function seconds(milliseconds) {
  return milliseconds == null ? "—" : `${decimal(milliseconds / 1000, 2)} s`;
}

function pageMilliseconds(item) {
  return item.avg_render_ms == null || item.avg_ocr_ms == null ? null : item.avg_render_ms + item.avg_ocr_ms;
}

function renderTimeChart(metrics) {
  const timed = metrics.filter((item) => pageMilliseconds(item) != null);
  const longest = Math.max(...timed.map(pageMilliseconds), 1);
  $("chart-time").innerHTML = timed.map((item) => {
    const value = pageMilliseconds(item);
    return `
      <div class="bar-row">
        <span class="bar-label">${item.dpi} DPI</span>
        <div class="bar-track"><div class="bar-fill time" style="width: ${(value / longest) * 100}%"></div></div>
        <span class="bar-value">${seconds(value)}</span>
      </div>`;
  }).join("") || '<p class="empty">Nenhuma página com tempo medido ainda.</p>';
}

function renderFlaggedChart(metrics) {
  const highest = Math.max(...metrics.map((item) => (item.pages ? item.flagged / item.pages : 0)), 0.0001);
  $("chart-flagged").innerHTML = metrics.map((item) => {
    const share = (count) => (item.pages ? (count / item.pages / highest) * 100 : 0);
    return `
      <div class="bar-row">
        <span class="bar-label">${item.dpi} DPI</span>
        <div class="bar-track">
          <div class="bar-fill document" style="width: ${share(item.document_discrepancies)}%"></div>
          <div class="bar-fill ocr" style="width: ${share(item.ocr_errors)}%"></div>
          <div class="bar-fill pending" style="width: ${share(item.pending_review)}%"></div>
        </div>
        <span class="bar-value">${percent(item.flagged, item.pages)}</span>
      </div>`;
  }).join("");
}

async function loadMetrics() {
  let metrics;
  try {
    const response = await api("/api/v1/metrics");
    if (!response.ok) return;
    metrics = await response.json();
  } catch {
    return;
  }

  renderDashboardMetrics(metrics);
  const empty = metrics.length === 0;
  $("metrics-empty").hidden = !empty;
  $("metrics-charts").hidden = empty;
  $("metrics-table-wrapper").hidden = empty;
  if (empty) return;

  renderTimeChart(metrics);
  renderFlaggedChart(metrics);

  $("body-metrics").innerHTML = metrics.map((item) => {
    const confidence = [item.avg_code_confidence, item.avg_amount_confidence]
      .map((value) => (value == null ? "—" : `${Math.round(value)}%`)).join(" / ");
    const errorRate = percent(item.ocr_errors, item.pages) + (item.pending_review ? " (limite inferior)" : "");
    return `
      <tr>
        <td><strong>${item.dpi ?? "—"}</strong></td>
        <td>${item.audits}</td>
        <td>${item.pages}</td>
        <td>${seconds(pageMilliseconds(item))}</td>
        <td>${item.wall_seconds_per_page == null ? "—" : `${decimal(item.wall_seconds_per_page, 2)} s`}</td>
        <td>${percent(item.consensus_pages, item.pages)}</td>
        <td>${item.flagged}</td>
        <td>${item.document_discrepancies}</td>
        <td class="${item.ocr_errors ? "discrepancy" : ""}">${item.ocr_errors}</td>
        <td>${item.pending_review}</td>
        <td>${errorRate}</td>
        <td>${confidence}</td>
      </tr>`;
  }).join("");
}

$("refresh-metrics-button").addEventListener("click", loadMetrics);

/* =========================================================
   Document profiles: which layout of slip and master list to read
   ========================================================= */

let profileList = [{ id: "padrao", name: "Guia padrão (matrícula com -0)", description: "", built_in: true }];

function profileName(id) {
  const profile = profileList.find((item) => item.id === (id || "padrao"));
  return profile ? profile.name : (id || "Guia padrão");
}

async function loadProfiles() {
  try {
    const response = await api("/api/v1/profiles");
    if (!response.ok) return;
    profileList = await response.json();
  } catch {
    return;
  }
  const select = $("profile-select");
  const current = select.value || "padrao";
  select.innerHTML = profileList.map((profile) =>
    `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.name)}</option>`
  ).join("");
  select.value = profileList.some((profile) => profile.id === current) ? current : "padrao";

  $("body-profiles").innerHTML = profileList.map((profile) => `
    <tr>
      <td><strong>${escapeHtml(profile.name)}</strong><br><span class="profile-name">${escapeHtml(profile.id)}</span></td>
      <td>${escapeHtml(profile.description)}</td>
      <td><span class="${profile.built_in ? "badge" : "tag"}">${profile.built_in ? "Embutido" : "Personalizado"}</span></td>
      <td>${profile.built_in ? "" : `<button type="button" class="link-button" data-delete-profile="${escapeHtml(profile.id)}">Excluir</button>`}</td>
    </tr>`).join("");
}

$("body-profiles").addEventListener("click", async (event) => {
  const button = event.target?.closest?.("[data-delete-profile]");
  if (!button) return;
  const id = button.dataset.deleteProfile;
  if (!window.confirm(`Excluir o modelo "${profileName(id)}"? Auditorias antigas continuam no histórico.`)) return;
  const response = await api(`/api/v1/profiles/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (response.ok) loadProfiles();
});

/* ---------- setup assistant ---------- */

let draft = null;        // profile definition being built
let slipFile = null;     // the sample slip, kept to test again after drawing regions
let markMode = null;     // "code" or "amount" while drawing a region
let drawing = null;

function setStep(step) {
  for (const number of [1, 2, 3]) {
    $(`profile-step-${number}`).classList.toggle("active", number === step);
    $(`profile-step-${number}`).classList.toggle("done", number < step);
  }
}

function resetWizard() {
  draft = null;
  slipFile = null;
  setMarkMode(null);
  for (const id of ["profile-name", "profile-code-example", "profile-id",
    "profile-reference-pattern", "profile-slip-pattern", "profile-slip-allowed"]) $(id).value = "";
  for (const id of ["profile-reference-notice", "profile-slip-notice", "profile-notice"]) $(id).textContent = "";
  $("profile-reference-result").hidden = true;
  $("profile-region-editor").hidden = true;
  $("profile-boxes").innerHTML = "";
  $("profile-slip-result").innerHTML = "";
  setStep(1);
}

$("profile-new-button").addEventListener("click", () => {
  resetWizard();
  $("profile-wizard").hidden = false;
  $("profile-wizard").scrollIntoView({ behavior: "smooth", block: "start" });
});
$("profile-cancel-button").addEventListener("click", () => { $("profile-wizard").hidden = true; });

/** Copy the advanced fields into the draft, so manual edits are kept. */
function applyAdvanced() {
  if (!draft) return;
  const referencePattern = $("profile-reference-pattern").value.trim();
  const slipPattern = $("profile-slip-pattern").value.trim();
  const allowed = $("profile-slip-allowed").value.trim();
  if (referencePattern) draft.reference.code = referencePattern;
  if (slipPattern) draft.slip.code.pattern = slipPattern;
  if (allowed) draft.slip.code.allowed = allowed;
  const name = $("profile-name").value.trim();
  if (name) draft.name = name;
  const id = $("profile-id").value.trim();
  if (id) draft.id = id;
}

async function ensureDraft() {
  if (draft) {
    applyAdvanced();
    return draft;
  }
  const example = $("profile-code-example").value.trim();
  if (!example) throw new Error("Digite um código como ele aparece na lista mestre, por exemplo 54700-Z.");
  const response = await api("/api/v1/profiles/suggest",
    jsonRequest("POST", { name: $("profile-name").value.trim(), code_example: example }));
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(body, "Não foi possível gerar as regras."));
  draft = body;
  $("profile-reference-pattern").value = draft.reference.code;
  $("profile-slip-pattern").value = draft.slip.code.pattern;
  $("profile-slip-allowed").value = draft.slip.code.allowed;
  $("profile-id").value = draft.id;
  return draft;
}

$("profile-code-example").addEventListener("change", () => { draft = null; });

$("profile-reference-button").addEventListener("click", async () => {
  const notice = $("profile-reference-notice");
  notice.textContent = "";
  try {
    await ensureDraft();
    const file = $("profile-reference-file").files?.[0];
    if (!file) {
      notice.textContent = "Regras geradas. Envie um PDF da lista mestre para conferir se elas encontram os lançamentos.";
      setStep(2);
      return;
    }
    const form = new FormData();
    form.append("reference", file);
    form.append("definition", JSON.stringify(draft));
    const response = await api("/api/v1/profiles/reference-preview", { method: "POST", body: form });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(body, "Não foi possível ler a lista mestre."));

    if (body.pages && !body.text_pages) {
      // Scanned pages carry no text: this is almost always the slips PDF chosen by mistake.
      $("profile-reference-result").hidden = true;
      setStep(1);
      throw new Error(`"${file.name}" não tem texto selecionável: parece ser um PDF digitalizado, como o das guias. ` +
        "Aqui vai o PDF da lista mestre, que tem os códigos e valores em texto.");
    }
    if (!body.count) {
      $("profile-reference-result").hidden = true;
      setStep(1);
      throw new Error(`Nenhum código no formato "${$("profile-code-example").value.trim()}" foi encontrado em "${file.name}". ` +
        "Confira se o exemplo foi copiado exatamente como aparece na lista, ou ajuste as regras avançadas.");
    }
    $("profile-reference-summary").innerHTML =
      `<strong>${body.count}</strong> lançamentos encontrados · <strong>${body.complete}</strong> com código e valor`;
    $("body-profile-entries").innerHTML = body.entries.slice(0, 12).map((entry, index) => `
      <tr><td>${index + 1}</td><td>${escapeHtml(entry.code)}</td><td>${escapeHtml(entry.amount)}</td></tr>`).join("");
    $("profile-reference-result").hidden = false;
    setStep(2);
  } catch (error) {
    notice.textContent = error.message;
  }
});

function describeReading(body) {
  const votes = (count) => (count ? ` · ${count} de ${body.strategies_run} estratégias` : "");
  return `
    <div><i class="marker blue"></i> Código lido: <strong>${escapeHtml(body.code && body.code !== "0" ? body.code : "não encontrado")}</strong>${votes(body.code_votes)}</div>
    <div><i class="marker green"></i> Valor lido: <strong>${escapeHtml(body.amount && body.amount !== "0,00" ? body.amount : "não encontrado")}</strong>${votes(body.amount_votes)}</div>`;
}

async function testSlip() {
  const notice = $("profile-slip-notice");
  notice.textContent = "";
  try {
    await ensureDraft();
    slipFile = $("profile-slip-file").files?.[0] || slipFile;
    if (!slipFile) throw new Error("Escolha um PDF com pelo menos uma guia deste modelo.");
    $("profile-slip-result").innerHTML = '<span class="hint">Lendo a guia com as 8 estratégias…</span>';
    const form = new FormData();
    form.append("payment_slips", slipFile);
    form.append("definition", JSON.stringify(draft));
    const response = await api("/api/v1/profiles/slip-preview", { method: "POST", body: form });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(body, "Não foi possível ler a guia."));
    $("profile-page-image").src = body.annotated_image;
    $("profile-region-editor").hidden = false;
    $("profile-slip-result").innerHTML = describeReading(body);
    renderRegions();
    setStep(body.code && body.code !== "0" && body.amount && body.amount !== "0,00" ? 3 : 2);
  } catch (error) {
    $("profile-slip-result").innerHTML = "";
    notice.textContent = error.message;
  }
}

$("profile-slip-button").addEventListener("click", testSlip);
$("profile-test-button").addEventListener("click", testSlip);

/* Regions are drawn on the page image and stored as fractions of the page. */

function setMarkMode(mode) {
  markMode = mode;
  $("profile-mark-code").classList.toggle("active", mode === "code");
  $("profile-mark-amount").classList.toggle("active", mode === "amount");
  $("profile-stage").classList.toggle("drawing", Boolean(mode));
}

$("profile-mark-code").addEventListener("click", () => setMarkMode(markMode === "code" ? null : "code"));
$("profile-mark-amount").addEventListener("click", () => setMarkMode(markMode === "amount" ? null : "amount"));
$("profile-clear-regions").addEventListener("click", () => {
  if (!draft) return;
  draft.slip.code.region = null;
  draft.slip.amount.region = null;
  renderRegions();
});

function regionBox(region, kind, label) {
  if (!region) return "";
  const [left, top, right, bottom] = region.map((value) => value * 100);
  return `<div class="region-box ${kind}" style="left:${left}%;top:${top}%;width:${right - left}%;height:${bottom - top}%">
    <span>${label}</span></div>`;
}

function renderRegions(preview) {
  if (!draft) return;
  $("profile-boxes").innerHTML =
    regionBox(draft.slip.code.region, "code", "Código") +
    regionBox(draft.slip.amount.region, "amount", "Valor") +
    (preview ? regionBox(preview, `${markMode} preview`, "") : "");
}

function pointerFraction(event) {
  const rect = $("profile-stage").getBoundingClientRect();
  const clamp = (value) => Math.min(1, Math.max(0, value));
  return [clamp((event.clientX - rect.left) / rect.width), clamp((event.clientY - rect.top) / rect.height)];
}

function regionFrom(start, end) {
  const round = (value) => Math.round(value * 1000) / 1000;
  return [round(Math.min(start[0], end[0])), round(Math.min(start[1], end[1])),
    round(Math.max(start[0], end[0])), round(Math.max(start[1], end[1]))];
}

$("profile-stage").addEventListener("pointerdown", (event) => {
  if (!markMode || !draft) return;
  event.preventDefault();
  drawing = { start: pointerFraction(event) };
  $("profile-stage").setPointerCapture?.(event.pointerId);
});
$("profile-stage").addEventListener("pointermove", (event) => {
  if (!drawing) return;
  renderRegions(regionFrom(drawing.start, pointerFraction(event)));
});
$("profile-stage").addEventListener("pointerup", (event) => {
  if (!drawing) return;
  const region = regionFrom(drawing.start, pointerFraction(event));
  drawing = null;
  if (region[2] - region[0] > 0.01 && region[3] - region[1] > 0.01) {
    draft.slip[markMode].region = region;
    $("profile-mark-hint").textContent = "Região marcada. Teste a leitura ou marque o outro campo.";
  }
  setMarkMode(null);
  renderRegions();
});

$("profile-save-button").addEventListener("click", async () => {
  const notice = $("profile-notice");
  notice.textContent = "";
  notice.classList.toggle("success", false);
  try {
    await ensureDraft();
    applyAdvanced();
    const response = await api("/api/v1/profiles", jsonRequest("POST", draft));
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(body, "Não foi possível salvar o modelo."));
    notice.textContent = `Modelo "${body.name}" salvo. Ele já aparece na escolha do modelo na tela de Auditoria.`;
    notice.classList.toggle("success", true);
    await loadProfiles();
    $("profile-select").value = body.id;
  } catch (error) {
    notice.textContent = error.message;
  }
});

initSession();
