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

from flask import (
    Flask,
    request,
    redirect,
    session,
    jsonify,
    render_template_string,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "chave-cacador-ofertas"
)

ML_CLIENT_ID = os.getenv(
    "ML_CLIENT_ID",
    ""
).strip()

ML_CLIENT_SECRET = os.getenv(
    "ML_CLIENT_SECRET",
    ""
).strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"

ML_AUTH = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN = (
    "https://api.mercadolibre.com/oauth/token"
)

SITE_ID = "MLB"

DB_FILE = "ofertas.db"

MIN_PRODUCT_PRICE = 69.90

REQUEST_TIMEOUT = 30

MAX_PRODUCTS = 80


# ============================================================
# FONTES PÚBLICAS
# ============================================================

COUPON_SOURCE_URLS = [
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/l/promocoes",
]


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
        "realme smartphone",
    ],

    "🌸 Perfumes": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "perfume nacional",
        "eau de parfum",
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
        "suplementos",
    ],

    "🔧 Ferramentas": [
        "furadeira",
        "parafusadeira",
        "esmerilhadeira",
        "kit ferramentas",
        "maleta ferramentas",
        "serra",
        "chave de impacto",
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "headset",
        "smartwatch",
        "tablet",
        "caixa de som bluetooth",
        "camera digital",
        "power bank",
    ],

    "🏠 Casa": [
        "aspirador de pó",
        "liquidificador",
        "cafeteira",
        "air fryer",
        "ventilador",
        "ferro de passar",
    ],

    "🍳 Cozinha": [
        "air fryer",
        "panela elétrica",
        "jogo de panelas",
        "cafeteira",
        "liquidificador",
        "sandwichera",
    ],

    "🚗 Automotivo": [
        "compressor automotivo",
        "aspirador automotivo",
        "suporte celular carro",
        "carregador automotivo",
        "ferramentas automotivas",
        "tapete automotivo",
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "mochila",
        "relogio masculino",
        "bolsa feminina",
        "oculos de sol",
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

            coupon_code TEXT,
            coupon_type TEXT,
            coupon_value REAL,
            coupon_discount REAL,
            coupon_final_price REAL,

            coupon_confirmed INTEGER DEFAULT 0,
            coupon_source TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            code TEXT UNIQUE,
            description TEXT,

            discount_percent REAL DEFAULT 0,
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


def safe_float(value, default=0):

    try:

        if value is None:
            return default

        if isinstance(
            value,
            (int, float)
        ):

            return float(value)

        value = str(value)

        value = (
            value
            .replace("R$", "")
            .replace(" ", "")
        )

        if "," in value:

            value = (
                value
                .replace(".", "")
                .replace(",", ".")
            )

        return float(value)

    except Exception:

        return default


def norm(text):

    if not text:
        return ""

    text = str(text).lower()

    trans = str.maketrans(
        "áàãâäéèêëíìîïóòõôöúùûüç",
        "aaaaaeeeeiiiiooooouuuuc"
    )

    text = text.translate(trans)

    text = re.sub(
        r"[^a-z0-9\s]+",
        " ",
        text
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def model_name(title):

    if not title:
        return "Produto"

    title = re.sub(
        r"\b(?:novo|original|oficial)\b",
        "",
        str(title),
        flags=re.I
    )

    return re.sub(
        r"\s+",
        " ",
        title
    ).strip()


def discount_percent(
    price,
    original
):

    try:

        price = float(price)
        original = float(original)

        if original > price > 0:

            return round(
                (
                    1
                    - price / original
                ) * 100,
                2
            )

    except Exception:
        pass

    return 0


def total_price(
    price,
    shipping
):

    try:

        return round(
            float(price)
            + float(shipping or 0),
            2
        )

    except Exception:

        return float(price or 0)


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
            "realme",
        ],

        "bad": [
            "capa",
            "capinha",
            "pelicula",
            "suporte",
            "carregador",
            "cabo",
            "adaptador",
            "bateria",
            "case",
        ],
    },

    "perfume": {

        "strong": [
            "perfume",
            "eau de parfum",
            "eau de toilette",
            "parfum",
        ],

        "bad": [
            "frasco vazio",
            "decant",
            "amostra",
            "porta perfume",
        ],
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
            "suplemento",
        ],

        "bad": [
            "capa",
            "adesivo",
            "suporte",
            "peca de reposicao",
        ],
    },

    "ferramenta": {

        "strong": [
            "furadeira",
            "parafusadeira",
            "esmerilhadeira",
            "ferramenta",
            "serra",
            "impacto",
        ],

        "bad": [
            "broca avulsa",
            "peca",
            "carvao",
            "capa",
        ],
    },
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
            "celular",
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
            "suplemento",
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
            "serra",
        ]
    ):

        return "ferramenta"

    return None


def relevance(
    title,
    query
):

    title = norm(title)
    query = norm(query)

    profile = profile_for(
        query
    )

    score = 0

    data = PROFILES.get(
        profile,
        {}
    )

    for word in data.get(
        "strong",
        []
    ):

        if word in title:

            score += 35

    for word in query.split():

        if (
            len(word) >= 3
            and word in title
        ):

            score += 10

    for word in data.get(
        "bad",
        []
    ):

        if word in title:

            score -= 90

    return score


# ============================================================
# OAUTH PKCE
# ============================================================

def generate_pkce():

    verifier = secrets.token_urlsafe(
        64
    )

    digest = hashlib.sha256(
        verifier.encode()
    ).digest()

    challenge = (
        base64.urlsafe_b64encode(
            digest
        )
        .decode()
        .rstrip("=")
    )

    return verifier, challenge


def tokens():

    conn = get_db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id=1"
    ).fetchone()

    conn.close()

    return (
        dict(row)
        if row
        else None
    )


