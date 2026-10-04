
import os
import sqlite3
from datetime import datetime
from urllib.parse import urlparse, quote_plus

import requests
from flask import Flask, jsonify, request, render_template_string, redirect

APP_NAME = "Caçador de Ofertas"
DB_PATH = os.getenv("DB_PATH", "ofertas_mvp.db")
PORT = int(os.getenv("PORT", "8080"))

ML_API = "https://api.mercadolibre.com"
ML_SITE = "MLB"
ML_ACCESS_TOKEN = os.getenv("ML_ACCESS_TOKEN", "").strip()

MIN_PRICE = float(os.getenv("MIN_PRICE", "69.90"))
MIN_DISCOUNT = float(os.getenv("MIN_DISCOUNT", "10"))
SEARCH_LIMIT = 20

SEARCHES = [
    ("Eletrônicos", "fone bluetooth"),
    ("Eletrônicos", "caixa de som bluetooth"),
    ("Celulares", "celular"),
    ("Celulares", "smartphone"),
    ("Casa", "air fryer"),
    ("Casa", "liquidificador"),
    ("Casa", "aspirador"),
    ("Ferramentas", "furadeira"),
    ("Ferramentas", "kit ferramentas"),
    ("Moda", "tenis feminino"),
    ("Moda", "tenis masculino"),
    ("Moda", "mochila"),
    ("Academia", "creatina"),
    ("Academia", "halter"),
    ("Automotivo", "ferramentas automotivas"),
]

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
            confirmado_em TEXT,
            item_id TEXT DEFAULT '',
            categoria TEXT DEFAULT ''
        )
    """)
    # Compatibilidade com banco criado pela versão anterior.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(ofertas)").fetchall()}
    for name, sql in [
        ("item_id", "ALTER TABLE ofertas ADD COLUMN item_id TEXT DEFAULT ''"),
        ("categoria", "ALTER TABLE ofertas ADD COLUMN categoria TEXT DEFAULT ''"),
    ]:
        if name not in cols:
            conn.execute(sql)
    conn.commit()
    conn.close()


def row_dict(row):
    return dict(row) if row else None


def desconto(preco, anterior):
    try:
        preco = float(preco or 0)
        anterior = float(anterior or 0)
        if anterior > preco > 0:
            return round((1 - preco / anterior) * 100, 1)
    except Exception:
        pass
    return 0.0


def validar_url(url):
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


# ============================================================
# MERCADO LIVRE - BUSCA AUTOMÁTICA
# ============================================================

def ml_headers():
    if ML_ACCESS_TOKEN:
        return {"Authorization": f"Bearer {ML_ACCESS_TOKEN}"}
    return {}


def ml_search(query, limit=SEARCH_LIMIT):
    """
    Busca anúncios reais no Mercado Livre.
    O token é opcional no código para a aplicação subir normalmente,
    mas para a busca automática funcionar no Railway, configure
    ML_ACCESS_TOKEN nas Variables.
    """
    params = {
        "q": query,
        "limit": min(int(limit), 50),
        "sort": "relevance",
    }

    try:
        r = requests.get(
            f"{ML_API}/sites/{ML_SITE}/search",
            params=params,
            headers=ml_headers(),
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"[ML ERRO REDE] {query} | {e}")
        return [], {"ok": False, "status": 0, "erro": str(e)}

    print(f"[ML BUSCA] {query} | HTTP {r.status_code}")

    if r.status_code != 200:
        print(f"[ML RESPOSTA] {r.text[:500]}")
        return [], {
            "ok": False,
            "status": r.status_code,
            "erro": r.text[:500],
        }

    try:
        data = r.json()
    except Exception:
        return [], {"ok": False, "status": r.status_code, "erro": "JSON inválido"}

    return data.get("results", []) or [], {
        "ok": True,
        "status": 200,
        "total": data.get("paging", {}).get("total", 0),
    }


def normalizar_item(item, categoria):
    if not isinstance(item, dict):
        return None

    item_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    permalink = str(item.get("permalink") or "").strip()
    price = item.get("price")

    try:
        price = float(price)
    except Exception:
        return None

    if not item_id or not title or not permalink or price < MIN_PRICE:
        return None

    # O preço anterior pode aparecer em diferentes campos do resultado.
    old_price = item.get("original_price")
    try:
        old_price = float(old_price) if old_price else 0.0
    except Exception:
        old_price = 0.0

    # Alguns anúncios não expõem preço anterior.
    # Não inventamos desconto.
    disc = desconto(price, old_price)

    if disc < MIN_DISCOUNT:
        return None

    thumb = item.get("thumbnail") or ""
    if thumb.startswith("http://"):
        thumb = "https://" + thumb[7:]

    return {
        "item_id": item_id,
        "titulo": title,
        "url": permalink,
        "preco": price,
        "preco_anterior": old_price,
        "desconto": disc,
        "imagem": thumb,
        "categoria": categoria,
    }


def salvar_oferta(item):
    conn = db()

    # Não duplica anúncio que já existe.
    existe = conn.execute(
        "SELECT id FROM ofertas WHERE item_id=? AND status!='ignorada' LIMIT 1",
        (item["item_id"],)
    ).fetchone()

    if existe:
        conn.close()
        return False

    conn.execute("""
        INSERT INTO ofertas
        (titulo,url,preco,preco_anterior,desconto,imagem,origem,status,criado_em,item_id,categoria)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        item["titulo"],
        item["url"],
        item["preco"],
        item["preco_anterior"],
        item["desconto"],
        item["imagem"],
        "mercado_livre",
        "pendente",
        datetime.now().isoformat(timespec="seconds"),
        item["item_id"],
        item["categoria"],
    ))
    conn.commit()
    conn.close()
    return True


