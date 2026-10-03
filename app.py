import os
import re
import time
import base64
import hashlib
import secrets
import sqlite3
import html as html_lib
from urllib.parse import urlencode

import requests
from flask import Flask, request, redirect, session, jsonify, render_template_string


# ============================================================
# APP
# ============================================================

app = Flask(__name__)
app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "chave-cacador-ofertas"
)

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

COUPONS_URL = (
    "https://www.mercadolivre.com.br/l/promocoes"
)

MIN_PRODUCT_PRICE = 69.90


# ============================================================
# CATÁLOGO
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
    ]
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
            fixed_discount REAL DEFAULT 0,
            min_purchase REAL,
            max_discount REAL,
            valid_until TEXT,
            source_url TEXT,
            conditions TEXT,
            active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        conn.execute(
            "ALTER TABLE cupons "
            "ADD COLUMN fixed_discount REAL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILIDADES
# ============================================================

def json_safe(value):
    if value is None:
        return None

    if isinstance(
        value,
        (str, int, float, bool)
    ):
        return value

    if isinstance(value, dict):
        return {
            str(k): json_safe(v)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [
            json_safe(v)
            for v in value
        ]

    return str(value)


def brl(value):
    try:
        return (
            f"R$ {float(value):,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except Exception:
        return "R$ 0,00"


def norm(value):
    if not value:
        return ""

    value = str(value).lower()

    trans = str.maketrans(
        "áàãâäéèêëíìîïóòõôöúùûüç",
        "aaaaaeeeeiiiiooooouuuuc"
    )

    value = value.translate(trans)

    value = re.sub(
        r"[^a-z0-9\s]+",
        " ",
        value
    )

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip()


def number(value):
    if value is None:
        return None

    match = re.search(
        r"(\d+(?:[.,]\d+)?)",
        str(value)
    )

    if not match:
        return None

    return float(
        match.group(1).replace(",", ".")
    )


def discount(price, original):
    try:
        price = float(price)
        original = float(original)

        if original > price > 0:
            return round(
                (1 - price / original) * 100,
                2
            )

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

    title = re.sub(
        r"\b(novo|original|oficial|promoção|frete grátis)\b",
        "",
        str(title),
        flags=re.I
    )

    return re.sub(
        r"\s+",
        " ",
        title
    ).strip()


def specs(title):
    if not title:
        return []

    result = []

    for value in re.findall(
        r"\b\d+(?:GB|TB)\b",
        str(title),
        re.I
    ):
        value = value.upper()

        if value not in result:
            result.append(value)

    for value in re.findall(
        r"\b(?:2G|3G|4G|5G)\b",
        str(title),
        re.I
    ):
        value = value.upper()

        if value not in result:
            result.append(value)

    if re.search(
        r"dual\s*sim",
        str(title),
        re.I
    ):
        result.append("Dual SIM")

    if re.search(
        r"\bnfc\b",
        str(title),
        re.I
    ):
        result.append("NFC")

    return result


# ============================================================
# RELEVÂNCIA
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
            "suplemento"
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
            "suporte"
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


def profile_for(query):
    query = norm(query)

    if any(
        x in query
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

    if "perfume" in query:
        return "perfume"

    if any(
        x in query
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
        x in query
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


def relevance(title, query):
    title = norm(title)
    query = norm(query)

    profile = profile_for(query)

    score = 0

    for word in PROFILES.get(
        profile,
        {}
    ).get("strong", []):
        if word in title:
            score += 35

    for word in query.split():
        if len(word) >= 3 and word in title:
            score += 10

    for word in PROFILES.get(
        profile,
        {}
    ).get("bad", []):
        if word in title:
            score -= 90

    return score


# ============================================================
# OAUTH
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
    conn = get_db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id=1"
    ).fetchone()

    conn.close()

    return dict(row) if row else None


def save_tokens(data, user=None):
    old = tokens() or {}

    conn = get_db()

    conn.execute("""
        INSERT INTO oauth_tokens(
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname
        )
        VALUES(1,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            access_token=excluded.access_token,
            refresh_token=COALESCE(
                excluded.refresh_token,
                oauth_tokens.refresh_token
            ),
            expires_at=excluded.expires_at,
            user_id=COALESCE(
                excluded.user_id,
                oauth_tokens.user_id
            ),
            nickname=COALESCE(
                excluded.nickname,
                oauth_tokens.nickname
            )
    """, (
        data.get("access_token"),
        data.get("refresh_token"),
        int(time.time())
        + int(
            data.get(
                "expires_in",
                21600
            )
        ),
        str(user.get("id"))
        if user and user.get("id")
        else old.get("user_id"),
        user.get("nickname")
        if user
        else old.get("nickname")
    ))

    conn.commit()
    conn.close()


def refresh():
    data = tokens()

    if not data:
        return None

    refresh_token = data.get(
        "refresh_token"
    )

    if not refresh_token:
        return None

    try:
        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type":
                    "refresh_token",

                "client_id":
                    ML_CLIENT_ID,

                "client_secret":
                    ML_CLIENT_SECRET,

                "refresh_token":
                    refresh_token
            },
            timeout=30
        )

        if response.status_code != 200:
            return None

        new_data = response.json()

        save_tokens(
            new_data,
            {
                "id":
                    data.get("user_id"),

                "nickname":
                    data.get("nickname")
            }
        )

        return new_data.get(
            "access_token"
        )

    except Exception:
        return None


def access_token():
    data = tokens()

    if not data:
        return None

    expires_at = data.get(
        "expires_at"
    ) or 0

    if (
        data.get("access_token")
        and time.time()
        < expires_at - 120
    ):
        return data["access_token"]

    return (
        refresh()
        or data.get("access_token")
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
        response = requests.get(
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
            data = response.json()
        except Exception:
            data = {
                "message":
                    response.text
            }

        return (
            data,
            response.status_code,
            dict(response.headers)
        )

    except requests.RequestException as error:
        return {
            "error": str(error)
        }, 500, {}


# ============================================================
# LOGIN
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
        + "?"
        + urlencode(params)
    )


@app.route("/mercadolivre/callback")
def ml_callback():
    if request.args.get("error"):
        return jsonify({
            "erro":
                request.args.get(
                    "error"
                ),

            "descricao":
                request.args.get(
                    "error_description"
                )
        }), 400

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    if (
        not code
        or state != session.get(
            "ml_state"
        )
    ):
        return jsonify({
            "erro":
                "Código ou state inválido."
        }), 400

    try:
        response = requests.post(
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

        if response.status_code != 200:
            return jsonify({
                "erro":
                    "Falha ao obter token.",

                "status":
                    response.status_code,

                "resposta":
                    response.text
            }), response.status_code

        data = response.json()
        user = None

        if data.get("access_token"):
            me = requests.get(
                ML_API + "/users/me",
                headers={
                    "Authorization":
                        "Bearer "
                        + data["access_token"]
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

    except Exception as error:
        return jsonify({
            "erro":
                str(error)
        }), 500


@app.route("/mercadolivre/logout")
def ml_logout():
    conn = get_db()

    conn.execute(
        "DELETE FROM oauth_tokens WHERE id=1"
    )

    conn.commit()
    conn.close()

    session.clear()

    return redirect("/")


# ============================================================
# MERCADO LIVRE - PRODUTOS
# ============================================================

def discover_categories(query):
    data, status, _ = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        {"q": query}
    )

    if (
        status != 200
        or not isinstance(data, list)
    ):
        return []

    result = []

    for item in data:
        category_id = (
            item.get("category_id")
            or item.get("id")
        )

        category_name = (
            item.get("category_name")
            or item.get("name")
            or category_id
        )

        if category_id:
            result.append({
                "category_id":
                    category_id,

                "category_name":
                    category_name
            })

    return result


def highlights(category_id):
    data, status, _ = ml_get(
        f"/highlights/"
        f"{SITE_ID}/category/"
        f"{category_id}"
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


def get_product(product_id):
    data, status, _ = ml_get(
        f"/products/{product_id}"
    )

    if (
        status == 200
        and isinstance(data, dict)
    ):
        return data

    return None


def product_items(product_id):
    data, status, _ = ml_get(
        f"/products/{product_id}/items"
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


def normalize_item(item):
    if (
        not isinstance(item, dict)
        or not item.get("item_id")
    ):
        return None

    shipping = (
        item.get("shipping")
        or {}
    )

    free_shipping = bool(
        shipping.get(
            "free_shipping"
        )
    )

    shipping_cost = (
        0
        if free_shipping
        else shipping.get("cost")
    )

    return {
        "item_id":
            item["item_id"],

        "seller_id":
            item.get("seller_id"),

        "price":
            item.get("price"),

        "original_price":
            item.get("original_price"),

        "condition":
            item.get("condition"),

        "listing_type_id":
            item.get("listing_type_id"),

        "free_shipping":
            free_shipping,

        "shipping_cost":
            shipping_cost,

        "permalink":
            item.get("permalink"),

        "user_product_id":
            item.get("user_product_id")
    }


# ============================================================
# CUPONS
# ============================================================

def normalize_coupon_text(raw):
    text = html_lib.unescape(
        raw or ""
    )

    text = re.sub(
        r"<script.*?</script>"
        r"|<style.*?</style>"
        r"|<noscript.*?</noscript>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"</(?:div|p|li|h1|h2|h3|h4|h5|h6|section|article|br|tr)>",
        "\n",
        text,
        flags=re.I
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = text.replace(
        "\r",
        "\n"
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n[ \t]+",
        "\n",
        text
    )

    return text.strip()


def valid_coupon_code(code):
    code = (
        code or ""
    ).strip().upper()

    if not re.fullmatch(
        r"[A-Z0-9][A-Z0-9_-]{5,29}",
        code
    ):
        return False

    if not re.search(
        r"[A-Z]",
        code
    ):
        return False

    if not re.search(
        r"\d",
        code
    ):
        return False

    return True


def coupon_blocks(text):
    """
    Cada cupom é separado do próximo.

    Isso evita o problema que estava acontecendo:
    1FRUIT pegando 15% / R$70 do S5PRUNK.
    """

    pattern = re.compile(
        r"\bCupom\s+"
        r"([A-Z0-9][A-Z0-9_-]{5,29})"
        r"\b"
        r"(?=\s+Cupom\s+v[aá]lido)",
        re.I
    )

    found = []

    for match in pattern.finditer(text):
        code = match.group(1).upper()

        if valid_coupon_code(code):
            found.append(
                (
                    match.start(),
                    match.end(),
                    code
                )
            )

    blocks = []

    for index, (
        start,
        end,
        code
    ) in enumerate(found):

        next_start = (
            found[index + 1][0]
            if index + 1 < len(found)
            else len(text)
        )

        block = text[
            end:next_start
        ].strip()

        for marker in [
            "Restrições de Uso",
            "Termos e Condições"
        ]:
            position = block.lower().find(
                marker.lower()
            )

            if position >= 0:
                block = block[
                    :position
                ].strip()

        blocks.append(
            (
                code,
                block
            )
        )

    return blocks


def parse_coupon(code, block):
    percent = None

    percent_patterns = [
        r"(?:até\s+)?"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%\s*"
        r"(?:off|de desconto)?",

        r"(?:desconto de|desconto)"
        r"\s+"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%"
    ]

    for pattern in percent_patterns:
        match = re.search(
            pattern,
            block,
            re.I
        )

        if match:
            percent = number(
                match.group(1)
            )
            break

    fixed = None

    fixed_patterns = [
        r"R\$\s*"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*(?:OFF|de desconto)",

        r"(?:desconto de|ganhe)"
        r"\s*R\$\s*"
        r"(\d+(?:[.,]\d+)?)"
    ]

    for pattern in fixed_patterns:
        match = re.search(
            pattern,
            block,
            re.I
        )

        if match:
            fixed = number(
                match.group(1)
            )
            break

    minimum = None

    minimum_patterns = [
        r"(?:a partir de|partir de|"
        r"compra mínima de|mínimo de|"
        r"valor mínimo de)"
        r"\s*R?\$?\s*"
        r"(\d+(?:[.,]\d+)?)",

        r"R\$\s*"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*(?:ou mais|em compras)"
    ]

    for pattern in minimum_patterns:
        match = re.search(
            pattern,
            block,
            re.I
        )

        if match:
            minimum = number(
                match.group(1)
            )
            break

    maximum = None

    maximum_patterns = [
        r"(?:máximo de|maximo de|"
        r"limitado a)"
        r"\s*R?\$?\s*"
        r"(\d+(?:[.,]\d+)?)",

        r"máximo.*?"
        r"R\$\s*"
        r"(\d+(?:[.,]\d+)?)"
    ]

    for pattern in maximum_patterns:
        match = re.search(
            pattern,
            block,
            re.I
        )

        if match:
            maximum = number(
                match.group(1)
            )
            break

    return {
        "code":
            code,

        "description":
            block[:2500],

        "discount_percent":
            percent,

        "fixed_discount":
            fixed or 0,

        "min_purchase":
            minimum,

        "max_discount":
            maximum,

        "source_url":
            COUPONS_URL,

        "conditions":
            block[:2500]
    }


def sync_coupons():
    try:
        response = requests.get(
            COUPONS_URL,
            headers={
                "User-Agent":
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "Chrome/140 Safari/537.36",

                "Accept-Language":
                    "pt-BR,pt;q=0.9"
            },
            timeout=30
        )

        if response.status_code != 200:
            return {
                "ok": False,
                "status":
                    response.status_code
            }

        text = normalize_coupon_text(
            response.text
        )

        blocks = coupon_blocks(
            text
        )

        parsed = []

        for code, block in blocks:
            coupon = parse_coupon(
                code,
                block
            )

            if (
                not coupon.get(
                    "discount_percent"
                )
                and not coupon.get(
                    "fixed_discount"
                )
            ):
                continue

            parsed.append(
                coupon
            )

        if not parsed:
            return {
                "ok": False,
                "erro":
                    "Nenhum cupom válido encontrado."
            }

        conn = get_db()

        # Desativa os antigos.
        conn.execute(
            "UPDATE cupons SET active=0"
        )

        for coupon in parsed:
            conn.execute("""
                INSERT INTO cupons(
                    code,
                    description,
                    discount_percent,
                    fixed_discount,
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
                    ?,
                    1,
                    CURRENT_TIMESTAMP
                )
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
            """, (
                coupon["code"],
                coupon["description"],
                coupon["discount_percent"],
                coupon["fixed_discount"],
                coupon["min_purchase"],
                coupon["max_discount"],
                coupon["source_url"],
                coupon["conditions"]
            ))

        conn.commit()
        conn.close()

        print("[CUPONS] Atualização concluída")

        for coupon in parsed:
            print(
                "[CUPOM OK]",
                coupon["code"],
                "|",
                coupon.get(
                    "discount_percent"
                ),
                "%",
                "| min:",
                coupon.get(
                    "min_purchase"
                ),
                "| max:",
                coupon.get(
                    "max_discount"
                )
            )

        return {
            "ok": True,
            "cupons_encontrados":
                len(parsed)
        }

    except Exception as error:
        return {
            "ok": False,
            "erro":
                str(error)
        }


def coupons():
    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM cupons
        WHERE active=1
        ORDER BY
            discount_percent DESC,
            max_discount DESC
    """).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


def coupon_discount(coupon, price):
    try:
        price = float(price)

        minimum = coupon.get(
            "min_purchase"
        )

        if (
            minimum
            and price < float(minimum)
        ):
            return 0

        discounts = []

        percent = float(
            coupon.get(
                "discount_percent"
            ) or 0
        )

        fixed = float(
            coupon.get(
                "fixed_discount"
            ) or 0
        )

        maximum = coupon.get(
            "max_discount"
        )

        if percent > 0:
            value = (
                price
                * percent
                / 100
            )

            if maximum:
                value = min(
                    value,
                    float(maximum)
                )

            discounts.append(
                value
            )

        if fixed > 0:
            value = fixed

            if maximum:
                value = min(
                    value,
                    float(maximum)
                )

            discounts.append(
                value
            )

        return round(
            max(
                discounts,
                default=0
            ),
            2
        )

    except Exception:
        return 0


def best_coupon(price):
    choices = []

    for coupon in coupons():
        saving = coupon_discount(
            coupon,
            price
        )

        if saving <= 0:
            continue

        item = dict(coupon)

        item[
            "desconto_estimado"
        ] = saving

        item[
            "percentual_efetivo"
        ] = round(
            saving
            / float(price)
            * 100,
            2
        )

        choices.append(
            item
        )

    return max(
        choices,
        key=lambda x: (
            x[
                "desconto_estimado"
            ],

            x[
                "percentual_efetivo"
            ]
        ),
        default=None
    )


# ============================================================
# CAÇA
# ============================================================

def scan_queries(
    queries,
    min_discount=0
):
    products = {}

    for query in queries:
        categories = discover_categories(
            query
        )[:4]

        for category in categories:
            items = highlights(
                category[
                    "category_id"
                ]
            )

            for item in items:
                if item.get(
                    "type"
                ) != "PRODUCT":
                    continue

                product_id = (
                    item.get("id")
                    or item.get(
                        "product_id"
                    )
                )

                if product_id:
                    products.setdefault(
                        product_id,
                        {
                            "category_id":
                                category[
                                    "category_id"
                                ],

                            "category_name":
                                category[
                                    "category_name"
                                ],

                            "query":
                                query
                        }
                    )

    offers = []
    seen_items = set()

    for product_id, base in products.items():

        product_data = get_product(
            product_id
        )

        if not product_data:
            continue

        title = (
            product_data.get("name")
            or product_data.get("title")
            or product_id
        )

        score = relevance(
            title,
            base["query"]
        )

        if score < 15:
            continue

        pictures = (
            product_data.get(
                "pictures"
            )
            or []
        )

        image = None

        if pictures:
            if isinstance(
                pictures[0],
                dict
            ):
                image = pictures[0].get(
                    "url"
                )

        for raw_item in product_items(
            product_id
        ):
            item = normalize_item(
                raw_item
            )

            if not item:
                continue

            if item[
                "item_id"
            ] in seen_items:
                continue

            seen_items.add(
                item["item_id"]
            )

            try:
                price = float(
                    item["price"]
                )
            except Exception:
                continue

            # Produto mínimo de R$69,90.
            if price < MIN_PRODUCT_PRICE:
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

            seller_discount = discount(
                price,
                original
            )

            if seller_discount < float(
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

            # =================================================
            # CUPOM PRINCIPAL
            # =================================================

            coupon = best_coupon(
                price
            )

            coupon_saving = (
                coupon[
                    "desconto_estimado"
                ]
                if coupon
                else 0
            )

            final_price = (
                round(
                    max(
                        0,
                        total_price
                        - coupon_saving
                    ),
                    2
                )
                if coupon
                else total_price
            )

            offers.append({
                "product_id":
                    product_id,

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

                "category_name":
                    base[
                        "category_name"
                    ],

                "permalink":
                    item.get(
                        "permalink"
                    )
                    or product_data.get(
                        "permalink"
                    )
                    or (
                        "https://www.mercadolivre.com.br/p/"
                        + product_id
                    ),

                "price":
                    price,

                "original_price":
                    original,

                # Esse é o desconto que já existe
                # no vendedor.
                "discount":
                    seller_discount,

                "seller_id":
                    item.get(
                        "seller_id"
                    ),

                "condition":
                    item.get(
                        "condition"
                    ),

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
                    coupon,

                "desconto_cupom":
                    coupon_saving,

                "percentual_cupom_efetivo":
                    (
                        round(
                            coupon_saving
                            / price
                            * 100,
                            2
                        )
                        if coupon_saving
                        else 0
                    ),

                "preco_com_cupom":
                    final_price
                    if coupon
                    else None,

                "preco_final_melhor":
                    final_price,

                "maior_desconto":
                    coupon_saving,

                "affiliate_link":
                    "",

                "extra_earnings":
                    0
            })

    # ============================================================
    # ORDEM
    # ============================================================

    offers.sort(
        key=lambda item: (
            -(
                item.get(
                    "maior_desconto"
                )
                or 0
            ),

            -(
                item.get(
                    "percentual_cupom_efetivo"
                )
                or 0
            ),

            item.get(
                "preco_final_melhor"
            )
            or 999999,

            0
            if item.get(
                "free_shipping"
            )
            else 1
        )
    )

    # ============================================================
    # AGRUPAMENTO
    # ============================================================

    groups = {}

    for offer in offers:
        product_id = offer[
            "product_id"
        ]

        if product_id not in groups:
            groups[product_id] = {
                "product_id":
                    product_id,

                "title":
                    offer["title"],

                "modelo_nome":
                    offer["modelo_nome"],

                "especificacoes":
                    offer[
                        "especificacoes"
                    ],

                "image":
                    offer["image"],

                "category_name":
                    offer[
                        "category_name"
                    ],

                "ofertas":
                    []
            }

        groups[
            product_id
        ][
            "ofertas"
        ].append(
            offer
        )

    models = []

    for group in groups.values():

        group[
            "ofertas"
        ].sort(
            key=lambda item: (
                -(
                    item.get(
                        "maior_desconto"
                    )
                    or 0
                ),

                item.get(
                    "preco_final_melhor"
                )
                or 999999
            )
        )

        if group["ofertas"]:
            best = group[
                "ofertas"
            ][0]

            for offer in group[
                "ofertas"
            ]:
                offer[
                    "menor_preco_modelo"
                ] = (
                    offer is best
                )

        models.append(
            group
        )

    # Evita repetir o mesmo modelo.
    unique = []
    signatures = set()

    for group in sorted(
        models,
        key=lambda x: (
            -(
                x["ofertas"][0].get(
                    "maior_desconto"
                )
                or 0
            ),

            x["ofertas"][0].get(
                "preco_final_melhor"
            )
            or 999999
        )
    ):
        signature = norm(
            group[
                "modelo_nome"
            ]
        )

        if signature in signatures:
            continue

        signatures.add(
            signature
        )

        unique.append(
            group
        )

    models = unique[:30]

    flat = [
        offer
        for group in models
        for offer in group[
            "ofertas"
        ]
    ]

    valores = [
        offer.get("price")
        for offer in flat
        if offer.get("price")
        is not None
    ]

    valores_com_desconto = [
        offer.get(
            "preco_final_melhor"
        )
        for offer in flat
        if offer.get(
            "preco_final_melhor"
        ) is not None
    ]

    # ============================================================
    # PAINEL — SOMENTE 4 INFORMAÇÕES
    # ============================================================

    stats = {
        "ofertas":
            len(flat),

        "cupom aplicável":
            sum(
                1
                for offer in flat
                if offer.get("cupom")
            ),

        "valor":
            brl(
                min(
                    valores,
                    default=0
                )
            ),

        "valor com o desconto":
            brl(
                min(
                    valores_com_desconto,
                    default=0
                )
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
        )[:3]
    else:
        queries = [
            query
            for values in CATALOG.values()
            for query in values
        ][:18]

    return scan_queries(
        queries,
        min_discount
    )


# ============================================================
# ANÚNCIO
# ============================================================

def ad_text(
    offer,
    affiliate=""
):
    lines = [
        "🔥 OFERTA ENCONTRADA!",
        "",
        "🛍️ "
        + offer.get(
            "title",
            "Produto"
        )
    ]

    if offer.get(
        "original_price"
    ):
        lines.append(
            "💸 De: "
            + brl(
                offer[
                    "original_price"
                ]
            )
        )

    lines.append(
        "🔥 Por: "
        + brl(
            offer[
                "price"
            ]
        )
    )

    if offer.get(
        "discount",
        0
    ) > 0:
        lines.append(
            "🏷️ "
            + str(
                offer[
                    "discount"
                ]
            )
            + "% OFF"
        )

    if offer.get(
        "free_shipping"
    ):
        lines.append(
            "🚚 Frete grátis"
        )

    coupon = offer.get(
        "cupom"
    )

    if coupon:
        lines += [
            "",
            "🎟️ CUPOM: "
            + coupon["code"]
        ]

        if coupon.get(
            "discount_percent"
        ):
            lines.append(
                "🔥 Até "
                + str(
                    coupon[
                        "discount_percent"
                    ]
                )
                + "% OFF"
            )

        if coupon.get(
            "max_discount"
        ):
            lines.append(
                "💰 Desconto máximo: "
                + brl(
                    coupon[
                        "max_discount"
                    ]
                )
            )

        lines.append(
            "💵 Desconto estimado: "
            + brl(
                offer.get(
                    "desconto_cupom",
                    0
                )
            )
        )

        lines += [
            "",
            "💥 PREÇO ESTIMADO COM CUPOM: "
            + brl(
                offer[
                    "preco_com_cupom"
                ]
            )
        ]

    lines += [
        "",
        "⚠️ Consulte as condições "
        "e confirme o cupom no checkout.",
        "",
        "🛒 PEGAR OFERTA:",
        affiliate
        or "Cole aqui seu link de afiliado."
    ]

    return "\n".join(lines)


# ============================================================
# API
# ============================================================

@app.route("/api/buscar")
def api_buscar():
    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:
        return jsonify({
            "erro":
                "Informe uma busca."
        }), 400

    sync_coupons()

    result = scan_queries(
        [query],
        request.args.get(
            "desconto",
            0
        )
    )

    return jsonify(
        json_safe(result)
    )


@app.route("/api/cacar")
def api_cacar():
    sync_coupons()

    category = request.args.get(
        "categoria",
        ""
    ).strip() or None

    result = auto_scan(
        category,
        request.args.get(
            "desconto",
            0
        )
    )

    return jsonify(
        json_safe(result)
    )


@app.route("/api/cupons")
def api_cupons():
    synchronization = None

    if request.args.get(
        "atualizar"
    ) == "1":
        synchronization = sync_coupons()

    return jsonify({
        "cupons":
            json_safe(
                coupons()
            ),

        "fonte":
            COUPONS_URL,

        "sincronizacao":
            json_safe(
                synchronization
            )
    })


@app.route("/api/gerar-anuncio")
def api_anuncio():
    offer = {
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
        conn = get_db()

        row = conn.execute(
            """
            SELECT *
            FROM cupons
            WHERE code=?
            AND active=1
            """,
            (code,)
        ).fetchone()

        conn.close()

        if row:
            coupon = dict(row)

            saving = coupon_discount(
                coupon,
                float(
                    offer["price"]
                )
            )

            coupon[
                "desconto_estimado"
            ] = saving

            offer[
                "cupom"
            ] = coupon

            offer[
                "preco_com_cupom"
            ] = max(
                0,
                float(
                    offer["price"]
                ) - saving
            )

    return jsonify({
        "anuncio":
            ad_text(
                offer,
                request.args.get(
                    "affiliate_link",
                    ""
                ).strip()
            )
    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/teste-produto-itens"
)
def teste_items():
    product_id = request.args.get(
        "product_id",
        "MLB58793248"
    )

    data, status, _ = ml_get(
        f"/products/"
        f"{product_id}/items"
    )

    return jsonify({
        "product_id":
            product_id,

        "status_http":
            status,

        "resposta":
            data
    }), status


@app.route(
    "/mercadolivre/teste-produto"
)
def teste_product():
    product_id = request.args.get(
        "product_id",
        "MLB58793248"
    )

    data, status, _ = ml_get(
        f"/products/{product_id}"
    )

    return jsonify({
        "product_id":
            product_id,

        "status_http":
            status,

        "resposta":
            data
    }), status


@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():
    data = tokens()

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

        result[
            "users_me"
        ] = {
            "status_http":
                status,

            "resposta":
                me
        }

    if data:
        result[
            "token_local"
        ] = {
            "user_id":
                data.get(
                    "user_id"
                ),

            "nickname":
                data.get(
                    "nickname"
                ),

            "expires_at":
                data.get(
                    "expires_at"
                )
        }

    return jsonify(result)


# ============================================================
# HTML
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
    font-family:Arial,sans-serif;
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
    box-shadow:
        0 5px 20px
        #0000000c
}

button,
input{
    width:100%;
    padding:13px;
    border-radius:10px;
    border:1px solid #ddd;
    font-size:15px
}

button{
    border:0;
    background:#3483fa;
    color:white;
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
            2,
            minmax(0,1fr)
        );
    gap:10px
}

.stat{
    background:#f3f4f6;
    padding:15px;
    border-radius:12px;
    min-width:0
}

.stat b{
    display:block;
    font-size:23px;
    margin-top:6px;
    word-break:break-word
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
    padding:10px;
    border-radius:8px;
    margin-top:8px
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

@media(max-width:600px){

    .stats{
        grid-template-columns:
            repeat(
                2,
                minmax(0,1fr)
            )
    }

    .stat{
        padding:13px
    }

    .stat b{
        font-size:20px
    }

}

</style>


<script>

async function cacar(category){

    document.getElementById(
        'status'
    ).textContent =
        '🔄 Caçando promoções...';

    const url =
        '/api/cacar'
        +
        (
            category
            ?
            '?categoria='
            +
            encodeURIComponent(
                category
            )
            :
            ''
        );

    try{

        const response =
            await fetch(url);

        const data =
            await response.json();

        render(data);

        document.getElementById(
            'status'
        ).textContent =
            '✅ Busca atualizada agora.';

    }catch(error){

        document.getElementById(
            'status'
        ).textContent =
            '❌ Erro ao buscar ofertas.';

        console.error(error);
    }
}


async function buscar(){

    const input =
        document.getElementById(
            'q'
        );

    const query =
        input.value.trim();

    if(!query){
        return;
    }

    document.getElementById(
        'status'
    ).textContent =
        '🔄 Procurando...';

    try{

        const response =
            await fetch(
                '/api/buscar?q='
                +
                encodeURIComponent(
                    query
                )
            );

        const data =
            await response.json();

        render(data);

        document.getElementById(
            'status'
        ).textContent =
            '✅ Busca atualizada agora.';

    }catch(error){

        document.getElementById(
            'status'
        ).textContent =
            '❌ Erro ao procurar.';

        console.error(error);
    }
}


function render(data){

    const stats =
        data.stats || {};

    document.getElementById(
        'stats'
    ).innerHTML =
        Object.entries(stats)
        .map(
            ([key,value]) => `
                <div class="stat">
                    ${esc(key)}
                    <b>
                        ${esc(value)}
                    </b>
                </div>
            `
        )
        .join('');


    const models =
        data.modelos || [];


    document.getElementById(
        'results'
    ).innerHTML =
        models
        .map(
            (model,index) => `

                <div class="modelo">

                    <div class="mh">

                        ${
                            model.image
                            ?
                            `
                            <img
                                src="${model.image}"
                            >
                            `
                            :
                            ''
                        }

                        <div>

                            <span class="tag">
                                🔥 MODELO
                                ${index + 1}
                            </span>

                            <div class="title">
                                ${esc(
                                    model.modelo_nome
                                )}
                            </div>

                            ${
                                (
                                    model.especificacoes
                                    || []
                                )
                                .map(
                                    spec => `
                                        <span class="tag">
                                            ${esc(spec)}
                                        </span>
                                    `
                                )
                                .join('')
                            }

                            <div class="small">
                                ${
                                    model.ofertas.length
                                }
                                vendedor(es)
                            </div>

                        </div>

                    </div>

                    ${
                        model.ofertas
                        .map(
                            (offer,offerIndex) =>
                                seller(
                                    offer,
                                    index,
                                    offerIndex
                                )
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


function seller(
    offer,
    modelIndex,
    offerIndex
){

    const id =
        'offer_'
        + modelIndex
        + '_'
        + offerIndex;

    const coupon =
        offer.cupom;


    return `

        <div class="seller">

            ${
                offer.menor_preco_modelo
                ?
                `
                <span
                    class="tag"
                    style="
                        background:#00a650;
                        color:white
                    "
                >
                    🏆 MELHOR OPÇÃO
                </span>
                `
                :
                ''
            }

            <div class="price">
                ${brl(offer.price)}
            </div>

            ${
                offer.original_price
                ?
                `
                <div class="old">
                    De:
                    ${brl(
                        offer.original_price
                    )}
                </div>
                `
                :
                ''
            }

            ${
                offer.discount > 0
                ?
                `
                <div class="green">
                    🔥
                    ${offer.discount}%
                    OFF
                    <span class="small">
                        desconto do vendedor
                    </span>
                </div>
                `
                :
                ''
            }

            ${
                offer.free_shipping
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
                offer.shipping_known
                ?
                `
                <div class="green">
                    💰 Total:
                    ${brl(
                        offer.total_price
                    )}
                </div>
                `
                :
                ''
            }


            ${
                coupon
                ?
                `

                <div class="coupon">

                    <b>
                        🎟️ CUPOM:
                        ${esc(
                            coupon.code
                        )}
                    </b>

                    ${
                        coupon.discount_percent
                        ?
                        `
                        <div>
                            🔥 Até
                            ${
                                coupon.discount_percent
                            }%
                            OFF
                        </div>
                        `
                        :
                        ''
                    }

                    ${
                        coupon.fixed_discount
                        ?
                        `
                        <div>
                            💰
                            ${brl(
                                coupon.fixed_discount
                            )}
                            OFF
                        </div>
                        `
                        :
                        ''
                    }

                    ${
                        coupon.min_purchase
                        ?
                        `
                        <div class="small">
                            Compra mínima:
                            ${brl(
                                coupon.min_purchase
                            )}
                        </div>
                        `
                        :
                        ''
                    }

                    ${
                        coupon.max_discount
                        ?
                        `
                        <div class="small">
                            Desconto máximo:
                            ${brl(
                                coupon.max_discount
                            )}
                        </div>
                        `
                        :
                        ''
                    }

                    <div>
                        💵 Desconto estimado:
                        <b>
                            ${brl(
                                offer.desconto_cupom
                            )}
                        </b>
                    </div>

                    <div class="final">
                        💥 Estimado com cupom:
                        ${brl(
                            offer.preco_com_cupom
                        )}
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
                ${
                    offer.seller_id
                    || 'N/A'
                }
            </div>

            <br>

            <a
                href="${offer.permalink}"
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
                    ${JSON.stringify(offer)}
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


async function anuncio(
    id,
    offer
){

    const link =
        document.getElementById(
            'link_' + id
        ).value;


    const params =
        new URLSearchParams({
            title:
                offer.title,

            price:
                offer.price,

            discount:
                offer.discount,

            shipping_free:
                offer.free_shipping
                ? '1'
                : '0',

            cupom:
                offer.cupom
                ? offer.cupom.code
                : '',

            affiliate_link:
                link
        });


    if(
        offer.original_price
    ){
        params.set(
            'original_price',
            offer.original_price
        );
    }


    const response =
        await fetch(
            '/api/gerar-anuncio?'
            + params
        );


    const data =
        await response.json();


    document.getElementById(
        'ad_' + id
    ).style.display =
        'block';


    document.getElementById(
        'ad_' + id
    ).textContent =
        data.anuncio;


    document.getElementById(
        'copy_' + id
    ).style.display =
        'block';
}


function copyAd(id){

    navigator.clipboard.writeText(
        document.getElementById(
            'ad_' + id
        ).textContent
    );

    alert(
        'Anúncio copiado!'
    );
}


function brl(value){

    return (
        'R$ '
        +
        Number(
            value || 0
        ).toLocaleString(
            'pt-BR',
            {
                minimumFractionDigits:2,
                maximumFractionDigits:2
            }
        )
    );
}


function esc(value){

    return String(
        value || ''
    ).replace(
        /[&<>"']/g,
        char => ({
            '&':'&amp;',
            '<':'&lt;',
            '>':'&gt;',
            '"':'&quot;',
            "'":'&#039;'
        }[char])
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
            Produtos a partir de
            R$ 69,90.
            O sistema procura o cupom
            que gera o maior desconto
            real disponível.
        </p>


        {% if conectado %}

            <div class="status">

                🟢 Mercado Livre conectado

                {% if nickname %}
                    <br>
                    <b>
                        {{nickname}}
                    </b>
                {% endif %}

            </div>


            <a
                href="/mercadolivre/logout"
            >
                <button>
                    Desconectar
                </button>
            </a>

        {% else %}

            <a
                href="/mercadolivre/login"
            >
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

            {% for category in categorias %}

                <button
                    class="cat"
                    onclick="cacar(
                        {{category|tojson}}
                    )"
                >
                    {{category}}
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
            placeholder="
                Ex: celular,
                perfume, furadeira...
            "
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

        <p class="small">
            O cupom mostrado é o que
            apresenta o maior desconto
            real para aquele produto.
        </p>

        <div id="results">

            <p>
                Faça uma busca
                para começar.
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
# PÁGINAS
# ============================================================

@app.route("/")
def index():
    data = tokens()

    return render_template_string(
        HTML,
        conectado=bool(
            access_token()
        ),
        nickname=(
            data.get("nickname")
            if data
            else None
        ),
        categorias=list(
            CATALOG.keys()
        )
    )


@app.route("/buscar")
def buscar_page():
    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:
        return redirect("/")

    sync_coupons()

    result = scan_queries(
        [query],
        request.args.get(
            "desconto",
            0
        )
    )

    data = tokens()

    return render_template_string(
        HTML,
        conectado=bool(
            access_token()
        ),
        nickname=(
            data.get("nickname")
            if data
            else None
        ),
        categorias=list(
            CATALOG.keys()
        ),
        resultado=result
    )


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

        "produto_minimo":
            MIN_PRODUCT_PRICE,

        "fluxo":
            "products/{product_id}/items",

        "cupons":
            "ativo",

        "ranking":
            "maior desconto real",

        "painel":
            [
                "ofertas",
                "cupom aplicável",
                "valor",
                "valor com o desconto"
            ]
    })


# ============================================================
# ERROS
# ============================================================

@app.errorhandler(404)
def error_404(error):
    return jsonify({
        "erro":
            "Rota não encontrada.",

        "rota":
            request.path
    }), 404


@app.errorhandler(500)
def error_500(error):
    return jsonify({
        "erro":
            "Erro interno no servidor.",

        "detalhes":
            str(error)
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