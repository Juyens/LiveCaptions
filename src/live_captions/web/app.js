"use strict";

// Python -> pagina: `window.lc.receive(lote)` con [[tipo, datos], ...] (ver bridge.py).
// Pagina -> Python: `window.pywebview.api.<metodo>()`, que devuelve una promesa (ver app.py).

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

const MAX_IMAGE_SIDE = 1280; // las capturas se reducen antes de enviarse

const ui = {
  ready: false,
  running: false,
  startedAt: 0,
  translatedIndex: 0, // ultima frase con traduccion final; los borradores anteriores sobran
  streaming: null, // cuerpo de la respuesta del chat que se esta escribiendo
  pendingImage: null, // data URL de la imagen pegada, a la espera de su nota
  imageCards: new Map(),
};

// -- utilidades --------------------------------------------------------------------------

function clock(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function nearBottom(node) {
  return node.scrollHeight - node.scrollTop - node.clientHeight < 32;
}

function keepAtBottom(node, update) {
  const stick = nearBottom(node);
  update();
  if (stick) node.scrollTop = node.scrollHeight;
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = el("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  if (button) {
    const label = button.textContent;
    button.textContent = "Copiado";
    setTimeout(() => (button.textContent = label), 1200);
  }
}

function copyButton(source) {
  const button = el("button", "ghost", "Copiar");
  button.type = "button";
  button.addEventListener("click", () => copyText(source(), button));
  return button;
}

function setStatus(node, text, error = false) {
  node.textContent = text || "";
  node.classList.toggle("error", Boolean(error));
}

// -- transcripciones ---------------------------------------------------------------------

class Transcript {
  constructor(name) {
    this.node = $(name);
    this.count = $(`count-${name}`);
    this.copy = document.querySelector(`[data-copy="${name}"]`);
    this.lines = [];
    this.partial = null; // <p> de la frase en curso
    this.words = []; // palabras que muestra ahora el borrador, para animar solo las nuevas
    this.copy.addEventListener("click", () => copyText(this.lines.join("\n"), this.copy));
  }

  append(seconds, text, who = "") {
    this.lines.push(who ? `${who}: ${text}` : text);
    keepAtBottom(this.node, () => {
      const line = el("p", who ? "line me" : "line");
      line.append(el("time", "", clock(seconds)));
      if (who) line.append(el("span", "who", who));
      line.append(document.createTextNode(text));
      this.node.insertBefore(line, this.partial);
    });
    this.count.textContent = String(this.lines.length);
    this.copy.disabled = false;
  }

  // Borrador: lo confirmado en gris, lo que aun puede cambiar en cursiva. Las palabras que
  // no estaban en el borrador anterior entran con un fundido; las demas no se tocan a la vista.
  setPartial(committed, tentative = "") {
    const settled = committed ? committed.split(/\s+/) : [];
    const loose = tentative ? tentative.split(/\s+/) : [];
    const words = [...settled, ...loose];
    keepAtBottom(this.node, () => {
      if (!words.length) {
        this.partial?.remove();
        this.partial = null;
        this.words = [];
        return;
      }
      if (!this.partial) {
        this.partial = el("p", "partial");
        this.node.append(this.partial);
      }
      const settledSpan = el("span", "settled");
      const looseSpan = el("span", "tentative");
      words.forEach((word, i) => {
        const span = el("span", this.words[i] === word ? "" : "fresh", word);
        const target = i < settled.length ? settledSpan : looseSpan;
        if (target.childNodes.length) target.append(" ");
        target.append(span);
      });
      this.partial.replaceChildren(settledSpan);
      if (loose.length) this.partial.append(settled.length ? " " : "", looseSpan);
      this.words = words;
    });
  }

  clear() {
    this.lines = [];
    this.partial = null;
    this.words = [];
    this.node.replaceChildren();
    this.count.textContent = "";
    this.copy.disabled = true;
  }
}

const english = new Transcript("english");
const spanish = new Transcript("spanish");

// -- barra superior ----------------------------------------------------------------------

const record = $("record");

function renderRecord() {
  record.textContent = ui.running ? "Detener" : "Escuchar";
  record.classList.toggle("live", ui.running);
  $("clock").classList.toggle("live", ui.running);
}

record.addEventListener("click", () => {
  record.disabled = true; // se reactiva con started/stopped/failed
  api().toggle();
});

setInterval(() => {
  if (ui.running) $("clock").textContent = clock((performance.now() - ui.startedAt) / 1000);
}, 250);

function toggleButton(id, onChange) {
  const button = $(id);
  button.addEventListener("click", () => {
    const on = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(on));
    onChange(on);
  });
  return (on) => button.setAttribute("aria-pressed", String(on));
}

