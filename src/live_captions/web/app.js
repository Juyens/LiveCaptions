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
  return node.scrollHeight - node.scrollTop - node.clientHeight < 40;
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
    const label = [...button.childNodes]; // puede llevar etiqueta larga y corta
    button.textContent = "Copiado";
    setTimeout(() => button.replaceChildren(...label), 1200);
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

// Palabras como <span>; las que no estaban en la version anterior entran con un fundido.
function wordSpans(target, words, previous, offset = 0) {
  words.forEach((word, i) => {
    if (target.childNodes.length) target.append(" ");
    target.append(el("span", previous[offset + i] === word ? "" : "fresh", word));
  });
}

// -- conversacion ------------------------------------------------------------------------
//
// Cada frase final es una fila: hora, ingles y, debajo, su espanol (un borrador atenuado
// hasta que llega la traduccion final, con el mismo indice). La ultima fila es la frase en
// curso: el borrador en ingles (confirmado + provisional) y el de su traduccion.

class Conversation {
  constructor() {
    this.scroller = $("scroller");
    this.node = $("lines");
    this.entries = []; // {seconds, speaker, en, es} para Copiar
    this.rows = new Map(); // indice -> {es: <p>, entry}
    this.lastFinal = 0; // indice de la ultima frase final: borradores con indice menor sobran
    this.draft = null; // {row, en, es, words}
    this.draftEs = "";
    this.scroller.addEventListener("scroll", () => this.updateJump());
    $("jump").addEventListener("click", () => {
      this.scroller.scrollTo({ top: this.scroller.scrollHeight, behavior: "smooth" });
    });
    $("copy-en").addEventListener("click", (e) => copyText(this.text("en"), e.currentTarget));
    $("copy-es").addEventListener("click", (e) => copyText(this.text("es"), e.currentTarget));
  }

  update(change) {
    keepAtBottom(this.scroller, change);
    this.updateJump();
  }

  updateJump() {
    $("jump").hidden = nearBottom(this.scroller);
  }

  addFinal({ index, seconds, speaker, text }) {
    const me = speaker === "me";
    const entry = { seconds, speaker, en: text, es: "" };
    this.entries.push(entry);
    this.lastFinal = Math.max(this.lastFinal, index);
    this.update(() => {
      $("empty")?.remove();
      const row = el("article", me ? "row me" : "row");
      const body = el("div");
      const en = el("p", "en");
      if (me) en.append(el("span", "who", "Tú"));
      en.append(document.createTextNode(text));
      body.append(en);
      if (!me) {
        // Mientras llega la traduccion final se deja el borrador que ya se estaba leyendo.
        const carried = this.draftEs;
        const es = el("p", carried ? "es provisional" : "es pending", carried || "…");
        body.append(es);
        this.rows.set(index, { es, entry });
        this.draftEs = "";
      }
      row.append(el("time", "", clock(seconds)), body);
      this.node.insertBefore(row, this.draft?.row ?? null);
    });
    $("count").textContent = `${this.entries.length} ${this.entries.length === 1 ? "frase" : "frases"}`;
    $("copy-en").disabled = false;
  }

  setTranslation({ index, text }) {
    const row = this.rows.get(index);
    if (!row) return;
    row.es.textContent = text;
    row.es.className = "es";
    row.entry.es = text;
    this.rows.delete(index);
    $("copy-es").disabled = false;
  }

  setDraft(committed, tentative) {
    const settled = committed ? committed.split(/\s+/) : [];
    const loose = tentative ? tentative.split(/\s+/) : [];
    const words = [...settled, ...loose];
    this.update(() => {
      if (!words.length) return this.dropDraft();
      const draft = this.ensureDraft();
      const settledSpan = el("span", "settled");
      const looseSpan = el("span", "tentative");
      wordSpans(settledSpan, settled, draft.words);
      wordSpans(looseSpan, loose, draft.words, settled.length);
      draft.en.replaceChildren(settledSpan);
      if (loose.length) draft.en.append(settled.length ? " " : "", looseSpan);
      draft.words = words;
    });
  }

  setDraftTranslation({ index, text }) {
    if (index <= this.lastFinal) return; // llego tarde: su frase ya es final
    this.draftEs = text;
    if (!this.draft) return;
    this.update(() => {
      this.draft.es.textContent = text;
    });
  }

  ensureDraft() {
    if (this.draft) return this.draft;
    $("empty")?.remove();
    const row = el("article", "row draft");
    const en = el("p", "en");
    const es = el("p", "es", this.draftEs);
    const body = el("div");
    body.append(en, es);
    row.append(el("time", "", "···"), body);
    this.node.append(row);
    this.draft = { row, en, es, words: [] };
    return this.draft;
  }

