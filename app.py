
import os
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

from flask import Flask, jsonify, request, render_template_string, redirect

APP_NAME = "Caçador de Ofertas"
DB_PATH = os.getenv("DB_PATH", "ofertas_mvp.db")
PORT = int(os.getenv("PORT", "8080"))

app = Flask(__name__)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            url TEXT NOT NULL,
            preco REAL NOT NULL DEFAULT 0,
            preco_anterior REAL NOT NULL DEFAULT 0,
            desconto REAL NOT NULL DEFAULT 0,
            imagem TEXT DEFAULT '',
            origem TEXT DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'pendente',
            criado_em TEXT NOT NULL,
            confirmado_em TEXT
        )
    """)
    conn.commit()
    conn.close()


def row_dict(row):
    return dict(row) if row else None


def calcular_desconto(preco, anterior):
    try:
        preco = float(preco or 0)
        anterior = float(anterior or 0)
        if anterior > 0 and preco > 0 and anterior > preco:
            return round((1 - preco / anterior) * 100, 1)
    except Exception:
        pass
    return 0


def validar_url(url):
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


# ============================================================
# PAGES
# ============================================================

HTML = r"""
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ app_name }}</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f4f6f8;font-family:Arial,Helvetica,sans-serif;color:#17202a}
header{background:#111827;color:#fff;padding:18px 20px;position:sticky;top:0;z-index:5}
.header{max-width:1100px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:12px}
.logo{font-size:21px;font-weight:800}
.badge{background:#16a34a;padding:7px 10px;border-radius:999px;font-size:12px;font-weight:700}
main{max-width:1100px;margin:20px auto;padding:0 14px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
.card{background:#fff;border-radius:14px;padding:16px;box-shadow:0 2px 10px #0000000b}
.num{font-size:28px;font-weight:800;margin-top:6px}
.label{font-size:12px;color:#6b7280}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}
button{border:0;border-radius:10px;padding:11px 14px;font-weight:700;cursor:pointer}
.green{background:#16a34a;color:#fff}.red{background:#dc2626;color:#fff}.dark{background:#111827;color:#fff}.light{background:#e5e7eb;color:#111827}
form{display:grid;grid-template-columns:2fr 2fr 1fr 1fr auto;gap:8px;background:#fff;padding:14px;border-radius:14px}
input{width:100%;padding:11px;border:1px solid #d1d5db;border-radius:9px;font-size:14px}
.offer{display:grid;grid-template-columns:90px 1fr auto;gap:14px;align-items:center;background:#fff;border-radius:14px;padding:13px;margin-bottom:10px;box-shadow:0 2px 9px #00000009}
.offer img{width:90px;height:90px;border-radius:10px;object-fit:cover;background:#eee}
.title{font-weight:800;margin-bottom:7px}
.meta{font-size:13px;color:#6b7280;line-height:1.5}
.price{font-size:21px;font-weight:800}
.old{text-decoration:line-through;color:#9ca3af;font-size:12px}
.actions{display:flex;gap:7px;flex-direction:column}
.status{display:inline-block;padding:5px 8px;border-radius:999px;font-size:11px;font-weight:700}
.pendente{background:#fef3c7;color:#92400e}.confirmada{background:#dcfce7;color:#166534}.ignorada{background:#fee2e2;color:#991b1b}
.empty{background:#fff;padding:35px;text-align:center;border-radius:14px;color:#6b7280}
.note{background:#eff6ff;border:1px solid #bfdbfe;padding:12px;border-radius:12px;font-size:13px;margin-bottom:14px}
@media(max-width:800px){.grid{grid-template-columns:repeat(2,1fr)}form{grid-template-columns:1fr}.offer{grid-template-columns:65px 1fr}.offer img{width:65px;height:65px}.actions{grid-column:1/-1;flex-direction:row}.actions button{flex:1}}
</style>
</head>
<body>
<header><div class="header"><div class="logo">🎯 {{ app_name }}</div><div class="badge">MVP • confirmação</div></div></header>
<main>
<div class="grid">
<div class="card"><div class="label">Pendentes</div><div class="num">{{ stats.pendente }}</div></div>
<div class="card"><div class="label">Confirmadas</div><div class="num">{{ stats.confirmada }}</div></div>
<div class="card"><div class="label">Ignoradas</div><div class="num">{{ stats.ignorada }}</div></div>
<div class="card"><div class="label">Total</div><div class="num">{{ stats.total }}</div></div>
</div>

<div class="note">
<b>Primeira etapa:</b> aqui nós validamos o fluxo de ofertas sem depender ainda de WhatsApp,
cupons, ranking ou automações complexas. Uma oferta só entra na fila de envio depois que você clicar em <b>CONFIRMAR</b>.
</div>

<form method="post" action="/oferta">
<input name="titulo" placeholder="Nome do produto" required>
<input name="url" placeholder="Link do produto" required>
<input name="preco" type="number" step="0.01" placeholder="Preço atual" required>
<input name="preco_anterior" type="number" step="0.01" placeholder="Preço anterior">
<input name="imagem" placeholder="URL da imagem">
<button class="dark">Adicionar</button>
</form>

<div class="toolbar">
<button class="light" onclick="filtrar('pendente')">Pendentes</button>
<button class="green" onclick="filtrar('confirmada')">Confirmadas / fila WhatsApp</button>
<button class="light" onclick="filtrar('todas')">Todas</button>
</div>

<div id="lista"></div>
</main>

<script>
let filtro = 'pendente';

async function carregar(){
  const r = await fetch('/api/ofertas?status=' + filtro);
  const data = await r.json();
  const el = document.getElementById('lista');

  if(!data.length){
    el.innerHTML = '<div class="empty">Nenhuma oferta nesta lista.</div>';
    return;
  }

  el.innerHTML = data.map(o => `
    <div class="offer">
      <img src="${esc(o.imagem || '')}" onerror="this.style.visibility='hidden'">
      <div>
        <div class="title">${esc(o.titulo)}</div>
        <div class="meta">
          <span class="status ${o.status}">${o.status}</span>
          &nbsp; ${o.desconto > 0 ? '<b>-' + o.desconto + '%</b>' : ''}
        </div>
        <div class="price">R$ ${Number(o.preco).toFixed(2).replace('.',',')}</div>
        ${o.preco_anterior > 0 ? '<div class="old">R$ '+Number(o.preco_anterior).toFixed(2).replace('.',',')+'</div>' : ''}
      </div>
      <div class="actions">
        ${o.status === 'pendente' ? `<button class="green" onclick="acao(${o.id},'confirmar')">CONFIRMAR</button><button class="red" onclick="acao(${o.id},'ignorar')">IGNORAR</button>` : ''}
        ${o.status !== 'pendente' ? `<button class="light" onclick="acao(${o.id},'reverter')">REVERTER</button>` : ''}
        <button class="dark" onclick="window.open('${esc(o.url)}','_blank')">ABRIR LINK</button>
      </div>
    </div>
  `).join('');
}

function esc(v){
  return String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'","&#039;");
}

async function acao(id, acao){
  const r = await fetch('/api/ofertas/' + id + '/' + acao, {method:'POST'});
  if(!r.ok){ alert('Não foi possível atualizar a oferta.'); return; }
  carregar();
}

function filtrar(s){ filtro=s; carregar(); }
carregar();
</script>
</body>
</html>
"""


@app.get("/")
def index():
    conn = db()
    rows = conn.execute("""
        SELECT status, COUNT(*) c FROM ofertas GROUP BY status
    """).fetchall()
    conn.close()

    stats = {"pendente": 0, "confirmada": 0, "ignorada": 0, "total": 0}
    for r in rows:
        stats[r["status"]] = r["c"]
        stats["total"] += r["c"]

    return render_template_string(HTML, app_name=APP_NAME, stats=stats)


# ============================================================
# OFFER CREATION
# ============================================================

@app.post("/oferta")
def criar_oferta():
    titulo = (request.form.get("titulo") or "").strip()
    url = (request.form.get("url") or "").strip()
    imagem = (request.form.get("imagem") or "").strip()

    try:
        preco = float(request.form.get("preco") or 0)
        anterior = float(request.form.get("preco_anterior") or 0)
    except ValueError:
        return "Preço inválido", 400

    if not titulo or not validar_url(url) or preco <= 0:
        return "Dados inválidos. Informe título, URL e preço.", 400

    desconto = calcular_desconto(preco, anterior)

    conn = db()
    conn.execute("""
        INSERT INTO ofertas
        (titulo,url,preco,preco_anterior,desconto,imagem,origem,status,criado_em)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        titulo, url, preco, anterior, desconto, imagem,
        "manual", "pendente", datetime.now().isoformat(timespec="seconds")
    ))
    conn.commit()
    conn.close()

    return redirect("/")


# ============================================================
# API
# ============================================================

@app.get("/api/ofertas")
def api_ofertas():
    status = request.args.get("status", "todas")
    conn = db()

    if status in ("pendente", "confirmada", "ignorada"):
        rows = conn.execute(
            "SELECT * FROM ofertas WHERE status=? ORDER BY id DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM ofertas ORDER BY id DESC").fetchall()

    conn.close()
    return jsonify([row_dict(r) for r in rows])


@app.post("/api/ofertas/<int:offer_id>/<action>")
def api_acao(offer_id, action):
    actions = {
        "confirmar": "confirmada",
        "ignorar": "ignorada",
        "reverter": "pendente",
    }

    if action not in actions:
        return jsonify({"ok": False, "erro": "ação inválida"}), 400

    novo_status = actions[action]
    agora = datetime.now().isoformat(timespec="seconds")

    conn = db()
    if novo_status == "confirmada":
        conn.execute(
            "UPDATE ofertas SET status=?, confirmado_em=? WHERE id=?",
            (novo_status, agora, offer_id)
        )
    else:
        conn.execute(
            "UPDATE ofertas SET status=?, confirmado_em=NULL WHERE id=?",
            (novo_status, offer_id)
        )

    changed = conn.total_changes
    conn.commit()
    conn.close()

    if not changed:
        return jsonify({"ok": False, "erro": "oferta não encontrada"}), 404

    return jsonify({"ok": True, "id": offer_id, "status": novo_status})


# Fila pronta para a próxima etapa: WhatsApp.
@app.get("/api/whatsapp/fila")
def whatsapp_fila():
    conn = db()
    rows = conn.execute("""
        SELECT id,titulo,url,preco,preco_anterior,desconto,imagem,confirmado_em
        FROM ofertas
        WHERE status='confirmada'
        ORDER BY confirmado_em ASC, id ASC
    """).fetchall()
    conn.close()

    return jsonify({
        "total": len(rows),
        "ofertas": [row_dict(r) for r in rows]
    })


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "app": APP_NAME,
        "etapa": "confirmacao_de_ofertas",
        "whatsapp": "fila_pronta_para_integracao"
    })


@app.get("/api/status")
def status():
    conn = db()
    rows = conn.execute("""
        SELECT status, COUNT(*) c FROM ofertas GROUP BY status
    """).fetchall()
    conn.close()

    data = {"pendente": 0, "confirmada": 0, "ignorada": 0}
    for r in rows:
        data[r["status"]] = r["c"]

    data["total"] = sum(data.values())
    return jsonify(data)


# ============================================================
# DEMO
# ============================================================

@app.post("/api/demo")
def demo():
    samples = [
        ("Oferta teste - Fone Bluetooth", "https://www.mercadolivre.com.br/", 79.90, 119.90),
        ("Oferta teste - Smartwatch", "https://www.mercadolivre.com.br/", 129.90, 179.90),
        ("Oferta teste - Caixa de Som", "https://www.mercadolivre.com.br/", 159.90, 229.90),
    ]

    conn = db()
    for titulo, url, preco, anterior in samples:
        conn.execute("""
            INSERT INTO ofertas
            (titulo,url,preco,preco_anterior,desconto,origem,status,criado_em)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            titulo, url, preco, anterior, calcular_desconto(preco, anterior),
            "demo", "pendente", datetime.now().isoformat(timespec="seconds")
        ))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "adicionadas": len(samples)})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