def save_tokens(
    data,
    user=None
):

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

        ON CONFLICT(id)
        DO UPDATE SET

            access_token =
                excluded.access_token,

            refresh_token =
                COALESCE(
                    excluded.refresh_token,
                    oauth_tokens.refresh_token
                ),

            expires_at =
                excluded.expires_at,

            user_id =
                COALESCE(
                    excluded.user_id,
                    oauth_tokens.user_id
                ),

            nickname =
                COALESCE(
                    excluded.nickname,
                    oauth_tokens.nickname
                )
    """, (

        data.get(
            "access_token"
        ),

        data.get(
            "refresh_token"
        ),

        int(time.time())
        + int(
            data.get(
                "expires_in",
                21600
            )
        ),

        str(
            user.get("id")
        )
        if user
        and user.get("id")
        else old.get(
            "user_id"
        ),

        user.get(
            "nickname"
        )
        if user
        else old.get(
            "nickname"
        ),
    ))

    conn.commit()

    conn.close()


def refresh_token():

    data = tokens()

    if not data:
        return None

    refresh = data.get(
        "refresh_token"
    )

    if not refresh:
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
                    refresh,
            },

            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            return None

        result = response.json()

        if not result.get(
            "access_token"
        ):

            return None

        save_tokens(
            result,
            {
                "id":
                    data.get(
                        "user_id"
                    ),

                "nickname":
                    data.get(
                        "nickname"
                    ),
            }
        )

        return result.get(
            "access_token"
        )

    except Exception:

        return None


def access_token():

    data = tokens()

    if not data:
        return None

    token = data.get(
        "access_token"
    )

    expires = int(
        data.get(
            "expires_at"
        ) or 0
    )

    if (
        token
        and time.time()
        < expires - 120
    ):

        return token

    return (
        refresh_token()
        or token
    )


# ============================================================
# ML GET
# ============================================================

def ml_get(
    path,
    params=None
):

    token = access_token()

    if not token:

        return {}, 401

    url = (
        path
        if path.startswith("http")
        else ML_API + path
    )

    headers = {

        "Authorization":
            "Bearer " + token,

        "Accept":
            "application/json",

        "User-Agent":
            "CacadorDeOfertas/6.0",
    }

    try:

        response = requests.get(

            url,

            headers=headers,

            params=params,

            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 401:

            token = refresh_token()

            if token:

                headers[
                    "Authorization"
                ] = "Bearer " + token

                response = requests.get(

                    url,

                    headers=headers,

                    params=params,

                    timeout=REQUEST_TIMEOUT
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
            response.status_code
        )

    except Exception as e:

        return {
            "error":
                str(e)
        }, 500


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return (
            "ML_CLIENT_ID não configurado.",
            500
        )

    verifier, challenge = (
        generate_pkce()
    )

    state = secrets.token_urlsafe(
        32
    )

    session[
        "ml_state"
    ] = state

    session[
        "ml_code_verifier"
    ] = verifier

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
            "S256",
    }

    return redirect(
        ML_AUTH
        + "?"
        + urlencode(params)
    )


@app.route(
    "/mercadolivre/callback"
)
def mercadolivre_callback():

    if request.args.get(
        "error"
    ):

        return jsonify({

            "erro":
                request.args.get(
                    "error"
                ),

            "descricao":
                request.args.get(
                    "error_description"
                ),

        }), 400

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    if (
        not code
        or state
        != session.get(
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
                    ),
            },

            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            return jsonify({

                "erro":
                    "Falha ao obter token.",

                "status":
                    response.status_code,

                "resposta":
                    response.text,

            }), response.status_code

        data = response.json()

        user = None

        if data.get(
            "access_token"
        ):

            me = requests.get(

                ML_API
                + "/users/me",

                headers={
                    "Authorization":
                        "Bearer "
                        + data[
                            "access_token"
                        ]
                },

                timeout=REQUEST_TIMEOUT
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


@app.route(
    "/mercadolivre/logout"
)
def mercadolivre_logout():

    conn = get_db()

    conn.execute(
        "DELETE FROM oauth_tokens WHERE id=1"
    )

    conn.commit()

    conn.close()

    session.clear()

    return redirect("/")


# ============================================================
# BUSCA DE CATEGORIAS
# ============================================================

def discover_categories(
    query
):

    data, status = ml_get(

        f"/sites/{SITE_ID}/domain_discovery/search",

        {
            "q":
                query
        }
    )

    if status != 200:
        return []

    if not isinstance(
        data,
        list
    ):

        return []

    result = []

    for item in data:

        category_id = (
            item.get(
                "category_id"
            )
            or item.get(
                "id"
            )
        )

        category_name = (
            item.get(
                "category_name"
            )
            or item.get(
                "name"
            )
            or category_id
        )

        if category_id:

            result.append({

                "category_id":
                    category_id,

                "category_name":
                    category_name,
            })

    return result


# ============================================================
# NOVO: BUSCA DIRETA DE PRODUTOS
# ============================================================

def search_products(
    query,
    limit=30
):

    data, status = ml_get(

        f"/products/search",

        {

            "site_id":
                SITE_ID,

            "q":
                query,

            "status":
                "active",

            "limit":
                limit,

            "offset":
                0,
        }
    )

    if status != 200:
        return []

    if not isinstance(
        data,
        dict
    ):

        return []

    return (
        data.get(
            "results"
        )
        or []
    )


# ============================================================
# MAIS VENDIDOS / DESTAQUES
# ============================================================

def highlights(
    category_id
):

    data, status = ml_get(

        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    if status != 200:
        return []

    if isinstance(
        data,
        list
    ):

        return data

    if isinstance(
        data,
        dict
    ):

        return (
            data.get(
                "content"
            )
            or data.get(
                "results"
            )
            or []
        )

    return []


# ============================================================
# PRODUTO
# ============================================================

def get_product(
    product_id
):

    data, status = ml_get(
        f"/products/{product_id}"
    )

    if (
        status == 200
        and isinstance(
            data,
            dict
        )
    ):

        return data

    return None


# ============================================================
# ITENS / VENDEDORES
# ============================================================

def product_items(
    product_id
):

    data, status = ml_get(

        f"/products/{product_id}/items"
    )

    if status != 200:
        return []

    if isinstance(
        data,
        list
    ):

        return data

    if isinstance(
        data,
        dict
    ):

        return (
            data.get(
                "results"
            )
            or []
        )

    return []


def normalize_item(
    item
):

    if (
        not isinstance(
            item,
            dict
        )
        or not item.get(
            "item_id"
        )
    ):

        return None

    shipping = (
        item.get(
            "shipping"
        )
        or {}
    )

    free_shipping = bool(
        shipping.get(
            "free_shipping"
        )
    )

    shipping_cost = (
        shipping.get(
            "cost"
        )
    )

    return {

        "item_id":
            item.get(
                "item_id"
            ),

        "seller_id":
            item.get(
                "seller_id"
            ),

        "price":
            item.get(
                "price"
            ),

        "original_price":
            item.get(
                "original_price"
            ),

        "condition":
            item.get(
                "condition"
            ),

        "listing_type_id":
            item.get(
                "listing_type_id"
            ),

        "free_shipping":
            free_shipping,

        "shipping_cost":
            shipping_cost,

        "permalink":
            item.get(
                "permalink"
            ),
    }


# ============================================================
# CUPONS PÚBLICOS
# ============================================================

def clean_public_text(
    raw
):

    text = html_lib.unescape(
        raw or ""
    )

    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<noscript.*?</noscript>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"</(?:div|p|li|article|section|h1|h2|h3|h4|h5|h6|br|tr)>",
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
        r"\n\s+",
        "\n",
        text
    )

    return text.strip()


def fetch_public_coupon_pages():

    headers = {

        "User-Agent":
            "Mozilla/5.0 "
            "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 "
            "Version/18.0 Mobile/15E148 Safari/604.1",

        "Accept-Language":
            "pt-BR,pt;q=0.9",

        "Accept":
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8",
    }

    pages = []

    for url in COUPON_SOURCE_URLS:

        try:

            response = requests.get(

                url,

                headers=headers,

                timeout=REQUEST_TIMEOUT
            )

            if response.status_code == 200:

                pages.append({

                    "url":
                        response.url,

                    "text":
                        clean_public_text(
                            response.text
                        ),

                })

        except Exception as e:

            print(
                "[CUPOM PAGE]",
                url,
                e
            )

    return pages


# ============================================================
# DINHEIRO NO TEXTO
# ============================================================

def money_regex():

    return (
        r"R\$\s*"
        r"([0-9]{1,3}"
        r"(?:\.[0-9]{3})*,[0-9]{2}"
        r"|[0-9]+,[0-9]{2}"
        r"|[0-9]+(?:\.[0-9]{2})?)"
    )


def parse_money(
    text
):

    if not text:
        return None

    match = re.search(
        money_regex(),
        text,
        re.I
    )

    if not match:
        return None

    value = match.group(1)

    try:

        if "," in value:

            value = (
                value
                .replace(".", "")
                .replace(",", ".")
            )

        return float(value)

    except Exception:

        return None


# ============================================================
# CUPOM
# ============================================================

def detect_coupon(
    text
):

    if not text:
        return None

    # FIXO
    match = re.search(

        r"\bCupom\s+"
        r"R\$\s*"
        r"([\d\.,]+)"
        r"\s*OFF\b",

        text,

        flags=re.I
    )

    if match:

        value = parse_money(
            "R$ "
            + match.group(1)
        )

        if value:

            return {

                "type":
                    "fixed",

                "value":
                    value,

                "label":
                    f"Cupom {brl(value)} OFF",
            }

    # PERCENTUAL
    match = re.search(

        r"\bCupom\s+"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%\s*OFF\b",

        text,

        flags=re.I
    )

    if match:

        value = float(
            match.group(1)
            .replace(",", ".")
        )

        if (
            0
            < value
            <= 100
        ):

            return {

                "type":
                    "percent",

                "value":
                    value,

                "label":
                    f"Cupom {value:g}% OFF",
            }

    return None


# ============================================================
# EXTRAI PRODUTOS DA PÁGINA PÚBLICA
# ============================================================

def parse_public_coupon_products():

    pages = (
        fetch_public_coupon_pages()
    )

    results = []

    coupon_pattern = re.compile(

        r"\bCupom\s+"
        r"(?:R\$\s*[\d\.,]+\s*OFF"
        r"|\d+(?:[.,]\d+)?\s*%\s*OFF)",

        re.I
    )

    for page in pages:

        text = page[
            "text"
        ]

        matches = list(
            coupon_pattern.finditer(
                text
            )
        )

        for index, match in enumerate(
            matches
        ):

            coupon = detect_coupon(
                match.group(0)
            )

            if not coupon:
                continue

            # ------------------------------------------------
            # BLOCO LOCAL
            # ------------------------------------------------

            start = max(
                0,
                match.start() - 900
            )

            end = min(
                len(text),
                match.end() + 150
            )

            block = text[
                start:end
            ]

            # ------------------------------------------------
            # PREÇOS DO BLOCO
            # ------------------------------------------------

            money_matches = list(
                re.finditer(
                    money_regex(),
                    block,
                    re.I
                )
            )

            values = []

            for money in money_matches:

                value = parse_money(
                    money.group(0)
                )

                if value is not None:

                    values.append({

                        "value":
                            value,

                        "start":
                            money.start(),

                        "end":
                            money.end(),
                    })

            if not values:
                continue

            # ------------------------------------------------
            # PREÇO ATUAL
            # ------------------------------------------------

            current_price = None

            # Primeiro tenta:
            # R$ atual + X% OFF
            seller_price = re.findall(

                r"(R\$\s*[\d\.,]+)"
                r"\s+"
                r"\d+(?:[.,]\d+)?"
                r"\s*%\s*OFF",

                block,

                flags=re.I
            )

            if seller_price:

                current_price = parse_money(
                    seller_price[-1]
                )

            # Caso não tenha percentual de vendedor,
            # usa o último preço antes do cupom.
            if current_price is None:

                before_coupon = block[
                    :max(
                        0,
                        block.lower().rfind(
                            "cupom"
                        )
                    )
                ]

                before_values = []

                for m in re.finditer(
                    money_regex(),
                    before_coupon,
                    re.I
                ):

                    value = parse_money(
                        m.group(0)
                    )

                    if value is not None:

                        before_values.append(
                            value
                        )

                if before_values:

                    current_price = (
                        min(
                            before_values
                        )
                    )

            if (
                current_price is None
                or current_price
                < MIN_PRODUCT_PRICE
            ):

                continue

            # ------------------------------------------------
            # ORIGINAL
            # ------------------------------------------------

            original_price = None

            larger = [
                x["value"]
                for x in values
                if x["value"]
                > current_price
            ]

            if larger:

                original_price = min(
                    larger
                )

            # ------------------------------------------------
            # TÍTULO
            # ------------------------------------------------

            # A ideia é pegar somente o texto imediatamente
            # anterior ao primeiro preço.
            first_price = re.search(
                money_regex(),
                block,
                re.I
            )

            if first_price:

                raw_title = block[
                    :first_price.start()
                ]

            else:

                raw_title = block

            raw_title = re.sub(
                r"\b(?:no Pix|em outros meios|Cupom|OFF)\b.*$",
                "",
                raw_title,
                flags=re.I
            )

            raw_title = re.sub(
                r"\b(?:hoje|amanhã|amanha|domingo|segunda-feira|terça-feira|quarta-feira|quinta-feira|sexta-feira|sábado)\b.*$",
                "",
                raw_title,
                flags=re.I
            )

            title = re.sub(
                r"\s+",
                " ",
                raw_title
            ).strip(
                " -|•"
            )

            # Se pegou lixo anterior, tenta linhas menores.
            if (
                len(title) < 8
                or len(title) > 220
            ):

                parts = re.split(
                    r"[\n|•]",
                    block
                )

                candidates = []

                for part in parts:

                    part = re.sub(
                        r"\s+",
                        " ",
                        part
                    ).strip()

                    if (
                        len(part) >= 8
                        and "cupom"
                        not in part.lower()
                        and "R$"
                        not in part
                    ):

                        candidates.append(
                            part
                        )

                if candidates:

                    title = max(
                        candidates,
                        key=len
                    )

            if not title:
                continue

            results.append({

                "title":
                    title,

                "price":
                    round(
                        current_price,
                        2
                    ),

                "original_price":
                    (
                        round(
                            original_price,
                            2
                        )
                        if original_price
                        else None
                    ),

                "coupon":
                    coupon,

                "source":
                    page["url"],
            })

    # --------------------------------------------------------
    # REMOVE DUPLICADOS
    # --------------------------------------------------------

    unique = {}

    for item in results:

        key = (
            norm(
                item["title"]
            )
            + "|"
            + str(
                item["price"]
            )
            + "|"
            + item[
                "coupon"
            ][
                "label"
            ]
        )

        if key not in unique:

            unique[key] = item

    result = list(
        unique.values()
    )

    print(
        "[CUPONS PÚBLICOS]",
        len(result)
    )

    for item in result[:15]:

        print(
            "[CUPOM PRODUTO]",
            item["title"][:90],
            "|",
            brl(item["price"]),
            "|",
            item["coupon"]["label"]
        )

    return result


# ============================================================
# MATCH PRODUTO ↔ CUPOM
# ============================================================

STOP_WORDS = {

    "de",
    "da",
    "do",
    "das",
    "dos",
    "com",
    "para",
    "por",
    "e",
    "em",
    "no",
    "na",
    "um",
    "uma",
    "original",
    "novo",
    "oficial",
}


def title_tokens(
    title
):

    return {
        word
        for word in norm(
            title
        ).split()
        if len(word) >= 3
        and word not in STOP_WORDS
    }


def title_similarity(
    title_a,
    title_b
):

    a = title_tokens(
        title_a
    )

    b = title_tokens(
        title_b
    )

    if not a or not b:
        return 0

    intersection = (
        a & b
    )

    base = max(
        1,
        min(
            len(a),
            len(b)
        )
    )

    score = (
        len(intersection)
        / base
    )

    # Bônus para marcas/modelos muito específicos.
    important = [

        "iphone",
        "galaxy",
        "samsung",
        "motorola",
        "xiaomi",
        "redmi",
        "poco",
        "realme",

        "kappa",
        "nike",
        "adidas",

        "smartwatch",

        "air",
        "fryer",

        "whey",
        "creatina",

        "perfume",
    ]

    na = norm(
        title_a
    )

    nb = norm(
        title_b
    )

    for word in important:

        if (
            word in na
            and word in nb
        ):

            score += 0.10

    return min(
        score,
        1
    )


def match_coupon(
    product_title,
    product_price,
    public_products
):

    candidates = []

    product_norm = norm(
        product_title
    )

    for public in public_products:

        public_title = public[
            "title"
        ]

        score = title_similarity(
            product_title,
            public_title
        )

        # Preço ajuda a evitar associação errada.
        public_price = safe_float(
            public.get(
                "price"
            )
        )

        price_score = 0

        if public_price > 0:

            difference = abs(
                product_price
                - public_price
            )

            # Até R$ 15 de diferença:
            # forte evidência de mesma oferta.
            if difference <= 5:

                price_score = 0.25

            elif difference <= 15:

                price_score = 0.12

        final_score = (
            score
            + price_score
        )

        if final_score >= 0.65:

            candidates.append(
                (
                    final_score,
                    public
                )
            )

    if not candidates:

        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    score, best = (
        candidates[0]
    )

    coupon = dict(
        best[
            "coupon"
        ]
    )

    coupon[
        "match_score"
    ] = round(
        score,
        3
    )

    coupon[
        "public_title"
    ] = best[
        "title"
    ]

    coupon[
        "public_price"
    ] = best[
        "price"
    ]

    coupon[
        "source"
    ] = best[
        "source"
    ]

    return coupon


# ============================================================
# CÁLCULO
# ============================================================

def calculate_coupon(
    price,
    coupon
):

    if not coupon:
        return None

    price = float(
        price
    )

    minimum = coupon.get(
        "min_purchase"
    )

    if (
        minimum
        and price < float(
            minimum
        )
    ):

        return None

    if coupon[
        "type"
    ] == "fixed":

        discount = float(
            coupon[
                "value"
            ]
        )

    else:

        discount = (
            price
            * float(
                coupon[
                    "value"
                ]
            )
            / 100
        )

    max_discount = coupon.get(
        "max_discount"
    )

    if max_discount:

        discount = min(
            discount,
            float(
                max_discount
            )
        )

    discount = min(
        discount,
        price
    )

    final = max(
        price - discount,
        0
    )

    return {

        "discount":
            round(
                discount,
                2
            ),

        "final":
            round(
                final,
                2
            ),

        "effective_percent":
            round(
                discount
                / price
                * 100,
                2
            ),
    }


# ============================================================
# SALVA CUPOM
# ============================================================

def save_coupons(
    public_products
):

    conn = get_db()

    conn.execute(
        "DELETE FROM cupons"
    )

    for item in public_products:

        coupon = item[
            "coupon"
        ]

        conn.execute("""
            INSERT OR IGNORE INTO cupons(
                code,
                description,
                discount_percent,
                fixed_discount,
                source_url,
                conditions,
                active
            )
            VALUES(?,?,?,?,?,?,1)
        """, (

            coupon[
                "label"
            ],

            item[
                "title"
            ],

            coupon[
                "value"
            ]
            if coupon[
                "type"
            ] == "percent"
            else 0,

            coupon[
                "value"
            ]
            if coupon[
                "type"
            ] == "fixed"
            else 0,

            item[
                "source"
            ],

            item[
                "title"
            ],
        ))

    conn.commit()

    conn.close()


# ============================================================
# COLETA PRODUTOS
# ============================================================

def collect_products(
    queries
):

    products = {}

    for query in queries:

        print(
            "[BUSCA PRODUTOS]",
            query
        )

        # ----------------------------------------------------
        # 1. BUSCA DIRETA
        # ----------------------------------------------------

        direct = search_products(
            query,
            limit=30
        )

        for item in direct:

            product_id = (
                item.get(
                    "id"
                )
                or item.get(
                    "product_id"
                )
            )

            if not product_id:
                continue

            products[
                product_id
            ] = {

                "query":
                    query,

                "category_name":
                    None,

                "category_id":
                    None,
            }

        # ----------------------------------------------------
        # 2. DOMAIN DISCOVERY + HIGHLIGHTS
        # ----------------------------------------------------

        discovered = (
            discover_categories(
                query
            )
        )

        for category in discovered[:3]:

            category_id = (
                category[
                    "category_id"
                ]
            )

            category_name = (
                category[
                    "category_name"
                ]
            )

            items = highlights(
                category_id
            )

            for item in items:

                product_id = (
                    item.get(
                        "id"
                    )
                    or item.get(
                        "product_id"
                    )
                )

                if not product_id:
                    continue

                if product_id not in products:

                    products[
                        product_id
                    ] = {

                        "query":
                            query,

                        "category_name":
                            category_name,

                        "category_id":
                            category_id,
                    }

                if (
                    len(products)
                    >= MAX_PRODUCTS
                ):

                    break

            if (
                len(products)
                >= MAX_PRODUCTS
            ):

                break

        if (
            len(products)
            >= MAX_PRODUCTS
        ):

            break

    print(
        "[PRODUTOS ENCONTRADOS]",
        len(products)
    )

    return products


# ============================================================
# CAÇADOR
# ============================================================

def scan_queries(
    queries
):

    # --------------------------------------------------------
    # PRIMEIRO: CUPONS
    # --------------------------------------------------------

    public_products = (
        parse_public_coupon_products()
    )

    save_coupons(
        public_products
    )

    # --------------------------------------------------------
    # PRODUTOS
    # --------------------------------------------------------

    products = collect_products(
        queries
    )

    groups = {}

    for product_id, info in (
        products.items()
    ):

        product = get_product(
            product_id
        )

        if not product:
            continue

        title = (
            product.get(
                "name"
            )
            or product.get(
                "title"
            )
            or ""
        )

        if not title:
            continue

        score = relevance(
            title,
            info[
                "query"
            ]
        )

        # Para busca direta, não seja excessivamente rígido.
        if score < 10:

            continue

        # ----------------------------------------------------
        # IMAGEM
        # ----------------------------------------------------

        image = None

        pictures = (
            product.get(
                "pictures"
            )
            or []
        )

        if pictures:

            first = pictures[0]

            if isinstance(
                first,
                dict
            ):

                image = (
                    first.get(
                        "url"
                    )
                    or first.get(
                        "secure_url"
                    )
                )

        # ----------------------------------------------------
        # ITENS
        # ----------------------------------------------------

        items = product_items(
            product_id
        )

        best_offer = None

        for raw_item in items:

            item = normalize_item(
                raw_item
            )

            if not item:
                continue

            price = safe_float(
                item[
                    "price"
                ]
            )

            # MÍNIMO
            if (
                price
                < MIN_PRODUCT_PRICE
            ):

                continue

            original = item.get(
                "original_price"
            )

            if original is not None:

                original = safe_float(
                    original
                )

            seller_discount = (
                discount_percent(
                    price,
                    original
                )
            )

            shipping = item.get(
                "shipping_cost"
            )

            if shipping is not None:

                shipping = safe_float(
                    shipping
                )

            total = total_price(
                price,
                shipping
            )

            # ------------------------------------------------
            # CUPOM ESPECÍFICO
            # ------------------------------------------------

            coupon = match_coupon(

                title,

                price,

                public_products
            )

            # SEM CUPOM CONFIRMADO:
            # não entra como oportunidade.
            if not coupon:

                continue

            calculation = (
                calculate_coupon(
                    price,
                    coupon
                )
            )

            if not calculation:

                continue

            coupon_final = (
                calculation[
                    "final"
                ]
            )

            offer = {

                "product_id":
                    product_id,

                "item_id":
                    item[
                        "item_id"
                    ],

                "title":
                    title,

                "model":
                    model_name(
                        title
                    ),

                "image":
                    image,

                "category_name":
                    (
                        info.get(
                            "category_name"
                        )
                        or "Categoria"
                    ),

                "permalink":
                    (
                        item.get(
                            "permalink"
                        )
                        or product.get(
                            "permalink"
                        )
                        or (
                            "https://www.mercadolivre.com.br/p/"
                            + str(product_id)
                        )
                    ),

                "price":
                    price,

                "original_price":
                    original,

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

                "total_price":
                    total,

                "coupon":
                    coupon,

                "coupon_discount":
                    calculation[
                        "discount"
                    ],

                "coupon_final_price":
                    coupon_final,

                "coupon_effective_percent":
                    calculation[
                        "effective_percent"
                    ],

                "coupon_confirmed":
                    True,

                "coupon_source":
                    coupon.get(
                        "source"
                    ),

                "coupon_match_score":
                    coupon.get(
                        "match_score"
                    ),

                "relevance":
                    score,
            }

            # ------------------------------------------------
            # MELHOR VENDEDOR PARA O MESMO PRODUTO
            # ------------------------------------------------

            if best_offer is None:

                best_offer = offer

            else:

                new_key = (

                    offer[
                        "coupon_discount"
                    ],

                    offer[
                        "coupon_effective_percent"
                    ],

                    -offer[
                        "coupon_final_price"
                    ],

                    1
                    if offer[
                        "free_shipping"
                    ]
                    else 0,
                )

                old_key = (

                    best_offer[
                        "coupon_discount"
                    ],

                    best_offer[
                        "coupon_effective_percent"
                    ],

                    -best_offer[
                        "coupon_final_price"
                    ],

                    1
                    if best_offer[
                        "free_shipping"
                    ]
                    else 0,
                )

                if new_key > old_key:

                    best_offer = offer

        # ----------------------------------------------------
        # PRODUTO
        # ----------------------------------------------------

        if best_offer:

            groups[
                product_id
            ] = {

                "product_id":
                    product_id,

                "title":
                    title,

                "model":
                    model_name(
                        title
                    ),

                "image":
                    image,

                "category_name":
                    best_offer[
                        "category_name"
                    ],

                "offer":
                    best_offer,
            }

    # --------------------------------------------------------
    # ORDENA
    # --------------------------------------------------------

    models = list(
        groups.values()
    )

    models.sort(

        key=lambda x: (

            -x[
                "offer"
            ][
                "coupon_discount"
            ],

            -x[
                "offer"
            ][
                "coupon_effective_percent"
            ],

            x[
                "offer"
            ][
                "coupon_final_price"
            ],

            0
            if x[
                "offer"
            ][
                "free_shipping"
            ]
            else 1,
        )
    )

    # --------------------------------------------------------
    # SALVA
    # --------------------------------------------------------

    conn = get_db()

    conn.execute(
        "DELETE FROM ofertas"
    )

    conn.commit()

    conn.close()

    for model in models:

        offer = model[
            "offer"
        ]

        conn = get_db()

        conn.execute("""
            INSERT INTO ofertas(

                product_id,
                item_id,
                title,
                permalink,

                price,
                original_price,
                discount,

                seller_id,
                image,

                category_name,

                condition,
                listing_type_id,

                free_shipping,
                shipping_cost,

                total_price,

                coupon_code,
                coupon_type,
                coupon_value,
                coupon_discount,
                coupon_final_price,

                coupon_confirmed,
                coupon_source

            )
            VALUES(
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?,
                ?,?
            )
        """, (

            offer[
                "product_id"
            ],

            offer[
                "item_id"
            ],

            offer[
                "title"
            ],

            offer[
                "permalink"
            ],

            offer[
                "price"
            ],

            offer[
                "original_price"
            ],

            offer[
                "discount"
            ],

            offer[
                "seller_id"
            ],

            offer[
                "image"
            ],

            offer[
                "category_name"
            ],

            offer[
                "condition"
            ],

            None,

            1
            if offer[
                "free_shipping"
            ]
            else 0,

            offer[
                "shipping_cost"
            ],

            offer[
                "total_price"
            ],

            offer[
                "coupon"
            ][
                "label"
            ],

            offer[
                "coupon"
            ][
                "type"
            ],

            offer[
                "coupon"
            ][
                "value"
            ],

            offer[
                "coupon_discount"
            ],

            offer[
                "coupon_final_price"
            ],

            1,

            offer[
                "coupon_source"
            ],
        ))

        conn.commit()

        conn.close()

    # --------------------------------------------------------
    # ESTATÍSTICAS
    # --------------------------------------------------------

    offers = [
        x[
            "offer"
        ]
        for x in models
    ]

    return {

        "stats": {

            "produtos":
                len(
                    models
                ),

            "cupons":
                len(
                    offers
                ),

            "maior_desconto":
                brl(
                    max(
                        [
                            x[
                                "coupon_discount"
                            ]
                            for x in offers
                        ]
                        or [0]
                    )
                ),

            "menor_preco_final":
                brl(
                    min(
                        [
                            x[
                                "coupon_final_price"
                            ]
                            for x in offers
                        ]
                        or [0]
                    )
                ),
        },

        "modelos":
            models,

        "ofertas":
            offers,
    }


# ============================================================
# API CAÇAR
# ============================================================

@app.route(
    "/api/cacar"
)
def api_cacar():

    if not access_token():

        return jsonify({

            "erro":
                "Mercado Livre não conectado."

        }), 401

    categories = request.args.getlist(
        "categoria"
    )

    # Compatibilidade
    if not categories:

        single = request.args.get(
            "categoria",
            ""
        ).strip()

        if single:

            categories = [
                single
            ]

    if not categories:

        categories = list(
            CATALOG.keys()
        )

    categories = [
        x
        for x in categories
        if x in CATALOG
    ]

    # --------------------------------------------------------
    # QUERIES SOMENTE DAS CATEGORIAS ESCOLHIDAS
    # --------------------------------------------------------

    queries = []

    for category in categories:

        queries.extend(
            CATALOG.get(
                category,
                []
            )
        )

    # Evita uma consulta absurda.
    queries = queries[:24]

    try:

        result = scan_queries(
            queries
        )

        result[
            "categorias"
        ] = categories

        result[
            "queries"
        ] = queries

        return jsonify(
            json_safe(
                result
            )
        )

    except Exception as e:

        print(
            "[ERRO CAÇADOR]",
            repr(e)
        )

        return jsonify({

            "erro":
                str(e)

        }), 500


# ============================================================
# OFERTAS
# ============================================================

@app.route(
    "/api/ofertas"
)
def api_ofertas():

    conn = get_db()

    rows = conn.execute("""

        SELECT *

        FROM ofertas

        ORDER BY
            coupon_discount DESC,
            coupon_final_price ASC

    """).fetchall()

    conn.close()

    return jsonify({

        "ofertas": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# CUPONS
# ============================================================

@app.route(
    "/api/cupons"
)
def api_cupons():

    conn = get_db()

    rows = conn.execute("""

        SELECT *

        FROM cupons

        WHERE active=1

        ORDER BY
            updated_at DESC

    """).fetchall()

    conn.close()

    return jsonify({

        "cupons": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# WHATSAPP
# ============================================================

def generate_whatsapp(
    offer,
    affiliate_link=""
):

    title = offer.get(
        "title",
        "Produto"
    )

    price = safe_float(
        offer.get(
            "price"
        )
    )

    original = offer.get(
        "original_price"
    )

    coupon = offer.get(
        "coupon"
    )

    discount = safe_float(
        offer.get(
            "coupon_discount"
        )
    )

    final_price = safe_float(
        offer.get(
            "coupon_final_price"
        )
    )

    lines = [

        "🔥 OFERTA ENCONTRADA!",

        "",

        f"🛍️ {title}",

        "",
    ]

    if original:

        lines.append(
            f"De: {brl(original)}"
        )

    lines.append(
        f"💰 Preço: {brl(price)}"
    )

    if offer.get(
        "discount",
        0
    ):

        lines.append(
            f"🏷️ {offer['discount']}% OFF"
        )

    if offer.get(
        "free_shipping"
    ):

        lines.append(
            "🚚 Frete grátis"
        )

    if coupon:

        lines.extend([

            "",

            "🎟️ CUPOM: "
            + coupon[
                "label"
            ],

            "💸 Desconto do cupom: "
            + brl(discount),

            "🔥 PREÇO ESTIMADO COM CUPOM: "
            + brl(final_price),

            "",

            "⚠️ Confirme a aplicação "
            "do cupom no checkout.",
        ])

    lines.extend([

        "",

        "🛒 PEGAR OFERTA:",

        affiliate_link
        or offer.get(
            "permalink",
            ""
        ),
    ])

    return "\n".join(
        lines
    )


@app.route(
    "/api/gerar-anuncio"
)
def api_gerar_anuncio():

    title = request.args.get(
        "title",
        "Produto"
    )

    price = safe_float(
        request.args.get(
            "price"
        )
    )

    original = safe_float(
        request.args.get(
            "original_price"
        )
    )

    discount = safe_float(
        request.args.get(
            "discount"
        )
    )

    coupon_label = request.args.get(
        "cupom",
        ""
    )

    coupon_discount = safe_float(
        request.args.get(
            "coupon_discount"
        )
    )

    final_price = safe_float(
        request.args.get(
            "final"
        )
    )

    link = request.args.get(
        "link",
        ""
    )

    offer = {

        "title":
            title,

        "price":
            price,

        "original_price":
            original
            or None,

        "discount":
            discount,

        "coupon": (
            {
                "label":
                    coupon_label
            }
            if coupon_label
            else None
        ),

        "coupon_discount":
            coupon_discount,

        "coupon_final_price":
            final_price,

        "free_shipping":
            request.args.get(
                "shipping_free"
            ) == "1",

        "permalink":
            link,
    }

    text = generate_whatsapp(
        offer,

        request.args.get(
            "affiliate_link",
            ""
        )
    )

    return jsonify({

        "ok":
            True,

        "anuncio":
            text,
    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

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
            ),

        "redirect_uri":
            ML_REDIRECT_URI,

    }

    if data:

        result[
            "usuario"
        ] = {

            "id":
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
                ),
        }

    if access_token():

        me, status = ml_get(
            "/users/me"
        )

        result[
            "users_me"
        ] = {

            "status":
                status,

            "resposta":
                me,
        }

    return jsonify(
        result
    )


# ============================================================
# TESTE DE PRODUTO
# ============================================================

@app.route(
    "/mercadolivre/teste-produto-itens"
)
def teste_produto_itens():

    product_id = request.args.get(
        "product_id",
        "MLB58793248"
    )

    data, status = ml_get(

        f"/products/{product_id}/items"
    )

    return jsonify({

        "product_id":
            product_id,

        "status_http":
            status,

        "resposta":
            data,

    }), status


@app.route(
    "/mercadolivre/teste-busca-produtos"
)
def teste_busca_produtos():

    query = request.args.get(
        "q",
        "smartphone"
    )

    data, status = ml_get(

        "/products/search",

        {

            "site_id":
                SITE_ID,

            "q":
                query,

            "status":
                "active",

            "limit":
                10,

            "offset":
                0,
        }
    )

    return jsonify({

        "query":
            query,

        "status_http":
            status,

        "resposta":
            data,

    }), status


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health"
)
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

        "categorias":
            len(
                CATALOG
            ),

        "produto_minimo":
            MIN_PRODUCT_PRICE,

        "busca_produtos":
            "products/search",

        "itens":
            "products/{product_id}/items",

        "cupom":
            "associacao_por_produto",

    })


# ============================================================
# HOME
# ============================================================

HTML = r"""
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
Caçador de Ofertas
</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    background: #f4f5f7;

    color: #222;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