  // La frase en curso termino: si acabo en frase final, esta ya se llevo el borrador en
  // espanol (llega antes que el borrado); si Whisper la descarto, el borrador sobra.
  dropDraft() {
    this.draft?.row.remove();
    this.draft = null;
    this.draftEs = "";
  }

  text(language) {
    return this.entries
      .filter((e) => language === "en" || e.es)
      .map((e) => {
        const who = language === "en" && e.speaker === "me" ? "Tú: " : "";
        return `[${clock(e.seconds)}] ${who}${language === "en" ? e.en : e.es}`;
      })
      .join("\n");
  }

  clear() {
    this.entries = [];
    this.rows.clear();
    this.draft = null;
    this.draftEs = "";
    this.node.replaceChildren();
    $("count").textContent = "";
    $("copy-en").disabled = true;
    $("copy-es").disabled = true;
    $("jump").hidden = true;
  }

  restart() {
    this.lastFinal = 0; // los indices de frase vuelven a empezar en cada sesion
  }
}

const conversation = new Conversation();

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

const pressed = (id) => $(id).getAttribute("aria-pressed") === "true";

const setMic = toggleButton("mic", (on) => api().set_mic(on));
const setShowSpanish = toggleButton("show-spanish", () => layoutChanged());
// Un solo estado para el asistente, sea cual sea el tamano: activo sigue activo al agrandar o
// achicar la ventana; lo que cambia es solo si va al lado o encima de la conversacion.
function setAssistant(on) {
  ui.showAssistant = on;
  layoutChanged();
}
$("show-assistant").addEventListener("click", () => setAssistant(!ui.showAssistant));

// Menus desplegables: uno abierto a la vez; se cierran con clic fuera o Escape.
const menus = [
  { menu: $("menu"), button: $("more") },
  { menu: $("mic-menu"), button: $("mic-pick") },
];

function showMenu(target, open) {
  for (const { menu, button } of menus) {
    const show = menu === target && open;
    menu.hidden = !show;
    button.setAttribute("aria-expanded", String(show));
  }
}

document.addEventListener("click", (event) => {
  if (!menus.some(({ menu, button }) => menu.contains(event.target) || button.contains(event.target))) {
    showMenu(null, false);
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  showMenu(null, false);
  // Superpuesto tapa la conversacion: Escape lo cierra (igual que su X).
  if (narrow.matches && ui.showAssistant && !settingsDialog.open) setAssistant(false);
});

$("more").addEventListener("click", () => showMenu($("menu"), $("menu").hidden));

const onTop = $("menu").querySelector('[data-action="on-top"]');
$("menu").addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  if (action === "on-top") {
    const on = onTop.getAttribute("aria-checked") !== "true";
    onTop.setAttribute("aria-checked", String(on));
    api().set_on_top(on);
    return; // el menu sigue abierto para ver la marca
  }
  showMenu(null, false);
  if (action === "clear") conversation.clear();
  else if (action === "folder") api().open_folder();
  else if (action === "settings") openSettings();
});

// Microfono: la lista se pide a Windows cada vez que se abre (pueden conectar unos cascos).
async function openMicMenu() {
  const menu = $("mic-menu");
  if (!menu.hidden) return showMenu(null, false);
  const { devices, default: fallback, selected } = await api().list_microphones();
  const item = (name, label) => {
    const button = el("button", "", "");
    button.setAttribute("role", "menuitemradio");
    button.setAttribute("aria-checked", String(name === selected));
    button.title = label;
    button.append(el("span", "check"), el("span", "device", label));
    button.addEventListener("click", () => {
      api().set_mic_device(name);
      $("mic").title = `Microfono: ${label}`;
      showMenu(null, false);
    });
    return button;
  };
  menu.replaceChildren(
    el("div", "menu-title", "Microfono"),
    item("", fallback ? `Predeterminado de Windows (${fallback})` : "Predeterminado de Windows"),
  );
  if (devices.length) menu.append(el("hr"));
  for (const name of devices) menu.append(item(name, name));
  if (selected && !devices.includes(selected)) {
    menu.append(el("div", "backend", `«${selected}» no esta conectado; se usa el predeterminado.`));
  }
  showMenu(menu, true);
}
$("mic-pick").addEventListener("click", openMicMenu);

// -- distribucion: espanol intercalado y barra del asistente -----------------------------

