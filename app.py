import random
import os
import sqlite3
import secrets
import hashlib
import base64
import time
import re
import html as html_lib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor, as_completed
from urllib.parse import urlencode, quote, urlparse

import requests
from flask import Flask, request, redirect, session, jsonify, render_template_string

app = Flask(__name__)

# DESATIVADO MODO TESTE APENAS PERFUMES PARA EXIBIR TODAS AS CATEGORIAS
TESTE_SOMENTE_PERFUMES = False
app.secret_key = os.getenv("FLASK_SECRET_KEY", "chave-cacador-ofertas")

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"

# ============================================================
# WHATSAPP BOT
# ============================================================

WHATSAPP_BOT_URL = os.getenv(
    "WHATSAPP_BOT_URL",
    "https://whatsapp-bot-production-c647.up.railway.app"
).strip().rstrip("/")
WHATSAPP_BOT_KEY = os.getenv("WHATSAPP_BOT_KEY", "").strip()

# ============================================================
# CONFIGURAÇÕES DE AMBIENTE
# ============================================================

OPENAI_API_KEY = ""
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2"
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app"
).strip().rstrip("/")
WHATSAPP_IMAGE_DIR = os.path.join("/tmp", "cacador_whatsapp_images")
os.makedirs(WHATSAPP_IMAGE_DIR, exist_ok=True)
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"
SITE_ID = "MLB"

PERSISTENT_DATA_DIR = "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(PERSISTENT_DATA_DIR, "ofertas.db")
COUPONS_URL = "https://www.mercadolivre.com.br/l/promocoes"
COUPON_SOURCE_URLS = [
    "https://www.mercadolivre.com.br/l/promocoes",
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/ofertas/cupons",
]

# REDUZIDO PARA EXIBIR MAIS OPÇÕES DE PRODUTOS
MIN_PRODUCT_PRICE = 29.90

# ============================================================
# FILTROS FLEXIBILIZADOS PARA EVITAR MENSAGEM DE "SEM PRODUTOS"
# ============================================================
MIN_ITEM_SOLD_QUANTITY = 5
ALLOWED_POWER_SELLER_STATUS = {"gold", "platinum", "silver", "5_green", None, ""}
REQUIRE_FULL_LOGISTICS = False

_ITEM_QUALITY_CACHE = {}
_ITEM_QUALITY_CACHE_LOCK = threading.Lock()
_SELLER_QUALITY_CACHE = {}
_SELLER_QUALITY_CACHE_LOCK = threading.Lock()

# ============================================================
# CATÁLOGO EXPANDIDO
# ============================================================

CATALOG = {
    "📱 Celulares": [
        "Smartphone Samsung Galaxy",
        "iPhone 13 128GB",
        "Xiaomi Redmi Note",
        "Motorola Moto G",
    ],
    "🌸 Perfumes": [
        "Perfume contratipo inspirado",
        "Perfume importado 30ml masculino",
        "Perfume importado 50ml feminino",
        "Body splash colônia corporal",
    ],
    "🌙 Perfumes Árabes": [
        "Perfume Lattafa Asad",
        "Perfume Lattafa Yara",
        "Perfume Maison Alhambra",
    ],
    "🎧 Eletrônicos": [
        "Fone de Ouvido Bluetooth",
        "Smartwatch Relogio Inteligente",
        "Caixa de Som Portatil",
    ],
    "🔧 Ferramentas": [
        "Parafusadeira Bateria",
        "Furadeira de Impacto",
        "Jogo de Ferramentas",
    ],
}

