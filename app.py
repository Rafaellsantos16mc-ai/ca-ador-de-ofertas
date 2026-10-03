import os
import sqlite3
import secrets
import hashlib
import base64
import time
import re
import html as html_lib
from urllib.parse import urlencode

import requests
from flask import Flask, request, redirect, session, jsonify, render_template_string


# ============================================================
# APP
# ============================================================

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


# ============================================================
# CATÁLOGO AUTOMÁTICO
# ============================================================

CATALOG = {

    "📱 Celulares": [
        "smartphone",
        "iphone",
        "samsung galaxy",
        "motorola moto",
        "xiaomi redmi",
        "poco smartphone",
        "realme smartphone"
    ],

    "🌸 Perfumes": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "perfume nacional",
        "perfume eau de parfum"
    ],

    # SOMENTE ROUPAS, TÊNIS E SUPLEMENTOS
    "🏋️ Academia": [
        "roupa academia masculina",
        "roupa academia feminina",
        "camiseta academia",
        "short academia",
        "legging academia",
        "tenis academia",
        "tenis corrida",
        "tenis treino",
        "whey protein",
        "creatina",
        "pre treino",
        "suplementos"
    ],

    "🔧 Ferramentas": [
        "furadeira",
        "parafusadeira",
        "esmerilhadeira",
        "kit ferramentas",
        "maleta ferramentas",
        "serra",
        "chave de impacto"
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "headset",
        "smartwatch",
        "tablet",
        "caixa de som bluetooth",
        "camera digital",
        "power bank"
    ],

    "🏠 Casa": [
        "aspirador de pó",
        "liquidificador",
        "cafeteira",
        "air fryer",
        "ventilador",
        "ferro de passar"
    ],

    "🍳 Cozinha": [
        "air fryer",
        "panela elétrica",
        "jogo de panelas",
        "cafeteira",
        "liquidificador",
        "sandwichera"
    ],

    "🚗 Automotivo": [
        "compressor automotivo",
        "aspirador automotivo",
        "suporte celular carro",
        "carregador automotivo",
        "ferramentas automotivas",
        "tapete automotivo"
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "mochila",
        "relogio masculino",
        "bolsa feminina",
        "oculos de sol"
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
            product_id TEXT,
            item_id TEXT,
            title TEXT,
            permalink TEXT,
            price REAL,
            original_price REAL,
            discount REAL,
            seller_id TEXT,
            image TEXT,
            category_id TEXT,
            category_name TEXT,
            condition TEXT,
            listing_type_id TEXT,
            free_shipping INTEGER DEFAULT 0,
            shipping_cost REAL,
            total_price REAL,
            relevance_score REAL DEFAULT 0,
            affiliate_link TEXT,
            extra_earnings REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            description TEXT,
            discount_percent REAL,
            min_purchase REAL,
            max_discount REAL,
            valid_until TEXT,
            source_url TEXT,
            conditions TEXT,
            active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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
        return {
            str(k): json_safe(x)
            for k, x in v.items()
        }

    if isinstance(v, list):
        return [
            json_safe(x)
            for x in v
        ]

    return str(v)


def brl(v):

    try:
        return (
            f"R$ {float(v):,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except Exception:
        return "R$ 0,00"


def norm(s):

    if not s:
        return ""

    s = str(s).lower()

    trans = str.maketrans(
        "áàãâäéèêëíìîïóòõôöúùûüç",
        "aaaaaeeeeiiiiooooouuuuc"
    )

    s = s.translate(trans)

    return re.sub(
        r"\s+",
        " ",
        re.sub(
            r"[^a-z0-9\s]+",
            " ",
            s
        )
    ).strip()


def discount(price, original):

    try:

        p = float(price)
        o = float(original)

        if o > p > 0:
            return round((1 - p / o) * 100, 2)

    except Exception:
        pass

    return 0


def total(price, shipping):

    try:
        return round(
            float(price) + float(shipping or 0),
            2
        )

    except Exception:
        return None


def model_name(title):

    if not title:
        return "Produto"

    t = re.sub(
        r"\b(novo|original|oficial|promoção|frete grátis)\b",
        "",
        str(title),
        flags=re.I
    )

    return re.sub(
        r"\s+",
        " ",
        t
    ).strip()


def specs(title):

    if not title:
        return []

    t = str(title)
    out = []

    for x in re.findall(
        r"\b\d+(?:GB|TB)\b",
        t,
        re.I
    ):

        x = x.upper()

        if x not in out:
            out.append(x)

    for x in re.findall(
        r"\b\d+\s*GB\s*(?:RAM|MEMORIA|DE MEMORIA)\b",
        t,
        re.I
    ):

        x = re.sub(
            r"\s+",
            " ",
            x.upper()
        )

        if x not in out:
            out.append(x)

    for x in re.findall(
        r"\b(?:2G|3G|4G|5G)\b",
        t,
        re.I
    ):

        x = x.upper()

        if x not in out:
            out.append(x)

    for label, pattern in [
        ("Dual SIM", r"dual\s*sim"),
        ("NFC", r"\bnfc\b")
    ]:

        if re.search(
            pattern,
            t,
            re.I
        ) and label not in out:

            out.append(label)

    return out


# ============================================================
# PERFIS / RELEVÂNCIA
# ============================================================

PROFILES = {

    "celular": {

        "strong": [
            "smartphone",
            "iphone",
            "galaxy",
            "samsung",
            "motorola",
            "xiaomi",
            "redmi",
            "poco",
            "realme"
        ],

        "bad": [
            "capa",
            "capinha",
            "pelicula",
            "suporte",
            "ventosa",
            "carregador",
            "cabo",
            "adaptador",
            "bateria",
            "case",
            "holder"
        ]
    },


    "perfume": {

        "strong": [
            "perfume",
            "eau de parfum",
            "eau de toilette",
            "parfum"
        ],

        "bad": [
            "frasco vazio",
            "decant",
            "amostra",
            "porta perfume",
            "refil vazio"
        ]
    },


    # ACADEMIA:
    # SOMENTE ROUPAS, TÊNIS E SUPLEMENTOS
    "academia": {

        "strong": [
            "roupa",
            "camiseta",
            "short",
            "legging",
            "tenis",
            "corrida",
            "treino",
            "whey",
            "creatina",
            "pre treino",
            "suplemento",
            "suplementos"
        ],

        "bad": [
            "halter",
            "halteres",
            "anilha",
            "barra",
            "banco musculacao",
            "caneleira",
            "elastico",
            "adesivo",
            "capa",
            "suporte",
            "peca de reposicao"
        ]
    },


    "ferramenta": {

        "strong": [
            "furadeira",
            "parafusadeira",
            "esmerilhadeira",
            "ferramenta",
            "serra",
            "impacto"
        ],

        "bad": [
            "broca avulsa",
            "peca",
            "carvao",
            "bateria avulsa",
            "capa"
        ]
    }
}


def profile_for(q):

    t = norm(q)

    if any(
        x in t
        for x in [
            "iphone",
            "samsung",
            "galaxy",
            "motorola",
            "xiaomi",
            "redmi",
            "poco",
            "smartphone",
            "celular"
        ]
    ):
        return "celular"

    if "perfume" in t:
        return "perfume"

    if any(
        x in t
        for x in [
            "academia",
            "roupa academia",
            "camiseta academia",
            "short academia",
            "legging academia",
            "tenis academia",
            "tenis corrida",
            "tenis treino",
            "whey",
            "creatina",
            "pre treino",
            "suplemento"
        ]
    ):
        return "academia"

    if any(
        x in t
        for x in [
            "furadeira",
            "parafusadeira",
            "ferramenta",
            "esmerilhadeira",
            "serra"
        ]
    ):
        return "ferramenta"

    return None


def relevance(title, q):

    t = norm(title)
    b = norm(q)

    p = profile_for(q)

    score = sum(
        35
        for x in (
            PROFILES.get(
                p,
                {}
            ).get(
                "strong",
                []
            )
            if p else []
        )
        if x in t
    )

    score += sum(
        10
        for x in b.split()
        if len(x) >= 3 and x in t
    )

    score -= sum(
        90
        for x in PROFILES.get(
            p,
            {}
        ).get(
            "bad",
            []
        )
        if x in t
    )

    return score


# ============================================================
# OAUTH / PKCE
# ============================================================

def pkce():

    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(
        verifier.encode()
    ).digest()

    challenge = base64.urlsafe_b64encode(
        digest
    ).decode().rstrip("=")

    return verifier, challenge


def tokens():

    c = get_db()

    r = c.execute(
        "SELECT * FROM oauth_tokens WHERE id=1"
    ).fetchone()

    c.close()

    return dict(r) if r else None


def save_tokens(data, user=None):

    old = tokens() or {}

    c = get_db()

    c.execute("""
        INSERT INTO oauth_tokens(
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname
        )
        VALUES(
            1,
            ?,
            ?,
            ?,
            ?,
            ?
        )

        ON CONFLICT(id) DO UPDATE SET

            access_token=excluded.access_token,

            refresh_token=
                COALESCE(
                    excluded.refresh_token,
                    oauth_tokens.refresh_token
                ),

            expires_at=excluded.expires_at,

            user_id=
                COALESCE(
                    excluded.user_id,
                    oauth_tokens.user_id
                ),

            nickname=
                COALESCE(
                    excluded.nickname,
                    oauth_tokens.nickname
                )
    """, (

        data.get("access_token"),

        data.get("refresh_token"),

        int(
            time.time()
        )
        +
        int(
            data.get(
                "expires_in",
                21600
            )
        ),

        str(
            user.get("id")
        )
        if user and user.get("id")
        else old.get("user_id"),

        user.get("nickname")
        if user
        else old.get("nickname")
    ))

    c.commit()
    c.close()


def refresh():

    t = tokens()

    if not t or not t.get("refresh_token"):
        return None

    try:

        r = requests.post(
            ML_TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": t["refresh_token"]
            },
            timeout=30
        )

        if r.status_code != 200:
            return None

        save_tokens(
            r.json(),
            {
                "id": t.get("user_id"),
                "nickname": t.get("nickname")
            }
        )

        return r.json().get(
            "access_token"
        )

    except Exception:
        return None


def access_token():

    t = tokens()

    if not t:
        return None

    if (
        t.get("access_token")
        and time.time()
        <
        (t.get("expires_at") or 0) - 120
    ):
        return t["access_token"]

    return refresh() or t.get(
        "access_token"
    )


def ml_get(path, params=None):

    token = access_token()

    if not token:
        return {}, 401, {}

    url = (
        path
        if path.startswith("http")
        else ML_API + path
    )

    try:

        r = requests.get(
            url,
            headers={
                "Authorization":
                    f"Bearer {token}",
                "Accept":
                    "application/json"
            },
            params=params,
            timeout=30
        )

        try:
            data = r.json()

        except Exception:
            data = {
                "message": r.text
            }

        return (
            data,
            r.status_code,
            dict(r.headers)
        )

    except requests.RequestException as e:

        return {
            "error": str(e)
        }, 500, {}


# ============================================================
# LOGIN MERCADO LIVRE
# ============================================================

@app.route("/mercadolivre/login")
def ml_login():

    if not ML_CLIENT_ID:

        return jsonify({
            "erro":
                "ML_CLIENT_ID não configurado."
        }), 500

    verifier, challenge = pkce()

    state = secrets.token_urlsafe(32)

    session["ml_state"] = state
    session["ml_code_verifier"] = verifier

    params = {

        "response_type":
            "code",

        "client_id":
            ML_CLIENT_ID,

        "redirect_uri":
            ML_REDIRECT_URI,

        "state":
            state,

        "code_challenge":
            challenge,

        "code_challenge_method":
            "S256"
    }

    return redirect(
        ML_AUTH
        +
        "?"
        +
        urlencode(params)
    )


@app.route("/mercadolivre/callback")
def ml_callback():

    if request.args.get("error"):

        return jsonify({

            "erro":
                request.args.get("error"),

            "descricao":
                request.args.get(
                    "error_description"
                )

        }), 400

    code = request.args.get("code")
    state = request.args.get("state")

    if (
        not code
        or
        state != session.get("ml_state")
    ):

        return jsonify({
            "erro":
                "Código ou state inválido."
        }), 400

    try:

        r = requests.post(

            ML_TOKEN,

            data={

                "grant_type":
                    "authorization_code",

                "client_id":
                    ML_CLIENT_ID,

                "client_secret":
                    ML_CLIENT_SECRET,

                "code":
                    code,

                "redirect_uri":
                    ML_REDIRECT_URI,

                "code_verifier":
                    session.get(
                        "ml_code_verifier"
                    )
            },

            timeout=30
        )

        if r.status_code != 200:

            return jsonify({

                "erro":
                    "Falha ao obter token.",

                "status":
                    r.status_code,

                "resposta":
                    r.text

            }), r.status_code

        data = r.json()

        user = None

        if data.get("access_token"):

            me = requests.get(

                ML_API + "/users/me",

                headers={
                    "Authorization":
                        "Bearer "
                        +
                        data["access_token"]
                },

                timeout=30
            )

            if me.status_code == 200:
                user = me.json()

        save_tokens(
            data,
            user
        )

        session.pop(
            "ml_state",
            None
        )

        session.pop(
            "ml_code_verifier",
            None
        )

        return redirect(
            "/?conectado=1"
        )

    except Exception as e:

        return jsonify({
            "erro":
                str(e)
        }), 500


@app.route("/mercadolivre/logout")
def ml_logout():

    c = get_db()

    c.execute(
        "DELETE FROM oauth_tokens WHERE id=1"
    )

    c.commit()
    c.close()

    session.clear()

    return redirect("/")


# ============================================================
# MERCADO LIVRE - CATEGORIAS
# ============================================================

def discover_categories(q):

    data, status, _ = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        {
            "q": q
        }
    )

    if (
        status != 200
        or
        not isinstance(data, list)
    ):
        return []

    out = []

    for x in data:

        cid = (
            x.get("category_id")
            or
            x.get("id")
        )

        name = (
            x.get("category_name")
            or
            x.get("name")
            or
            cid
        )

        if cid:

            out.append({

                "category_id":
                    cid,

                "category_name":
                    name
            })

    return out