const layout = $("layout");
const sidebar = $("assistant");
// Por debajo de este ancho no caben lado a lado la conversacion (320 px) y el asistente
// (280 px): el CSS lo pone encima (misma cifra en app.css). Aqui solo decide si Escape lo
// cierra, porque encima tapa la conversacion.
const narrow = window.matchMedia("(max-width: 660px)");
ui.showAssistant = true; // activo o no, en cualquier tamano (se guarda)
ui.sidebarWidth = null; // ancho elegido arrastrando, en px

function assistantVisible() {
  return ui.showAssistant;
}

function saveLayout() {
  api().set_layout(pressed("show-spanish"), ui.showAssistant, ui.sidebarWidth);
}

function applyLayout() {
  const visible = assistantVisible();
  const opening = visible && sidebar.hidden;
  layout.classList.toggle("no-es", !pressed("show-spanish"));
  sidebar.hidden = !visible;
  $("splitter").hidden = !visible;
  $("show-assistant").setAttribute("aria-pressed", String(visible));
  if (visible) $("show-assistant").classList.remove("news");
  // Lo que llego con el panel cerrado no pudo desplazarse: al abrir, a lo ultimo.
  if (opening) $("feed").scrollTop = $("feed").scrollHeight;
}

function layoutChanged() {
  applyLayout();
  saveLayout();
}

$("close-assistant").addEventListener("click", () => setAssistant(false));

// Una sugerencia con el asistente cerrado deja un punto en su boton.
function notifyAssistant() {
  if (!assistantVisible()) $("show-assistant").classList.add("news");
}

$("splitter").addEventListener("pointerdown", (event) => {
  const splitter = event.currentTarget;
  const startX = event.clientX;
  const startWidth = sidebar.getBoundingClientRect().width;
  splitter.setPointerCapture(event.pointerId);
  splitter.classList.add("dragging");
  const move = (e) => {
    const limit = layout.getBoundingClientRect().width * 0.6;
    const width = Math.round(Math.max(280, Math.min(limit, startWidth + startX - e.clientX)));
    layout.style.setProperty("--sidebar", `${width}px`);
    ui.sidebarWidth = width;
  };
  const stop = () => {
    splitter.classList.remove("dragging");
    splitter.removeEventListener("pointermove", move);
    splitter.removeEventListener("pointerup", stop);
    saveLayout();
  };
  splitter.addEventListener("pointermove", move);
  splitter.addEventListener("pointerup", stop);
});

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
  notifyAssistant();
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
  // PNG conserva nitido el texto pequeno (preguntas, tablas, codigo) y las capturas de texto
  // pesan poco; JPEG solo para fotos grandes, bajo el limite de 4 MB de los proveedores.
  const png = canvas.toDataURL("image/png");
  return png.length < 3_000_000 ? png : canvas.toDataURL("image/jpeg", 0.9);
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

// `replacing`: la tarjeta de un intento fallido, que se sustituye en su sitio al reintentar.
async function sendImage(dataUrl, note, replacing = null) {
  const result = await api().send_image(dataUrl, note);
  if (!result.ok) return; // el asistente ya aviso del motivo por `assistant_status`
  const id = result.id;
  const remove = el("button", "ghost", "Quitar del contexto");
  remove.type = "button";
  const card = el("article", "card user");
  card.append(cardHead(`Imagen ${id}`, { action: remove }));
  const img = el("img");
  img.src = dataUrl;
  img.alt = note || `Imagen ${id}`;
  card.append(img);
  if (note) card.append(el("div", "body", note));
  const reading = el("div", "dim reading", "Leyendo la imagen...");
  card.append(reading);
  remove.addEventListener("click", () => {
    api().remove_image(id);
    ui.imageCards.delete(id);
    card.remove();
  });
  ui.imageCards.set(id, { card, reading, dataUrl, note });
  if (replacing) replacing.replaceWith(card);
  else addCard(card);
}

// Lo que leyo el modelo, plegado: es lo que el asistente sabe de la imagen, para comprobarlo.
function imageRead({ id, description }) {
  const entry = ui.imageCards.get(id);
  if (!entry) return;
  const details = el("details", "reading-done");
  details.append(el("summary", "", "Lo que entendio el asistente"));
  details.append(el("div", "body", description.replace(/\*\*/g, "")));
  entry.reading.replaceWith(details);
  entry.reading = details;
}