.container {

    max-width: 1100px;

    margin: auto;

    padding: 18px;
}

.card {

    background: white;

    border-radius: 18px;

    padding: 20px;

    margin-bottom: 18px;

    box-shadow:
        0 5px 20px
        rgba(0,0,0,.06);
}

h1 {

    margin: 0 0 8px;

    font-size: 29px;
}

h2 {

    margin-top: 0;
}

.subtitle {

    color: #777;
}

button,
a.button {

    border: 0;

    border-radius: 12px;

    padding: 13px 17px;

    font-weight: bold;

    cursor: pointer;

    text-decoration: none;

    display: inline-block;
}

.primary {

    background: #3483fa;

    color: white;
}

.login {

    background: #ffe600;

    color: #222;
}

.select-all {

    background: #111827;

    color: white;

    width: 100%;

    margin-bottom: 12px;
}

.category-grid {

    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(180px,1fr)
        );

    gap: 10px;
}

.category {

    border: 2px solid #ddd;

    border-radius: 14px;

    padding: 15px;

    background: white;

    cursor: pointer;

    transition: .15s;

    text-align: left;
}

.category.active {

    border-color: #3483fa;

    background: #eef5ff;
}

.category input {

    display: none;
}

.category-title {

    font-weight: bold;

    font-size: 16px;
}