# ============================================================
# MAIS VENDIDOS / HIGHLIGHTS
# ============================================================

def highlights(category_id):

    data, status, _ = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    if status != 200:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        return data.get(
            "content",
            data.get(
                "results",
                []
            )
        )

    return []


# ============================================================
# PRODUTO
# ============================================================

def product(pid):

    data, status, _ = ml_get(
        f"/products/{pid}"
    )

    if (
        status == 200
        and
        isinstance(data, dict)
    ):
        return data

    return None


# ============================================================
# ITENS DO PRODUTO
# ============================================================

def product_items(pid):

    data, status, _ = ml_get(
        f"/products/{pid}/items"
    )

    if status != 200:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        return data.get(
            "results",
            []
        )

    return []


def normalize_item(x):

    if (
        not isinstance(x, dict)
        or
        not x.get("item_id")
    ):
        return None

    sh = x.get("shipping") or {}

    free = bool(
        sh.get(
            "free_shipping"
        )
    )

    cost = (
        0
        if free
        else
        sh.get("cost")
    )

    return {

        "item_id":
            x["item_id"],

        "seller_id":
            x.get("seller_id"),

        "price":
            x.get("price"),

        "original_price":
            x.get("original_price"),

        "condition":
            x.get("condition"),

        "listing_type_id":
            x.get("listing_type_id"),

        "free_shipping":
            free,

        "shipping_cost":
            cost,

        "permalink":
            x.get("permalink"),

        "user_product_id":
            x.get("user_product_id")
    }