function imageFailed({ id, reason }) {
  const entry = ui.imageCards.get(id);
  if (!entry) return;
  ui.imageCards.delete(id); // el asistente ya la descarto; reintentar la envia de nuevo
  const failed = el("div", "image-failed");
  const retry = el("button", "", "Reintentar");
  retry.type = "button";
  retry.addEventListener("click", () => sendImage(entry.dataUrl, entry.note, entry.card));
  failed.append(el("span", "", `No se pudo leer la imagen: ${reason}`), retry);
  entry.reading.replaceWith(failed);
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
const FIELDS = ["provider", "base_url", "model", "vision_model", "api_key", "user_name", "vocabulary"];

function formValues() {
  return Object.fromEntries(FIELDS.map((name) => [name, field(name).value.trim()]));
}

function showResult(ok, message) {
  settingsResult.textContent = message;
  settingsResult.className = `result ${ok ? "ok" : "error"}`;
}

// Combobox de modelos: se escribe libremente (el proveedor puede tener modelos que no
// lista) o se elige de la lista, que muestra todo al abrirla y filtra al escribir.

// `vision`: los que aceptan imagenes, o null si el proveedor no lo indica (se ofrecen todos).
const models = { list: [], vision: null, state: "idle", error: "", request: 0 };
const comboList = $("combo-list");
let combo = null; // {input, items, active}

function comboOptions(input) {
  return input.name === "vision_model" && models.vision ? models.vision : models.list;
}

function setupCombo(input) {
  const toggle = el("button", "combo-toggle");
  toggle.type = "button";
  toggle.tabIndex = -1;
  toggle.setAttribute("aria-label", "Ver modelos");
  input.after(toggle);
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-controls", "combo-list");
  input.setAttribute("aria-expanded", "false");

  toggle.addEventListener("pointerdown", (event) => event.preventDefault()); // no robar el foco
  toggle.addEventListener("click", () => {
    if (combo?.input === input) return closeCombo();
    input.focus();
    showCombo(input, "");
  });
  input.addEventListener("input", () => showCombo(input, input.value.trim()));
  input.addEventListener("blur", closeCombo);
  input.addEventListener("keydown", (event) => {
    const open = combo?.input === input;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) return showCombo(input, "");
      moveActive(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter" && open && combo.active >= 0) {
      event.preventDefault(); // elige el modelo en vez de enviar el formulario
      chooseModel(combo.items[combo.active]);
    } else if (event.key === "Escape" && open) {
      event.preventDefault(); // cierra la lista, no el dialogo
      closeCombo();
    }
  });
}

function note(text) {
  return el("li", "note", text);
}

function showCombo(input, filter) {
  const needle = filter.toLowerCase();
  const options = comboOptions(input);
  const items = needle ? options.filter((m) => m.toLowerCase().includes(needle)) : options;
  const current = input.value.trim();
  combo = { input, items, active: items.indexOf(current) };

  if (models.state === "loading") comboList.replaceChildren(note("Cargando modelos..."));
  else if (models.state === "error") comboList.replaceChildren(note(models.error));
  else if (!items.length) comboList.replaceChildren(note(needle ? "Ningun modelo coincide" : "El proveedor no lista modelos"));
  else {
    comboList.replaceChildren(
      ...items.map((model, i) => {
        const li = el("li");
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", String(model === current));
        li.title = model;
        const at = needle ? model.toLowerCase().indexOf(needle) : -1;
        if (at >= 0) {
          li.append(model.slice(0, at), el("mark", "", model.slice(at, at + needle.length)), model.slice(at + needle.length));
        } else {
          li.textContent = model;
        }
        li.addEventListener("pointerdown", (event) => event.preventDefault());
        li.addEventListener("click", () => chooseModel(model));
        li.addEventListener("pointermove", () => setActive(i));
        return li;
      }),
    );
  }
  input.setAttribute("aria-expanded", "true");
  comboList.hidden = false;
  placeCombo();
  setActive(combo.active);
}

// Debajo del campo, o encima si abajo no cabe; `fixed` para que el scroll del dialogo no la corte.
function placeCombo() {
  const box = combo.input.parentElement.getBoundingClientRect();
  const below = window.innerHeight - box.bottom - 12;
  const above = box.top - 12;
  const upward = below < 200 && above > below;
  comboList.style.left = `${box.left}px`;
  comboList.style.width = `${box.width}px`;
  comboList.style.maxHeight = `${Math.min(280, upward ? above : below)}px`;
  comboList.style.top = upward ? "" : `${box.bottom + 4}px`;
  comboList.style.bottom = upward ? `${window.innerHeight - box.top + 4}px` : "";
}

function setActive(index) {
  if (!combo) return;
  combo.active = index;
  const options = comboList.querySelectorAll('[role="option"]');
  options.forEach((li, i) => li.classList.toggle("active", i === index));
  options[index]?.scrollIntoView({ block: "nearest" });
}

