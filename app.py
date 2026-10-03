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
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode, quote

import requests
from flask import Flask, request, redirect, session, jsonify, render_template_string

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "chave-cacador-ofertas")

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"
SITE_ID = "MLB"
DB_FILE = "ofertas.db"
COUPONS_URL = "https://www.mercadolivre.com.br/l/promocoes"
COUPON_SOURCE_URLS = [
    "https://www.mercadolivre.com.br/l/promocoes",
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/ofertas/cupons",
]
MIN_PRODUCT_PRICE = 69.90

# ============================================================
# CATÁLOGO AUTOMÁTICO
# ============================================================

CATALOG = {
    "📱 Celulares": [
        "smartphone", "iphone", "samsung galaxy", "motorola moto",
        "xiaomi redmi", "poco smartphone", "realme smartphone"
    ],
    "🌸 Perfumes": [
        "perfume masculino", "perfume feminino", "perfume importado",
        "perfume nacional", "perfume eau de parfum"
    ],
    "🏋️ Academia": [
        "roupa academia masculina", "roupa academia feminina",
        "camiseta academia", "short academia", "legging academia",
        "tenis academia", "tenis corrida", "tenis treino",
        "whey protein", "creatina", "pre treino", "suplementos"
    ],
    "🔧 Ferramentas": [
        "furadeira", "parafusadeira", "esmerilhadeira",
        "kit ferramentas", "maleta ferramentas", "serra",
        "chave de impacto"
    ],
    "🎧 Eletrônicos": [
        "fone bluetooth", "headset", "smartwatch", "tablet",
        "caixa de som bluetooth", "camera digital", "power bank"
    ],
    "🏠 Casa": [
        "aspirador de pó", "liquidificador", "cafeteira",
        "air fryer", "ventilador", "ferro de passar"
    ],
    "🍳 Cozinha": [
        "air fryer", "panela elétrica", "jogo de panelas",
        "cafeteira", "liquidificador", "sandwichera"
    ],
    "🚗 Automotivo": [
        "compressor automotivo", "aspirador automotivo",
        "suporte celular carro", "carregador automotivo",
        "ferramentas automotivas", "tapete automotivo"
    ],
    "👕 Moda": [
        "tenis masculino", "tenis feminino", "mochila",
        "relogio masculino", "bolsa feminina", "oculos de sol"
    ],
}

# ============================================================
# BANCO
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
    # Compatibilidade com bancos criados pelas versões anteriores.
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
    for x in re.findall(r"\b(?:2G|3G|4G|5G)\b", t, re.I):
        x = x.upper()
        if x not in out:
            out.append(x)
    for label, pattern in [
        ("Dual SIM", r"dual\s*sim"),
        ("NFC", r"\bnfc\b")
    ]:
        if re.search(pattern, t, re.I) and label not in out:
            out.append(label)
    return out

# ============================================================
# PERFIS / RELEVÂNCIA
# ============================================================

PROFILES = {
    "celular": {
        "strong": ["smartphone","iphone","galaxy","samsung","motorola","xiaomi","redmi","poco","realme"],
        "bad": ["capa","capinha","pelicula","suporte","ventosa","carregador","cabo","adaptador","bateria","case","holder"]
    },
    "perfume": {
        "strong": ["perfume","eau de parfum","eau de toilette","parfum"],
        "bad": ["frasco vazio","decant","amostra","porta perfume","refil vazio"]
    },
    "academia": {
        "strong": [
            "roupa", "camiseta", "short", "legging",
            "tenis", "corrida", "treino",
            "whey", "creatina", "pre treino",
            "suplemento", "suplementos"
        ],
        "bad": [
            "halter", "halteres", "anilha", "barra",
            "banco musculacao", "caneleira", "elastico",
            "adesivo", "capa", "suporte", "peca de reposicao"
        ]
    },
    "ferramenta": {
        "strong": ["furadeira","parafusadeira","esmerilhadeira","ferramenta","serra","impacto"],
        "bad": ["broca avulsa","peca","carvao","bateria avulsa","capa"]
    },
}

def is_requested_product(title, query, category=None):
    """Filtra acessórios/peças e garante que o título pertence à categoria pedida."""
    t = norm(title)
    qn = norm(query)
    cat = category or query_category(query) or _demand_category_from_text(title)

    # Itens que normalmente contaminam buscas de produto principal.
    generic_bad = [
        "capa", "capinha", "pelicula", "película", "suporte", "holder",
        "cabo", "adaptador", "adesivo", "peca de reposicao", "peca avulsa",
        "refil vazio", "frasco vazio", "amostra", "decant", "miniatura",
        "pingente", "chaveiro", "brinde", "molde", "manual digital"
    ]

    category_rules = {
        "📱 Celulares": (
            ["smartphone", "celular", "iphone", "galaxy", "samsung", "motorola", "xiaomi", "redmi", "poco", "realme"],
            generic_bad + ["carregador", "bateria avulsa", "case"]
        ),
        "🌸 Perfumes": (
            ["perfume", "parfum", "eau de parfum", "eau de toilette", "fragrancia"],
            generic_bad + ["porta perfume", "estojo vazio"]
        ),
        "🏋️ Academia": (
            ["academia", "treino", "corrida", "legging", "camiseta", "short", "tenis", "whey", "creatina", "suplemento", "pre treino"],
            ["capa", "adesivo", "suporte", "peca de reposicao", "broca"]
        ),
        "🔧 Ferramentas": (
            ["furadeira", "parafusadeira", "esmerilhadeira", "ferramenta", "serra", "chave de impacto", "impacto"],
            ["broca avulsa", "carvao", "bateria avulsa", "capa", "peca de reposicao"]
        ),
        "🎧 Eletrônicos": (
            ["fone", "headset", "smartwatch", "tablet", "caixa de som", "camera", "power bank"],
            ["cabo", "case", "capa", "pelicula", "suporte", "peca de reposicao"]
        ),
        "🏠 Casa": (
            ["aspirador", "liquidificador", "cafeteira", "air fryer", "ventilador", "ferro de passar"],
            ["peca", "refil", "capa", "suporte", "acessorio"]
        ),
        "🍳 Cozinha": (
            ["air fryer", "panela eletrica", "jogo de panelas", "cafeteira", "liquidificador", "sanduicheira"],
            ["peca", "refil", "capa", "suporte", "acessorio"]
        ),
        "🚗 Automotivo": (
            ["compressor automotivo", "aspirador automotivo", "carregador automotivo", "ferramenta automotiva", "tapete automotivo"],
            ["capa de celular", "pelicula", "brinde", "adesivo"]
        ),
        "👕 Moda": (
            ["tenis", "mochila", "relogio", "bolsa", "oculos", "camiseta", "vestido"],
            ["capa", "pelicula", "suporte", "peca de reposicao"]
        ),
    }

    strong, bad = category_rules.get(cat, ([], generic_bad))
    if any(x in t for x in bad):
        return False

    # Para consultas específicas, exige que pelo menos uma palavra/expressão
    # importante da consulta apareça no título.
    q_terms = [x for x in qn.split() if len(x) >= 4]
    if q_terms and not any(x in t for x in q_terms):
        # A categoria ainda pode validar o produto quando a consulta é um
        # termo genérico como "smartphone" ou "perfume".
        if not any(x in t for x in strong):
            return False

    if strong and not any(x in t for x in strong):
        return False

    return True


def query_category(q):
    """Retorna a categoria do catálogo que corresponde à consulta."""
    nq = norm(q)
    if not nq:
        return None

    # Primeiro procura a consulta exata entre as buscas cadastradas.
    for category, queries in CATALOG.items():
        for item in queries:
            if nq == norm(item):
                return category

    # Depois usa correspondência por palavras para consultas extras.
    best_category = None
    best_score = 0
    q_words = {w for w in nq.split() if len(w) >= 3}
    for category, queries in CATALOG.items():
        for item in queries:
            iw = {w for w in norm(item).split() if len(w) >= 3}
            score = len(q_words & iw)
            if score > best_score:
                best_score = score
                best_category = category

    return best_category


def profile_for(q):
    t = norm(q)
    if any(x in t for x in ["iphone","samsung","galaxy","motorola","xiaomi","redmi","poco","smartphone","celular"]):
        return "celular"
    if "perfume" in t:
        return "perfume"
    if any(x in t for x in [
        "academia", "roupa academia", "camiseta academia",
        "short academia", "legging academia",
        "tenis academia", "tenis corrida", "tenis treino",
        "whey", "creatina", "pre treino", "suplemento"
    ]):
        return "academia"
    if any(x in t for x in ["furadeira","parafusadeira","ferramenta","esmerilhadeira","serra"]):
        return "ferramenta"
    return None

def relevance(title, q):
    t, b = norm(title), norm(q)
    p = profile_for(q)
    score = sum(35 for x in (PROFILES.get(p, {}).get("strong", []) if p else []) if x in t)
    score += sum(10 for x in b.split() if len(x) >= 3 and x in t)
    score -= sum(90 for x in PROFILES.get(p, {}).get("bad", []) if x in t)
    return score

# ============================================================
# OAUTH
# ============================================================

def pkce():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge

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
    if not token:
        return {}, 401, {}
    url = path if path.startswith("http") else ML_API + path
    try:
        r = requests.get(url, headers={"Authorization":f"Bearer {token}","Accept":"application/json"}, params=params, timeout=30)
        try:
            data = r.json()
        except Exception:
            data = {"message": r.text}
        return data, r.status_code, dict(r.headers)
    except requests.RequestException as e:
        return {"error":str(e)}, 500, {}

# ============================================================
# LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def ml_login():
    if not ML_CLIENT_ID:
        return jsonify({"erro":"ML_CLIENT_ID não configurado."}), 500
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(32)
    session["ml_state"] = state
    session["ml_code_verifier"] = verifier
    params = {
        "response_type":"code","client_id":ML_CLIENT_ID,
        "redirect_uri":ML_REDIRECT_URI,"state":state,
        "code_challenge":challenge,"code_challenge_method":"S256"
    }
    return redirect(ML_AUTH + "?" + urlencode(params))

@app.route("/mercadolivre/callback")
def ml_callback():
    if request.args.get("error"):
        return jsonify({"erro":request.args.get("error"),"descricao":request.args.get("error_description")}), 400
    code, state = request.args.get("code"), request.args.get("state")
    if not code or state != session.get("ml_state"):
        return jsonify({"erro":"Código ou state inválido."}), 400
    try:
        r = requests.post(ML_TOKEN, data={
            "grant_type":"authorization_code","client_id":ML_CLIENT_ID,
            "client_secret":ML_CLIENT_SECRET,"code":code,
            "redirect_uri":ML_REDIRECT_URI,
            "code_verifier":session.get("ml_code_verifier")
        }, timeout=30)
        if r.status_code != 200:
            return jsonify({"erro":"Falha ao obter token.","status":r.status_code,"resposta":r.text}), r.status_code
        data = r.json()
        user = None
        if data.get("access_token"):
            me = requests.get(ML_API+"/users/me", headers={"Authorization":"Bearer "+data["access_token"]}, timeout=30)
            if me.status_code == 200:
                user = me.json()
        save_tokens(data, user)
        session.pop("ml_state", None)
        session.pop("ml_code_verifier", None)
        return redirect("/?conectado=1")
    except Exception as e:
        return jsonify({"erro":str(e)}), 500