const setMic = toggleButton("mic", (on) => api().set_mic(on));
const setShowSpanish = toggleButton("show-spanish", () => layoutChanged());
const setShowAssistant = toggleButton("show-assistant", () => layoutChanged());

// Menu "⋯"
const menu = $("menu");
const more = $("more");
function showMenu(open) {
  menu.hidden = !open;
  more.setAttribute("aria-expanded", String(open));
}
more.addEventListener("click", (event) => {
  event.stopPropagation();
  showMenu(menu.hidden);
});
document.addEventListener("click", (event) => {
  if (!menu.hidden && !menu.contains(event.target)) showMenu(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") showMenu(false);
});
const onTop = menu.querySelector('[data-action="on-top"]');
menu.addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  if (action === "on-top") {
    const on = onTop.getAttribute("aria-checked") !== "true";
    onTop.setAttribute("aria-checked", String(on));
    api().set_on_top(on);
    return; // el menu sigue abierto para ver la marca
  }
  showMenu(false);
  if (action === "clear") {
    english.clear();
    spanish.clear();
  } else if (action === "folder") {
    api().open_folder();
  } else if (action === "settings") {
    openSettings();
  }
});

// -- paneles: visibilidad y anchos -------------------------------------------------------

const panes = $("panes");
const paneNodes = [...panes.querySelectorAll(".pane")];

function visiblePanes() {
  return paneNodes.filter((pane) => !pane.hidden);
}

// Un separador entre cada par de paneles visibles, recolocados tras mostrar u ocultar.
function placeSplitters() {
  panes.querySelectorAll(".splitter").forEach((node) => node.remove());
  const shown = visiblePanes();
  shown.slice(1).forEach((pane) => {
    const splitter = el("div", "splitter");
    splitter.addEventListener("pointerdown", startDrag);
    pane.before(splitter);
  });
}

function widths() {
  return paneNodes.map((pane) => Number(pane.style.flexGrow) || 1);
}

function saveLayout() {
  api().set_layout(
    $("show-spanish").getAttribute("aria-pressed") === "true",
    $("show-assistant").getAttribute("aria-pressed") === "true",
    widths(),
  );
}

function layoutChanged() {
  $("pane-spanish").hidden = $("show-spanish").getAttribute("aria-pressed") !== "true";
  $("pane-assistant").hidden = $("show-assistant").getAttribute("aria-pressed") !== "true";
  placeSplitters();
  saveLayout();
}

function startDrag(event) {
  const splitter = event.currentTarget;
  const left = splitter.previousElementSibling;
  const right = splitter.nextElementSibling;
  const startX = event.clientX;
  const leftWidth = left.getBoundingClientRect().width;
  const rightWidth = right.getBoundingClientRect().width;
  const grow = (Number(left.style.flexGrow) || 1) + (Number(right.style.flexGrow) || 1);
  const minimum = 220;
  splitter.setPointerCapture(event.pointerId);
  splitter.classList.add("dragging");

  const move = (e) => {
    const delta = Math.max(minimum - leftWidth, Math.min(rightWidth - minimum, e.clientX - startX));
    const share = (leftWidth + delta) / (leftWidth + rightWidth);
    left.style.flexGrow = (grow * share).toFixed(4);
    right.style.flexGrow = (grow * (1 - share)).toFixed(4);
  };
  const stop = () => {
    splitter.classList.remove("dragging");
    splitter.removeEventListener("pointermove", move);
    splitter.removeEventListener("pointerup", stop);
    saveLayout();
  };
  splitter.addEventListener("pointermove", move);
  splitter.addEventListener("pointerup", stop);
}