def buscar_ofertas():
    if not ML_ACCESS_TOKEN:
        return {
            "ok": False,
            "erro": "ML_ACCESS_TOKEN não configurado no Railway.",
            "encontradas": 0,
            "adicionadas": 0,
            "buscas": [],
        }

    total_raw = 0
    validas = 0
    adicionadas = 0
    buscas = []

    seen = set()

    for categoria, query in SEARCHES:
        results, info = ml_search(query)

        item_validos = 0
        item_adicionados = 0

        for raw in results:
            total_raw += 1
            item = normalizar_item(raw, categoria)
            if not item:
                continue

            validas += 1

            if item["item_id"] in seen:
                continue

            seen.add(item["item_id"])

            if salvar_oferta(item):
                adicionadas += 1
                item_adicionados += 1

            item_validos += 1

        buscas.append({
            "categoria": categoria,
            "query": query,
            "status": info.get("status"),
            "total_api": info.get("total", 0),
            "validas": item_validos,
            "adicionadas": item_adicionados,
        })

        print(
            f"[BUSCA] {categoria} | {query} | "
            f"api={info.get('status')} validas={item_validos} "
            f"novas={item_adicionados}"
        )

    return {
        "ok": True,
        "encontradas": validas,
        "adicionadas": adicionadas,
        "brutas": total_raw,
        "buscas": buscas,
        "min_preco": MIN_PRICE,
        "min_desconto": MIN_DISCOUNT,
    }


