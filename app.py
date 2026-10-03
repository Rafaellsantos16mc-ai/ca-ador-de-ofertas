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
from urllib.parse import urlencode

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

def product_items(pid):
    data, status, _ = ml_get(f"/products/{pid}/items")
    if status != 200:
        return []
    if isinstance(data, list):
        return data
    return data.get("results", []) if isinstance(data, dict) else []

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
    """Converte a página em texto com quebras úteis sem misturar blocos."""
    text = html_lib.unescape(raw_html or "")
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", text, flags=re.I | re.S)
    # Mantém uma quebra em elementos de bloco/lista/título.
    text = re.sub(r"</(?:div|p|li|h1|h2|h3|h4|h5|h6|section|article|br|tr)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
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

    return {
        "code": code,
        "description": block[:2500],
        "discount_percent": pct,
        "fixed_discount": fixed or 0,
        "min_purchase": mn,
        "max_discount": mx,
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
                min_purchase,max_discount,source_url,conditions,active,updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP)
            ON CONFLICT(code) DO UPDATE SET
                description=excluded.description,
                discount_percent=excluded.discount_percent,
                fixed_discount=excluded.fixed_discount,
                min_purchase=excluded.min_purchase,
                max_discount=excluded.max_discount,
                source_url=excluded.source_url,
                conditions=excluded.conditions,
                active=1,
                updated_at=CURRENT_TIMESTAMP
        """,(
            c["code"],c["description"],c["discount_percent"],c["fixed_discount"],
            c["min_purchase"],c["max_discount"],c["source_url"],c["conditions"]
        ))

    conn.commit()
    conn.close()

    print("[CUPONS] Cupons encontrados em todas as fontes:")
    for c in sorted(parsed, key=lambda x: (-(x.get("max_discount") or 0), -(x.get("discount_percent") or 0))):
        print(
            f"[CUPOM OK] {c['code']} | {c.get('discount_percent') or 0}% | "
            f"fixo R$ {c.get('fixed_discount') or 0:.2f} | "
            f"mín R$ {c.get('min_purchase') or 0:.2f} | "
            f"máx R$ {c.get('max_discount') or 0:.2f} | {c.get('source_url')}"
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
            x = dict(c)
            x["desconto_estimado"] = d
            # O desempate prioriza percentual efetivo e depois menor compra mínima.
            x["percentual_efetivo"] = round((d / float(price)) * 100, 2) if float(price) > 0 else 0
            choices.append(x)
    return max(
        choices,
        key=lambda x:(
            x["desconto_estimado"],
            x["percentual_efetivo"],
            -(float(x.get("min_purchase") or 0))
        ),
        default=None
    )


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

def public_coupon_product_cards():
    """Extrai associações produto -> cupom da página pública de cupons.

    O Mercado Livre pode alterar o HTML e nem sempre expõe MLB no HTML.
    Por isso o cruzamento usa título + preço e só confirma quando há
    correspondência suficientemente forte. Nunca aplica um cupom genérico
    a todos os produtos.
    """
    url = "https://www.mercadolivre.com.br/l/descontaco-cupons"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            print("[CUPOM PRODUTO] HTTP", r.status_code)
            return []
        text = normalize_coupon_html(r.text)
    except Exception as e:
        print("[CUPOM PRODUTO] ERRO", repr(e))
        return []

    # Formas públicas observadas: "Cupom R$ 15 OFF" e "Cupom 10% OFF".
    pat = re.compile(r"\bCupom\s+(?:R\$\s*[\d\.]+,[\d]{2}\s*OFF|\d+(?:[.,]\d+)?\s*%\s*OFF)\b", re.I)
    cards = []
    matches = list(pat.finditer(text))

    for m in matches:
        a = max(0, m.start() - 900)
        b = min(len(text), m.end() + 120)
        block = text[a:b]
        coupon = detect_public_coupon(m.group(0))
        if not coupon:
            continue

        # Preços imediatamente antes do desconto/cupom.
        money = []
        for mm in re.finditer(r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)", block, re.I):
            val = parse_public_money(mm.group(0))
            if val is not None:
                money.append((val, mm.start(), mm.end()))

        if not money:
            continue

        # Quando há "R$ 169,99 R$ 69,34 59% OFF ... Cupom R$ 15 OFF",
        # o preço atual é o imediatamente anterior ao percentual do vendedor.
        current = None
        sm = re.search(r"(R\$\s*[\d\.]+,[\d]{2}|R\$\s*\d+(?:[.,]\d+)?)\s+\d+(?:[.,]\d+)?\s*%\s*OFF", block, re.I)
        if sm:
            current = parse_public_money(sm.group(1))

        if current is None:
            before = block[:max(0, block.lower().rfind("cupom"))]
            vals = [parse_public_money(x.group(0)) for x in re.finditer(r"R\$\s*[\d\.]+,[\d]{2}|R\$\s*\d+(?:[.,]\d+)?", before, re.I)]
            vals = [x for x in vals if x is not None]
            if vals:
                current = vals[-1]

        if current is None or current < MIN_PRODUCT_PRICE:
            continue

        original = None
        larger = [x[0] for x in money if x[0] > current]
        if larger:
            original = min(larger)

        # O título normalmente fica antes do primeiro preço do card.
        first = re.search(r"R\$\s*[\d\.]+,[\d]{2}|R\$\s*\d+(?:[.,]\d+)?", block, re.I)
        title = block[:first.start()] if first else block
        title = re.sub(r"\b(?:no Pix|em outros meios|chegará|chega|cupom|off)\b.*$", "", title, flags=re.I)
        title = re.sub(r"\b(?:domingo|segunda-feira|terça-feira|quarta-feira|quinta-feira|sexta-feira|sábado)\b.*$", "", title, flags=re.I)
        title = re.sub(r"\s+", " ", title).strip(" -|•")

        if len(title) < 8:
            continue

        cards.append({
            "title": title,
            "price": round(current, 2),
            "original_price": round(original, 2) if original else None,
            "coupon": coupon,
            "source_url": url,
        })

    unique = {}
    for c in cards:
        key = (norm(c["title"]), c["price"], c["coupon"]["label"])
        unique[key] = c

    print("[CARDS DE CUPOM]", len(unique))
    for c in list(unique.values())[:15]:
        print("[CARD]", c["title"][:80], "|", brl(c["price"]), "|", c["coupon"]["label"])
    return list(unique.values())


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


def match_public_coupon(title, price, cards):
    best = None
    best_score = 0
    for card in cards:
        score = title_similarity(title, card["title"])
        diff = abs(float(price) - float(card["price"]))
        if diff <= 5:
            score += 0.25
        elif diff <= 15:
            score += 0.12
        if score > best_score:
            best_score = score
            best = card
    if best is None or best_score < 0.65:
        return None
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


def search_products_direct(q, limit=20):
    data, status, _ = ml_get("/products/search", {
        "site_id": SITE_ID,
        "q": q,
        "status": "active",
        "limit": limit,
        "offset": 0,
    })
    if status != 200 or not isinstance(data, dict):
        return []
    return data.get("results") or []


def scan_queries(queries, min_discount=0):
    # Nesta etapa o foco é SOMENTE descobrir produtos.
    # O cruzamento de cupons ficará para a próxima etapa, depois que
    # tivermos uma lista estável de produtos. Isso evita travar a busca.
    public_cards = []

    products = {}
    for q in queries:
        # Busca direta: não depende de aparecer nos highlights.
        for item in search_products_direct(q, 10):
            pid = item.get("id") or item.get("product_id")
            if pid:
                products.setdefault(pid, {"category_id":None, "category_name":None, "query":q})

        # Complementa com categorias/destaques.
        for cat in discover_categories(q)[:2]:
            for h in highlights(cat["category_id"]):
                pid = h.get("id") or h.get("product_id")
                if pid:
                    products.setdefault(pid, {
                        "category_id":cat["category_id"],
                        "category_name":cat["category_name"],
                        "query":q
                    })

    print("[PRODUTOS CANDIDATOS]", len(products))

    offers = []
    seen = set()
    for pid, base in products.items():
        p = product(pid)
        if not p:
            continue
        title = p.get("name") or p.get("title") or pid
        score = relevance(title, base["query"])
        # Busca direta pode retornar bons produtos mesmo com score baixo.
        if score < 5:
            continue
        pics = p.get("pictures") or []
        image = pics[0].get("url") if pics and isinstance(pics[0],dict) else None

        for raw in product_items(pid):
            item = normalize_item(raw)
            if not item or item["item_id"] in seen:
                continue
            seen.add(item["item_id"])
            try:
                price = float(item["price"])
            except Exception:
                continue
            if price < MIN_PRODUCT_PRICE:
                continue

            original = item.get("original_price")
            try:
                original = float(original) if original is not None else None
            except Exception:
                original = None

            seller_disc = discount(price, original)
            if seller_disc < float(min_discount or 0):
                continue

            shipping = item.get("shipping_cost")
            total_price = total(price, shipping) if shipping is not None else price

            # CUPONS NÃO SÃO CONSULTADOS NESTA ETAPA.
            # Primeiro entregamos os melhores produtos; depois cruzamos
            # cada produto com o cupom realmente aplicável.
            cup = None
            cup_disc = 0
            coupon_final = None

            cash_disc, cash_label = detect_cash_discount(raw, price)
            cash_final = round(max(0, total_price - cash_disc), 2) if cash_disc > 0 else None

            if cash_final is not None and (coupon_final is None or cash_final < coupon_final):
                best_final = cash_final
                best_mode = "pix"
                best_saving = cash_disc
            else:
                best_final = coupon_final
                best_mode = "cupom" if cup else None
                best_saving = cup_disc

            offers.append({
                "product_id":pid,"item_id":item["item_id"],"title":title,
                "modelo_nome":model_name(title),"especificacoes":specs(title),
                "image":image,"category_name":base.get("category_name") or "Produto",
                "permalink":item.get("permalink") or p.get("permalink") or f"https://www.mercadolivre.com.br/p/{pid}",
                "price":price,"original_price":original,"discount":seller_disc,
                "seller_id":item.get("seller_id"),"condition":item.get("condition"),
                "free_shipping":item.get("free_shipping"),"shipping_cost":shipping,
                "shipping_known":shipping is not None,"total_price":total_price,
                "relevance_score":score,"cupom":cup,
                "desconto_cupom":cup_disc,
                "percentual_cupom_efetivo":round((cup_disc/price)*100,2) if cup_disc else 0,
                "cash_discount":cash_disc,"cash_label":cash_label,"cash_final":cash_final,
                "melhor_forma":best_mode,"maior_desconto":best_saving,
                "preco_com_cupom":coupon_final,"preco_final_melhor":best_final,
                "affiliate_link":"","extra_earnings":0
            })

    # Produtos com cupom confirmado vêm primeiro; depois produtos sem cupom.
    offers.sort(key=lambda o:(
        0 if o.get("cupom") else 1,
        -(o.get("maior_desconto") or 0),
        -(o.get("percentual_cupom_efetivo") or 0),
        o.get("preco_final_melhor") if o.get("preco_final_melhor") is not None else o.get("price",999999),
        0 if o.get("free_shipping") else 1,
    ))

    groups = {}
    for o in offers:
        groups.setdefault(o["product_id"], {
            "product_id":o["product_id"],"title":o["title"],
            "modelo_nome":o["modelo_nome"],"especificacoes":o["especificacoes"],
            "image":o["image"],"category_name":o.get("category_name", ""),"ofertas":[]
        })["ofertas"].append(o)

    models = []
    for g in groups.values():
        g["ofertas"].sort(key=lambda x:(
            0 if x.get("cupom") else 1,
            -(x.get("maior_desconto") or 0),
            -(x.get("percentual_cupom_efetivo") or 0),
            x.get("preco_final_melhor") if x.get("preco_final_melhor") is not None else x.get("price",999999),
            0 if x.get("free_shipping") else 1
        ))
        if g["ofertas"]:
            g["ofertas"] = g["ofertas"][:1]
            g["ofertas"][0]["menor_preco_modelo"] = True
            models.append(g)

    models = sorted(models, key=lambda g:(
        0 if g["ofertas"][0].get("cupom") else 1,
        -(g["ofertas"][0].get("maior_desconto") or 0),
        g["ofertas"][0].get("preco_final_melhor") if g["ofertas"][0].get("preco_final_melhor") is not None else g["ofertas"][0].get("price",999999)
    ))[:30]

    flat = [o for g in models for o in g["ofertas"]]
    with_coupon = [o for o in flat if o.get("cupom")]
    valores = [o.get("price") for o in flat if o.get("price") is not None]
    finais = [o.get("preco_final_melhor") for o in with_coupon if o.get("preco_final_melhor") is not None]

    stats = {
        "ofertas": len(flat),
        "cupom aplicável": len(with_coupon),
        "produtos sem cupom": len(flat) - len(with_coupon),
        "maior desconto": brl(max([o.get("desconto_cupom",0) for o in with_coupon] or [0])),
        "menor preço final": brl(min(finais or [0])),
        "menor preço": brl(min(valores or [0])),
    }
    return {"stats":stats,"modelos":models,"ofertas":flat}

def auto_scan(category=None, min_discount=0):
    queries = CATALOG.get(category, []) if category else [q for qs in CATALOG.values() for q in qs]
    # Limita a 3 buscas por categoria por ciclo para não sobrecarregar a API.
    queries = queries[:8] if category else queries[:24]
    return scan_queries(queries, min_discount)

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
        update_job(job_id, status="running", progress=5, message="🔎 Procurando produtos de alto giro...")

        if category:
            queries = CATALOG.get(category, [])[:5]
        else:
            # Um ciclo inicial enxuto. Novos ciclos podem atualizar novamente.
            queries = [q for qs in CATALOG.values() for q in qs][:18]

        update_job(job_id, progress=20, message=f"🛒 Consultando Mercado Livre ({len(queries)} buscas)...")
        result = scan_queries(queries)
        update_job(job_id, progress=90, message="📊 Organizando os melhores produtos...")

        update_job(
            job_id,
            status="done",
            progress=100,
            message=f"✅ Produtos atualizados: {result.get('stats', {}).get('ofertas', 0)} encontrados.",
            result=json_safe(result),
        )
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
    return jsonify(json_safe(scan_queries([q], request.args.get("desconto",0))))

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
 document.getElementById('results').innerHTML='<p>🔎 Procurando produtos de alto giro...</p>';
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
   <span class="tag">🔥 MODELO ${mi+1}</span><div class="title">${esc(m.modelo_nome)}</div>
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
 ${o.menor_preco_modelo?'<span class="tag" style="background:#00a650;color:white">🏆 MENOR PREÇO</span>':''}
 <div class="price">${brl(o.price)}</div>
 ${o.original_price?`<div class="old">De: ${brl(o.original_price)}</div>`:''}
 ${o.discount>0?`<div class="green">🔥 ${o.discount}% OFF</div>`:''}
 ${o.free_shipping?'<div class="green">🚚 Frete grátis</div>':''}
 ${o.shipping_known?`<div class="green">💰 Total: ${brl(o.total_price)}</div>`:''}
 ${cup?`<div class="coupon"><b>🎟️ CUPOM: ${esc(cup.code)}</b>
 ${cup.discount_percent?`<div>🔥 Até ${cup.discount_percent}% OFF</div>`:''}
 ${cup.fixed_discount?`<div>💰 ${brl(cup.fixed_discount)} OFF</div>`:''}
 ${cup.min_purchase?`<div class="small">Compra mínima: ${brl(cup.min_purchase)}</div>`:''}
 ${cup.max_discount?`<div class="small">Desconto máximo: ${brl(cup.max_discount)}</div>`:''}
 <div>💵 Desconto estimado: <b>${brl(o.desconto_cupom)}</b> (${Number(o.percentual_cupom_efetivo||0).toFixed(2)}%)</div>
 <div class="final">💥 Estimado com cupom: ${brl(o.preco_com_cupom)}</div>
 <div class="small">⚠️ Estimativa. Confirme no checkout.</div></div>`:''}
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
<div class="card"><h2>🔥 Encontrar melhores produtos</h2><button onclick="cacar('')">🚀 ATUALIZAR PRODUTOS</button><div class="grid" style="margin-top:10px">{% for c in categorias %}<button class="cat" onclick="cacar({{c|tojson}})">{{c}}</button>{% endfor %}</div><p id="status" class="small">Escolha uma categoria ou toque em atualizar produtos.</p></div>
<div class="card"><h2>🔎 Busca manual</h2><input id="q" placeholder="Ex: celular, perfume, furadeira..."><button onclick="buscar()">Procurar</button></div>
<div class="card"><h2>📊 Resultado</h2><div id="stats" class="stats"></div></div>
<div class="card"><h2>🏆 Melhores oportunidades</h2><p class="small">Prioridade: maior desconto real do cupom → maior economia percentual → menor preço final. Valores com cupom são estimativas e precisam ser confirmados no checkout.</p><div id="results"><p>Faça uma busca para começar.</p></div></div>
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
        "cupons":"ativo","cupom_primeiro":"ativo","produto_minimo":MIN_PRODUCT_PRICE,"gerador_anuncio":"ativo",
        "produtos_alto_giro":"ativo","link_afiliado":"gerador_oficial"
    })

@app.errorhandler(404)
def e404(e): return jsonify({"erro":"Rota não encontrada.","rota":request.path}),404

@app.errorhandler(500)
def e500(e): return jsonify({"erro":"Erro interno no servidor.","detalhes":str(e)}),500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","8080")), debug=False)