# ============================================================
# CUPONS
# ============================================================

def number(s):

    if s is None:
        return None

    m = re.search(
        r"(\d+(?:[.,]\d+)?)",
        str(s)
    )

    if not m:
        return None

    return float(
        m.group(1)
        .replace(",", ".")
    )


def sync_coupons():

    try:

        r = requests.get(
            COUPONS_URL,
            headers={
                "User-Agent":
                    "Mozilla/5.0"
            },
            timeout=30
        )

        if r.status_code != 200:

            return {
                "ok": False,
                "status": r.status_code
            }

        text = html_lib.unescape(
            r.text
        )

        text = re.sub(
            r"<script.*?</script>|<style.*?</style>|<[^>]+>",
            " ",
            text,
            flags=re.I | re.S
        )

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        codes = list(
            dict.fromkeys(
                x.upper()
                for x in re.findall(
                    r"(?:Cupom\s+)([A-Z0-9]{5,20})",
                    text,
                    re.I
                )
            )
        )

        c = get_db()

        found = 0

        for code in codes:

            pos = text.lower().find(
                code.lower()
            )

            trecho = text[
                max(0, pos - 100):
                min(len(text), pos + 900)
            ]

            pct_match = re.search(
                r"até\s+(\d+(?:[.,]\d+)?)\s*%",
                trecho,
                re.I
            )

            pct = (
                number(
                    pct_match.group(1)
                )
                if pct_match
                else None
            )

            min_match = re.search(
                r"(?:a partir de|partir de)\s*R?\$?\s*(\d+(?:[.,]\d+)?)",
                trecho,
                re.I
            )

            mn = (
                number(
                    min_match.group(1)
                )
                if min_match
                else None
            )

            max_match = re.search(
                r"(?:máximo de|maximo de)\s*R?\$?\s*(\d+(?:[.,]\d+)?)",
                trecho,
                re.I
            )

            mx = (
                number(
                    max_match.group(1)
                )
                if max_match
                else None
            )

            if pct is None and mx is None:
                continue

            c.execute("""

                INSERT INTO cupons(
                    code,
                    description,
                    discount_percent,
                    min_purchase,
                    max_discount,
                    source_url,
                    conditions,
                    active,
                    updated_at
                )

                VALUES(
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    1,
                    CURRENT_TIMESTAMP
                )

                ON CONFLICT(code)
                DO UPDATE SET

                    description =
                        excluded.description,

                    discount_percent =
                        excluded.discount_percent,

                    min_purchase =
                        excluded.min_purchase,

                    max_discount =
                        excluded.max_discount,

                    conditions =
                        excluded.conditions,

                    active =
                        1,

                    updated_at =
                        CURRENT_TIMESTAMP

            """, (
                code,
                trecho,
                pct,
                mn,
                mx,
                COUPONS_URL,
                trecho
            ))

            found += 1

        c.commit()
        c.close()

        return {
            "ok": True,
            "cupons_encontrados":
                found
        }

    except Exception as e:

        return {
            "ok": False,
            "erro": str(e)
        }