@app.route("/mercadolivre/logout")
def ml_logout():
    c = get_db()
    c.execute("DELETE FROM oauth_tokens WHERE id=1")
    c.commit()
    c.close()
    session.clear()
    return redirect("/")

# ============================================================
# MERCADO LIVRE - PRODUTOS
# ============================================================

def discover_categories(q):
    data, status, _ = ml_get(f"/sites/{SITE_ID}/domain_discovery/search", {"q":q})
    if status != 200 or not isinstance(data, list):
        return []
    out = []
    for x in data:
        cid = x.get("category_id") or x.get("id")
        name = x.get("category_name") or x.get("name") or cid
        if cid:
            out.append({"category_id":cid,"category_name":name})
    return out

def highlights(category_id):
    data, status, _ = ml_get(f"/highlights/{SITE_ID}/category/{category_id}")
    if status != 200:
        return []
    if isinstance(data, list):
        return data
    return data.get("content", data.get("results", [])) if isinstance(data, dict) else []

def product(pid):
    data, status, _ = ml_get(f"/products/{pid}")
    return data if status == 200 and isinstance(data, dict) else None

def _build_item_from_buy_box(bb):
    """Normaliza o buy_box_winner retornado pelo catálogo em formato de item."""
    if not isinstance(bb, dict):
        return None

    item_id = (
        bb.get("item_id")
        or bb.get("id")
        or (bb.get("item") or {}).get("item_id") if isinstance(bb.get("item"), dict) else None
    )
    if not item_id:
        # Alguns retornos podem trazer o item dentro de winner
        winner = bb.get("winner")
        if isinstance(winner, dict):
            item_id = winner.get("item_id") or winner.get("id")

    if not item_id:
        return None

    shipping = bb.get("shipping") or {}
    if not isinstance(shipping, dict):
        shipping = {}

    free = bool(
        shipping.get("free_shipping")
        or bb.get("free_shipping") is True
        or bb.get("shipping_free") is True
    )

    cost = 0 if free else (
        shipping.get("cost")
        if shipping.get("cost") is not None
        else bb.get("shipping_cost")
    )

    price = bb.get("price")
    if price is None:
        price = bb.get("sale_price")
    if price is None:
        price = bb.get("regular_price")

    original = bb.get("original_price")
    if original is None:
        original = bb.get("regular_price")

    return {
        "item_id": item_id,
        "seller_id": bb.get("seller_id") or bb.get("seller", {}).get("id") if isinstance(bb.get("seller"), dict) else bb.get("seller_id"),
        "price": price,
        "original_price": original,
        "condition": bb.get("condition"),
        "listing_type_id": bb.get("listing_type_id"),
        "free_shipping": free,
        "shipping_cost": cost,
        "permalink": bb.get("permalink"),
        "user_product_id": bb.get("user_product_id"),
        "sold_quantity": bb.get("sold_quantity") or bb.get("sales") or 0,
    }

def product_items(pid):
    data, status, _ = ml_get(f"/products/{pid}/items")
    if status != 200:
        return []
    if isinstance(data, list):
        return data
    return data.get("results", []) if isinstance(data, dict) else []

PRICE_CACHE = {}

def get_current_sale_price(item_id):
    if not item_id:
        return None, None
    if item_id in PRICE_CACHE:
        return PRICE_CACHE[item_id]
    data, status, _ = ml_get(
        f"/items/{item_id}/sale_price",
        {"context": "channel_marketplace"}
    )
    if status == 200 and isinstance(data, dict):
        try:
            amount = float(data.get("amount")) if data.get("amount") is not None else None
        except Exception:
            amount = None
        try:
            regular = float(data.get("regular_amount")) if data.get("regular_amount") is not None else None
        except Exception:
            regular = None
        if data.get("currency_id") in (None, "BRL") and amount is not None and amount > 0:
            PRICE_CACHE[item_id] = (amount, regular)
            return amount, regular
    PRICE_CACHE[item_id] = (None, None)
    return None, None

def valid_catalog_price(price):
    try:
        value = float(price)
    except Exception:
        return False
    if value < MIN_PRODUCT_PRICE:
        return False
    # Bloqueia valores claramente corrompidos, como R$1.169.000 para um celular.
    if value > 100000:
        return False
    return True

def normalize_item(x):
    if not isinstance(x, dict) or not x.get("item_id"):
        return None
    sh = x.get("shipping") or {}
    free = bool(sh.get("free_shipping"))
    cost = 0 if free else sh.get("cost")
    return {
        "item_id":x["item_id"], "seller_id":x.get("seller_id"),
        "price":x.get("price"), "original_price":x.get("original_price"),
        "condition":x.get("condition"), "listing_type_id":x.get("listing_type_id"),
        "free_shipping":free, "shipping_cost":cost,
        "permalink":x.get("permalink"), "user_product_id":x.get("user_product_id")
    }

# ============================================================
# CUPONS
# ============================================================

