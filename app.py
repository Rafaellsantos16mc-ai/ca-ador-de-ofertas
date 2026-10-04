import express from "express";
import qrcode from "qrcode";
import pino from "pino";
import crypto from "crypto";
import makeWASocket, {
  Browsers,
  DisconnectReason,
  useMultiFileAuthState
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import fs from "fs";

const app = express();

app.use(express.json({ limit: "1mb" }));
app.use(express.urlencoded({ extended: false }));

const PORT = Number(process.env.PORT || 3000);
const BOT_KEY = process.env.BOT_KEY || "";

const AUTH_DIR =
  process.env.WHATSAPP_AUTH_DIR ||
  "/data/whatsapp-auth";

const CONFIG_FILE =
  process.env.WHATSAPP_CONFIG_FILE ||
  "/data/whatsapp-config.json";

fs.mkdirSync(AUTH_DIR, { recursive: true });

let sock = null;
let currentQR = null;
let connected = false;
let starting = false;
let statusText = "Iniciando WhatsApp...";
let lastError = "";

let groupsCache = new Map();
let groupsLoading = false;

const logger = pino({
  level: process.env.LOG_LEVEL || "warn"
});

function loadConfig() {
  try {
    if (!fs.existsSync(CONFIG_FILE)) {
      return {
        selectedGroupId: ""
      };
    }

    const data = JSON.parse(
      fs.readFileSync(CONFIG_FILE, "utf8")
    );

    return {
      selectedGroupId:
        typeof data.selectedGroupId === "string"
          ? data.selectedGroupId
          : ""
    };
  } catch {
    return {
      selectedGroupId: ""
    };
  }
}

function saveConfig(config) {
  fs.writeFileSync(
    CONFIG_FILE,
    JSON.stringify(config, null, 2),
    "utf8"
  );
}

let config = loadConfig();

function status() {
  return {
    connected,
    hasQR: Boolean(currentQR),
    status: statusText,
    error: lastError || null,
    selectedGroupId:
      config.selectedGroupId || null
  };
}

function safeEqual(a, b) {
  if (!a || !b) return false;

  const aa = Buffer.from(String(a));
  const bb = Buffer.from(String(b));

  if (aa.length !== bb.length) {
    return false;
  }

  return crypto.timingSafeEqual(
    aa,
    bb
  );
}

function makeAdminToken() {
  const payload = `${Date.now()}`;

  const signature = crypto
    .createHmac("sha256", BOT_KEY)
    .update(payload)
    .digest("hex");

  return `${payload}.${signature}`;
}

function validAdminToken(token) {
  if (!BOT_KEY || !token) {
    return false;
  }

  const parts =
    String(token).split(".");

  if (parts.length !== 2) {
    return false;
  }

  const [timestamp, signature] =
    parts;

  if (!/^\d+$/.test(timestamp)) {
    return false;
  }

  const age =
    Date.now() -
    Number(timestamp);

  if (
    age < 0 ||
    age > 24 * 60 * 60 * 1000
  ) {
    return false;
  }

  const expected =
    crypto
      .createHmac(
        "sha256",
        BOT_KEY
      )
      .update(timestamp)
      .digest("hex");

  return safeEqual(
    signature,
    expected
  );
}

function getCookie(req, name) {
  const header =
    req.headers.cookie || "";

  const parts =
    header
      .split(";")
      .map(
        (item) => item.trim()
      );

  for (const part of parts) {
    const index =
      part.indexOf("=");

    if (index === -1) {
      continue;
    }

    const key =
      part.slice(0, index);

    const value =
      part.slice(index + 1);

    if (key === name) {
      return decodeURIComponent(
        value
      );
    }
  }

  return "";
}

function authorized(req) {
  const headerKey =
    req.get("x-bot-key") || "";

  if (
    safeEqual(
      headerKey,
      BOT_KEY
    )
  ) {
    return true;
  }

  const token =
    getCookie(
      req,
      "wa_admin"
    );

  return validAdminToken(
    token
  );
}

function requireBotKey(
  req,
  res,
  next
) {
  if (!authorized(req)) {
    return res.status(401).json({
      ok: false,
      error: "Não autorizado."
    });
  }

  next();
}

function normalizeGroup(group) {
  if (
    !group ||
    !group.id ||
    !String(group.id).endsWith("@g.us")
  ) {
    return null;
  }

  return {
    id: String(group.id),
    name:
      group.subject ||
      "(sem nome)",
    participants:
      Array.isArray(
        group.participants
      )
        ? group.participants.length
        : 0
  };
}

function updateGroupCache(group) {
  const g =
    normalizeGroup(group);

  if (!g) {
    return;
  }

  const old =
    groupsCache.get(g.id) ||
    {};

  groupsCache.set(
    g.id,
    {
      ...old,
      ...g
    }
  );
}

function sortedCachedGroups() {
  return Array.from(
    groupsCache.values()
  ).sort((a, b) =>
    a.name.localeCompare(
      b.name,
      "pt-BR"
    )
  );
}

async function getGroups(
  force = false
) {
  if (
    !connected ||
    !sock
  ) {
    throw new Error(
      "WhatsApp ainda não está conectado."
    );
  }

  if (
    groupsCache.size > 0 &&
    !force
  ) {
    console.log(
      `[GRUPOS] Usando cache: ${groupsCache.size} grupos.`
    );

    return sortedCachedGroups();
  }

  if (groupsLoading) {
    return sortedCachedGroups();
  }

  groupsLoading = true;

  try {
    console.log(
      "[GRUPOS] Buscando grupos do WhatsApp..."
    );

    const timeout =
      new Promise(
        (_, reject) => {
          setTimeout(
            () =>
              reject(
                new Error(
                  "Consulta de grupos expirou."
                )
              ),
            25000
          );
        }
      );

    const request =
      sock.groupFetchAllParticipating();

    const result =
      await Promise.race([
        request,
        timeout
      ]);

    for (
      const group of Object.values(
        result || {}
      )
    ) {
      updateGroupCache(
        group
      );
    }

    console.log(
      `[GRUPOS] Cache atualizado: ${groupsCache.size} grupos.`
    );

    console.log(
      `[GRUPOS] Consulta concluída: ${groupsCache.size} grupos.`
    );

    return sortedCachedGroups();

  } finally {
    groupsLoading = false;
  }
}

async function sendGroupMessage(
  jid,
  text
) {
  if (
    !connected ||
    !sock
  ) {
    throw new Error(
      "WhatsApp ainda não está conectado."
    );
  }

  if (
    !jid ||
    !String(jid).endsWith("@g.us")
  ) {
    throw new Error(
      "Grupo inválido."
    );
  }

  await sock.sendMessage(
    jid,
    {
      text
    }
  );
}

async function startWhatsApp() {
  if (starting) {
    return;
  }

  starting = true;

  try {
    const {
      state,
      saveCreds
    } =
      await useMultiFileAuthState(
        AUTH_DIR
      );

    sock =
      makeWASocket({
        auth: state,

        browser:
          Browsers.ubuntu(
            "Cacador de Ofertas"
          ),

        markOnlineOnConnect:
          false,

        logger,

        syncFullHistory:
          false
      });

    sock.ev.on(
      "creds.update",
      saveCreds
    );

    sock.ev.on(
      "groups.upsert",
      (groups) => {
        for (
          const group of groups || []
        ) {
          updateGroupCache(
            group
          );
        }

        console.log(
          `[GRUPOS] groups.upsert: ${groups?.length || 0} em cache.`
        );
      }
    );

    sock.ev.on(
      "groups.update",
      (updates) => {
        for (
          const update of updates || []
        ) {
          updateGroupCache(
            update
          );
        }

        console.log(
          `[GRUPOS] groups.update: ${updates?.length || 0} em cache.`
        );
      }
    );

    sock.ev.on(
      "groups.delete",
      (ids) => {
        for (
          const id of ids || []
        ) {
          groupsCache.delete(id);
        }

        console.log(
          `[GRUPOS] groups.delete: ${ids?.length || 0}.`
        );
      }
    );

    sock.ev.on(
      "connection.update",
      (update) => {
        const {
          connection,
          lastDisconnect,
          qr
        } = update;

        if (qr) {
          currentQR = qr;
          connected = false;

          statusText =
            "QR Code pronto. Escaneie pelo WhatsApp.";

          console.log(
            "[WHATSAPP] QR Code disponível."
          );
        }

        if (
          connection === "open"
        ) {
          currentQR = null;
          connected = true;
          starting = false;
          lastError = "";

          statusText =
            "WhatsApp conectado.";

          console.log(
            "[WHATSAPP] CONECTADO."
          );

          setTimeout(
            async () => {
              if (
                !connected ||
                !sock ||
                groupsCache.size > 0
              ) {
                return;
              }

              try {
                const groups =
                  await getGroups(
                    false
                  );

                console.log(
                  `[GRUPOS] Cache inicial carregado: ${groups.length} grupos.`
                );

              } catch (err) {
                console.error(
                  "[GRUPOS] Falha no carregamento inicial:",
                  err?.message ||
                    err
                );
              }
            },
            2500
          );
        }

        if (
          connection === "close"
        ) {
          connected = false;
          starting = false;

          const code =
            new Boom(
              lastDisconnect?.error
            )?.output
              ?.statusCode;

          if (
            code ===
            DisconnectReason.loggedOut
          ) {
            currentQR = null;

            statusText =
              "Sessão encerrada. Será necessário escanear um novo QR Code.";

            console.log(
              "[WHATSAPP] Sessão encerrada."
            );

            return;
          }

          statusText =
            "Conexão perdida. Reconectando...";

          lastError =
            lastDisconnect
              ?.error
              ?.message ||
            "Conexão encerrada.";

          setTimeout(
            () =>
              startWhatsApp()
                .catch(() => {}),
            3000
          );
        }
      }
    );

  } catch (err) {
    starting = false;
    connected = false;

    lastError =
      err?.message ||
      String(err);

    statusText =
      "Erro ao iniciar WhatsApp.";

    console.error(
      "[WHATSAPP]",
      err
    );

    setTimeout(
      () =>
        startWhatsApp()
          .catch(() => {}),
      5000
    );
  }
}

app.get(
  "/",
  async (_req, res) => {
    let content =
      `<p>${statusText}</p>`;

    if (currentQR) {
      try {
        const data =
          await qrcode.toDataURL(
            currentQR,
            {
              margin: 2,
              width: 360
            }
          );

        content += `
          <div class="qr">
            <img
              src="${data}"
              alt="QR Code WhatsApp"
            >
            <p>
              WhatsApp →
              Configurações →
              Dispositivos conectados →
              Conectar dispositivo
            </p>
            <p>
              Escaneie este QR Code.
            </p>
          </div>
        `;

      } catch {
        content +=
          "<p>Erro ao gerar QR Code.</p>";
      }

    } else if (connected) {

      content += `
        <div class="ok">
          🟢 WhatsApp conectado
        </div>
      `;

    } else {

      content += `
        <div class="wait">
          ⏳ Aguardando conexão...
        </div>
      `;
    }

    res
      .type("html")
      .send(`
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta
  name="viewport"
  content="width=device-width,initial-scale=1"
>
<title>WhatsApp Bot</title>

<style>
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:#111827;
  color:#fff;
  padding:24px;
}

.card{
  max-width:620px;
  margin:auto;
  background:#1f2937;
  border-radius:20px;
  padding:24px;
  text-align:center;
}

.qr{
  display:inline-block;
  background:#fff;
  color:#111;
  padding:16px;
  border-radius:16px;
}

.qr img{
  max-width:100%;
  display:block;
}

.ok,.wait{
  margin-top:18px;
  padding:16px;
  border-radius:12px;
  background:#111827;
}

.ok{
  color:#86efac;
}

p{
  line-height:1.5;
  color:#cbd5e1;
}

a{
  color:#c4b5fd;
  text-decoration:none;
}
</style>

</head>

<body>

<div class="card">

<h1>🤖 Caçador de Ofertas</h1>

${content}

<p>
<a href="/admin">
Área administrativa
</a>
</p>

</div>

<script>
setTimeout(
  () => location.reload(),
  5000
);
</script>

</body>
</html>
`);
  }
);

app.get(
  "/admin",
  async (req, res) => {

    const logged =
      authorized(req);

    if (!logged) {
      return res
        .type("html")
        .send(`
<!doctype html>
<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>Admin - WhatsApp</title>

<style>

body{
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
background:#111827;
color:#fff;
padding:24px
}

.card{
max-width:520px;
margin:auto;
background:#1f2937;
padding:24px;
border-radius:20px
}

input,button{
width:100%;
box-sizing:border-box;
padding:14px;
border-radius:10px;
border:0;
margin-top:12px;
font-size:16px
}

button{
background:#7c3aed;
color:white;
font-weight:700
}

p{
color:#cbd5e1;
line-height:1.5
}

</style>

</head>

<body>

<div class="card">

<h1>
🔐 Área administrativa
</h1>

<p>
Digite a BOT_KEY configurada no Railway.
</p>

<form
method="post"
action="/admin/login"
>

<input
type="password"
name="key"
placeholder="BOT_KEY"
autocomplete="current-password"
required
>

<button type="submit">
Entrar
</button>

</form>

</div>

</body>

</html>
`);
    }

    let groups =
      sortedCachedGroups();

    let loadError = "";

    if (
      !groups.length &&
      connected
    ) {
      try {

        groups =
          await getGroups(
            false
          );

      } catch (err) {

        loadError =
          err?.message ||
          String(err);
      }
    }

    const esc =
      (value) =>
        String(
          value ?? ""
        )
          .replaceAll(
            "&",
            "&amp;"
          )
          .replaceAll(
            "<",
            "&lt;"
          )
          .replaceAll(
            ">",
            "&gt;"
          )
          .replaceAll(
            '"',
            "&quot;"
          )
          .replaceAll(
            "'",
            "&#039;"
          );

    const groupsHtml =
      groups.length
        ? groups
            .map((g) => {

              const selected =
                config.selectedGroupId ===
                g.id;

              const encoded =
                encodeURIComponent(
                  g.id
                );

              return `
<div class="group">

<strong>
${esc(g.name)}
${selected ? " ✅" : ""}
</strong>

<small>
${g.participants || 0}
participantes
</small>

<button
onclick="selectGroup('${encoded}')"
>
${
  selected
    ? "Grupo selecionado"
    : "Selecionar este grupo"
}
</button>

<button
class="test"
onclick="sendTest('${encoded}')"
>
🟢 Enviar TESTE
</button>

</div>
`;

            })
            .join("")

        : `
<p>
${esc(
  loadError ||
    "Nenhum grupo carregado."
)}
</p>
`;

    const adminStatusText =
      connected
        ? `🟢 WhatsApp conectado — ${groups.length} grupos carregados`
        : `🟡 ${esc(statusText)}`;

    res
      .type("html")
      .send(`
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>
Admin - Caçador de Ofertas
</title>

<style>

body{
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
background:#111827;
color:#fff;
padding:18px
}

.card{
max-width:700px;
margin:auto;
background:#1f2937;
padding:22px;
border-radius:20px
}

h1{
font-size:24px
}

.status{
padding:14px;
border-radius:12px;
background:#111827;
margin:14px 0
}

.ok{
color:#86efac
}

.warn{
color:#fbbf24
}

.group{
padding:15px;
margin:10px 0;
border-radius:14px;
background:#111827
}

.group strong{
display:block;
font-size:17px
}

.group small{
display:block;
margin-top:5px;
color:#94a3b8
}

button{
width:100%;
padding:13px;
margin-top:10px;
border:0;
border-radius:10px;
background:#7c3aed;
color:#fff;
font-size:16px;
font-weight:700
}

button.test{
background:#059669
}

#result{
margin-top:14px;
padding:12px;
border-radius:10px;
background:#111827;
color:#cbd5e1
}

</style>

</head>

<body>

<div class="card">

<h1>
📱 Grupos do WhatsApp
</h1>

<div
id="status"
class="status ${
  connected
    ? "ok"
    : "warn"
}"
>
${adminStatusText}
</div>

<button
onclick="loadGroups()"
>
🔄 Atualizar grupos
</button>

<div id="groups">
${groupsHtml}
</div>

<div id="result">
</div>

</div>

<script>

function esc(v){
return String(v)
.replaceAll("&","&amp;")
.replaceAll("<","&lt;")
.replaceAll(">","&gt;")
.replaceAll('"',"&quot;")
.replaceAll("'","&#039;");
}

async function loadGroups(){

const box =
document.getElementById("groups");

const result =
document.getElementById("result");

box.innerHTML =
"<p>Atualizando grupos...</p>";

result.innerText = "";

try{

const r =
await fetch(
"/api/groups?refresh=1",
{
cache:"no-store",
credentials:"same-origin"
}
);

const d =
await r.json();

if(!r.ok){

box.innerHTML =
"<p>Erro: "+
esc(
d.error ||
"não autorizado"
)+
"</p>";

return;
}

if(
!d.groups ||
!d.groups.length
){

box.innerHTML =
"<p>Nenhum grupo encontrado.</p>";

return;
}

box.innerHTML =
d.groups
.map(
g => {

const id =
encodeURIComponent(
g.id
);

const selected =
d.selectedGroupId ===
g.id;

return `
<div class="group">

<strong>
${esc(g.name)}
${selected ? " ✅" : ""}
</strong>

<small>
${g.participants || 0}
participantes
</small>

<button
onclick="selectGroup('${id}')"
>
${
selected
? "Grupo selecionado"
: "Selecionar este grupo"
}
</button>

<button
class="test"
onclick="sendTest('${id}')"
>
🟢 Enviar TESTE
</button>

</div>
`;

}
)
.join("");

}
catch(e){

box.innerHTML =
"<p>Erro ao carregar grupos: "+
esc(e.message)+
"</p>";

}

}

async function selectGroup(
encodedId
){

const id =
decodeURIComponent(
encodedId
);

const result =
document.getElementById(
"result"
);

result.innerText =
"Salvando grupo...";

try{

const r =
await fetch(
"/api/select-group",
{
method:"POST",
headers:{
"Content-Type":
"application/json"
},
credentials:
"same-origin",
body:
JSON.stringify({
jid:id
})
}
);

const d =
await r.json();

result.innerText =
d.ok
? "✅ Grupo selecionado com sucesso."
: "❌ "+
(
d.error ||
"Erro."
);

if(d.ok){
loadGroups();
}

}
catch(e){

result.innerText =
"❌ Erro ao selecionar grupo.";

}

}

async function sendTest(
encodedId
){

const id =
decodeURIComponent(
encodedId
);

const result =
document.getElementById(
"result"
);

result.innerText =
"Enviando mensagem de teste...";

try{

const r =
await fetch(
"/api/send-test",
{
method:"POST",
headers:{
"Content-Type":
"application/json"
},
credentials:
"same-origin",
body:
JSON.stringify({
jid:id
})
}
);

const d =
await r.json();

result.innerText =
d.ok
? "✅ TESTE enviado para o grupo."
: "❌ "+
(
d.error ||
"Erro."
);

}
catch(e){

result.innerText =
"❌ Erro ao enviar TESTE.";

}

}

</script>

</body>
</html>
`);
  }
);

app.post(
  "/admin/login",
  (req, res) => {

    const key =
      String(
        req.body?.key || ""
      );

    if (
      !safeEqual(
        key,
        BOT_KEY
      )
    ) {

      return res
        .status(401)
        .type("html")
        .send(`
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>Erro</title>

</head>

<body
style="font-family:sans-serif;padding:30px"
>

<h2>
❌ BOT_KEY inválida
</h2>

<p>
Volte e tente novamente.
</p>

</body>

</html>
`);

    }

    const token =
      makeAdminToken();

    res.setHeader(
      "Set-Cookie",
      `wa_admin=${encodeURIComponent(token)}; Path=/; Max-Age=86400; HttpOnly; Secure; SameSite=Lax`
    );

    res.redirect(
      "/admin"
    );
  }
);

app.post(
  "/admin/logout",
  (_req, res) => {

    res.setHeader(
      "Set-Cookie",
      "wa_admin=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
    );

    res.redirect(
      "/admin"
    );
  }
);

app.get(
  "/health",
  (_req, res) => {

    res.json({
      ok: true,
      ...status()
    });

  }
);

app.get(
  "/api/status",
  requireBotKey,
  (_req, res) => {

    res.json(
      status()
    );

  }
);

app.get(
  "/api/groups",
  requireBotKey,
  async (req, res) => {

    try {

      const groups =
        await getGroups(
          req.query.refresh === "1"
        );

      res.json({
        ok: true,

        selectedGroupId:
          config.selectedGroupId ||
          null,

        groups
      });

    } catch (err) {

      res.status(503).json({
        ok: false,

        error:
          err?.message ||
          String(err)
      });

    }

  }
);

app.post(
  "/api/select-group",
  requireBotKey,
  async (req, res) => {

    const jid =
      String(
        req.body?.jid || ""
      ).trim();

    if (
      !jid.endsWith("@g.us")
    ) {

      return res
        .status(400)
        .json({
          ok: false,
          error:
            "ID de grupo inválido."
        });

    }

    try {

      const groups =
        await getGroups();

      const exists =
        groups.some(
          (group) =>
            group.id === jid
        );

      if (!exists) {

        return res
          .status(404)
          .json({
            ok: false,
            error:
              "Esse grupo não foi encontrado na sua conta."
          });

      }

      config.selectedGroupId =
        jid;

      saveConfig(
        config
      );

      res.json({
        ok: true,
        selectedGroupId:
          jid
      });

    } catch (err) {

      res
        .status(503)
        .json({
          ok: false,
          error:
            err?.message ||
            String(err)
        });

    }

  }
);

app.post(
  "/api/send-test",
  requireBotKey,
  async (req, res) => {

    const jid =
      String(
        req.body?.jid || ""
      ).trim();

    if (
      !jid.endsWith("@g.us")
    ) {

      return res
        .status(400)
        .json({
          ok: false,
          error:
            "ID de grupo inválido."
        });

    }

    try {

      await sendGroupMessage(
        jid,
        "🟢 TESTE — Caçador de Ofertas conectado ao WhatsApp."
      );

      res.json({
        ok: true,
        message:
          "Mensagem de teste enviada."
      });

    } catch (err) {

      res
        .status(500)
        .json({
          ok: false,
          error:
            err?.message ||
            String(err)
        });

    }

  }
);

/*
============================================================
ENVIO DE OFERTA
============================================================
*/

app.post(
  "/api/send-offer",
  requireBotKey,
  async (req, res) => {

    try {

      const text =
        String(
          req.body?.text || ""
        ).trim();

      if (!text) {

        return res
          .status(400)
          .json({
            ok: false,
            error:
              "Mensagem vazia."
          });

      }

      const jid =
        config.selectedGroupId;

      if (
        !jid ||
        !jid.endsWith("@g.us")
      ) {

        return res
          .status(400)
          .json({
            ok: false,
            error:
              "Nenhum grupo do WhatsApp foi selecionado."
          });

      }

      await sendGroupMessage(
        jid,
        text
      );

      console.log(
        `[OFERTA] Mensagem enviada para ${jid}`
      );

      return res.json({
        ok: true,
        message:
          "Oferta enviada para o grupo.",
        groupId:
          jid
      });

    } catch (err) {

      console.error(
        "[OFERTA] Erro ao enviar:",
        err
      );

      return res
        .status(500)
        .json({
          ok: false,
          error:
            err?.message ||
            String(err)
        });

    }

  }
);

app.listen(
  PORT,
  "0.0.0.0",
  () => {

    console.log(
      `[WEB] Bot na porta ${PORT}`
    );

    console.log(
      `[WEB] Sessão: ${AUTH_DIR}`
    );

    console.log(
      `[WEB] Configuração: ${CONFIG_FILE}`
    );

    startWhatsApp()
      .catch(
        console.error
      );

  }
);