.stats {

    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(150px,1fr)
        );

    gap: 10px;
}

.stat {

    background: #f3f4f6;

    padding: 15px;

    border-radius: 12px;
}

.stat b {

    display: block;

    font-size: 23px;

    margin-top: 6px;
}

.status {

    padding: 12px;

    background: #eef8f0;

    border-radius: 10px;

    color: #24713c;
}

.product {

    border: 2px solid #eee;

    border-radius: 16px;

    padding: 16px;

    margin-bottom: 15px;
}

.product-head {

    display: flex;

    gap: 15px;

    align-items: center;
}

.product-image {

    width: 90px;

    height: 90px;

    object-fit: contain;

    background: #fafafa;

    border-radius: 12px;
}

.product-title {

    font-size: 18px;

    font-weight: bold;
}

.category-tag {

    display: inline-block;

    background: #eef4ff;

    color: #3483fa;

    padding: 5px 8px;

    border-radius: 7px;

    font-size: 11px;

    margin-top: 6px;
}

.offer {

    margin-top: 15px;

    background: #fafafa;

    border: 1px solid #eee;

    border-radius: 13px;

    padding: 15px;
}

.price {

    font-size: 25px;

    font-weight: bold;
}

.old {

    color: #888;

    text-decoration:
        line-through;
}