def coupons():

    c = get_db()

    rows = c.execute("""

        SELECT *
        FROM cupons
        WHERE active=1
        ORDER BY
            discount_percent DESC,
            max_discount DESC

    """).fetchall()

    c.close()

    return [
        dict(x)
        for x in rows
    ]


def coupon_discount(cupom, price):

    try:

        price = float(price)

        if (
            cupom.get("min_purchase")
            and
            price <
            float(
                cupom["min_purchase"]
            )
        ):
            return 0

        d = (
            price
            *
            float(
                cupom.get(
                    "discount_percent"
                )
                or 0
            )
            /
            100
        )

        if cupom.get("max_discount"):

            d = min(
                d,
                float(
                    cupom["max_discount"]
                )
            )

        return round(
            max(0, d),
            2
        )

    except Exception:
        return 0


def best_coupon(price):

    choices = []

    for c in coupons():

        d = coupon_discount(
            c,
            price
        )

        if d > 0:

            x = dict(c)

            x["desconto_estimado"] = d

            choices.append(x)

    return max(
        choices,
        key=lambda x:
            x["desconto_estimado"],
        default=None
    )


# ============================================================
# CAÇADOR
# ============================================================

def scan_queries(
    queries,
    min_discount=0
):

    products = {}

    # --------------------------------------------------------
    # DESCOBRE CATEGORIAS
    # --------------------------------------------------------

    for q in queries:

        for cat in discover_categories(q)[:4]:

            for h in highlights(
                cat["category_id"]
            ):

                if h.get("type") != "PRODUCT":
                    continue

                pid = (
                    h.get("id")
                    or
                    h.get("product_id")
                )

                if pid:

                    products.setdefault(
                        pid,
                        {
                            "category_id":
                                cat["category_id"],

                            "category_name":
                                cat["category_name"],

                            "query":
                                q
                        }
                    )

    offers = []

    seen = set()

    # --------------------------------------------------------
    # BUSCA PRODUTOS
    # --------------------------------------------------------

    for pid, base in products.items():

        p = product(pid)

        if not p:
            continue

        title = (
            p.get("name")
            or
            p.get("title")
            or
            pid
        )

        score = relevance(
            title,
            base["query"]
        )

        if score < 15:
            continue

        pics = p.get(
            "pictures"
        ) or []

        image = (
            pics[0].get("url")
            if pics
            and isinstance(
                pics[0],
                dict
            )
            else None
        )

        for raw in product_items(pid):

            item = normalize_item(raw)

            if not item:
                continue

            if item["item_id"] in seen:
                continue

            seen.add(
                item["item_id"]
            )

            try:

                price = float(
                    item["price"]
                )

            except Exception:
                continue

            original = item.get(
                "original_price"
            )

            try:

                original = (
                    float(original)
                    if original is not None
                    else None
                )

            except Exception:

                original = None

            disc = discount(
                price,
                original
            )

            if disc < float(
                min_discount or 0
            ):
                continue

            shipping = item.get(
                "shipping_cost"
            )

            total_price = (
                total(
                    price,
                    shipping
                )
                if shipping is not None
                else price
            )

            cup = best_coupon(
                price
            )

            cup_disc = (
                cup["desconto_estimado"]
                if cup
                else 0
            )

            final_est = round(
                max(
                    0,
                    total_price - cup_disc
                ),
                2
            )

            offers.append({

                "product_id":
                    pid,

                "item_id":
                    item["item_id"],

                "title":
                    title,

                "modelo_nome":
                    model_name(title),

                "especificacoes":
                    specs(title),

                "image":
                    image,

                "permalink":
                    item.get("permalink")
                    or
                    p.get("permalink")
                    or
                    f"https://www.mercadolivre.com.br/p/{pid}",

                "price":
                    price,

                "original_price":
                    original,

                "discount":
                    disc,

                "seller_id":
                    item.get("seller_id"),

                "condition":
                    item.get("condition"),

                "free_shipping":
                    item.get(
                        "free_shipping"
                    ),

                "shipping_cost":
                    shipping,

                "shipping_known":
                    shipping is not None,

                "total_price":
                    total_price,

                "relevance_score":
                    score,

                "cupom":
                    cup,

                "preco_com_cupom":
                    final_est
                    if cup
                    else None,

                "affiliate_link":
                    "",

                "extra_earnings":
                    0,

                "category_id":
                    base.get(
                        "category_id"
                    ),

                "category_name":
                    base.get(
                        "category_name"
                    )
            })

    # --------------------------------------------------------
    # AGRUPA POR PRODUTO
    # --------------------------------------------------------

    groups = {}

    for o in offers:

        groups.setdefault(
            o["product_id"],
            {
                "product_id":
                    o["product_id"],

                "title":
                    o["title"],

                "modelo_nome":
                    o["modelo_nome"],

                "especificacoes":
                    o["especificacoes"],

                "image":
                    o["image"],

                "category_name":
                    o.get(
                        "category_name",
                        ""
                    ),

                "ofertas":
                    []
            }
        )["ofertas"].append(o)

    models = []

    for g in groups.values():

        g["ofertas"].sort(
            key=lambda x:
                x["preco_com_cupom"]
                if
                x["preco_com_cupom"]
                is not None
                else
                x["total_price"]
        )

        if g["ofertas"]:

            best = g["ofertas"][0]

            for o in g["ofertas"]:

                o["menor_preco_modelo"] = (
                    o is best
                )

        models.append(g)

    # --------------------------------------------------------
    # EVITA REPETIÇÃO DO MESMO PRODUTO
    # --------------------------------------------------------

    unique = []

    signatures = set()

    for g in sorted(

        models,

        key=lambda x: (

            -(
                x["ofertas"][0].get(
                    "discount"
                )
                or 0
            ),

            x["ofertas"][0].get(
                "preco_com_cupom"
            )
            or
            x["ofertas"][0].get(
                "total_price"
            )
            or
            999999
        )
    ):

        sig = norm(
            g["modelo_nome"]
        )

        if sig in signatures:
            continue

        signatures.add(sig)

        unique.append(g)

    models = unique[:30]

    flat = [
        o
        for g in models
        for o in g["ofertas"]
    ]

    sellers = {
        str(o["seller_id"])
        for o in flat
        if o.get("seller_id")
    }

    stats = {

        "produtos_catalogo":
            len(products),

        "modelos":
            len(models),

        "vendedores":
            len(sellers),

        "anuncios":
            len(flat),

        "com_desconto":
            sum(
                1
                for o in flat
                if o["discount"] > 0
            ),

        "com_cupom":
            sum(
                1
                for o in flat
                if o["cupom"]
            ),

        "frete_gratis":
            sum(
                1
                for o in flat
                if o["free_shipping"]
            )
    }

    return {

        "stats":
            stats,

        "modelos":
            models,

        "ofertas":
            flat
    }