# ============================================================
# BANCO DE DADOS
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY CHECK(id=1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT, item_id TEXT, title TEXT, permalink TEXT,
            price REAL, original_price REAL, discount REAL,
            seller_id TEXT, image TEXT, category_id TEXT,
            category_name TEXT, condition TEXT, listing_type_id TEXT,
            free_shipping INTEGER DEFAULT 0, shipping_cost REAL,
            total_price REAL, relevance_score REAL DEFAULT 0,
            affiliate_link TEXT, extra_earnings REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE, description TEXT, discount_percent REAL,
            fixed_discount REAL DEFAULT 0, min_purchase REAL, max_discount REAL, valid_until TEXT,
            source_url TEXT, conditions TEXT, active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_publicacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT UNIQUE,
            last_price REAL NOT NULL,
            last_permalink TEXT,
            last_title TEXT,
            published_count INTEGER DEFAULT 1,
            last_published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        conn.execute("ALTER TABLE cupons ADD COLUMN fixed_discount REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE cupons ADD COLUMN usage_limit INTEGER")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()

# ============================================================
# UTILIDADES
# ============================================================

def json_safe(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [json_safe(x) for x in v]
    return str(v)

def brl(v):
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"

def norm(s):
    if not s:
        return ""
    s = str(s).lower()
    trans = str.maketrans("áàãâäéèêëíìîïóòõôöúùûüç", "aaaaaeeeeiiiiooooouuuuc")
    s = s.translate(trans)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", s)).strip()

def discount(price, original):
    try:
        p, o = float(price), float(original)
        return round((1 - p / o) * 100, 2) if o > p > 0 else 0
    except Exception:
        return 0

def total(price, shipping):
    try:
        return round(float(price) + float(shipping or 0), 2)
    except Exception:
        return None

def model_name(title):
    if not title:
        return "Produto"
    t = re.sub(r"\b(novo|original|oficial|promoção|frete grátis)\b", "", str(title), flags=re.I)
    return re.sub(r"\s+", " ", t).strip()

def specs(title):
    if not title:
        return []
    t = str(title)
    out = []
    for x in re.findall(r"\b\d+(?:GB|TB)\b", t, re.I):
        x = x.upper()
        if x not in out:
            out.append(x)
    for x in re.findall(r"\b\d+\s*GB\s*(?:RAM|MEMORIA|DE MEMORIA)\b", t, re.I):
        x = re.sub(r"\s+", " ", x.upper())
        if x not in out:
            out.append(x)
    return out

# ============================================================
# MERCADO LIVRE API & OAUTH
# ============================================================

def tokens():
    c = get_db()
    r = c.execute("SELECT * FROM oauth_tokens WHERE id=1").fetchone()
    c.close()
    return dict(r) if r else None

def save_tokens(data, user=None):
    old = tokens() or {}
    c = get_db()
    c.execute("""
        INSERT INTO oauth_tokens(id,access_token,refresh_token,expires_at,user_id,nickname)
        VALUES(1,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
        access_token=excluded.access_token,
        refresh_token=COALESCE(excluded.refresh_token,oauth_tokens.refresh_token),
        expires_at=excluded.expires_at,
        user_id=COALESCE(excluded.user_id,oauth_tokens.user_id),
        nickname=COALESCE(excluded.nickname,oauth_tokens.nickname)
    """, (
        data.get("access_token"),
        data.get("refresh_token"),
        int(time.time()) + int(data.get("expires_in", 21600)),
        str(user.get("id")) if user and user.get("id") else old.get("user_id"),
        user.get("nickname") if user else old.get("nickname")
    ))
    c.commit()
    c.close()

def refresh():
    t = tokens()
    if not t or not t.get("refresh_token"):
        return None
    try:
        r = requests.post(ML_TOKEN, data={
            "grant_type":"refresh_token",
            "client_id":ML_CLIENT_ID,
            "client_secret":ML_CLIENT_SECRET,
            "refresh_token":t["refresh_token"]
        }, timeout=30)
        if r.status_code != 200:
            return None
        save_tokens(r.json(), {"id": t.get("user_id"), "nickname": t.get("nickname")})
        return r.json().get("access_token")
    except Exception:
        return None

def access_token():
    t = tokens()
    if not t:
        return None
    if t.get("access_token") and time.time() < (t.get("expires_at") or 0) - 120:
        return t["access_token"]
    return refresh() or t.get("access_token")

def ml_get(path, params=None):
    token = access_token()
    url = path if path.startswith("http") else ML_API + path
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code in (401, 403) and token:
            new_token = refresh()
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                r = requests.get(url, headers=headers, params=params, timeout=30)
        try:
            data = r.json()
        except Exception:
            data = {"message": r.text}
        return data, r.status_code, dict(r.headers)
    except requests.RequestException as e:
        return {"error": str(e)}, 500, {}

# ============================================================
# ROTAS FLASK & BUSCA
# ============================================================

def search_real_listings(query, limit=50):
    data, status, _ = ml_get(f"/sites/{SITE_ID}/search", {
        "q": str(query or "").strip(),
        "limit": min(int(limit or 50), 50),
        "offset": 0,
    })
    if status == 200 and isinstance(data, dict):
        return data.get("results") or []
    return []

def _direct_perfume_offer_from_listing(row, cat, position, query):
    if not isinstance(row, dict):
        return None
    item_id = str(row.get("id") or row.get("item_id") or "").strip()
    if not item_id:
        return None

    title = str(row.get("title") or row.get("name") or item_id).strip()
    price = float(row.get("price") or 0.0)
    original = float(row.get("original_price") or row.get("regular_price") or 0.0) or None

    shipping = row.get("shipping") if isinstance(row.get("shipping"), dict) else {}
    free = bool(shipping.get("free_shipping"))
    shipping_cost = shipping.get("cost") if not free else 0

    image = row.get("thumbnail") or row.get("secure_thumbnail") or ""
    permalink = row.get("permalink") or f"https://produto.mercadolivre.com.br/MLB-{item_id}"

    return {
        "product_id": item_id,
        "item_id": item_id,
        "title": title,
        "modelo_nome": model_name(title),
        "especificacoes": specs(title),
        "image": image,
        "category_name": cat,
        "permalink": permalink,
        "price": price,
        "original_price": original,
        "discount": discount(price, original),
        "seller_id": row.get("seller", {}).get("id") if isinstance(row.get("seller"), dict) else row.get("seller_id"),
        "condition": row.get("condition") or "",
        "free_shipping": free,
        "shipping_cost": shipping_cost,
        "shipping_known": True,
        "total_price": total(price, shipping_cost),
        "relevance_score": 1.0,
        "sold_quantity": row.get("sold_quantity") or 0,
        "logistic_type": shipping.get("logistic_type") or "",
        "seller_nickname": None,
        "best_seller_position": position,
        "cupom": None,
        "desconto_cupom": 0,
        "preco_com_cupom": None,
        "affiliate_link": "",
    }

def scan_queries(queries, min_discount=0, apply_coupons=False):
    direct = []
    seen_direct = set()
    
    for q in queries:
        cat = "Geral"
        for k, v in CATALOG.items():
            if q in v or q == k:
                cat = k
                break
        rows = search_real_listings(q, limit=30)
        for pos, row in enumerate(rows, start=1):
            iid = str(row.get("id") or "").strip()
            if not iid or iid in seen_direct:
                continue
            offer = _direct_perfume_offer_from_listing(row, cat, pos, q)
            if offer and offer["price"] >= MIN_PRODUCT_PRICE:
                seen_direct.add(iid)
                direct.append(offer)

    models = []
    for o in direct:
        models.append({
            "product_id": o["product_id"],
            "title": o["title"],
            "modelo_nome": o["modelo_nome"],
            "especificacoes": o["especificacoes"],
            "image": o["image"],
            "category_name": o["category_name"],
            "ofertas": [o],
        })

    stats = {
        "ofertas": len(direct),
        "mais vendidos": len(direct),
        "menor preço do produto": brl(min([o["price"] for o in direct] or [0])),
        "modo": "busca rápida otimizada",
    }
    return {"stats": stats, "modelos": models, "ofertas": direct}

@app.route("/")
def index():
    t = tokens()
    return render_template_string(HTML, conectado=bool(access_token()), nickname=t.get("nickname") if t else None, categorias=list(CATALOG.keys()))

@app.route("/api/cacar")
def api_cacar():
    categoria = request.args.get("categoria", "").strip()
    queries = CATALOG.get(categoria, []) if categoria in CATALOG else list(CATALOG.keys())
    res = scan_queries(queries)
    return jsonify({"status": "done", "progress": 100, "message": "Concluído", "result": json_safe(res)})

@app.route("/api/cacar/status/<job_id>")
def api_cacar_status(job_id):
    queries = list(CATALOG.keys())
    res = scan_queries(queries)
    return jsonify({"status": "done", "progress": 100, "message": "Concluído", "result": json_safe(res)})

@app.route("/api/buscar")
def api_buscar():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"erro": "Digite algo para buscar"}), 400
    res = scan_queries([q])
    return jsonify(json_safe(res))