// -- asistente ---------------------------------------------------------------------------

const feed = $("feed");
const question = $("question");
const assistantStatus = $("assistant-status");

function addCard(card) {
  $("feed-empty")?.remove();
  feed.append(card);
  feed.scrollTop = feed.scrollHeight;
}

function cardHead(tag, { seconds, action } = {}) {
  const head = el("div", "card-head");
  if (seconds !== undefined) head.append(el("time", "", clock(seconds)));
  head.append(el("span", "tag", tag));
  if (action) head.append(action);
  return head;
}

function addSuggestion(s) {
  const card = el("article", "card");
  card.append(cardHead("Te preguntan", { seconds: s.seconds }));
  card.append(el("p", "card-title", s.question_es || s.question));
  if (s.question_es) card.append(el("p", "dim", s.question));
  for (const answer of s.answers) {
    const row = el("div", "answer");
    const texts = el("div");
    texts.append(el("div", "en", answer.en));
    if (answer.es) texts.append(el("div", "dim", answer.es));
    row.append(texts, copyButton(() => answer.en));
    card.append(row);
  }
  addCard(card);
}

function chatStarted(text) {
  const mine = el("article", "card user");
  mine.append(cardHead("Tú"), el("div", "body", text));
  addCard(mine);
  const reply = el("article", "card");
  const body = el("div", "body streaming");
  reply.append(cardHead("Asistente", { action: copyButton(() => body.textContent) }), body);
  addCard(reply);
  ui.streaming = body;
  question.disabled = true;
}

function chatDelta(delta) {
  if (!ui.streaming) return;
  keepAtBottom(feed, () => ui.streaming.append(delta));
}

function chatDone(answer) {
  if (ui.streaming) {
    ui.streaming.textContent = answer;
    ui.streaming.classList.remove("streaming");
  }
  ui.streaming = null;
  question.disabled = false;
  question.focus();
}

function assistantFailed() {
  ui.streaming?.classList.remove("streaming");
  ui.streaming = null;
  question.disabled = false;
  for (const card of ui.imageCards.values()) {
    const reading = card.querySelector(".reading");
    if (reading) reading.textContent = "No se pudo leer la imagen.";
  }
}

// Imagenes: se pegan (Ctrl+V) o se eligen; se envian con la nota que se escriba despues.

async function toDataUrl(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_IMAGE_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = el("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return canvas.toDataURL("image/jpeg", 0.88);
}

async function setPendingImage(file) {
  try {
    ui.pendingImage = await toDataUrl(file);
  } catch {
    setStatus(assistantStatus, "No se pudo abrir esa imagen.", true);
    return;
  }
  $("preview-img").src = ui.pendingImage;
  $("preview").hidden = false;
  question.placeholder = "¿De que trata la imagen? (Enter para enviar)";
  question.focus();
}

function discardImage() {
  ui.pendingImage = null;
  $("preview").hidden = true;
  question.placeholder = "Pregunta al asistente... (Enter para enviar)";
}

async function sendImage(dataUrl, note) {
  const result = await api().send_image(dataUrl, note);
  if (!result.ok) return; // el asistente ya aviso del motivo por `assistant_status`
  const remove = el("button", "ghost", "Quitar del contexto");
  remove.type = "button";
  const card = el("article", "card user");
  card.append(cardHead(`Imagen ${result.id}`, { action: remove }));
  const img = el("img");
  img.src = dataUrl;
  img.alt = note || `Imagen ${result.id}`;
  card.append(img);
  if (note) card.append(el("div", "body", note));
  card.append(el("div", "dim reading", "Leyendo la imagen..."));
  remove.addEventListener("click", () => {
    api().remove_image(result.id);
    ui.imageCards.delete(result.id);
    card.remove();
  });
  ui.imageCards.set(result.id, card);
  addCard(card);
}

function imageRead({ id, description }) {
  const card = ui.imageCards.get(id);
  const reading = card?.querySelector(".reading");
  if (!reading) return;
  reading.classList.remove("reading");
  reading.textContent = description;
}

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = question.value.trim();
  if (ui.pendingImage) {
    const image = ui.pendingImage;
    discardImage();
    question.value = "";
    sendImage(image, text);
    return;
  }
  if (!text) return;
  question.value = "";
  api().ask(text);
});