def auto_scan(
    category=None,
    min_discount=0
):

    if category:

        queries = CATALOG.get(
            category,
            []
        )

        queries = queries[:3]

    else:

        queries = [
            q
            for qs in CATALOG.values()
            for q in qs
        ]

        queries = queries[:18]

    return scan_queries(
        queries,
        min_discount
    )


# ============================================================
# GERADOR DE ANÚNCIO
# ============================================================

def ad_text(
    o,
    affiliate=""
):

    lines = [

        "🔥 OFERTA ENCONTRADA!",

        "",

        f"🛍️ {o.get('title','Produto')}"
    ]

    if o.get(
        "original_price"
    ):

        lines.append(
            f"💸 De: {brl(o['original_price'])}"
        )

    lines.append(
        f"🔥 Por: {brl(o['price'])}"
    )

    if o.get(
        "discount",
        0
    ) > 0:

        lines.append(
            f"🏷️ {o['discount']}% OFF"
        )

    if o.get(
        "free_shipping"
    ):

        lines.append(
            "🚚 Frete grátis"
        )

    if o.get("cupom"):

        c = o["cupom"]

        lines += [

            "",

            f"🎟️ CUPOM: {c['code']}"
        ]

        if c.get(
            "discount_percent"
        ):

            lines.append(
                f"🔥 Até {c['discount_percent']}% OFF"
            )

        if c.get(
            "max_discount"
        ):

            lines.append(
                f"💰 Até {brl(c['max_discount'])} OFF"
            )

        if c.get(
            "min_purchase"
        ):

            lines.append(
                f"🛒 Compra mínima: {brl(c['min_purchase'])}"
            )

        lines += [

            "",

            f"💥 PREÇO ESTIMADO COM CUPOM: "
            f"{brl(o['preco_com_cupom'])}"
        ]

    lines += [

        "",

        "⚠️ Consulte as condições e confirme o cupom no checkout.",

        "",

        "🛒 PEGAR OFERTA:",

        affiliate
        or
        "Cole aqui seu link de afiliado."
    ]

    return "\n".join(lines)


# ============================================================
# API
# ============================================================

@app.route("/api/buscar")
def api_buscar():

    q = request.args.get(
        "q",
        ""
    ).strip()

    if not q:

        return jsonify({
            "erro":
                "Informe uma busca."
        }), 400

    sync_coupons()

    return jsonify(
        json_safe(
            scan_queries(
                [q],
                request.args.get(
                    "desconto",
                    0
                )
            )
        )
    )