@app.route("/proxy-ml-image")
def proxy_ml_image():
    from flask import Response
    source = str(request.args.get("url") or "").strip()
    if not source.startswith(("https://", "http://")):
        return "Imagem inválida.", 400
    try:
        r = requests.get(source, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        ctype = (r.headers.get("content-type") or "image/jpeg").split(";")[0]
        return Response(r.content, status=200, mimetype=ctype)
    except Exception:
        return "Erro ao carregar foto.", 404

# INTERFACE HTML DA APLICAÇÃO
HTML = r"""
<!doctype html><html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Caçador de Ofertas</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f5f7;font-family:Arial;color:#222}
.container{max-width:1050px;margin:auto;padding:18px}.card{background:#fff;border-radius:16px;padding:18px;margin-bottom:18px;box-shadow:0 5px 20px #0000000c}
button,input,select{width:100%;padding:13px;border-radius:10px;border:1px solid #ddd;font-size:15px}button{border:0;background:#3483fa;color:#fff;cursor:pointer;margin-top:7px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px}.cat{background:#fff;border:1px solid #ddd;color:#222;text-align:left}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:9px}.stat{background:#f3f4f6;padding:13px;border-radius:11px}.stat b{display:block;font-size:23px;margin-top:4px}
.modelo{border:2px solid #eee;border-radius:15px;padding:14px;margin:13px 0}.mh{display:flex;gap:12px;align-items:center}.mh img{width:85px;height:85px;object-fit:contain;background:#fafafa;border-radius:10px}.title{font-size:18px;font-weight:bold}.tag{display:inline-block;background:#eef4ff;color:#3483fa;border-radius:7px;padding:5px 8px;font-size:11px;margin:3px}.seller{background:#fafafa;border:1px solid #eee;border-radius:12px;padding:12px;margin-top:10px}.price{font-size:22px;font-weight:bold}.green{color:#00a650;font-weight:bold}.small{font-size:12px;color:#666}
</style>
<script>
async function cacar(cat){
 document.getElementById('status').textContent='🔄 Procurando ofertas...';
 const r=await fetch('/api/cacar?categoria='+encodeURIComponent(cat||''));
 const data=await r.json();
 if(data.result) render(data.result);
 document.getElementById('status').textContent='✅ Ofertas encontradas!';
}
async function buscar(){
 const q=document.getElementById('q').value.trim();if(!q)return;
 document.getElementById('status').textContent='🔄 Buscando...';
 const r=await fetch('/api/buscar?q='+encodeURIComponent(q));
 const data=await r.json();
 render(data);
 document.getElementById('status').textContent='✅ Busca concluída!';
}
function render(data){
 document.getElementById('stats').innerHTML=Object.entries(data.stats||{}).map(([k,v])=>`<div class="stat">${k}<b>${v}</b></div>`).join('');
 document.getElementById('results').innerHTML=(data.modelos||[]).map((m,mi)=>`
 <div class="modelo">
  <div class="mh">${m.image?`<img src="/proxy-ml-image?url=${encodeURIComponent(m.image)}">`:''}<div>
   <span class="tag">🔥 PRODUTO ${mi+1}</span><div class="title">${m.modelo_nome}</div>
  </div></div>
  ${m.ofertas.map(o=>`
   <div class="seller">
    <div class="price">R$ ${o.price.toFixed(2)}</div>${o.free_shipping?'<div class="green">🚚 Frete grátis</div>':''}
    <a href="${o.permalink}" target="_blank"><button>🛒 Ver no Mercado Livre</button></a>
   </div>
  `).join('')}
 </div>`).join('') || '<p>Nenhum produto encontrado no momento.</p>';
}
</script></head><body><div class="container">
<div class="card"><h1>🛒 Caçador de Ofertas</h1><p>Buscador de ofertas otimizado.</p></div>
<div class="card"><h2>🔥 Escolha uma categoria</h2>
<button class="cat" style="background:#3483fa;color:#fff;font-weight:bold" onclick="cacar('')">🔎 BUSCAR TODAS AS CATEGORIAS</button>
<div class="grid" style="margin-top:10px">{% for c in categorias %}<button class="cat" onclick="cacar({{c|tojson}})">{{c}}</button>{% endfor %}</div>
<p id="status" class="small"></p></div>
<div class="card"><h2>🔎 Busca Manual</h2><input id="q" placeholder="Digite um produto..."><button onclick="buscar()">Buscar</button></div>
<div class="card"><h2>📊 Estatísticas</h2><div id="stats" class="stats"></div></div>
<div class="card"><h2>🏆 Resultados</h2><div id="results"></div></div>
</div></body></html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=False)