.green {

    color: #008a3e;

    font-weight: bold;

    margin-top: 5px;
}

.coupon {

    background: #fff8d6;

    border: 2px dashed #d1aa00;

    border-radius: 13px;

    padding: 13px;

    margin-top: 12px;
}

.coupon-title {

    font-weight: bold;

    font-size: 18px;
}

.final {

    background: #eaf8ef;

    color: #008a3e;

    padding: 12px;

    border-radius: 10px;

    margin-top: 10px;

    font-size: 23px;

    font-weight: bold;
}

.warning {

    color: #777;

    font-size: 12px;

    margin-top: 8px;
}

.actions {

    display: flex;

    gap: 8px;

    flex-wrap: wrap;

    margin-top: 14px;
}

.actions a,
.actions button {

    padding: 11px 13px;

    border-radius: 9px;

    font-size: 13px;
}

.view {

    background: #3483fa;

    color: white;

    text-decoration: none;
}

.whatsapp {

    background: #16a34a;

    color: white;
}

input {

    width: 100%;

    padding: 12px;

    border-radius: 10px;

    border: 1px solid #ddd;

    margin-top: 8px;
}

textarea {

    width: 100%;

    min-height: 180px;

    margin-top: 10px;

    padding: 12px;

    border: 1px solid #ddd;

    border-radius: 10px;
}