question.addEventListener("paste", (event) => {
  const item = [...(event.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
  if (!item) return;
  event.preventDefault();
  setPendingImage(item.getAsFile());
});
$("attach").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", (event) => {
  const [file] = event.target.files;
  if (file) setPendingImage(file);
  event.target.value = "";
});
$("discard-image").addEventListener("click", discardImage);

$("clear-feed").addEventListener("click", () => {
  feed.replaceChildren();
  ui.streaming = null;
  ui.imageCards.clear();
  question.disabled = false;
  api().clear_images();
});

let contextTimer = 0;
$("context").addEventListener("input", (event) => {
  clearTimeout(contextTimer);
  contextTimer = setTimeout(() => api().set_context(event.target.value), 400);
});

function renderAssistantConfig({ configured, badge }) {
  $("assistant-badge").textContent = badge;
  setStatus(
    assistantStatus,
    configured ? "" : "Pulsa Ajustes y pega la API key del proveedor para activar el asistente.",
    !configured,
  );
}

// -- ajustes -----------------------------------------------------------------------------

const settingsDialog = $("settings");
const form = $("settings-form");
const field = (name) => form.elements.namedItem(name);
const settingsResult = $("settings-result");

function formValues() {
  const values = {};
  for (const name of ["provider", "base_url", "model", "vision_model", "api_key", "user_name", "vocabulary"]) {
    values[name] = field(name).value.trim();
  }
  return values;
}

function showResult(ok, message) {
  settingsResult.textContent = message;
  settingsResult.className = `result ${ok ? "ok" : "error"}`;
}

async function openSettings() {
  const data = await api().load_settings();
  const provider = field("provider");
  provider.replaceChildren(
    ...Object.entries(data.presets).map(([key, preset]) => {
      const option = el("option", "", preset.label);
      option.value = key;
      return option;
    }),
  );
  for (const name of ["provider", "base_url", "model", "vision_model", "api_key", "user_name", "vocabulary"]) {
    field(name).value = data[name] ?? "";
  }
  field("api_key").disabled = data.presets[data.provider]?.needs_key === false;
  $("models").replaceChildren();
  settingsResult.textContent = "";
  settingsDialog.showModal();
}

field("provider").addEventListener("change", async (event) => {
  const preset = await api().preset(event.target.value);
  field("base_url").value = preset.base_url;
  field("model").value = preset.model;
  field("vision_model").value = preset.vision_model;
  field("api_key").value = preset.api_key;
  field("api_key").disabled = !preset.needs_key;
  $("models").replaceChildren();
});

$("show-key").addEventListener("click", (event) => {
  const shown = field("api_key").type === "text";
  field("api_key").type = shown ? "password" : "text";
  event.target.textContent = shown ? "Mostrar" : "Ocultar";
});

async function busy(button, message, work) {
  button.disabled = true;
  settingsResult.className = "result";
  settingsResult.textContent = message;
  try {
    await work();
  } finally {
    button.disabled = false;
  }
}

$("test-connection").addEventListener("click", (event) =>
  busy(event.target, "Probando...", async () => {
    const result = await api().test_connection(formValues());
    showResult(result.ok, result.message);
  }),
);