@app.route("/api/cacar")
def api_cacar():

    sync_coupons()

    categoria = request.args.get(
        "categoria",
        ""
    ).strip() or None

    return jsonify(
        json_safe(
            auto_scan(
                categoria,
                request.args.get(
                    "desconto",
                    0
                )
            )
        )
    )


@app.route("/api/cupons")
def api_cupons():

    if request.args.get(
        "atualizar"
    ) == "1":

        sync_coupons()

    return jsonify({

        "cupons":
            json_safe(
                coupons()
            ),

        "fonte":
            COUPONS_URL
    })


@app.route("/api/gerar-anuncio")
def api_anuncio():

    o = {

        "title":
            request.args.get(
                "title",
                "Produto"
            ),

        "price":
            request.args.get(
                "price",
                0
            ),

        "original_price":
            request.args.get(
                "original_price"
            ),

        "discount":
            float(
                request.args.get(
                    "discount",
                    0
                )
                or 0
            ),

        "free_shipping":
            request.args.get(
                "shipping_free"
            ) == "1",

        "cupom":
            None,

        "preco_com_cupom":
            None
    }

    code = request.args.get(
        "cupom",
        ""
    ).strip().upper()

    if code:

        c = get_db()

        row = c.execute(
            """
            SELECT *
            FROM cupons
            WHERE code=?
            AND active=1
            """,
            (code,)
        ).fetchone()

        c.close()

        if row:

            cup = dict(row)

            cup["desconto_estimado"] = (
                coupon_discount(
                    cup,
                    float(
                        o["price"]
                    )
                )
            )

            o["cupom"] = cup

            o["preco_com_cupom"] = max(
                0,
                float(o["price"])
                -
                cup["desconto_estimado"]
            )

    return jsonify({

        "anuncio":
            ad_text(
                o,
                request.args.get(
                    "affiliate_link",
                    ""
                ).strip()
            )
    })


# ============================================================
# TESTES / DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/teste-produto-itens"
)
def teste_items():

    pid = request.args.get(
        "product_id",
        "MLB58793248"
    )

    data, status, _ = ml_get(
        f"/products/{pid}/items"
    )

    return jsonify({

        "product_id":
            pid,

        "status_http":
            status,

        "resposta":
            data

    }), status


@app.route(
    "/mercadolivre/teste-produto"
)
def teste_product():

    pid = request.args.get(
        "product_id",
        "MLB58793248"
    )

    data, status, _ = ml_get(
        f"/products/{pid}"
    )

    return jsonify({

        "product_id":
            pid,

        "status_http":
            status,

        "resposta":
            data

    }), status


@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    t = tokens()

    result = {

        "configurado":
            bool(
                ML_CLIENT_ID
            ),

        "conectado":
            bool(
                access_token()
            )
    }

    if access_token():

        me, status, _ = ml_get(
            "/users/me"
        )

        result["users_me"] = {

            "status_http":
                status,

            "resposta":
                me
        }

    if t:

        result["token_local"] = {

            "user_id":
                t.get("user_id"),

            "nickname":
                t.get("nickname"),

            "expires_at":
                t.get("expires_at")
        }

    return jsonify(result)


# ============================================================
# INTERFACE
# ============================================================