# ============================================================
# INTERFACE
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
.logo{font-size:21px;font-weight:800}.badge{background:#16a34a;padding:7px 10px;border-radius:999px;font-size:12px;font-weight:700}
main{max-width:1100px;margin:20px auto;padding:0 14px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
.card{background:#fff;border-radius:14px;padding:16px;box-shadow:0 2px 10px #0000000b}
.num{font-size:28px;font-weight:800;margin-top:6px}.label{font-size:12px;color:#6b7280}
.panel{background:#fff;border-radius:14px;padding:15px;margin-bottom:14px}
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{border:0;border-radius:10px;padding:11px 14px;font-weight:700;cursor:pointer}
.green{background:#16a34a;color:#fff}.red{background:#dc2626;color:#fff}.dark{background:#111827;color:#fff}.light{background:#e5e7eb;color:#111827}
.note{background:#eff6ff;border:1px solid #bfdbfe;padding:12px;border-radius:12px;font-size:13px;margin-bottom:14px}
.statusbox{font-size:13px;color:#4b5563;margin-top:10px;white-space:pre-wrap}
form{display:grid;grid-template-columns:2fr 2fr 1fr 1fr auto;gap:8px;background:#fff;padding:14px;border-radius:14px;margin-bottom:14px}
input{width:100%;padding:11px;border:1px solid #d1d5db;border-radius:9px;font-size:14px}
.offer{display:grid;grid-template-columns:90px 1fr auto;gap:14px;align-items:center;background:#fff;border-radius:14px;padding:13px;margin-bottom:10px;box-shadow:0 2px 9px #00000009}
.offer img{width:90px;height:90px;border-radius:10px;object-fit:cover;background:#eee}
.title{font-weight:800;margin-bottom:7px}.meta{font-size:13px;color:#6b7280;line-height:1.5}
.price{font-size:21px;font-weight:800}.old{text-decoration:line-through;color:#9ca3af;font-size:12px}
.actions{display:flex;gap:7px;flex-direction:column}
.status{display:inline-block;padding:5px 8px;border-radius:999px;font-size:11px;font-weight:700}
.pendente{background:#fef3c7;color:#92400e}.confirmada{background:#dcfce7;color:#166534}.ignorada{background:#fee2e2;color:#991b1b}
.empty{background:#fff;padding:35px;text-align:center;border-radius:14px;color:#6b7280}
@media(max-width:800px){.grid{grid-template-columns:repeat(2,1fr)}form{grid-template-columns:1fr}.offer{grid-template-columns:65px 1fr}.offer img{width:65px;height:65px}.actions{grid-column:1/-1;flex-direction:row}.actions button{flex:1}}
</style>
</head>
<body>
<header><div class="header"><div class="logo">🎯 {{ app_name }}</div><div class="badge">MVP • busca automática</div></div></header>
<main>

<div class="grid">
<div class="card"><div class="label">Pendentes</div><div class="num">{{ stats.pendente }}</div></div>
<div class="card"><div class="label">Confirmadas</div><div class="num">{{ stats.confirmada }}</div></div>
<div class="card"><div class="label">Ignoradas</div><div class="num">{{ stats.ignorada }}</div></div>
<div class="card"><div class="label">Total</div><div class="num">{{ stats.total }}</div></div>
</div>

<div class="note">
<b>Etapa 2:</b> o botão abaixo busca anúncios reais do Mercado Livre.
As ofertas encontradas entram como <b>PENDENTES</b>. Nada é enviado automaticamente.
Você continua decidindo o que vai para a fila do WhatsApp.
</div>

<div class="panel">
<div class="controls">
<button class="green" onclick="buscar()">🔎 BUSCAR OFERTAS AGORA</button>
<button class="dark" onclick="carregar()">↻ ATUALIZAR</button>
</div>
<div id="statusbusca" class="statusbox"></div>
</div>

<form method="post" action="/oferta">
<input name="titulo" placeholder="Nome do produto" required>
<input name="url" placeholder="Link do produto" required>
<input name="preco" type="number" step="0.01" placeholder="Preço atual" required>
<input name="preco_anterior" type="number" step="0.01" placeholder="Preço anterior">
<input name="imagem" placeholder="URL da imagem">
<button class="dark">Adicionar</button>
</form>

<div class="controls" style="margin:14px 0">
<button class="light" onclick="filtrar('pendente')">Pendentes</button>
<button class="green" onclick="filtrar('confirmada')">Confirmadas / fila WhatsApp</button>
<button class="light" onclick="filtrar('todas')">Todas</button>
</div>

<div id="lista"></div>
</main>

<script>
let filtro='pendente';

async function carregar(){
  const r=await fetch('/api/ofertas?status='+filtro);
  const data=await r.json();
  const el=document.getElementById('lista');

  if(!data.length){
    el.innerHTML='<div class="empty">Nenhuma oferta nesta lista.</div>';
    return;
  }

  el.innerHTML=data.map(o=>`
    <div class="offer">
      <img src="${esc(o.imagem||'')}" onerror="this.style.visibility='hidden'">
      <div>
        <div class="title">${esc(o.titulo)}</div>
        <div class="meta">
          <span class="status ${o.status}">${o.status}</span>
          &nbsp; ${o.categoria ? esc(o.categoria)+' • ' : ''}
          ${o.desconto>0 ? '<b>-'+o.desconto+'%</b>' : ''}
        </div>
        <div class="price">R$ ${Number(o.preco).toFixed(2).replace('.',',')}</div>
        ${o.preco_anterior>0 ? '<div class="old">R$ '+Number(o.preco_anterior).toFixed(2).replace('.',',')+'</div>' : ''}
      </div>
      <div class="actions">
        ${o.status==='pendente' ? `<button class="green" onclick="acao(${o.id},'confirmar')">CONFIRMAR</button><button class="red" onclick="acao(${o.id},'ignorar')">IGNORAR</button>` : ''}
        ${o.status!=='pendente' ? `<button class="light" onclick="acao(${o.id},'reverter')">REVERTER</button>` : ''}
        <button class="dark" onclick="window.open('${esc(o.url)}','_blank')">ABRIR LINK</button>
      </div>
    </div>
  `).join('');
}

async function acao(id,a){
  const r=await fetch('/api/ofertas/'+id+'/'+a,{method:'POST'});
  if(!r.ok){alert('Não foi possível atualizar a oferta.');return}
  carregar();
}

async function buscar(){
  const box=document.getElementById('statusbusca');
  box.textContent='Buscando anúncios reais no Mercado Livre...';
  const r=await fetch('/api/buscar',{method:'POST'});
  const data=await r.json();

  if(!data.ok){
    box.textContent='⚠️ '+(data.erro||'Falha na busca.');
    return;
  }

  box.textContent=
    'Busca concluída.\\n'+
    'Anúncios analisados: '+data.brutas+'\\n'+
    'Ofertas que passaram pelos filtros: '+data.encontradas+'\\n'+
    'Novas ofertas adicionadas: '+data.adicionadas+'\\n'+
    'Preço mínimo: R$ '+Number(data.min_preco).toFixed(2).replace('.',',')+
    ' • Desconto mínimo: '+data.min_desconto+'%';

  carregar();
}

function filtrar(s){filtro=s;carregar()}

function esc(v){
 return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'","&#039;");
}

carregar();
</script>
</body>
</html>
"""


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def index():
    conn = db()
    rows = conn.execute("SELECT status,COUNT(*) c FROM ofertas GROUP BY status").fetchall()
    conn.close()

    stats={"pendente":0,"confirmada":0,"ignorada":0,"total":0}
    for r in rows:
        stats[r["status"]]=r["c"]
        stats["total"]+=r["c"]

    return render_template_string(HTML, app_name=APP_NAME, stats=stats)


@app.post("/oferta")
def criar_oferta():
    titulo=(request.form.get("titulo") or "").strip()
    url=(request.form.get("url") or "").strip()
    imagem=(request.form.get("imagem") or "").strip()

    try:
        preco=float(request.form.get("preco") or 0)
        anterior=float(request.form.get("preco_anterior") or 0)
    except ValueError:
        return "Preço inválido",400

    if not titulo or not validar_url(url) or preco<=0:
        return "Dados inválidos.",400

    conn=db()
    conn.execute("""
        INSERT INTO ofertas
        (titulo,url,preco,preco_anterior,desconto,imagem,origem,status,criado_em,item_id,categoria)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """,(
        titulo,url,preco,anterior,desconto(preco,anterior),imagem,
        "manual","pendente",datetime.now().isoformat(timespec="seconds"),"",""
    ))
    conn.commit()
    conn.close()
    return redirect("/")


@app.get("/api/ofertas")
def api_ofertas():
    status=request.args.get("status","todas")
    conn=db()

    if status in ("pendente","confirmada","ignorada"):
        rows=conn.execute("SELECT * FROM ofertas WHERE status=? ORDER BY id DESC",(status,)).fetchall()
    else:
        rows=conn.execute("SELECT * FROM ofertas ORDER BY id DESC").fetchall()

    conn.close()
    return jsonify([row_dict(r) for r in rows])


@app.post("/api/ofertas/<int:offer_id>/<action>")
def api_acao(offer_id,action):
    actions={"confirmar":"confirmada","ignorar":"ignorada","reverter":"pendente"}
    if action not in actions:
        return jsonify({"ok":False,"erro":"ação inválida"}),400

    novo=actions[action]
    agora=datetime.now().isoformat(timespec="seconds")
    conn=db()

    if novo=="confirmada":
        conn.execute("UPDATE ofertas SET status=?,confirmado_em=? WHERE id=?",(novo,agora,offer_id))
    else:
        conn.execute("UPDATE ofertas SET status=?,confirmado_em=NULL WHERE id=?",(novo,offer_id))

    changed=conn.total_changes
    conn.commit()
    conn.close()

    if not changed:
        return jsonify({"ok":False,"erro":"oferta não encontrada"}),404

    return jsonify({"ok":True,"id":offer_id,"status":novo})


@app.post("/api/buscar")
def api_buscar():
    return jsonify(buscar_ofertas())


@app.get("/api/whatsapp/fila")
def whatsapp_fila():
    conn=db()
    rows=conn.execute("""
        SELECT id,titulo,url,preco,preco_anterior,desconto,imagem,confirmado_em,item_id,categoria
        FROM ofertas WHERE status='confirmada'
        ORDER BY confirmado_em ASC,id ASC
    """).fetchall()
    conn.close()

    return jsonify({"total":len(rows),"ofertas":[row_dict(r) for r in rows]})


@app.get("/api/status")
def api_status():
    conn=db()
    rows=conn.execute("SELECT status,COUNT(*) c FROM ofertas GROUP BY status").fetchall()
    conn.close()
    data={"pendente":0,"confirmada":0,"ignorada":0}
    for r in rows:data[r["status"]]=r["c"]
    data["total"]=sum(data.values())
    return jsonify(data)


@app.get("/mercadolivre/status")
def mercado_livre_status():
    return jsonify({
        "ok": bool(ML_ACCESS_TOKEN),
        "site": ML_SITE,
        "token_configurado": bool(ML_ACCESS_TOKEN),
        "min_preco": MIN_PRICE,
        "min_desconto": MIN_DISCOUNT,
        "buscas_configuradas": len(SEARCHES),
        "mensagem": (
            "ML_ACCESS_TOKEN configurado."
            if ML_ACCESS_TOKEN
            else "Configure ML_ACCESS_TOKEN nas Variables do Railway."
        )
    })


@app.get("/health")
def health():
    return jsonify({
        "ok":True,
        "app":APP_NAME,
        "etapa":"busca_automatica",
        "mercado_livre_token":bool(ML_ACCESS_TOKEN),
        "whatsapp_fila":True
    })


init_db()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=PORT)