function moveActive(step) {
  if (!combo?.items.length) return;
  const count = combo.items.length;
  setActive(combo.active < 0 ? (step > 0 ? 0 : count - 1) : (combo.active + step + count) % count);
}

function chooseModel(model) {
  combo.input.value = model;
  closeCombo();
}

function closeCombo() {
  combo?.input.setAttribute("aria-expanded", "false");
  combo = null;
  comboList.hidden = true;
}

// Pide la lista al proveedor con lo que haya escrito en el dialogo (aunque no se haya guardado).
async function refreshModels({ report = false } = {}) {
  const request = ++models.request;
  models.state = "loading";
  if (combo) showCombo(combo.input, "");
  const result = await api().list_models(formValues());
  if (request !== models.request) return; // llego otra peticion despues (cambio de proveedor)
  models.list = result.ok ? result.models : [];
  models.vision = result.ok ? result.vision : null;
  models.state = result.ok ? "ready" : "error";
  models.error = result.ok ? "" : `No se pudo cargar la lista: ${result.message}`;
  if (combo) showCombo(combo.input, "");
  const current = field("model").value.trim();
  const vision = field("vision_model").value.trim();
  if (result.ok && current && models.list.length && !models.list.includes(current)) {
    showResult(false, `«${current}» ya no esta entre los modelos del proveedor`);
  } else if (result.ok && vision && models.vision && !models.vision.includes(vision)) {
    const hint = models.vision.length ? `; prueba ${models.vision[0]}` : "";
    showResult(false, `«${vision}» no existe o no acepta imagenes${hint}`);
  } else if (report) {
    showResult(result.ok, result.message);
  }
}

setupCombo(field("model"));
setupCombo(field("vision_model"));
window.addEventListener("resize", closeCombo);
settingsDialog.addEventListener("close", closeCombo);
$("cancel-settings").addEventListener("click", () => settingsDialog.close("cancel"));

async function openSettings() {
  const data = await api().load_settings();
  field("provider").replaceChildren(
    ...Object.entries(data.presets).map(([key, preset]) => {
      const option = el("option", "", preset.label);
      option.value = key;
      return option;
    }),
  );
  for (const name of FIELDS) field(name).value = data[name] ?? "";
  field("api_key").disabled = data.presets[data.provider]?.needs_key === false;
  settingsResult.textContent = "";
  settingsDialog.showModal();
  refreshModels();
}

$("open-settings").addEventListener("click", openSettings);

field("provider").addEventListener("change", async (event) => {
  const preset = await api().preset(event.target.value);
  field("base_url").value = preset.base_url;
  field("model").value = preset.model;
  field("vision_model").value = preset.vision_model;
  field("api_key").value = preset.api_key;
  field("api_key").disabled = !preset.needs_key;
  settingsResult.textContent = "";
  refreshModels();
});

// Una key recien pegada puede desbloquear la lista.
field("api_key").addEventListener("change", () => refreshModels());

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
  busy(event.target, "Consultando modelos...", () => refreshModels({ report: true })),
);

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
    conversation.restart();
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
  mic_opened(name) {
    $("mic").title = `Microfono: ${name}`;
  },
  partial: ({ committed, tentative }) => conversation.setDraft(committed, tentative),
  final: (line) => conversation.addFinal(line),
  translated: (line) => conversation.setTranslation(line),
  partial_translated: (draft) => conversation.setDraftTranslation(draft),
  suggestion: addSuggestion,
  image_read: imageRead,
  image_failed: imageFailed,
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

// pywebview avisa con `pywebviewready`; si la API ya estaba inyectada al cargar este script,
// el aviso pudo pasar antes de escucharlo, asi que se arranca directamente (una sola vez).
let started = false;
if (window.pywebview?.api?.state) init();
else window.addEventListener("pywebviewready", init);

async function init() {
  if (started) return;
  started = true;
  const state = await api().state();
  ui.ready = state.ready;
  ui.running = state.running;
  ui.startedAt = performance.now() - state.elapsed * 1000;
  renderRecord();
  record.disabled = !state.ready;
  setStatus($("status"), state.status.text, state.status.error);
  if (state.backend) handlers.ready(state.backend);
  setMic(state.mic);
  if (state.mic_device) $("mic").title = `Microfono: ${state.mic_device}`;
  onTop.setAttribute("aria-checked", String(state.on_top));
  setShowSpanish(state.spanish);
  ui.showAssistant = state.assistant;
  if (state.sidebar_width) {
    ui.sidebarWidth = state.sidebar_width;
    layout.style.setProperty("--sidebar", `${state.sidebar_width}px`);
  }
  applyLayout();
  $("context").value = state.context;
  renderAssistantConfig(state.assistant_config);
}