HTML = r"""
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
    Caçador de Ofertas
</title>

<style>

*{
    box-sizing:border-box
}

body{
    margin:0;
    background:#f4f5f7;
    font-family:Arial;
    color:#222
}

.container{
    max-width:1050px;
    margin:auto;
    padding:18px
}

.card{
    background:#fff;
    border-radius:16px;
    padding:18px;
    margin-bottom:18px;
    box-shadow:0 5px 20px #0000000c
}

button,
input,
select{
    width:100%;
    padding:13px;
    border-radius:10px;
    border:1px solid #ddd;
    font-size:15px
}

button{
    border:0;
    background:#3483fa;
    color:#fff;
    cursor:pointer;
    margin-top:7px
}

.login{
    background:#ffe600;
    color:#222
}

.grid{
    display:grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(150px,1fr)
        );
    gap:9px
}

.cat{
    background:#fff;
    border:1px solid #ddd;
    color:#222;
    text-align:left
}

.stats{
    display:grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(120px,1fr)
        );
    gap:9px
}

.stat{
    background:#f3f4f6;
    padding:13px;
    border-radius:11px
}

.stat b{
    display:block;
    font-size:23px;
    margin-top:4px
}

.modelo{
    border:2px solid #eee;
    border-radius:15px;
    padding:14px;
    margin:13px 0
}

.mh{
    display:flex;
    gap:12px;
    align-items:center
}

.mh img{
    width:85px;
    height:85px;
    object-fit:contain;
    background:#fafafa;
    border-radius:10px
}

.title{
    font-size:18px;
    font-weight:bold
}

.tag{
    display:inline-block;
    background:#eef4ff;
    color:#3483fa;
    border-radius:7px;
    padding:5px 8px;
    font-size:11px;
    margin:3px
}

.seller{
    background:#fafafa;
    border:1px solid #eee;
    border-radius:12px;
    padding:12px;
    margin-top:10px
}

.price{
    font-size:22px;
    font-weight:bold
}

.green{
    color:#00a650;
    font-weight:bold
}

.old{
    text-decoration:line-through;
    color:#777
}

.coupon{
    background:#fff8d6;
    border:1px dashed #d7ad00;
    border-radius:10px;
    padding:10px;
    margin-top:9px
}

.final{
    background:#eaf8ef;
    color:#008a3e;
    font-weight:bold;
    padding:9px;
    border-radius:8px;
    margin-top:7px
}

.ad{
    display:none;
    white-space:pre-wrap;
    background:#f7f7f7;
    padding:10px;
    border-radius:9px;
    margin-top:8px;
    font-size:13px
}

.small{
    font-size:12px;
    color:#666
}

.status{
    background:#eef8f0;
    padding:10px;
    border-radius:9px
}

</style>


<script>

async function cacar(cat){

    document.getElementById(
        'status'
    ).textContent =
        '🔄 Caçando promoções...';

    const url =
        '/api/cacar'
        +
        (
            cat
            ?
            '?categoria='
            +
            encodeURIComponent(cat)
            :
            ''
        );

    const r =
        await fetch(url);

    const data =
        await r.json();

    render(data);

    document.getElementById(
        'status'
    ).textContent =
        '✅ Busca atualizada agora.';
}


async function buscar(){

    const q =
        document.getElementById(
            'q'
        ).value.trim();

    if(!q)
        return;

    document.getElementById(
        'status'
    ).textContent =
        '🔄 Procurando...';

    const r =
        await fetch(
            '/api/buscar?q='
            +
            encodeURIComponent(q)
        );

    const data =
        await r.json();

    render(data);

    document.getElementById(
        'status'
    ).textContent =
        '✅ Busca atualizada agora.';
}


function render(data){

    document.getElementById(
        'stats'
    ).innerHTML =

        Object.entries(
            data.stats || {}
        )
        .map(
            ([k,v]) =>
                `<div class="stat">
                    ${k}
                    <b>${v}</b>
                </div>`
        )
        .join('');


    document.getElementById(
        'results'
    ).innerHTML =

        (data.modelos || [])
        .map(
            (m,mi) => `

            <div class="modelo">

                <div class="mh">

                    ${
                        m.image
                        ?
                        `<img src="${m.image}">`
                        :
                        ''
                    }

                    <div>

                        <span class="tag">
                            🔥 MODELO ${mi+1}
                        </span>

                        <div class="title">
                            ${esc(m.modelo_nome)}
                        </div>

                        ${
                            (m.especificacoes || [])
                            .map(
                                s =>
                                `<span class="tag">
                                    ${esc(s)}
                                </span>`
                            )
                            .join('')
                        }

                        <div class="small">
                            ${m.ofertas.length}
                            vendedor(es)
                        </div>

                    </div>

                </div>

                ${
                    m.ofertas
                    .map(
                        (o,oi) =>
                            seller(o,mi,oi)
                    )
                    .join('')
                }

            </div>
        `
        )
        .join('')

        ||

        '<p>Nenhuma oportunidade encontrada.</p>';
}


function seller(o,mi,oi){

    const id =
        'a'
        +
        mi
        +
        '_'
        +
        oi;

    const cup =
        o.cupom;

    return `

    <div class="seller">

        ${
            o.menor_preco_modelo
            ?
            `
            <span
                class="tag"
                style="
                    background:#00a650;
                    color:white
                "
            >
                🏆 MENOR PREÇO
            </span>
            `
            :
            ''
        }

        <div class="price">
            ${brl(o.price)}
        </div>

        ${
            o.original_price
            ?
            `
            <div class="old">
                De: ${brl(o.original_price)}
            </div>
            `
            :
            ''
        }

        ${
            o.discount > 0
            ?
            `
            <div class="green">
                🔥 ${o.discount}% OFF
            </div>
            `
            :
            ''
        }

        ${
            o.free_shipping
            ?
            `
            <div class="green">
                🚚 Frete grátis
            </div>
            `
            :
            ''
        }

        ${
            o.shipping_known
            ?
            `
            <div class="green">
                💰 Total:
                ${brl(o.total_price)}
            </div>
            `
            :
            ''
        }

        ${
            cup
            ?
            `
            <div class="coupon">

                <b>
                    🎟️ CUPOM:
                    ${esc(cup.code)}
                </b>

                ${
                    cup.discount_percent
                    ?
                    `
                    <div>
                        🔥 Até
                        ${cup.discount_percent}% OFF
                    </div>
                    `
                    :
                    ''
                }

                ${
                    cup.min_purchase
                    ?
                    `
                    <div class="small">
                        Compra mínima:
                        ${brl(cup.min_purchase)}
                    </div>
                    `
                    :
                    ''
                }

                ${
                    cup.max_discount
                    ?
                    `
                    <div class="small">
                        Desconto máximo:
                        ${brl(cup.max_discount)}
                    </div>
                    `
                    :
                    ''
                }

                <div class="final">
                    💥 Estimado com cupom:
                    ${brl(o.preco_com_cupom)}
                </div>

                <div class="small">
                    ⚠️ Estimativa.
                    Confirme no checkout.
                </div>

            </div>
            `
            :
            ''
        }

        <div class="small">
            👤 Vendedor:
            ${o.seller_id || 'N/A'}
        </div>

        <br>

        <a
            href="${o.permalink}"
            target="_blank"
        >
            🛒 Ver produto
        </a>

        <input
            id="link_${id}"
            placeholder="Cole seu link de afiliado"
        >

        <button
            onclick='anuncio(
                "${id}",
                ${JSON.stringify(o)}
            )'
        >
            📢 Gerar anúncio
        </button>

        <button
            id="copy_${id}"
            style="
                display:none;
                background:#ff8a00
            "
            onclick="copyAd('${id}')"
        >
            📋 Copiar anúncio
        </button>

        <div
            id="ad_${id}"
            class="ad"
        ></div>

    </div>

    `;
}


async function anuncio(id,o){

    const link =
        document.getElementById(
            'link_'+id
        ).value;

    const p =
        new URLSearchParams({

            title:
                o.title,

            price:
                o.price,

            discount:
                o.discount,

            shipping_free:
                o.free_shipping
                ?
                '1'
                :
                '0',

            cupom:
                o.cupom
                ?
                o.cupom.code
                :
                '',

            affiliate_link:
                link
        });


    if(o.original_price){

        p.set(
            'original_price',
            o.original_price
        );
    }


    const r =
        await fetch(
            '/api/gerar-anuncio?'
            +
            p
        );

    const d =
        await r.json();


    document.getElementById(
        'ad_'+id
    ).style.display =
        'block';

    document.getElementById(
        'ad_'+id
    ).textContent =
        d.anuncio;


    document.getElementById(
        'copy_'+id
    ).style.display =
        'block';
}


function copyAd(id){

    navigator.clipboard.writeText(
        document.getElementById(
            'ad_'+id
        ).textContent
    );

    alert(
        'Anúncio copiado!'
    );
}


function brl(v){

    return (
        'R$ '
        +
        Number(v || 0)
        .toLocaleString(
            'pt-BR',
            {
                minimumFractionDigits:2,
                maximumFractionDigits:2
            }
        )
    );
}


function esc(s){

    return String(
        s || ''
    ).replace(
        /[&<>"']/g,
        c =>
            ({
                '&':'&amp;',
                '<':'&lt;',
                '>':'&gt;',
                '"':'&quot;',
                "'":'&#039;'
            }[c])
    );
}

</script>

</head>


<body>

<div class="container">


<div class="card">

    <h1>
        🛒 Caçador de Ofertas
    </h1>

    <p>
        Caça automaticamente produtos selecionados,
        compara preços, descontos, cupons e frete.
    </p>


    {% if conectado %}

        <div class="status">

            🟢 Mercado Livre conectado

            {% if nickname %}
                <br>
                <b>{{nickname}}</b>
            {% endif %}

        </div>

        <a href="/mercadolivre/logout">

            <button>
                Desconectar
            </button>

        </a>

    {% else %}

        <a href="/mercadolivre/login">

            <button class="login">
                🔗 Conectar Mercado Livre
            </button>

        </a>

    {% endif %}

</div>


<div class="card">

    <h2>
        🔥 Caçar promoções
    </h2>


    <button
        onclick="cacar('')"
    >
        🚀 CAÇAR TODAS AS CATEGORIAS
    </button>


    <div
        class="grid"
        style="margin-top:10px"
    >

        {% for c in categorias %}

            <button
                class="cat"
                onclick="cacar({{c|tojson}})"
            >
                {{c}}
            </button>

        {% endfor %}

    </div>


    <p
        id="status"
        class="small"
    >
        Escolha uma categoria
        ou cace tudo.
    </p>

</div>


<div class="card">

    <h2>
        🔎 Busca manual
    </h2>

    <input
        id="q"
        placeholder="Ex: celular, perfume, furadeira..."
    >

    <button
        onclick="buscar()"
    >
        Procurar
    </button>

</div>


<div class="card">

    <h2>
        📊 Resultado
    </h2>

    <div
        id="stats"
        class="stats"
    ></div>

</div>


<div class="card">

    <h2>
        🏆 Melhores oportunidades
    </h2>

    <div id="results">

        <p>
            Faça uma busca para começar.
        </p>

    </div>

</div>


<div class="card">

    <a
        href="/api/cupons?atualizar=1"
        target="_blank"
    >
        🎟️ Atualizar/consultar cupons
    </a>

    <br>
    <br>

    <a
        href="/mercadolivre/diagnostico"
        target="_blank"
    >
        🧪 Diagnóstico Mercado Livre
    </a>

</div>


</div>

</body>

</html>
"""


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.route("/")
def index():

    t = tokens()

    return render_template_string(

        HTML,

        conectado=
            bool(
                access_token()
            ),

        nickname=
            t.get("nickname")
            if t
            else None,

        categorias=
            list(
                CATALOG.keys()
            )
    )