</style>

</head>

<body>

<div class="container">


<div class="card">

    <h1>
        🛒 Caçador de Ofertas
    </h1>

    <p class="subtitle">
        Encontre produtos das categorias
        escolhidas e mostre somente ofertas
        com cupom associado.
    </p>

    {% if conectado %}

        <div class="status">

            🟢 Mercado Livre conectado

            {% if nickname %}
                — <b>{{ nickname }}</b>
            {% endif %}

        </div>

        <br>

        <a
            href="/mercadolivre/logout"
            class="button"
        >
            Desconectar
        </a>

    {% else %}

        <a
            href="/mercadolivre/login"
            class="button login"
        >
            🔗 Conectar Mercado Livre
        </a>

    {% endif %}

</div>


<div class="card">

    <h2>
        🎯 Categorias
    </h2>

    <button
        class="select-all"
        onclick="selectAll()"
    >
        ☑️ Selecionar todas
    </button>

    <div class="category-grid">

        {% for category in categorias %}

        <label
            class="category"
        >

            <input
                type="checkbox"
                value="{{ category }}"
                onchange="updateCategory(this)"
            >

            <div class="category-title">
                {{ category }}
            </div>

        </label>

        {% endfor %}

    </div>

    <br>

    <button
        class="primary"
        style="width:100%"
        onclick="cacar()"
    >
        🔎 CAÇAR OFERTAS
    </button>

    <p
        id="status"
        class="subtitle"
    >
        Selecione as categorias desejadas.
    </p>

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
        🏆 Oportunidades encontradas
    </h2>

    <div id="results">

        <p class="subtitle">
            As ofertas aparecerão aqui.
        </p>

    </div>