def number(s):
    if s is None:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(s))
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def normalize_coupon_html(raw_html):
    """Converte HTML do Mercado Livre em texto preservando ALT das imagens.

    As páginas públicas de ofertas frequentemente repetem o título no ALT da
    imagem e no conteúdo do card. Preservar o ALT deixa o parser capaz de
    separar corretamente produto + preço + cupom sem misturar cards vizinhos.
    """
    text = html_lib.unescape(raw_html or "")

    # Remove scripts/styles que não são conteúdo visual do card, mas preserva
    # texto útil de imagens antes de remover as demais tags.
    def img_alt(m):
        tag = m.group(0)
        alt = re.search(r'\balt\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        if alt and alt.group(1).strip():
            return "\nImage: " + alt.group(1).strip() + "\n"
        return " "

    text = re.sub(r"<img\b[^>]*>", img_alt, text, flags=re.I)
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", text, flags=re.I | re.S)
    text = re.sub(r"</(?:div|p|li|h1|h2|h3|h4|h5|h6|section|article|br|tr|header|footer)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def looks_like_coupon_code(code):
    code = (code or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{5,29}", code):
        return False
    # Evita palavras que aparecem em frases como "Cupom não cumulativo".
    if not re.search(r"[A-Z]", code) or not re.search(r"\d", code):
        return False
    bad = {"NAOCOUPOM", "CUPOMVALIDO", "VALIDO", "DESCONTO"}
    return code not in bad


def coupon_blocks(text):
    """Retorna blocos individuais iniciados por 'Cupom CODE'.

    A versão anterior usava janelas grandes ao redor da palavra 'cupom'.
    Isso fazia 1FRUIT herdar os 15%/R$70 do S5PRUNK. Aqui cada cupom fica
    isolado do próximo cupom.
    """
    # O cabeçalho real é seguido por "Cupom válido". Isso elimina ocorrências
    # como "Cupom não cumulativo" dentro das condições.
    # O padrão não depende de quebra de linha porque a página pode entregar
    # alguns blocos em uma única linha após a remoção das tags HTML.
    pat = re.compile(
        r"\bCupom\s+([A-Z0-9][A-Z0-9_-]{5,29})\b(?=\s+Cupom\s+v[aá]lido)",
        re.I
    )
    candidates = []
    for m in pat.finditer(text):
        code = m.group(1).upper()
        if not looks_like_coupon_code(code):
            continue
        candidates.append((m.start(), m.end(), code))

    blocks = []
    for i, (a, b, code) in enumerate(candidates):
        end = candidates[i+1][0] if i+1 < len(candidates) else len(text)
        block = text[b:end].strip()
        # Limita o bloco a seções gerais que não pertencem ao cupom.
        for marker in ["Restrições de Uso", "Termos e Condições"]:
            pos = block.lower().find(marker.lower())
            if pos >= 0:
                block = block[:pos].strip()
        blocks.append((code, block))
    return blocks


def parse_coupon_block(code, block, source_url=COUPONS_URL):
    # Percentual: procura a primeira oferta explícita do bloco.
    pct = None
    for pat in [
        r"(?:até\s+)?(\d+(?:[.,]\d+)?)\s*%\s*(?:off|de desconto)?",
        r"(?:desconto de|desconto)\s+(\d+(?:[.,]\d+)?)\s*%",
    ]:
        m = re.search(pat, block, re.I)
        if m:
            pct = number(m.group(1))
            break

    # Cupom em dinheiro: captura frases do tipo R$ 30 OFF / R$ 30 de desconto.
    fixed = None
    fixed_patterns = [
        r"R\$\s*(\d+(?:[.,]\d+)?)\s*(?:OFF|de desconto|de\s+desconto)",
        r"(?:desconto de|ganhe)\s*R\$\s*(\d+(?:[.,]\d+)?)",
    ]
    for pat in fixed_patterns:
        m = re.search(pat, block, re.I)
        if m:
            fixed = number(m.group(1))
            break

    mn = None
    for pat in [
        r"(?:a partir de|partir de|compra mínima de|mínimo de|valor mínimo de)\s*R?\$?\s*(\d+(?:[.,]\d+)?)",
        r"R\$\s*(\d+(?:[.,]\d+)?)\s*(?:ou mais|em compras)",
    ]:
        m = re.search(pat, block, re.I)
        if m:
            mn = number(m.group(1))
            break

    mx = None
    for pat in [
        r"(?:desconto\s+)?(?:máximo de|maximo de|limitado a)\s*R?\$?\s*(\d+(?:[.,]\d+)?)\b",
        r"R\$\s*(\d+(?:[.,]\d+)?)\s*(?:de desconto no máximo|máximo de desconto)",
    ]:
        m = re.search(pat, block, re.I)
        if m:
            mx = number(m.group(1))
            break

    # Evita confundir preço mínimo com teto quando a frase contém "até X%".
    if mx is None:
        m = re.search(r"(?:máximo|limite).*?R\$\s*(\d+(?:[.,]\d+)?)", block, re.I)
        if m:
            mx = number(m.group(1))

    usage_limit = None
    for pat in [
        r"(?:limite de|limite:|até)\s*([0-9]{1,3}(?:[\.,][0-9]{3})*|[0-9]{2,7})\s*(?:usos|utiliza(?:ções|coes)|cupons|clientes)",
        r"([0-9]{1,3}(?:[\.,][0-9]{3})*|[0-9]{2,7})\s*(?:usos|utiliza(?:ções|coes)|cupons|clientes)",
        r"(?:disponível|disponiveis)\s*para\s*(?:os )?([0-9]{1,3}(?:[\.,][0-9]{3})*|[0-9]{2,7})\s*(?:primeiros )?(?:usos|clientes|cupons)",
    ]:
        m = re.search(pat, block, re.I)
        if m:
            try:
                usage_limit = int(str(m.group(1)).replace('.', '').replace(',', ''))
            except Exception:
                usage_limit = None
            break

    return {
        "code": code,
        "description": block[:2500],
        "discount_percent": pct,
        "fixed_discount": fixed or 0,
        "min_purchase": mn,
        "max_discount": mx,
        "usage_limit": usage_limit,
        "source_url": source_url,
        "conditions": block[:2500],
    }


def sync_coupons():
    """Caça cupons em várias áreas públicas do Mercado Livre.

    Não existe um teto artificial de desconto no programa. Todos os cupons
    encontrados são considerados. O único limite aplicado ao cálculo é o
    próprio limite/condição informado pelo Mercado Livre para aquele cupom.
    """
    headers = {
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Accept-Language":"pt-BR,pt;q=0.9,en;q=0.8",
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    parsed_by_code = {}
    errors = []

    for source_url in COUPON_SOURCE_URLS:
        try:
            r = requests.get(source_url, headers=headers, timeout=30)
            if r.status_code != 200:
                errors.append(f"{source_url}: HTTP {r.status_code}")
                continue

            text = normalize_coupon_html(r.text)
            blocks = coupon_blocks(text)
            for code, block in blocks:
                c = parse_coupon_block(code, block, source_url)
                if not c.get("discount_percent") and not c.get("fixed_discount"):
                    continue

                # Se o mesmo código aparecer em mais de uma página, mantém
                # a versão com mais informação/condições.
                old = parsed_by_code.get(code)
                if not old or len(c.get("conditions", "")) > len(old.get("conditions", "")):
                    parsed_by_code[code] = c

        except Exception as e:
            errors.append(f"{source_url}: {e}")

    parsed = list(parsed_by_code.values())

    if not parsed:
        return {
            "ok":False,
            "erro":"Nenhum cupom público válido foi identificado nas fontes consultadas.",
            "erros":errors
        }

    conn = get_db()
    conn.execute("UPDATE cupons SET active=0")

    for c in parsed:
        conn.execute("""
            INSERT INTO cupons(
                code,description,discount_percent,fixed_discount,
                min_purchase,max_discount,usage_limit,source_url,conditions,active,updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP)
            ON CONFLICT(code) DO UPDATE SET
                description=excluded.description,
                discount_percent=excluded.discount_percent,
                fixed_discount=excluded.fixed_discount,
                min_purchase=excluded.min_purchase,
                max_discount=excluded.max_discount,
                usage_limit=excluded.usage_limit,
                source_url=excluded.source_url,
                conditions=excluded.conditions,
                active=1,
                updated_at=CURRENT_TIMESTAMP
        """,(
            c["code"],c["description"],c["discount_percent"],c["fixed_discount"],
            c["min_purchase"],c["max_discount"],c.get("usage_limit"),c["source_url"],c["conditions"]
        ))

    conn.commit()
    conn.close()

    print("[CUPONS] Cupons encontrados em todas as fontes:")
    for c in sorted(parsed, key=lambda x: (-(x.get("max_discount") or 0), -(x.get("discount_percent") or 0))):
        print(
            f"[CUPOM OK] {c['code']} | {c.get('discount_percent') or 0}% | "
            f"fixo R$ {c.get('fixed_discount') or 0:.2f} | "
            f"mín R$ {c.get('min_purchase') or 0:.2f} | "
            f"máx R$ {c.get('max_discount') or 0:.2f} | "
            f"limite usos {c.get('usage_limit') or 'não informado'} | {c.get('source_url')}"
        )

    return {
        "ok":True,
        "cupons_encontrados":len(parsed),
        "fontes_consultadas":len(COUPON_SOURCE_URLS),
        "erros":errors
    }


def coupons():
    c = get_db()
    rows = c.execute(
        "SELECT * FROM cupons WHERE active=1 ORDER BY updated_at DESC, discount_percent DESC, max_discount DESC"
    ).fetchall()
    c.close()
    return [dict(x) for x in rows]


def coupon_discount(cupom, price):
    try:
        price = float(price)
        minimum = cupom.get("min_purchase")
        if minimum and price < float(minimum):
            return 0

        candidates = []
        pct = float(cupom.get("discount_percent") or 0)
        fixed = float(cupom.get("fixed_discount") or 0)

        if pct > 0:
            d = price * pct / 100
            if cupom.get("max_discount"):
                d = min(d, float(cupom["max_discount"]))
            candidates.append(d)

        if fixed > 0:
            d = fixed
            if cupom.get("max_discount"):
                d = min(d, float(cupom["max_discount"]))
            candidates.append(d)

        return round(max(0, max(candidates, default=0)), 2)
    except Exception:
        return 0


def best_coupon(price):
    choices = []
    for c in coupons():
        d = coupon_discount(c, price)
        if d > 0:
            x = dict(c); x["desconto_estimado"] = d
            x["percentual_efetivo"] = round((d / float(price)) * 100, 2) if float(price) > 0 else 0
            x["match_type"] = "regras_de_preco"
            choices.append(x)
    return max(choices, key=lambda x:(x["desconto_estimado"],x["percentual_efetivo"],-(float(x.get("min_purchase") or 0))), default=None)

def choose_best_coupon(title, price, public_cards=None, item_id=None):
    """Escolhe somente cupons com associação pública ao produto.

    IMPORTANTE: não aplicamos mais um cupom genérico só porque o preço
    atende ao mínimo/máximo. Isso foi o que fazia o S5PRUNK/R$70 aparecer
    em praticamente todos os produtos. O Mercado Livre informa que cupons
    são condicionados a produtos selecionados; portanto, sem uma associação
    pública produto->cupom, o app não chama o cupom de aplicável.

    O mesmo cupom pode continuar sendo usado em vários produtos quando cada
    produto tiver sua própria associação pública.
    """
    if not public_cards:
        return None

    matched = match_public_coupon(title, price, public_cards, item_id)
    if not matched:
        return None

    d = calculate_public_coupon(matched, price)
    if d <= 0:
        return None

    x = dict(matched)
    x["desconto_estimado"] = d
    x["percentual_efetivo"] = round((d / float(price)) * 100, 2) if float(price) > 0 else 0
    x["match_type"] = "produto_publico"
    return x

def detect_cash_discount(item, price):
    """Só aceita desconto à vista/Pix quando o próprio dado da API o informa.
    Não assume que todo Pix tem desconto e não soma com cupom sem indicação de cumulatividade.
    """
    if not isinstance(item, dict):
        return 0, None
    p = float(price or 0)
    if p <= 0:
        return 0, None

    explicit = []
    for key in ("pix_discount", "cash_discount", "discount_pix", "payment_discount", "cashback_discount"):
        v = item.get(key)
        if isinstance(v, (int,float)) and float(v) > 0:
            explicit.append(float(v))

    for key in ("pix_price", "cash_price", "price_pix", "price_cash"):
        v = item.get(key)
        if isinstance(v, (int,float)) and 0 < float(v) < p:
            explicit.append(p - float(v))

    payments = item.get("payment_methods") or item.get("payments") or {}
    if isinstance(payments, dict):
        for k, v in payments.items():
            if "pix" not in str(k).lower() and "avista" not in norm(k):
                continue
            if isinstance(v, dict):
                for key in ("discount", "discount_amount", "amount_discount"):
                    n = v.get(key)
                    if isinstance(n,(int,float)) and float(n)>0:
                        explicit.append(float(n))
                for key in ("price", "final_price"):
                    n = v.get(key)
                    if isinstance(n,(int,float)) and 0 < float(n) < p:
                        explicit.append(p-float(n))

    d = round(max(explicit, default=0),2)
    return d, ("Pix/à vista" if d > 0 else None)

# ============================================================
# CAÇADOR
# ============================================================

def _extract_public_coupon_cards_from_text(text, source_url):
    """Extrai associações produto -> cupom de páginas públicas.

    O front do Mercado Livre muda bastante o HTML. Por isso o parser não
    exige mais que o cupom esteja em uma linha isolada nem que exista código.
    Ele aceita tanto ``Cupom R$15 OFF`` quanto ``Cupom 10% OFF`` e procura o
    título/preço mais próximos dentro do mesmo card.
    """
    cards = []
    raw = html_lib.unescape(text or "")
    clean = normalize_coupon_html(raw)

    def add_card(title, price, original, coupon):
        if not title or not coupon or price is None:
            return
        try:
            price = float(price)
        except Exception:
            return
        if price < MIN_PRODUCT_PRICE:
            return
        cards.append({
            "title": re.sub(r"\s+", " ", str(title)).strip(),
            "price": round(price, 2),
            "original_price": round(float(original), 2) if original and float(original) > price else None,
            "coupon": coupon,
            "source_url": source_url,
        })

    # 1) Primeiro tenta os blocos já normalizados por linhas.
    lines = [re.sub(r"\s+", " ", x).strip() for x in clean.splitlines()]
    lines = [x for x in lines if x]
    coupon_re = re.compile(
        r"\bCupom\s+(?:R\$\s*[\d\.]+(?:,[\d]{2})?|\d+(?:[.,]\d+)?)\s*(?:OFF|%\s*OFF)\b",
        re.I,
    )
    money_re = re.compile(r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)", re.I)
    percent_off_re = re.compile(r"(\d+(?:[.,]\d+)?)\s*%\s*OFF", re.I)

    for i, line in enumerate(lines):
        m_coupon = coupon_re.search(line)
        if not m_coupon:
            continue
        coupon = detect_public_coupon(m_coupon.group(0))
        if not coupon:
            continue

        # Procura o título mais próximo acima. Não exige Image: exatamente na
        # linha imediatamente anterior porque o HTML pode inserir etiquetas.
        title = ""
        title_idx = None
        for j in range(i, max(-1, i - 35), -1):
            if lines[j].lower().startswith("image:"):
                candidate = lines[j].split(":", 1)[1].strip()
                if len(candidate) >= 8 and not re.search(r"^(logo|mercado livre|cupom|oferta)", candidate, re.I):
                    title = candidate
                    title_idx = j
                    break
        if not title:
            # Última tentativa: texto próximo que parece nome de produto.
            for j in range(i - 1, max(-1, i - 12), -1):
                candidate = lines[j].strip()
                if len(candidate) >= 12 and not re.search(r"^(r\$|oferta|mais vendido|cupom|frete|chegará|10x|\d+% off)", candidate, re.I):
                    title = candidate
                    title_idx = j
                    break
        if not title:
            continue

        # Preço atual: prioriza a linha com percentual OFF do próprio card.
        price = None
        original = None
        search_start = title_idx if title_idx is not None else max(0, i - 20)
        for j in range(search_start, i + 1):
            vals = [parse_public_money(x.group(0)) for x in money_re.finditer(lines[j])]
            vals = [v for v in vals if v is not None]
            if percent_off_re.search(lines[j]) and vals:
                price = vals[-1]
                bigger = [v for v in vals[:-1] if v > price]
                if bigger:
                    original = min(bigger)
                break

        if price is None:
            # Procura os últimos preços antes do cupom, ignorando parcelamento.
            vals = []
            for j in range(search_start, i):
                vals.extend(parse_public_money(x.group(0)) for x in money_re.finditer(lines[j]))
            vals = [v for v in vals if v is not None and v >= MIN_PRODUCT_PRICE]
            if vals:
                price = vals[-1]
                bigger = [v for v in vals if v > price]
                if bigger:
                    original = min(bigger)

        add_card(title, price, original, coupon)

    # 2) O servidor pode devolver o card em uma única linha ou dentro de JSON.
    # Procura "título ... preço ... OFF ... Cupom" em janelas próximas.
    flat = re.sub(r"\s+", " ", clean)
    patterns = [
        re.compile(r"(?:Image:\s*)?(.{8,220}?)\s+(R\$\s*[\d\.]+,[\d]{2}|R\$\s*\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)\s*%\s*OFF\s+(Cupom\s+(?:R\$\s*[\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s*(?:OFF|%\s*OFF))", re.I),
        re.compile(r"(?:Image:\s*)?(.{8,220}?)\s+(Cupom\s+(?:R\$\s*[\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s*(?:OFF|%\s*OFF))\s+(?:Chegará|Frete|Mais vendido|Oferta)", re.I),
    ]
    for pat in patterns:
        for m in pat.finditer(flat):
            title = m.group(1).strip()
            # Remove rótulos que claramente não são produto.
            title = re.sub(r"^(?:OFERTA DO DIA|OFERTA IMPERDÍVEL|OFERTA RELÂMPAGO)\s+", "", title, flags=re.I)
            coupon_text = m.group(4) if m.lastindex and m.lastindex >= 4 else m.group(2)
            coupon = detect_public_coupon(coupon_text)
            if not coupon:
                continue
            price_text = m.group(2) if m.lastindex and m.lastindex >= 4 else None
            price = parse_public_money(price_text) if price_text else None
            add_card(title, price, None, coupon)

    # 3) Último fallback: para cada ocorrência de Cupom, pega a janela de
    # texto anterior e tenta identificar um título e um preço nela.
    if not cards:
        for m in re.finditer(r"Cupom\s+(?:R\$\s*[\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s*(?:OFF|%\s*OFF)", flat, re.I):
            coupon = detect_public_coupon(m.group(0))
            if not coupon:
                continue
            before = flat[max(0, m.start() - 900):m.start()]
            money = list(money_re.finditer(before))
            if not money:
                continue
            price = parse_public_money(money[-1].group(0))
            title_candidates = re.findall(r"(?:Image:\s*)?([A-Za-zÀ-ÿ0-9][^|]{12,180}?)\s+(?:R\$|\d+%\s*OFF)", before, re.I)
            title = title_candidates[-1].strip() if title_candidates else ""
            if title:
                add_card(title, price, None, coupon)

    # Deduplica cards muito semelhantes.
    unique = {}
    for c in cards:
        key = (norm(c["title"]), round(c["price"], 2), c["coupon"].get("label"))
        unique[key] = c
    return list(unique.values())

def public_coupon_product_cards():
    """Lê associações produto -> cupom da página pública.

    A página pública pode ser renderizada de formas diferentes. Tentamos
    primeiro a página principal e depois páginas públicas de busca do Mercado
    Livre quando a página de cupons não entregar cards no HTML recebido.
    """
    urls = [
        "https://www.mercadolivre.com.br/l/descontaco-cupons",
        "https://www.mercadolivre.com.br/l/promocoes",
        "https://www.mercadolivre.com.br/ofertas/cupons",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    cards = []
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=25, allow_redirects=True)
            if r.status_code != 200:
                print("[CUPOM PRODUTO]", url, "HTTP", r.status_code)
                continue
            text = normalize_coupon_html(r.text)
            found = _extract_public_coupon_cards_from_text(text, r.url or url)
            print("[CUPOM PRODUTO]", url, "cards=", len(found))
            cards.extend(found)
        except Exception as e:
            print("[CUPOM PRODUTO] ERRO", url, repr(e))

    unique = {}
    for c in cards:
        key = (norm(c["title"]), round(float(c["price"]), 2), c["coupon"]["label"])
        unique[key] = c

    out = list(unique.values())
    print("[CARDS DE CUPOM]", len(out))
    for c in out[:25]:
        print("[CARD]", c["title"][:90], "|", brl(c["price"]), "|", c["coupon"]["label"])
    return out


PUBLIC_PRODUCT_COUPON_CACHE = {}
PUBLIC_PRODUCT_COUPON_LOCK = threading.Lock()


def _search_public_listing_for_coupon(title, price, item_id=None):
    """Fallback por busca pública do Mercado Livre.

    É usado quando a página geral de cupons não entregou o card. A busca é
    pública e o cupom só é aceito se o resultado tiver forte semelhança com o
    produto e preço compatível. Assim evitamos transformar cupom genérico em
    cupom aplicável.
    """
    key = f"{norm(title)}|{round(float(price or 0),2)}"
    with PUBLIC_PRODUCT_COUPON_LOCK:
        if key in PUBLIC_PRODUCT_COUPON_CACHE:
            return PUBLIC_PRODUCT_COUPON_CACHE[key]

    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", norm(title))).strip("-")[:180]
    if not slug:
        return None

    url = "https://lista.mercadolivre.com.br/" + quote(slug)
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    result = None
    try:
        r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        if r.status_code == 200:
            text = normalize_coupon_html(r.text)
            cards = _extract_public_coupon_cards_from_text(text, r.url or url)
            best = None
            best_score = 0
            for card in cards:
                sim = title_similarity(title, card["title"])
                diff = abs(float(price) - float(card["price"]))
                tolerance = max(15.0, float(price) * 0.18)
                if diff <= tolerance:
                    sim += 0.18
                if sim > best_score:
                    best_score = sim
                    best = card
            if best is not None and best_score >= 0.82:
                result = dict(best["coupon"])
                result["match_score"] = round(best_score, 3)
                result["public_title"] = best["title"]
                result["public_price"] = best["price"]
                result["source_url"] = best["source_url"]
    except Exception as e:
        print("[CUPOM BUSCA PÚBLICA]", repr(e))

    with PUBLIC_PRODUCT_COUPON_LOCK:
        PUBLIC_PRODUCT_COUPON_CACHE[key] = result
    return result

def detect_public_coupon(text):
    m = re.search(r"Cupom\s+R\$\s*([\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s*OFF", text, re.I)
    if m:
        value = parse_public_money("R$ " + m.group(1))
        return {"type":"fixed", "value":value, "label":f"Cupom {brl(value)} OFF"} if value else None
    m = re.search(r"Cupom\s+(\d+(?:[.,]\d+)?)\s*%\s*OFF", text, re.I)
    if m:
        value = float(m.group(1).replace(",", "."))
        return {"type":"percent", "value":value, "label":f"Cupom {value:g}% OFF"}
    return None


def parse_public_money(text):
    if not text:
        return None
    m = re.search(r"([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)", str(text))
    if not m:
        return None
    v = m.group(1)
    try:
        return float(v.replace(".", "").replace(",", ".")) if "," in v else float(v)
    except Exception:
        return None


def title_similarity(a, b):
    ta = {x for x in norm(a).split() if len(x) >= 3 and x not in STOP_WORDS}
    tb = {x for x in norm(b).split() if len(x) >= 3 and x not in STOP_WORDS}
    if not ta or not tb:
        return 0.0
    common = ta & tb
    score = len(common) / max(1, min(len(ta), len(tb)))
    na, nb = norm(a), norm(b)
    for word in ("iphone","galaxy","samsung","motorola","xiaomi","redmi","poco","kappa","nike","adidas","smartwatch","air","fryer","whey","creatina","perfume"):
        if word in na and word in nb:
            score += 0.10
    return min(score, 1.0)


STOP_WORDS = {"de","da","do","das","dos","com","para","por","e","em","no","na","um","uma","original","novo","oficial"}


def match_public_coupon(title, price, cards, item_id=None):
    best = None
    best_score = 0
    for card in cards or []:
        score = title_similarity(title, card["title"])
        diff = abs(float(price) - float(card["price"]))
        tolerance = max(10.0, float(price) * 0.12)
        if diff <= tolerance:
            score += 0.25
        elif diff <= max(20.0, float(price) * 0.20):
            score += 0.08
        if score > best_score:
            best_score = score
            best = card
    if best is None or best_score < 0.78:
        # Segunda fonte: busca pública do próprio Mercado Livre.
        fallback = _search_public_listing_for_coupon(title, price, item_id)
        return fallback
    c = dict(best["coupon"])
    c["match_score"] = round(best_score, 3)
    c["public_title"] = best["title"]
    c["public_price"] = best["price"]
    c["source_url"] = best["source_url"]
    return c

def calculate_public_coupon(coupon, price):
    if not coupon:
        return 0
    d = 0
    if coupon["type"] == "fixed":
        d = float(coupon["value"])
    else:
        d = float(price) * float(coupon["value"]) / 100
    return round(min(max(d, 0), float(price)), 2)


def search_products_direct(q, limit=30):
    """Busca candidatos sem exigir que todos tenham detalhe de catálogo."""
    data, status, _ = ml_get("/products/search", {
        "site_id": SITE_ID,
        "q": q,
        "status": "active",
        "limit": min(int(limit or 30), 50),
        "offset": 0,
    })
    if status != 200 or not isinstance(data, dict):
        print(f"[BUSCA] {q} -> HTTP {status}")
        return []
    results = data.get("results") or []
    print(f"[BUSCA] {q} -> {len(results)} candidatos")
    return results


def _candidate_fallback(pid, raw, query):
    """Monta uma oferta a partir do próprio resultado de /products/search.

    Alguns produtos não expõem /products/{id}/items para o token atual.
    Nesses casos não descartamos o produto inteiro se a própria busca já
    trouxe buy_box_winner ou dados suficientes.
    """
    if not isinstance(raw, dict):
        return None
    bb = raw.get("buy_box_winner") or raw.get("buy_box")
    if not isinstance(bb, dict):
        return None
    item = _build_item_from_buy_box(bb)
    if not item:
        return None
    p = dict(raw)
    p.setdefault("name", raw.get("title") or pid)
    return pid, p, item, {"category_id": None, "category_name": None, "query": query}


def _fetch_product_fast(pid, raw=None, base=None):
    base = base or {"category_id": None, "category_name": None, "query": ""}

    # 1) Primeiro tenta aproveitar o próprio /products/search.
    # Isso evita uma chamada extra quando o resultado já trouxe buy_box_winner.
    if isinstance(raw, dict):
        bb = raw.get("buy_box_winner") or raw.get("buy_box")
        item = _build_item_from_buy_box(bb)
        if item is not None:
            p = dict(raw)
            p.setdefault("name", raw.get("title") or pid)
            return pid, p, item, base

    # 2) Se a busca não trouxe o vencedor, tenta o detalhe do produto.
    p = product(pid)
    if p:
        bb = p.get("buy_box_winner") or p.get("buy_box")
        item = _build_item_from_buy_box(bb)
        if item is not None:
            return pid, p, item, base

    # 3) Último fallback: /products/{id}/items.
    # Esse endpoint foi o que funcionou para o catálogo em nossos testes.
    items = product_items(pid)
    best = None
    for candidate in items:
        item = normalize_item(candidate)
        if not item:
            continue
        item["sold_quantity"] = candidate.get("sold_quantity") or 0
        # Mantém o primeiro item válido, mas prefere frete grátis.
        if best is None or (item.get("free_shipping") and not best.get("free_shipping")):
            best = item
    if best is not None:
        if p is None:
            p = dict(raw or {})
        p.setdefault("name", (raw or {}).get("title") or pid)
        return pid, p, best, base

    # 4) Se não houver item de venda, não inventa preço.
    return None


# ============================================================
# SINAIS DE DEMANDA — LEVES, CACHEADOS E USADOS SÓ PARA RANKING
# ============================================================
# Estratégia:
# 1) /trends/MLB: 1 chamada, atualizada semanalmente pela API.
# 2) /highlights: TOP 20 por categoria. Os IDs de categoria ficam em cache.
# 3) Os sinais NUNCA bloqueiam a busca de produtos. Se um endpoint falhar,
#    a busca continua.
# 4) Só os melhores candidatos são enriquecidos com detalhes de produto.

DEMAND_TTL = 3600
CATEGORY_TTL = 86400
_DEMAND_CACHE = {"at": 0.0, "trend": [], "best": {}}
_CATEGORY_CACHE = {"at": 0.0, "ids": {}}
_DEMAND_LOCK = threading.Lock()

DEMAND_CATEGORY_QUERIES = {
    "📱 Celulares": "smartphone",
    "🌸 Perfumes": "perfume",
    "🏋️ Academia": "tenis corrida",
    "🔧 Ferramentas": "furadeira",
    "🎧 Eletrônicos": "fone bluetooth",
    "🏠 Casa": "aspirador de pó",
    "🍳 Cozinha": "air fryer",
    "🚗 Automotivo": "acessorios automotivos",
    "👕 Moda": "tenis masculino",
}

DEMAND_ANCHORS = {
    "📱 Celulares": ["smartphone", "celular", "iphone", "galaxy", "samsung", "motorola", "xiaomi", "redmi", "poco", "realme"],
    "🌸 Perfumes": ["perfume", "parfum", "eau de parfum", "eau de toilette", "fragrancia"],
    "🏋️ Academia": ["academia", "treino", "corrida", "tenis", "whey", "creatina", "suplemento", "legging"],
    "🔧 Ferramentas": ["furadeira", "parafusadeira", "esmerilhadeira", "serra", "ferramenta", "impacto"],
    "🎧 Eletrônicos": ["fone", "headset", "smartwatch", "tablet", "caixa de som", "camera", "power bank"],
    "🏠 Casa": ["aspirador", "liquidificador", "cafeteira", "air fryer", "ventilador", "ferro"],
    "🍳 Cozinha": ["air fryer", "panela", "cafeteira", "liquidificador", "sanduicheira", "cozinha"],
    "🚗 Automotivo": ["automotivo", "carro", "compressor", "aspirador automotivo", "carregador automotivo", "tapete"],
    "👕 Moda": ["tenis", "mochila", "relogio", "bolsa", "oculos", "camiseta", "vestido"],
}

def _demand_category_from_text(text):
    t = norm(text)
    best, hits = None, 0
    for cat, anchors in DEMAND_ANCHORS.items():
        h = sum(1 for a in anchors if norm(a) in t)
        if h > hits:
            best, hits = cat, h
    return best

def _get_best_seller_category_ids(force=False):
    now = time.time()
    with _DEMAND_LOCK:
        if not force and _CATEGORY_CACHE["ids"] and now - _CATEGORY_CACHE["at"] < CATEGORY_TTL:
            return dict(_CATEGORY_CACHE["ids"])

    ids = {}
    # Cada categoria tenta as duas primeiras categorias retornadas pelo
    # domain_discovery. Só guardamos uma que realmente tenha /highlights.
    for cat, q in DEMAND_CATEGORY_QUERIES.items():
        try:
            found = discover_categories(q)
            chosen = None
            for candidate in found[:3]:
                cid = candidate.get("category_id")
                if not cid:
                    continue
                rows = highlights(cid)
                if rows:
                    chosen = cid
                    break
            if chosen:
                ids[cat] = chosen
        except Exception as e:
            print("[CATEGORY ID]", cat, repr(e))

    with _DEMAND_LOCK:
        _CATEGORY_CACHE.update({"at": now, "ids": ids})
    return dict(ids)

def load_demand_signals(force=False):
    now = time.time()
    with _DEMAND_LOCK:
        if not force and now - _DEMAND_CACHE["at"] < DEMAND_TTL:
            return _DEMAND_CACHE["trend"], _DEMAND_CACHE["best"]

    trend = []
    best = {}

    # Uma única chamada para tendências nacionais. Os 10 primeiros são os
    # de maior crescimento; os 20 seguintes são os mais desejados; os últimos
    # 20 são tendências populares.
    try:
        data, status, _ = ml_get(f"/trends/{SITE_ID}")
        if status == 200 and isinstance(data, list):
            for pos, row in enumerate(data[:50], start=1):
                if not isinstance(row, dict):
                    continue
                kw = (row.get("keyword") or "").strip()
                if not kw:
                    continue
                trend.append({
                    "keyword": kw,
                    "rank": pos,
                    "category": _demand_category_from_text(kw),
                    "score": 130 if pos <= 10 else 90 if pos <= 30 else 60,
                    "bucket": "ALTA FORTE" if pos <= 10 else "MAIS PROCURADO" if pos <= 30 else "TENDÊNCIA",
                })
        else:
            print("[DEMANDA/TRENDS] HTTP", status)
    except Exception as e:
        print("[DEMANDA/TRENDS]", repr(e))

    # Mais vendidos: top 20 por categoria. Para reduzir chamadas, os IDs de
    # categoria são cacheados por 24h. O ranking é atualizado pela API a cada
    # nova coleta de sinais.
    ids = _get_best_seller_category_ids(force=False)
    for cat, cid in ids.items():
        try:
            rows = highlights(cid)
            for row in rows[:20]:
                if not isinstance(row, dict):
                    continue
                pid = str(row.get("id") or "")
                if not pid:
                    continue
                pos = int(row.get("position") or 99)
                old = best.get(pid)
                if old is None or pos < old["position"]:
                    best[pid] = {"position": pos, "category": cat, "category_id": cid}
        except Exception as e:
            print("[DEMANDA/HIGHLIGHTS]", cat, repr(e))

    with _DEMAND_LOCK:
        _DEMAND_CACHE.update({"at": now, "trend": trend, "best": best})
    print("[DEMANDA] trends=", len(trend), "mais_vendidos=", len(best))
    return trend, best

def demand_score(title, category, product_id, trend_items, best_map):
    t = norm(title)
    trend_score = 0.0
    trend_rank = None
    trend_keyword = None
    trend_bucket = None

    # Match da tendência pelo título, mas sem exigir que o produto seja uma
    # tendência para poder aparecer.
    for x in trend_items:
        xcat = x.get("category")
        kw = norm(x.get("keyword"))
        if not kw:
            continue
        if xcat and category and xcat != category:
            continue
        words = [w for w in kw.split() if len(w) >= 5]
        matched = kw in t or (words and sum(1 for w in words if w in t) >= max(1, len(words)//2))
        if matched and float(x.get("score") or 0) > trend_score:
            trend_score = float(x.get("score") or 0)
            trend_rank = x.get("rank")
            trend_keyword = x.get("keyword")
            trend_bucket = x.get("bucket")

    best = best_map.get(str(product_id))
    best_pos = best.get("position") if best else None
    best_cat = best.get("category") if best else None
    best_score = max(0.0, 100.0 - (float(best_pos or 99) - 1.0) * 5.0) if best_pos else 0.0
    both = trend_score > 0 and best_pos is not None

    # Aparição nos dois é o sinal mais forte. Depois vem posição de vendas e
    # procura. O score não depende de preço, então a demanda manda no ranking.
    return {
        "trend_score": trend_score,
        "trend_rank": trend_rank,
        "trend_keyword": trend_keyword,
        "trend_bucket": trend_bucket,
        "best_seller_position": best_pos,
        "best_seller_category": best_cat,
        "best_seller_score": best_score,
        "appears_both": both,
        "demand_score": (300 if both else 0) + best_score * 3.0 + trend_score * 2.0,
    }

def _search_seed_queries(category=None):
    if category:
        # Mantém a categoria selecionada focada nos produtos principais.
        return CATALOG.get(category, [])[:3]
    return [qs[0] for qs in CATALOG.values() if qs]

def scan_queries(queries, min_discount=0, apply_coupons=False):
    """Caça robusta e rápida.

    Regra principal:
      - busca direta em cada categoria escolhida para garantir volume;
      - acrescenta os TOP 20 de mais vendidos quando disponíveis;
      - Trends/Highlights servem para RANQUEAR, nunca para bloquear;
      - só os melhores candidatos são enriquecidos;
      - nenhum scraping e nenhum cupom durante a caça.
    """
    base_queries = [str(q).strip() for q in (queries or []) if str(q).strip()]
    if not base_queries:
        base_queries = _search_seed_queries()

    # Sinais de demanda são opcionais. Se falharem, a busca continua normal.
    try:
        trend_items, best_map = load_demand_signals()
    except Exception as e:
        print("[DEMANDA] ignorada:", repr(e))
        trend_items, best_map = [], {}

    requested_categories = set()
    for q in base_queries:
        c = query_category(q)
        if c:
            requested_categories.add(c)

    # --------------------------------------------------------
    # 1) BUSCA DIRETA: uma chamada por consulta/categoria.
    #    Isso garante que não ficaremos com 8 produtos só porque algum
    #    endpoint de ranking retornou poucos IDs.
    # --------------------------------------------------------
    raw_candidates = {}
    for q in base_queries:
        try:
            rows = search_products_direct(q, 15)
        except Exception as e:
            print("[BUSCA ERRO]", q, repr(e))
            rows = []
        cat = query_category(q)
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            pid = str(raw.get("id") or raw.get("product_id") or "").strip()
            if not pid:
                continue
            title = raw.get("name") or raw.get("title") or ""
            if not is_requested_product(title, q, cat):
                continue
            raw_candidates.setdefault(pid, {
                "raw": raw,
                "category_id": None,
                "category_name": cat,
                "query": q,
            })

    # --------------------------------------------------------
    # 2) TOP MAIS VENDIDOS: acrescenta IDs que a busca direta não trouxe.
    #    No máximo 3 por categoria para manter a atualização rápida.
    # --------------------------------------------------------
    best_added = 0
    for pid, info in sorted(best_map.items(), key=lambda kv: (
        int(kv[1].get("position") or 99), kv[0]
    )):
        cat = info.get("category")
        if requested_categories and cat not in requested_categories:
            continue
        if pid in raw_candidates:
            continue
        q = DEMAND_CATEGORY_QUERIES.get(cat, "")
        if not q:
            continue
        raw_candidates[pid] = {
            "raw": None,
            "category_id": info.get("category_id"),
            "category_name": cat,
            "query": q,
        }
        best_added += 1
        if best_added >= 27:
            break

    print("[CANDIDATOS ANTES DO RANKING]", len(raw_candidates))

    # --------------------------------------------------------
    # 3) Ranking barato antes de consultar detalhes.
    #    Quem já é mais vendido / aparece em tendência sobe primeiro.
    # --------------------------------------------------------
    prelim = []
    for pid, base in raw_candidates.items():
        raw = base.get("raw") or {}
        title = raw.get("name") or raw.get("title") or pid
        ds = demand_score(title, base.get("category_name"), pid, trend_items, best_map)
        relevance_score = relevance(title, base.get("query", ""))
        price = raw.get("price")
        try:
            price_n = float(price) if price is not None else 999999.0
        except Exception:
            price_n = 999999.0
        prelim_score = (
            ds.get("demand_score", 0) * 10
            + (1000 if ds.get("appears_both") else 0)
            + max(0, relevance_score) * 2
            + (50 if 69.90 <= price_n <= 2500 else 0)
        )
        prelim.append((prelim_score, pid, base))

    prelim.sort(key=lambda x: (-x[0], x[1]))
    # 42 candidatos no máximo. A maior parte sai diretamente do buy_box da
    # busca; os demais usam /products e só depois /products/{id}/items.
    shortlist = prelim[:42]

    # --------------------------------------------------------
    # 4) ENRIQUECIMENTO CONTROLADO.
    # --------------------------------------------------------
    fetched = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {
            executor.submit(_fetch_product_fast, pid, base.get("raw"), base): (pid, base)
            for _, pid, base in shortlist
        }
        for fut in as_completed(future_map):
            try:
                result = fut.result()
                if result:
                    fetched.append(result)
            except Exception as e:
                print("[PRODUTO FAST ERRO]", repr(e))

    print("[PRODUTOS APROVEITADOS]", len(fetched))

    offers = []
    seen = set()

    for pid, p, item, base in fetched:
        try:
            title = p.get("name") or p.get("title") or pid
            query_used = base.get("query", "")
            category = (
                base.get("category_name")
                or query_category(query_used)
                or _demand_category_from_text(title)
                or "Produto"
            )

            # Produtos vindos de highlights também passam pelo filtro de
            # categoria, mas nunca são descartados apenas por não aparecerem
            # em Trends.
            if not is_requested_product(title, query_used, category):
                continue
            if pid in seen:
                continue
            seen.add(pid)

            relevance_score = relevance(title, query_used)
            if relevance_score < -20:
                continue

            item_id = item.get("item_id")
            if not item_id:
                continue

            try:
                price = float(item.get("price")) if item.get("price") is not None else None
            except Exception:
                price = None
            try:
                original = float(item.get("original_price")) if item.get("original_price") is not None else None
            except Exception:
                original = None

            # Só chama sale_price quando o valor inicial estiver ausente ou
            # claramente suspeito. Isso mantém a busca rápida.
            if price is None or price <= 0 or price > 100000:
                sale, sale_original = get_current_sale_price(item_id)
                if sale is not None:
                    price = sale
                    if sale_original is not None:
                        original = sale_original
            if not valid_catalog_price(price):
                continue

            if original is None and isinstance(p.get("buy_box_winner"), dict):
                bb = p["buy_box_winner"]
                for key in ("regular_price", "original_price"):
                    try:
                        if bb.get(key) is not None:
                            original = float(bb[key])
                            break
                    except Exception:
                        pass

            seller_disc = discount(price, original)
            if seller_disc < float(min_discount or 0):
                continue

            shipping = item.get("shipping_cost")
            shipping_known = shipping is not None
            total_price = total(price, shipping) if shipping_known else price
            free = bool(item.get("free_shipping"))
            sold = item.get("sold_quantity") or 0
            try:
                sold = float(sold)
            except Exception:
                sold = 0

            ds = demand_score(title, category, pid, trend_items, best_map)
            giro_score = min(100, sold / 10) if sold > 0 else 0
            opportunity = round(
                ds["demand_score"]
                + giro_score
                + (20 if free else 0)
                + min(20, max(0, seller_disc))
                + max(0, relevance_score) * 0.05,
                2,
            )

            pictures = p.get("pictures") or []
            image = None
            if pictures and isinstance(pictures[0], dict):
                image = pictures[0].get("url") or pictures[0].get("secure_url")

            offers.append({
                "product_id": pid,
                "item_id": item_id,
                "title": title,
                "modelo_nome": model_name(title),
                "especificacoes": specs(title),
                "image": image,
                "category_name": category,
                "permalink": item.get("permalink") or p.get("permalink") or f"https://www.mercadolivre.com.br/p/{pid}",
                "price": price,
                "original_price": original,
                "discount": seller_disc,
                "seller_id": item.get("seller_id"),
                "condition": item.get("condition"),
                "free_shipping": free,
                "shipping_cost": shipping,
                "shipping_known": shipping_known,
                "total_price": total_price,
                "relevance_score": relevance_score,
                "sold_quantity": sold,
                "giro_score": giro_score,
                "trend_score": ds["trend_score"],
                "trend_keyword": ds["trend_keyword"],
                "trend_bucket": ds["trend_bucket"],
                "trend_rank": ds["trend_rank"],
                "best_seller_position": ds["best_seller_position"],
                "best_seller_category": ds["best_seller_category"],
                "appears_both": ds["appears_both"],
                "demand_score": ds["demand_score"],
                "opportunity_score": opportunity,
                "cupom": None,
                "desconto_cupom": 0,
                "percentual_cupom_efetivo": 0,
                "cupom_match": None,
                "cupom_uso_limite": None,
                "cash_discount": 0,
                "cash_label": None,
                "cash_final": None,
                "melhor_forma": None,
                "maior_desconto": 0,
                "preco_com_cupom": None,
                "preco_final_melhor": None,
                "affiliate_link": "",
                "extra_earnings": 0,
            })
        except Exception as e:
            print("[OFERTA ERRO]", repr(e))

    # Demanda primeiro; custo total é desempate.
    offers.sort(key=lambda o: (
        0 if o.get("appears_both") else 1,
        -(o.get("best_seller_position") is not None),
        float(o.get("best_seller_position") or 99),
        -(o.get("trend_score") or 0),
        -(o.get("demand_score") or 0),
        -(o.get("giro_score") or 0),
        0 if o.get("free_shipping") else 1,
        float(o.get("total_price") or o.get("price") or 999999),
    ))

    # Um produto de catálogo = uma oportunidade.
    grouped = {}
    for o in offers:
        grouped.setdefault(o["product_id"], []).append(o)

    models = []
    for pid, arr in grouped.items():
        arr.sort(key=lambda o: (
            0 if o.get("free_shipping") else 1,
            float(o.get("total_price") or o.get("price") or 999999),
        ))
        o = arr[0]
        models.append({
            "product_id": pid,
            "title": o["title"],
            "modelo_nome": o["modelo_nome"],
            "especificacoes": o["especificacoes"],
            "image": o["image"],
            "category_name": o.get("category_name", ""),
            "ofertas": [o],
        })

    models.sort(key=lambda g: (
        0 if g["ofertas"][0].get("appears_both") else 1,
        -(g["ofertas"][0].get("best_seller_position") is not None),
        float(g["ofertas"][0].get("best_seller_position") or 99),
        -(g["ofertas"][0].get("trend_score") or 0),
        -(g["ofertas"][0].get("demand_score") or 0),
        -(g["ofertas"][0].get("giro_score") or 0),
        float(g["ofertas"][0].get("total_price") or g["ofertas"][0].get("price") or 999999),
    ))
    models = models[:30]
    flat = [g["ofertas"][0] for g in models]

    valores = [o["price"] for o in flat if o.get("price") is not None]
    totais = [o["total_price"] for o in flat if o.get("shipping_known") and o.get("total_price") is not None]
    trend_count = sum(1 for o in flat if o.get("trend_score", 0) > 0)
    best_count = sum(1 for o in flat if o.get("best_seller_position") is not None)
    both_count = sum(1 for o in flat if o.get("appears_both"))

    stats = {
        "ofertas": len(flat),
        "produtos em alta": trend_count,
        "mais vendidos": best_count,
        "aparecem nos dois": both_count,
        "cupom candidato": 0,
        "cupons com limite": 0,
        "maior desconto estimado": brl(0),
        "menor preço com cupom": "—",
        "menor preço do produto": brl(min(valores or [0])),
        "menor total com frete": brl(min(totais or [0])),
        "produtos sem cupom": len(flat),
        "modo": "mais procurados + mais vendidos — busca rápida e ranking por demanda",
    }
    return {"stats": stats, "modelos": models, "ofertas": flat}

def auto_scan(category=None, min_discount=0):
    return scan_queries(_search_seed_queries(category), min_discount, apply_coupons=False)

# ============================================================
# ANÚNCIO
# ============================================================

def ad_text(o, affiliate=""):
    lines = ["🔥 OFERTA ENCONTRADA!","",f"🛍️ {o.get('title','Produto')}"]
    if o.get("original_price"):
        lines.append(f"💸 De: {brl(o['original_price'])}")
    lines.append(f"🔥 Por: {brl(o['price'])}")
    if o.get("discount",0)>0:
        lines.append(f"🏷️ {o['discount']}% OFF")
    if o.get("free_shipping"):
        lines.append("🚚 Frete grátis")
    if o.get("cupom"):
        c=o["cupom"]
        label = c.get("code") or c.get("label") or "Cupom confirmado"
        lines += ["",f"🎟️ CUPOM: {label}"]
        if c.get("discount_percent") or (c.get("type") == "percent"): lines.append(f"🔥 Até {c.get('discount_percent') or c.get('value')}% OFF")
        if c.get("fixed_discount") or c.get("type") == "fixed": lines.append(f"💰 {brl(c.get('fixed_discount') or c.get('value'))} OFF")
        if c.get("max_discount"): lines.append(f"💰 Limite do cupom: {brl(c['max_discount'])}")
        if o.get("desconto_cupom") is not None: lines.append(f"💵 Desconto estimado: {brl(o.get('desconto_cupom'))}")
        if c.get("min_purchase"): lines.append(f"🛒 Compra mínima: {brl(c['min_purchase'])}")
        lines += ["",f"💥 PREÇO ESTIMADO COM CUPOM: {brl(o['preco_com_cupom'])}"]
    lines += ["","⚠️ Consulte as condições e confirme o cupom no checkout.","","🛒 PEGAR OFERTA:",affiliate or "Gere o link pelo Gerador oficial do Mercado Livre."]
    return "\n".join(lines)

# ============================================================
# JOBS DE CAÇA EM SEGUNDO PLANO
# ============================================================

JOBS = {}
JOBS_LOCK = threading.Lock()

def create_job():
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "message": "Aguardando início...",
            "result": None,
            "error": None,
        }
    return job_id

def update_job(job_id, **kwargs):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)

def get_job(job_id):
    with JOBS_LOCK:
        return dict(JOBS.get(job_id, {
            "status": "not_found",
            "progress": 0,
            "message": "Caça não encontrada.",
            "result": None,
            "error": "not_found",
        }))

def run_caca_job(job_id, category=None):
    try:
        update_job(job_id, status="running", progress=5, message="🔎 Procurando produtos de alto giro rapidamente...")

        if category:
            queries = CATALOG.get(category, [])[:3]
        else:
            # Uma busca forte por categoria. Trends/Highlights só ranqueiam.
            queries = [qs[0] for qs in CATALOG.values() if qs]

        update_job(job_id, progress=12, message=f"🛒 Consultando Mercado Livre ({len(queries)} buscas)...")
        update_job(job_id, progress=55, message="📦 Encontrando produtos de alto giro...")
        result = scan_queries(queries, apply_coupons=False)
        update_job(job_id, progress=96, message="📊 Finalizando ranking...")
        update_job(job_id, status="done", progress=100, message=f"✅ Produtos atualizados: {result.get('stats', {}).get('ofertas', 0)} ofertas. Cupons ficam separados.", result=json_safe(result))
    except Exception as e:
        print("[ERRO JOB CAÇA]", repr(e))
        update_job(job_id, status="error", progress=100, message="❌ Erro durante a atualização.", error=str(e))

# ============================================================
# API
# ============================================================

@app.route("/api/buscar")
def api_buscar():
    q=request.args.get("q","").strip()
    if not q: return jsonify({"erro":"Informe uma busca."}),400
    return jsonify(json_safe(scan_queries([q], request.args.get("desconto",0), apply_coupons=False)))

@app.route("/api/cacar")
def api_cacar():
    categoria=request.args.get("categoria","").strip() or None
    job_id=create_job()
    thread=threading.Thread(target=run_caca_job, args=(job_id, categoria), daemon=True)
    thread.start()
    return jsonify({"ok":True,"job_id":job_id,"status":"queued"})

@app.route("/api/cacar/status/<job_id>")
def api_cacar_status(job_id):
    return jsonify(json_safe(get_job(job_id)))

@app.route("/api/cupons")
def api_cupons():
    sync = None
    if request.args.get("atualizar")=="1":
        sync = sync_coupons()
    return jsonify({"cupons":json_safe(coupons()),"fontes":COUPON_SOURCE_URLS,"sincronizacao":json_safe(sync)})

@app.route("/api/gerar-anuncio")
def api_anuncio():
    o = {
        "title":request.args.get("title","Produto"),
        "price":request.args.get("price",0),
        "original_price":request.args.get("original_price"),
        "discount":float(request.args.get("discount",0) or 0),
        "free_shipping":request.args.get("shipping_free")=="1",
        "cupom":None,"preco_com_cupom":None
    }
    code=request.args.get("cupom","").strip()
    if code:
        # Cupons de cards públicos podem ser "Cupom R$ 15 OFF" ou "Cupom 10% OFF"
        # e não possuem necessariamente um código digitável. Aceita ambos.
        public = detect_public_coupon(code)
        if public:
            cup = dict(public)
            d = calculate_public_coupon(cup, float(o["price"]))
            cup["desconto_estimado"] = d
            cup["label"] = cup.get("label") or code
            o["cupom"] = cup
            o["desconto_cupom"] = d
            o["preco_com_cupom"] = max(0, float(o["price"]) - d)
        else:
            c=get_db()
            row=c.execute("SELECT * FROM cupons WHERE code=? AND active=1",(code.upper(),)).fetchone()
            c.close()
            if row:
                cup=dict(row); cup["desconto_estimado"]=coupon_discount(cup,float(o["price"]))
                o["cupom"]=cup
                o["desconto_cupom"]=cup["desconto_estimado"]
                o["preco_com_cupom"]=max(0,float(o["price"])-cup["desconto_estimado"])
    return jsonify({"anuncio":ad_text(o,request.args.get("affiliate_link","").strip())})

# ============================================================
# TESTES / DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/teste-produto-itens")
def teste_items():
    pid=request.args.get("product_id","MLB58793248")
    data,status,_=ml_get(f"/products/{pid}/items")
    return jsonify({"product_id":pid,"status_http":status,"resposta":data}),status

@app.route("/mercadolivre/teste-produto")
def teste_product():
    pid=request.args.get("product_id","MLB58793248")
    data,status,_=ml_get(f"/products/{pid}")
    return jsonify({"product_id":pid,"status_http":status,"resposta":data}),status

@app.route("/mercadolivre/diagnostico")
def diagnostico():
    t=tokens()
    result={"configurado":bool(ML_CLIENT_ID),"conectado":bool(access_token())}
    if access_token():
        me,status,_=ml_get("/users/me")
        result["users_me"]={"status_http":status,"resposta":me}
    if t:
        result["token_local"]={"user_id":t.get("user_id"),"nickname":t.get("nickname"),"expires_at":t.get("expires_at")}
    return jsonify(result)

# ============================================================
# AFILIADOS - FLUXO OFICIAL
# ============================================================

AFFILIATE_GENERATOR_URL = "https://www.mercadolivre.com.br/l/afiliados-gere-seus-links"
AFFILIATE_PORTAL_URL = "https://www.mercadolivre.com.br/l/visite-o-portal-de-afiliados"

@app.route("/afiliado/gerador")
def afiliado_gerador():
    # O Mercado Livre documenta o Gerador oficial, mas não documenta uma API
    # pública para o nosso app transformar item_id em link de afiliado.
    # Portanto abrimos o gerador oficial em vez de fabricar um link inválido.
    return redirect(AFFILIATE_GENERATOR_URL)

@app.route("/afiliado/portal")
def afiliado_portal():
    return redirect(AFFILIATE_PORTAL_URL)

# ============================================================
# HTML
# ============================================================

HTML = r"""
<!doctype html><html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Caçador de Ofertas</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f5f7;font-family:Arial;color:#222}
.container{max-width:1050px;margin:auto;padding:18px}.card{background:#fff;border-radius:16px;padding:18px;margin-bottom:18px;box-shadow:0 5px 20px #0000000c}
button,input,select{width:100%;padding:13px;border-radius:10px;border:1px solid #ddd;font-size:15px}button{border:0;background:#3483fa;color:#fff;cursor:pointer;margin-top:7px}
.login{background:#ffe600;color:#222}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px}.cat{background:#fff;border:1px solid #ddd;color:#222;text-align:left}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:9px}.stat{background:#f3f4f6;padding:13px;border-radius:11px}.stat b{display:block;font-size:23px;margin-top:4px}
.modelo{border:2px solid #eee;border-radius:15px;padding:14px;margin:13px 0}.mh{display:flex;gap:12px;align-items:center}.mh img{width:85px;height:85px;object-fit:contain;background:#fafafa;border-radius:10px}.title{font-size:18px;font-weight:bold}.tag{display:inline-block;background:#eef4ff;color:#3483fa;border-radius:7px;padding:5px 8px;font-size:11px;margin:3px}.seller{background:#fafafa;border:1px solid #eee;border-radius:12px;padding:12px;margin-top:10px}.price{font-size:22px;font-weight:bold}.green{color:#00a650;font-weight:bold}.old{text-decoration:line-through;color:#777}.coupon{background:#fff8d6;border:1px dashed #d7ad00;border-radius:10px;padding:10px;margin-top:9px}.final{background:#eaf8ef;color:#008a3e;font-weight:bold;padding:9px;border-radius:8px;margin-top:7px}.ad{display:none;white-space:pre-wrap;background:#f7f7f7;padding:10px;border-radius:9px;margin-top:8px;font-size:13px}.small{font-size:12px;color:#666}.status{background:#eef8f0;padding:10px;border-radius:9px}
</style>
<script>
let cacarTimer=null;
async function cacar(cat){
 const status=document.getElementById('status');
 status.textContent='🔄 Iniciando atualização de produtos...';
 document.getElementById('results').innerHTML='<p>🔎 Procurando produtos de alto giro rapidamente...</p>';
 if(cacarTimer){clearTimeout(cacarTimer);cacarTimer=null;}
 try{
  const url='/api/cacar'+(cat?'?categoria='+encodeURIComponent(cat):'');
  const r=await fetch(url,{cache:'no-store'});
  const start=await r.json();
  if(!start.job_id){throw new Error(start.erro||'Não foi possível iniciar a atualização.');}
  acompanharCaca(start.job_id);
 }catch(e){
  status.textContent='❌ '+e.message;
 }
}
async function acompanharCaca(jobId){
 const status=document.getElementById('status');
 try{
  const r=await fetch('/api/cacar/status/'+encodeURIComponent(jobId),{cache:'no-store'});
  const job=await r.json();
  status.textContent=(job.message||'🔄 Atualizando...')+' '+(job.progress||0)+'%';
  if(job.status==='done'){
   if(job.result) render(job.result);
   status.textContent='✅ '+(job.message||'Produtos atualizados.');
   cacarTimer=null;
   return;
  }
  if(job.status==='error'){
   status.textContent='❌ '+(job.error||job.message||'Erro durante a atualização.');
   cacarTimer=null;
   return;
  }
  cacarTimer=setTimeout(()=>acompanharCaca(jobId),1200);
 }catch(e){
  status.textContent='⚠️ Aguardando resposta do servidor...';
  cacarTimer=setTimeout(()=>acompanharCaca(jobId),1800);
 }
}
async function buscar(){
 const q=document.getElementById('q').value.trim(); if(!q)return;
 document.getElementById('status').textContent='🔄 Procurando...';
 const r=await fetch('/api/buscar?q='+encodeURIComponent(q)); const data=await r.json(); render(data);
 document.getElementById('status').textContent='✅ Busca atualizada agora.';
}
function render(data){
 document.getElementById('stats').innerHTML=Object.entries(data.stats||{}).map(([k,v])=>`<div class="stat">${k}<b>${v}</b></div>`).join('');
 document.getElementById('results').innerHTML=(data.modelos||[]).map((m,mi)=>`
 <div class="modelo">
  <div class="mh">${m.image?`<img src="${m.image}">`:''}<div>
   <span class="tag">🔥 OPORTUNIDADE ${mi+1}</span><div class="title">${esc(m.modelo_nome)}</div>
   ${(m.especificacoes||[]).map(s=>`<span class="tag">${esc(s)}</span>`).join('')}
   <div class="small">${m.ofertas.length} vendedor(es)</div>
  </div></div>
  ${m.ofertas.map((o,oi)=>seller(o,mi,oi)).join('')}
 </div>`).join('') || '<p>Nenhuma oportunidade encontrada.</p>';
}
function seller(o,mi,oi){
 const id='a'+mi+'_'+oi;
 const cup=o.cupom;
 return `<div class="seller">
 ${o.menor_preco_modelo?'<span class="tag" style="background:#00a650;color:white">🏆 MELHOR CUSTO TOTAL</span>':''}
 <div class="price">${brl(o.price)}</div>
 ${o.original_price?`<div class="old">De: ${brl(o.original_price)}</div>`:''}
 ${o.appears_both?'<div class="tag" style="background:#e8fff0;color:#008a3e;font-weight:bold">🔥 APARECE NOS DOIS: PROCURADO + MAIS VENDIDO</div>':''}
 ${o.trend_bucket?`<div class="tag">📈 ${esc(o.trend_bucket)}${o.trend_rank?` #${o.trend_rank}`:''}</div>`:''}
 ${o.best_seller_position?`<div class="tag" style="background:#fff1d6;color:#8a5700">🏆 MAIS VENDIDO #${o.best_seller_position}</div>`:''}
 ${o.discount>0?`<div class="green">🔥 ${o.discount}% OFF</div>`:''}
 ${o.free_shipping?'<div class="green">🚚 Frete grátis</div>':''}
 ${o.shipping_known ? (Number(o.shipping_cost||0)>0 ? `<div>🚚 Frete: ${brl(o.shipping_cost)}</div><div class="green"><b>💰 Total pago estimado: ${brl(o.total_price)}</b></div>` : `<div class="green"><b>💰 Total pago: ${brl(o.total_price)}</b></div>`) : '<div class="small">🚚 Frete não informado pelo Mercado Livre</div>'}
 ${cup?`<div class="coupon"><b>🎟️ CUPOM: ${esc(cup.code || cup.label || 'Cupom disponível')}</b>
 ${cup.discount_percent?`<div>🔥 Até ${cup.discount_percent}% OFF</div>`:''}
 ${cup.fixed_discount?`<div>💰 ${brl(cup.fixed_discount)} OFF</div>`:''}
 ${cup.min_purchase?`<div class="small">Compra mínima: ${brl(cup.min_purchase)}</div>`:''}
 ${cup.max_discount?`<div class="small">Desconto máximo: ${brl(cup.max_discount)}</div>`:''}
 ${cup.usage_limit?`<div class="small">👥 Limite informado: ${Number(cup.usage_limit).toLocaleString('pt-BR')} usos</div>`:''}
 <div>💵 Desconto estimado: <b>${brl(o.desconto_cupom)}</b> (${Number(o.percentual_cupom_efetivo||0).toFixed(2)}%)</div>
 <div class="final">💥 Estimado com cupom: ${brl(o.preco_com_cupom)}</div>
 <div class="small">⚠️ ${o.cupom_match==='produto_publico'?'Cupom encontrado associado ao produto em fonte pública do Mercado Livre.':'Cupom encontrado em fonte pública para este produto.'} Confirme no checkout.</div></div>`:''}
 ${o.cash_discount>0?`<div class="coupon" style="background:#eefaf2;border-color:#78c995"><b>💳 ${esc(o.cash_label||'Pagamento à vista')}</b><div>Desconto informado: ${brl(o.cash_discount)}</div><div class="final">💥 Final estimado: ${brl(o.cash_final)}</div><div class="small">⚠️ Não somado ao cupom automaticamente.</div></div>`:''}
 <div class="small">👤 Vendedor: ${o.seller_id||'N/A'}</div><br>
 <a href="${o.permalink}" target="_blank">🛒 Ver produto</a>
 <button onclick="copiarUrl('${id}',${JSON.stringify(o.permalink)})" style="background:#555">🔗 Copiar URL do produto</button>
 <a href="/afiliado/gerador" target="_blank"><button style="background:#ffe600;color:#222">💰 Abrir Gerador oficial de afiliado</button></a>
 <button onclick="anuncio('${id}',${JSON.stringify(o)})">📢 Gerar anúncio</button>
 <button id="copy_${id}" style="display:none;background:#ff8a00" onclick="copyAd('${id}')">📋 Copiar oferta</button>
 <div id="ad_${id}" class="ad"></div>
 </div>`;
}

async function copiarUrl(id,url){
 try{
  if(navigator.clipboard && window.isSecureContext){await navigator.clipboard.writeText(url);}
  else{const ta=document.createElement('textarea');ta.value=url;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();document.execCommand('copy');ta.remove();}
  alert('✅ URL do produto copiada. Agora abra o Gerador oficial e cole a URL.');
 }catch(e){alert('URL do produto: '+url);}
}
async function anuncio(id,o){
 const p=new URLSearchParams({title:o.title,price:o.price,discount:o.discount,shipping_free:o.free_shipping?'1':'0',cupom:o.cupom?(o.cupom.code || o.cupom.label || ''):'',affiliate_link:''});
 if(o.original_price)p.set('original_price',o.original_price);
 const r=await fetch('/api/gerar-anuncio?'+p); const d=await r.json();
 document.getElementById('ad_'+id).style.display='block';document.getElementById('ad_'+id).textContent=d.anuncio;document.getElementById('copy_'+id).style.display='block';
}
async function copyAd(id){
 const el=document.getElementById('ad_'+id); const text=el.textContent.trim();
 if(!text){alert('Gere o anúncio primeiro.');return;}
 try{
  if(navigator.clipboard && window.isSecureContext){await navigator.clipboard.writeText(text);}
  else{
   const ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();document.execCommand('copy');ta.remove();
  }
  const b=document.getElementById('copy_'+id); const old=b.textContent; b.textContent='✅ Copiado!'; setTimeout(()=>b.textContent=old,1500);
 }catch(e){alert('Não foi possível copiar automaticamente. Selecione o texto do anúncio e copie.');}
}
function brl(v){return 'R$ '+Number(v||0).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})}
function esc(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}
</script></head><body><div class="container">
<div class="card"><h1>🛒 Caçador de Ofertas</h1>
<p>Encontra produtos de alto giro a partir de R$ 69,90. A conta do Mercado Livre permanece conectada automaticamente.</p>
{% if conectado %}<div class="status">🟢 Mercado Livre conectado{% if nickname %}<br><b>{{nickname}}</b>{% endif %}</div><a href="/mercadolivre/logout"><button>Desconectar</button></a>
{% else %}<a href="/mercadolivre/login"><button class="login">🔗 Conectar Mercado Livre</button></a>{% endif %}
</div>
<div class="card"><h2>🔥 Encontrar melhores produtos</h2><button onclick="cacar('')">🚀 ATUALIZAR PRODUTOS RÁPIDO</button><div class="grid" style="margin-top:10px">{% for c in categorias %}<button class="cat" onclick="cacar({{c|tojson}})">{{c}}</button>{% endfor %}</div><p id="status" class="small">Escolha uma categoria ou toque em atualizar produtos.</p></div>
<div class="card"><h2>🔎 Busca manual</h2><input id="q" placeholder="Ex: celular, perfume, furadeira..."><button onclick="buscar()">Procurar</button></div>
<div class="card"><h2>📊 Resultado</h2><div id="stats" class="stats"></div></div>
<div class="card"><h2>🏆 Melhores oportunidades</h2><p class="small">A busca principal é rápida e usa somente a API do Mercado Livre. Os cupons ficam em um módulo separado para não deixar a atualização dos produtos lenta nem aplicar descontos que não foram confirmados.</p><div id="results"><p>Faça uma busca para começar.</p></div></div>
<div class="card"><a href="/afiliado/portal" target="_blank">💰 Central de Afiliados</a><br><br><a href="/afiliado/gerador" target="_blank">🔗 Gerador oficial de links</a><br><br><a href="/api/cupons?atualizar=1" target="_blank">🎟️ Atualizar/consultar cupons</a><br><br><a href="/mercadolivre/diagnostico" target="_blank">🧪 Diagnóstico Mercado Livre</a></div>
</div></body></html>
"""

@app.route("/")
def index():
    t=tokens()
    return render_template_string(HTML, conectado=bool(access_token()), nickname=t.get("nickname") if t else None, categorias=list(CATALOG.keys()))

@app.route("/buscar")
def buscar_page():
    q=request.args.get("q","").strip()
    if not q:
        return redirect("/")
    resultado=scan_queries([q], request.args.get("desconto",0))
    t=tokens()
    return render_template_string(HTML, conectado=bool(access_token()), nickname=t.get("nickname") if t else None, categorias=list(CATALOG.keys()), resultado=resultado)

@app.route("/cupons")
def coupons_page():
    return jsonify({"cupons":json_safe(coupons()),"fontes":COUPON_SOURCE_URLS})

@app.route("/health")
def health():
    return jsonify({
        "status":"ok","app":"Cacador de Ofertas",
        "mercado_livre_conectado":bool(access_token()),
        "catalogo_categorias":len(CATALOG),
        "fluxo":"products/{product_id}/items",
        "cupons":"separado","cupom_por_produto":"separado","cupom_primeiro":"não aplicado na busca rápida","produto_minimo":MIN_PRODUCT_PRICE,"gerador_anuncio":"ativo",
        "produtos_alto_giro":"ativo","link_afiliado":"gerador_oficial"
    })

@app.errorhandler(404)
def e404(e): return jsonify({"erro":"Rota não encontrada.","rota":request.path}),404

@app.errorhandler(500)
def e500(e): return jsonify({"erro":"Erro interno no servidor.","detalhes":str(e)}),500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","8080")), debug=False)