# ============================================================
# BUSCA MANUAL
# ============================================================

@app.route("/buscar")
def buscar_page():

    q = request.args.get(
        "q",
        ""
    ).strip()

    if not q:
        return redirect("/")

    sync_coupons()

    resultado = scan_queries(
        [q],
        request.args.get(
            "desconto",
            0
        )
    )

    t = tokens()

    return render_template_string(

        HTML,

        conectado=
            bool(
                access_token()
            ),

        nickname=
            t.get("nickname")
            if t
            else None,

        categorias=
            list(
                CATALOG.keys()
            ),

        resultado=
            resultado
    )


# ============================================================
# CUPONS
# ============================================================

@app.route("/cupons")
def coupons_page():

    sync_coupons()

    return jsonify({

        "cupons":
            json_safe(
                coupons()
            ),

        "fonte":
            COUPONS_URL
    })


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({

        "status":
            "ok",

        "app":
            "Cacador de Ofertas",

        "mercado_livre_conectado":
            bool(
                access_token()
            ),

        "catalogo_categorias":
            len(CATALOG),

        "fluxo":
            "products/{product_id}/items",

        "cupons":
            "ativo",

        "gerador_anuncio":
            "ativo",

        "caca_automatica":
            "ativo"
    })


# ============================================================
# ERROS
# ============================================================

@app.errorhandler(404)
def e404(e):

    return jsonify({

        "erro":
            "Rota não encontrada.",

        "rota":
            request.path

    }), 404


@app.errorhandler(500)
def e500(e):

    return jsonify({

        "erro":
            "Erro interno no servidor.",

        "detalhes":
            str(e)

    }), 500


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8080"
            )
        ),
        debug=False
    )