</div>


<div class="card">

    <a
        href="/api/cupons"
        target="_blank"
    >
        🎟️ Cupons identificados
    </a>

    <br><br>

    <a
        href="/mercadolivre/diagnostico"
        target="_blank"
    >
        🧪 Diagnóstico Mercado Livre
    </a>

    <br><br>

    <a
        href="/health"
        target="_blank"
    >
        ❤️ Health
    </a>

</div>


</div>


<script>

let selectedCategories = [];


function updateCategory(
    checkbox
) {

    const label =
        checkbox.closest(
            ".category"
        );

    if (
        checkbox.checked
    ) {

        label.classList.add(
            "active"
        );

        if (
            !selectedCategories.includes(
                checkbox.value
            )
        ) {

            selectedCategories.push(
                checkbox.value
            );
        }

    } else {

        label.classList.remove(
            "active"
        );

        selectedCategories =
            selectedCategories.filter(
                x =>
                    x !== checkbox.value
            );
    }

    document.getElementById(
        "status"
    ).textContent =
        selectedCategories.length
        + " categoria(s) selecionada(s).";
}


function selectAll() {

    const boxes =
        document.querySelectorAll(
            ".category input"
        );

    const shouldSelect =
        selectedCategories.length
        !== boxes.length;

    boxes.forEach(
        box => {

            box.checked =
                shouldSelect;

            updateCategory(
                box
            );

        }
    );
}


async function cacar() {

    if (
        selectedCategories.length
        === 0
    ) {

        alert(
            "Selecione pelo menos uma categoria."
        );

        return;
    }

    document.getElementById(
        "status"
    ).textContent =
        "🔎 Buscando produtos e cruzando cupons...";


    const params =
        new URLSearchParams();


    selectedCategories.forEach(
        category => {

            params.append(
                "categoria",
                category
            );

        }
    );


    try {

        const response =
            await fetch(
                "/api/cacar?"
                + params.toString()
            );


        const data =
            await response.json();


        if (
            !response.ok
        ) {

            throw new Error(
                data.erro
                || "Erro na busca."
            );
        }


        render(
            data
        );


        document.getElementById(
            "status"
        ).textContent =
            "✅ Busca concluída.";


    } catch (error) {

        document.getElementById(
            "status"
        ).textContent =
            "❌ "
            + error.message;

    }
}