$("load-models").addEventListener("click", (event) =>
  busy(event.target, "Consultando modelos...", async () => {
    const result = await api().list_models(formValues());
    if (!result.ok) return showResult(false, result.message);
    $("models").replaceChildren(
      ...result.models.map((model) => {
        const option = el("option");
        option.value = model;
        return option;
      }),
    );
    const current = field("model").value.trim();
    if (current && !result.models.includes(current)) {
      showResult(false, `${result.message}; «${current}» ya no esta en la lista`);
    } else {
      showResult(true, result.message);
    }
    field("model").focus();
  }),
);

$("open-settings").addEventListener("click", openSettings);

// Guardar/Cancelar cierran el dialogo solos (method="dialog"); aqui solo se guarda.
form.addEventListener("submit", async (event) => {
  if (event.submitter?.value !== "save") return;
  renderAssistantConfig(await api().save_settings(formValues()));
});

// -- eventos de Python -------------------------------------------------------------------

const handlers = {
  status: ({ text, error }) => setStatus($("status"), text, error),
  ready(backend) {
    ui.ready = true;
    record.disabled = ui.running;
    const gpu = backend.startsWith("cuda");
    $("backend").textContent = `${gpu ? "●" : "○"} ${backend}`;
    $("backend").classList.toggle("gpu", gpu);
    if (!ui.running) record.disabled = false;
  },
  failed() {
    record.disabled = !ui.ready;
  },
  started({ path }) {
    ui.running = true;
    ui.startedAt = performance.now();
    ui.translatedIndex = 0; // los indices de frase vuelven a empezar
    $("clock").textContent = "00:00:00";
    $("clock").title = path;
    renderRecord();
    record.disabled = false;
  },
  stopped() {
    ui.running = false;
    $("level").style.width = "0";
    renderRecord();
    record.disabled = !ui.ready;
  },
  level(rms) {
    $("level").style.width = `${Math.min(100, Math.sqrt(rms) * 130)}%`;
  },
  partial: ({ committed, tentative }) => english.setPartial(committed, tentative),
  final({ seconds, speaker, text }) {
    english.append(seconds, text, speaker === "me" ? "Tú" : "");
  },
  translated({ index, seconds, text }) {
    ui.translatedIndex = Math.max(ui.translatedIndex, index);
    spanish.setPartial("");
    spanish.append(seconds, text);
  },
  partial_translated({ index, text }) {
    // Puede llegar tarde, cuando su frase ya tiene traduccion final: entonces sobra.
    if (index > ui.translatedIndex) spanish.setPartial("", text);
  },
  suggestion: addSuggestion,
  image_read: imageRead,
  chat_started: chatStarted,
  chat_delta: chatDelta,
  chat_done: chatDone,
  assistant_status({ text, error }) {
    setStatus(assistantStatus, text, error);
    if (error) assistantFailed();
  },
  assistant_config: renderAssistantConfig,
};

window.lc = {
  receive(batch) {
    for (const [kind, payload] of batch) {
      try {
        handlers[kind]?.(payload);
      } catch (error) {
        console.error(kind, error);
      }
    }
  },
};

// -- arranque ----------------------------------------------------------------------------

window.addEventListener("pywebviewready", async () => {
  const state = await api().state();
  ui.ready = state.ready;
  ui.running = state.running;
  ui.startedAt = performance.now() - state.elapsed * 1000;
  renderRecord();
  record.disabled = !state.ready;
  setStatus($("status"), state.status.text, state.status.error);
  if (state.backend) handlers.ready(state.backend);
  setMic(state.mic);
  onTop.setAttribute("aria-checked", String(state.on_top));
  setShowSpanish(state.spanish);
  setShowAssistant(state.assistant);
  if (Array.isArray(state.widths) && state.widths.length === paneNodes.length) {
    paneNodes.forEach((pane, i) => (pane.style.flexGrow = String(state.widths[i])));
  }
  $("pane-spanish").hidden = !state.spanish;
  $("pane-assistant").hidden = !state.assistant;
  placeSplitters();
  $("context").value = state.context;
  renderAssistantConfig(state.assistant_config);
});