function brl(value) {

    return Number(
        value || 0
    ).toLocaleString(
        "pt-BR",
        {
            style:
                "currency",

            currency:
                "BRL"
        }
    );
}


function esc(value) {

    return String(
        value || ""
    ).replace(
        /[&<>"']/g,
        function(char) {

            return {

                "&":
                    "&amp;",

                "<":
                    "&lt;",

                ">":
                    "&gt;",

                '"':
                    "&quot;",

                "'":
                    "&#039;"

            }[char];

        }
    );
}


function render(
    data
) {

    const stats =
        data.stats || {};


    document.getElementById(
        "stats"
    ).innerHTML = `

        <div class="stat">

            Produtos

            <b>
                ${stats.produtos || 0}
            </b>

        </div>


        <div class="stat">

            Cupons

            <b>
                ${stats.cupons || 0}
            </b>

        </div>


        <div class="stat">

            Maior desconto

            <b>
                ${stats.maior_desconto || "R$ 0,00"}
            </b>

        </div>


        <div class="stat">

            Menor preço final

            <b>
                ${stats.menor_preco_final || "R$ 0,00"}
            </b>

        </div>

    `;


    const models =
        data.modelos || [];


    if (
        models.length === 0
    ) {

        document.getElementById(
            "results"
        ).innerHTML = `

            <p class="subtitle">

                Nenhuma oportunidade com
                cupom associado foi encontrada
                nas categorias selecionadas.

            </p>

        `;

        return;
    }


    document.getElementById(
        "results"
    ).innerHTML =
        models
        .map(
            (
                model,
                index
            ) =>
                renderProduct(
                    model,
                    index
                )
        )
        .join("");
}


function renderProduct(
    model,
    index
) {

    const offer =
        model.offer;


    const coupon =
        offer.coupon;


    return `

        <div class="product">


            <div class="product-head">

                ${
                    model.image
                    ?

                    `
                    <img
                        class="product-image"
                        src="${esc(
                            model.image
                        )}"
                    >
                    `

                    :

                    ""
                }


                <div>

                    <div
                        class="product-title"
                    >

                        ${esc(
                            model.title
                        )}

                    </div>


                    <div
                        class="category-tag"
                    >

                        ${esc(
                            model.category_name
                        )}

                    </div>

                </div>

            </div>


            <div class="offer">


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

                    ""
                }


                <div class="price">

                    ${brl(
                        offer.price
                    )}

                </div>


                ${
                    offer.discount > 0
                    ?

                    `
                    <div class="green">

                        🔥
                        ${offer.discount}%
                        OFF direto no produto

                    </div>
                    `

                    :

                    ""
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

                    ""
                }


                <div class="coupon">


                    <div
                        class="coupon-title"
                    >

                        🎟️ CUPOM:
                        ${esc(
                            coupon.label
                        )}

                    </div>


                    ${
                        coupon.type === "percent"
                        ?

                        `
                        <div>

                            🔥
                            ${coupon.value}%
                            OFF

                        </div>
                        `

                        :

                        `
                        <div>

                            💰
                            ${brl(
                                coupon.value
                            )}
                            OFF

                        </div>
                        `
                    }


                    <div>

                        💸 Desconto:

                        <b>
                            ${brl(
                                offer.coupon_discount
                            )}
                        </b>

                    </div>


                    <div class="final">

                        💥 Preço com cupom:

                        ${brl(
                            offer.coupon_final_price
                        )}

                    </div>


                    <div class="warning">

                        ✓ Cupom associado
                        a esta oferta pública.

                        <br>

                        ⚠️ Confirme a aplicação
                        no checkout.

                    </div>

                </div>


                <div
                    class="actions"
                >

                    <a
                        class="view"
                        href="${esc(
                            offer.permalink
                        )}"
                        target="_blank"
                    >
                        🛒 Ver produto
                    </a>


                    <button
                        class="whatsapp"
                        onclick='gerarWhatsApp(
                            ${JSON.stringify(
                                offer
                            )}
                        )'
                    >
                        📲 Gerar WhatsApp
                    </button>

                </div>


                <div
                    id="whatsapp-${index}"
                ></div>


            </div>

        </div>

    `;
}


async function gerarWhatsApp(
    offer
) {

    const params =
        new URLSearchParams({

            title:
                offer.title,

            price:
                offer.price,

            original_price:
                offer.original_price
                || "",

            discount:
                offer.discount
                || 0,

            cupom:
                offer.coupon
                ? offer.coupon.label
                : "",

            coupon_discount:
                offer.coupon_discount
                || 0,

            final:
                offer.coupon_final_price
                || 0,

            shipping_free:
                offer.free_shipping
                ? "1"
                : "0",

            link:
                offer.permalink
                || "",

            affiliate_link:
                offer.permalink
                || "",
        });


    const response =
        await fetch(
            "/api/gerar-anuncio?"
            + params.toString()
        );


    const data =
        await response.json();


    const box =
        document.createElement(
            "div"
        );


    box.className =
        "card";


    box.innerHTML = `

        <h3>
            📲 Oferta para WhatsApp
        </h3>

        <textarea
            id="whatsapp-text"
        >${esc(
            data.anuncio
        )}</textarea>

        <button
            class="whatsapp"
            onclick="copiarWhatsApp()"
        >
            📋 Copiar texto
        </button>

    `;


    document
        .getElementById(
            "results"
        )
        .prepend(
            box
        );


    box.scrollIntoView({
        behavior:
            "smooth"
    });
}


async function copiarWhatsApp() {

    const textarea =
        document.getElementById(
            "whatsapp-text"
        );


    if (!textarea)
        return;


    try {

        await navigator
            .clipboard
            .writeText(
                textarea.value
            );


        alert(
            "✅ Texto copiado!"
        );

    } catch (error) {

        textarea.select();

        document.execCommand(
            "copy"
        );

        alert(
            "✅ Texto copiado!"
        );
    }
}

</script>

</body>

</html>
"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    data = tokens()

    return render_template_string(

        HTML,

        conectado=bool(
            access_token()
        ),

        nickname=(
            data.get(
                "nickname"
            )
            if data
            else None
        ),

        categorias=list(
            CATALOG.keys()
        ),
    )


# ============================================================
# 404
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({

        "erro":
            "Rota não encontrada.",

        "rota":
            request.path,

    }), 404


# ============================================================
# 500
# ============================================================

@app.errorhandler(500)
def server_error(error):

    return jsonify({

        "erro":
            "Erro interno.",

        "detalhes":
            str(error),

    }), 500


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print(
        "======================================"
    )

    print(
        "CAÇADOR DE OFERTAS"
    )

    print(
        "Busca: products/search"
    )

    print(
        "Itens: products/{product_id}/items"
    )

    print(
        "Cupom: associado ao produto"
    )

    print(
        "Preço mínimo:",
        brl(
            MIN_PRODUCT_PRICE
        )
    )

    print(
        "======================================"
    )

    app.run(

        host="0.0.0.0",

        port=int(
            os.getenv(
                "PORT",
                "8080"
            )
        ),

        debug=False,
    )