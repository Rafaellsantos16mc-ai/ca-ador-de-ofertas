import os
import sqlite3
import secrets
import hashlib
import base64
import time
import re
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
            "ALTER TABLE cupons ADD COLUMN fixed_discount REAL DEFAULT 0"
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

    if isinstance(value, (str, int, float, bool)):
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
        value = float(value)

        return (
            f"R$ {value:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except Exception:
        return "R$ 0,00"


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

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def calc_discount(price, original):

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


def calc_total(price, shipping):

    try:

        price = float(price)

        if shipping is None:
            return price

        return round(
            price + float(shipping),
            2
        )

    except Exception:
        return float(price or 0)


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


def extract_specs(title):

    if not title:
        return []

    title = str(title)

    result = []

    patterns = [
        r"\b\d+(?:GB|TB)\b",
        r"\b\d+\s*GB\s*(?:RAM|MEMORIA)\b",
        r"\b(?:2G|3G|4G|5G)\b"
    ]

    for pattern in patterns:

        for value in re.findall(
            pattern,
            title,
            flags=re.I
        ):

            value = re.sub(
                r"\s+",
                " ",
                value.upper()
            ).strip()

            if value not in result:
                result.append(value)

    if re.search(
        r"dual\s*sim",
        title,
        flags=re.I
    ):
        result.append("Dual SIM")

    if re.search(
        r"\bnfc\b",
        title,
        flags=re.I
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


def profile_for(query):

    q = norm(query)

    if any(
        x in q
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

    if "perfume" in q:
        return "perfume"

    if any(
        x in q
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
        x in q
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

    strong = PROFILES.get(
        profile,
        {}
    ).get(
        "strong",
        []
    )

    bad = PROFILES.get(
        profile,
        {}
    ).get(
        "bad",
        []
    )

    for word in strong:

        if word in title:
            score += 30

    for word in query.split():

        if len(word) >= 3 and word in title:
            score += 8

    for word in bad:

        if word in title:
            score -= 80

    return score


# ============================================================
# OAUTH
# ============================================================

def create_pkce():

    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(
        verifier.encode()
    ).digest()

    challenge = base64.urlsafe_b64encode(
        digest
    ).decode().rstrip("=")

    return verifier, challenge


def get_tokens():

    conn = get_db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id=1"
    ).fetchone()

    conn.close()

    return dict(row) if row else None


def save_tokens(data, user=None):

    old = get_tokens() or {}

    expires_in = int(
        data.get(
            "expires_in",
            21600
        )
    )

    conn = get_db()

    # CORREÇÃO:
    # O id é fixo como 1, então só existem 5
    # valores para os outros campos.
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

        data.get(
            "access_token"
        ),

        data.get(
            "refresh_token"
        ),

        int(time.time()) + expires_in,

        str(
            user.get("id")
        )
        if user and user.get("id")
        else old.get("user_id"),

        user.get("nickname")
        if user
        else old.get("nickname")
    ))

    conn.commit()
    conn.close()


def refresh_access_token():

    token = get_tokens()

    if not token:
        return None

    refresh_token = token.get(
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

            print(
                "[ERRO REFRESH]",
                response.status_code,
                response.text
            )

            return None

        data = response.json()

        save_tokens(
            data,

            {
                "id":
                    token.get(
                        "user_id"
                    ),

                "nickname":
                    token.get(
                        "nickname"
                    )
            }
        )

        return data.get(
            "access_token"
        )

    except Exception as e:

        print(
            "[ERRO REFRESH]",
            repr(e)
        )

        return None


def access_token():

    token = get_tokens()

    if not token:
        return None

    expires_at = int(
        token.get(
            "expires_at",
            0
        )
    )

    if (
        token.get("access_token")
        and time.time() <
        expires_at - 120
    ):

        return token[
            "access_token"
        ]

    return (
        refresh_access_token()
        or token.get(
            "access_token"
        )
    )


def ml_get(
    path,
    params=None
):

    token = access_token()

    if not token:

        return (
            {
                "error":
                    "Mercado Livre não conectado."
            },
            401,
            {}
        )

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
            dict(
                response.headers
            )
        )

    except requests.RequestException as e:

        return (
            {
                "error":
                    str(e)
            },
            500,
            {}
        )


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def ml_login():

    if not ML_CLIENT_ID:

        return jsonify({
            "erro":
                "ML_CLIENT_ID não configurado."
        }), 500

    verifier, challenge = create_pkce()

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
            "S256"
    }

    return redirect(
        ML_AUTH
        + "?"
        + urlencode(params)
    )


@app.route(
    "/mercadolivre/callback"
)
def ml_callback():

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
        or state !=
        session.get(
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

        if data.get(
            "access_token"
        ):

            me = requests.get(

                ML_API + "/users/me",

                headers={

                    "Authorization":
                        "Bearer "
                        + data[
                            "access_token"
                        ]
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


@app.route(
    "/mercadolivre/logout"
)
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
# BUSCA DE PRODUTOS
# ============================================================

def search_products(
    query,
    limit=20
):

    print(
        f"[BUSCA PRODUTOS] {query}"
    )

    data, status, _ = ml_get(

        "/products/search",

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
                0
        }
    )

    if status != 200:

        print(
            "[ERRO PRODUCTS SEARCH]",
            status,
            data
        )

        return []

    if not isinstance(
        data,
        dict
    ):

        return []

    results = data.get(
        "results",
        []
    )

    print(
        "[PRODUTOS ENCONTRADOS]",
        len(results)
    )

    return results


def get_product(
    product_id
):

    data, status, _ = ml_get(
        f"/products/{product_id}"
    )

    if status != 200:
        return None

    if not isinstance(
        data,
        dict
    ):
        return None

    return data


def get_product_items(
    product_id
):

    data, status, _ = ml_get(
        f"/products/{product_id}/items"
    )

    if status != 200:

        print(
            "[ERRO ITEMS]",
            product_id,
            status
        )

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

        return data.get(
            "results",
            []
        )

    return []


def normalize_item(
    item
):

    if not isinstance(
        item,
        dict
    ):

        return None

    item_id = item.get(
        "item_id"
    )

    if not item_id:
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

    shipping_cost = shipping.get(
        "cost"
    )

    if free_shipping:
        shipping_cost = 0

    return {

        "item_id":
            item_id,

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

        "permalink":
            item.get(
                "permalink"
            ),

        "free_shipping":
            free_shipping,

        "shipping_cost":
            shipping_cost
    }


# ============================================================
# COLETAR PRODUTOS
# ============================================================

def collect_products(
    queries
):

    products = {}

    for query in queries:

        results = search_products(
            query,
            limit=20
        )

        for item in results:

            if not isinstance(
                item,
                dict
            ):
                continue

            product_id = (
                item.get("id")
                or
                item.get("product_id")
            )

            if not product_id:
                continue

            title = (
                item.get("name")
                or
                item.get("title")
                or
                product_id
            )

            score = relevance(
                title,
                query
            )

            if score < 5:
                continue

            if product_id not in products:

                products[
                    product_id
                ] = {

                    "product_id":
                        product_id,

                    "title":
                        title,

                    "query":
                        query,

                    "category_id":
                        item.get(
                            "category_id"
                        ),

                    "category_name":
                        item.get(
                            "category_name"
                        ),

                    "score":
                        score
                }

            else:

                if score > products[
                    product_id
                ][
                    "score"
                ]:

                    products[
                        product_id
                    ][
                        "score"
                    ] = score

                    products[
                        product_id
                    ][
                        "query"
                    ] = query

    print(
        "[PRODUTOS ÚNICOS]",
        len(products)
    )

    return products


# ============================================================
# MONTAR RESULTADOS
# ============================================================

def build_offers(
    queries
):

    products = collect_products(
        queries
    )

    groups = {}

    for product_id, base in products.items():

        product_data = get_product(
            product_id
        )

        if not product_data:
            continue

        title = (
            product_data.get("name")
            or
            product_data.get("title")
            or
            base["title"]
        )

        pictures = (
            product_data.get(
                "pictures"
            )
            or []
        )

        image = None

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
                    or
                    first.get(
                        "secure_url"
                    )
                )

        permalink = (
            product_data.get(
                "permalink"
            )
            or
            f"https://www.mercadolivre.com.br/p/{product_id}"
        )

        items = get_product_items(
            product_id
        )

        if not items:
            continue

        valid_offers = []

        for raw_item in items:

            item = normalize_item(
                raw_item
            )

            if not item:
                continue

            try:

                price = float(
                    item["price"]
                )

            except Exception:

                continue

            if price < MIN_PRODUCT_PRICE:
                continue

            original_price = item.get(
                "original_price"
            )

            try:

                if original_price is not None:

                    original_price = float(
                        original_price
                    )

            except Exception:

                original_price = None

            seller_discount = calc_discount(
                price,
                original_price
            )

            shipping_cost = item.get(
                "shipping_cost"
            )

            total_price = calc_total(
                price,
                shipping_cost
            )

            offer = {

                "product_id":
                    product_id,

                "item_id":
                    item["item_id"],

                "title":
                    title,

                "modelo_nome":
                    model_name(
                        title
                    ),

                "especificacoes":
                    extract_specs(
                        title
                    ),

                "image":
                    image,

                "category_id":
                    base.get(
                        "category_id"
                    ),

                "category_name":
                    base.get(
                        "category_name"
                    ) or "",

                "query":
                    base.get(
                        "query"
                    ),

                "permalink":
                    item.get(
                        "permalink"
                    )
                    or
                    permalink,

                "price":
                    price,

                "original_price":
                    original_price,

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

                "listing_type_id":
                    item.get(
                        "listing_type_id"
                    ),

                "free_shipping":
                    item.get(
                        "free_shipping",
                        False
                    ),

                "shipping_cost":
                    shipping_cost,

                "shipping_known":
                    shipping_cost is not None,

                "total_price":
                    total_price,

                "relevance_score":
                    base.get(
                        "score",
                        0
                    ),

                "affiliate_link":
                    "",

                "extra_earnings":
                    0
            }

            valid_offers.append(
                offer
            )

        if not valid_offers:
            continue

        valid_offers.sort(
            key=lambda x: (

                x["price"],

                0
                if x["free_shipping"]
                else 1,

                x["shipping_cost"]
                if x["shipping_cost"]
                is not None
                else 999999
            )
        )

        best_offer = valid_offers[0]

        best_offer[
            "menor_preco_modelo"
        ] = True

        groups[
            product_id
        ] = {

            "product_id":
                product_id,

            "title":
                title,

            "modelo_nome":
                model_name(
                    title
                ),

            "especificacoes":
                extract_specs(
                    title
                ),

            "image":
                image,

            "category_name":
                base.get(
                    "category_name"
                ) or "",

            "ofertas":
                [
                    best_offer
                ]
        }

    models = list(
        groups.values()
    )

    models.sort(
        key=lambda group: (

            -group[
                "ofertas"
            ][0].get(
                "relevance_score",
                0
            ),

            group[
                "ofertas"
            ][0].get(
                "price",
                999999
            )
        )
    )

    models = models[:30]

    offers = [
        group[
            "ofertas"
        ][0]
        for group in models
    ]

    prices = [
        offer["price"]
        for offer in offers
        if offer.get(
            "price"
        ) is not None
    ]

    free_shipping_count = sum(
        1
        for offer in offers
        if offer.get(
            "free_shipping"
        )
    )

    discounted_count = sum(
        1
        for offer in offers
        if offer.get(
            "discount",
            0
        ) > 0
    )

    stats = {

        "produtos":
            len(offers),

        "com desconto":
            discounted_count,

        "frete grátis":
            free_shipping_count,

        "menor preço":
            brl(
                min(
                    prices,
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
            offers
    }


# ============================================================
# EXECUTAR BUSCA
# ============================================================

def run_search(
    category=None,
    manual_query=None
):

    if manual_query:

        queries = [
            manual_query
        ]

    elif category:

        queries = CATALOG.get(
            category,
            []
        )

    else:

        queries = []

        for category_queries in CATALOG.values():

            queries.extend(
                category_queries
            )

    # Mantém a busca controlada.
    queries = queries[:8]

    print(
        "======================================"
    )

    print(
        "[INICIANDO BUSCA DE PRODUTOS]"
    )

    print(
        "[QUERIES]",
        queries
    )

    print(
        "======================================"
    )

    result = build_offers(
        queries
    )

    print(
        "[BUSCA FINALIZADA]"
    )

    print(
        "[PRODUTOS]",
        len(
            result.get(
                "ofertas",
                []
            )
        )
    )

    return result


# ============================================================
# GERAR ANÚNCIO
# ============================================================

def generate_ad(
    offer,
    affiliate_link=""
):

    lines = [

        "🔥 OFERTA ENCONTRADA!",
        "",
        f"🛍️ {offer.get('title', 'Produto')}"
    ]

    if offer.get(
        "original_price"
    ):

        lines.append(
            f"💸 De: {brl(offer['original_price'])}"
        )

    lines.append(
        f"🔥 Por: {brl(offer.get('price', 0))}"
    )

    if offer.get(
        "discount",
        0
    ) > 0:

        lines.append(
            f"🏷️ {offer['discount']}% OFF"
        )

    if offer.get(
        "free_shipping"
    ):

        lines.append(
            "🚚 Frete grátis"
        )

    lines += [

        "",

        "🛒 PEGAR OFERTA:",

        affiliate_link
        or
        offer.get(
            "permalink",
            ""
        )
    ]

    return "\n".join(
        lines
    )


# ============================================================
# API BUSCAR
# ============================================================

@app.route(
    "/api/buscar"
)
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

    result = run_search(
        manual_query=query
    )

    return jsonify(
        json_safe(
            result
        )
    )


# ============================================================
# API CAÇAR
# ============================================================

@app.route(
    "/api/cacar"
)
def api_cacar():

    category = request.args.get(
        "categoria",
        ""
    ).strip()

    if category:

        result = run_search(
            category=category
        )

    else:

        result = run_search()

    return jsonify(
        json_safe(
            result
        )
    )


# ============================================================
# API ANÚNCIO
# ============================================================

@app.route(
    "/api/gerar-anuncio"
)
def api_gerar_anuncio():

    title = request.args.get(
        "title",
        "Produto"
    )

    price = request.args.get(
        "price",
        "0"
    )

    original_price = request.args.get(
        "original_price"
    )

    discount = request.args.get(
        "discount",
        "0"
    )

    shipping_free = (
        request.args.get(
            "shipping_free"
        )
        == "1"
    )

    affiliate_link = request.args.get(
        "affiliate_link",
        ""
    ).strip()

    offer = {

        "title":
            title,

        "price":
            float(
                price or 0
            ),

        "original_price":
            float(
                original_price
            )
            if original_price
            else None,

        "discount":
            float(
                discount or 0
            ),

        "free_shipping":
            shipping_free,

        "permalink":
            ""
    }

    return jsonify({

        "anuncio":
            generate_ad(
                offer,
                affiliate_link
            )
    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    token = get_tokens()

    conectado = bool(
        access_token()
    )

    result = {

        "configurado":
            bool(
                ML_CLIENT_ID
            ),

        "conectado":
            conectado
    }

    if token:

        result[
            "token_local"
        ] = {

            "user_id":
                token.get(
                    "user_id"
                ),

            "nickname":
                token.get(
                    "nickname"
                ),

            "expires_at":
                token.get(
                    "expires_at"
                )
        }

    if conectado:

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

    return jsonify(
        json_safe(
            result
        )
    )


# ============================================================
# TESTE PRODUTO
# ============================================================

@app.route(
    "/mercadolivre/teste-produto"
)
def teste_produto():

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


# ============================================================
# TESTE ITEMS
# ============================================================

@app.route(
    "/mercadolivre/teste-produto-itens"
)
def teste_produto_itens():

    product_id = request.args.get(
        "product_id",
        "MLB58793248"
    )

    data, status, _ = ml_get(
        f"/products/{product_id}/items"
    )

    return jsonify({

        "product_id":
            product_id,

        "status_http":
            status,

        "resposta":
            data
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

        "catalogo_categorias":
            len(
                CATALOG
            ),

        "fluxo":
            "products/search -> products/{id} -> products/{id}/items",

        "produto_minimo":
            MIN_PRODUCT_PRICE,

        "cupons":
            "fora do fluxo de busca"
    })


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
    box-sizing:border-box;
}

body{
    margin:0;
    background:#f4f5f7;
    font-family:Arial,sans-serif;
    color:#222;
}

.container{
    max-width:1050px;
    margin:auto;
    padding:18px;
}

.card{
    background:#fff;
    border-radius:16px;
    padding:18px;
    margin-bottom:18px;
    box-shadow:0 5px 20px #0000000c;
}

button,
input{
    width:100%;
    padding:13px;
    border-radius:10px;
    border:1px solid #ddd;
    font-size:15px;
}

button{
    border:0;
    background:#3483fa;
    color:#fff;
    cursor:pointer;
    margin-top:7px;
}

.login{
    background:#ffe600;
    color:#222;
}

.grid{
    display:grid;
    grid-template-columns:repeat(
        auto-fit,
        minmax(150px,1fr)
    );
    gap:9px;
}

.cat{
    background:#fff;
    border:1px solid #ddd;
    color:#222;
    text-align:left;
}

.stats{
    display:grid;
    grid-template-columns:repeat(
        auto-fit,
        minmax(120px,1fr)
    );
    gap:9px;
}

.stat{
    background:#f3f4f6;
    padding:13px;
    border-radius:11px;
}

.stat b{
    display:block;
    font-size:23px;
    margin-top:4px;
}

.modelo{
    border:2px solid #eee;
    border-radius:15px;
    padding:14px;
    margin:13px 0;
}

.mh{
    display:flex;
    gap:12px;
    align-items:center;
}

.mh img{
    width:85px;
    height:85px;
    object-fit:contain;
    background:#fafafa;
    border-radius:10px;
}

.title{
    font-size:18px;
    font-weight:bold;
}

.tag{
    display:inline-block;
    background:#eef4ff;
    color:#3483fa;
    border-radius:7px;
    padding:5px 8px;
    font-size:11px;
    margin:3px;
}

.seller{
    background:#fafafa;
    border:1px solid #eee;
    border-radius:12px;
    padding:12px;
    margin-top:10px;
}

.price{
    font-size:22px;
    font-weight:bold;
}

.green{
    color:#00a650;
    font-weight:bold;
}

.old{
    text-decoration:line-through;
    color:#777;
}

.small{
    font-size:12px;
    color:#666;
}

.status{
    background:#eef8f0;
    padding:10px;
    border-radius:9px;
}

.search-info{
    background:#eef4ff;
    border-radius:10px;
    padding:12px;
    margin-top:10px;
    color:#245da8;
}

.loading{
    text-align:center;
    padding:25px;
    font-size:16px;
}

.empty{
    padding:20px;
    text-align:center;
    color:#666;
}

a{
    color:#3483fa;
    font-weight:bold;
    text-decoration:none;
}

</style>

<script>

async function cacar(categoria){

    const status =
        document.getElementById(
            'status'
        );

    status.textContent =
        '🔄 Procurando produtos no Mercado Livre...';

    document.getElementById(
        'results'
    ).innerHTML =
        '<div class="loading">🔎 Buscando produtos...</div>';

    try{

        let url = '/api/cacar';

        if(categoria){

            url +=
                '?categoria='
                +
                encodeURIComponent(
                    categoria
                );
        }

        const response =
            await fetch(url);

        const data =
            await response.json();

        if(data.erro){

            throw new Error(
                data.erro
            );
        }

        render(data);

        status.textContent =
            '✅ Produtos encontrados.';

    }catch(error){

        console.error(error);

        status.textContent =
            '❌ Erro na busca.';

        document.getElementById(
            'results'
        ).innerHTML =
            '<div class="empty">❌ '
            +
            esc(error.message)
            +
            '</div>';
    }
}


async function buscar(){

    const q =
        document.getElementById(
            'q'
        ).value.trim();

    if(!q){

        alert(
            'Digite o produto que deseja procurar.'
        );

        return;
    }

    document.getElementById(
        'status'
    ).textContent =
        '🔄 Procurando produtos...';

    document.getElementById(
        'results'
    ).innerHTML =
        '<div class="loading">🔎 Buscando...</div>';

    try{

        const response =
            await fetch(
                '/api/buscar?q='
                +
                encodeURIComponent(q)
            );

        const data =
            await response.json();

        if(data.erro){

            throw new Error(
                data.erro
            );
        }

        render(data);

        document.getElementById(
            'status'
        ).textContent =
            '✅ Busca finalizada.';

    }catch(error){

        console.error(error);

        document.getElementById(
            'status'
        ).textContent =
            '❌ Erro na busca.';

        document.getElementById(
            'results'
        ).innerHTML =
            '<div class="empty">❌ '
            +
            esc(error.message)
            +
            '</div>';
    }
}


function render(data){

    const stats =
        document.getElementById(
            'stats'
        );

    stats.innerHTML =
        Object.entries(
            data.stats || {}
        )
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


    if(!models.length){

        document.getElementById(
            'results'
        ).innerHTML = `

            <div class="empty">

                😕 Nenhum produto acima
                de <b>R$ 69,90</b>
                foi encontrado.

            </div>

        `;

        return;
    }


    document.getElementById(
        'results'
    ).innerHTML =

        models.map(
            (model,index) => `

                <div class="modelo">

                    <div class="mh">

                        ${
                            model.image
                            ?
                            `
                            <img
                                src="${esc(model.image)}"
                                onerror="
                                    this.style.display='none'
                                "
                            >
                            `
                            :
                            ''
                        }

                        <div>

                            <span class="tag">

                                🛒 PRODUTO
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

                        </div>

                    </div>

                    ${
                        (
                            model.ofertas
                            || []
                        )
                        .map(
                            (offer,offerIndex) =>
                                renderOffer(
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
        .join('');
}


function renderOffer(
    offer,
    modelIndex,
    offerIndex
){

    const id =
        'offer_'
        +
        modelIndex
        +
        '_'
        +
        offerIndex;


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
                        color:white;
                    "
                >

                    🏆 MELHOR PREÇO

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
                    ${offer.discount}% OFF

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
                <div class="small">

                    🚚 Frete:
                    ${
                        offer.free_shipping
                        ?
                        'Grátis'
                        :
                        brl(
                            offer.shipping_cost
                        )
                    }

                </div>

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


            <div class="small">

                👤 Vendedor:
                ${
                    offer.seller_id
                    ||
                    'N/A'
                }

            </div>


            <br>


            <a
                href="${esc(
                    offer.permalink
                )}"
                target="_blank"
            >

                🛒 Ver produto

            </a>


            <input
                id="link_${id}"
                placeholder="Cole seu link de afiliado"
            >


            <button
                onclick='gerarAnuncio(
                    ${JSON.stringify(id)},
                    ${JSON.stringify(offer)}
                )'
            >

                📢 Gerar anúncio

            </button>


            <button
                id="copy_${id}"
                style="
                    display:none;
                    background:#ff8a00;
                "
                onclick="
                    copyAd('${id}')
                "
            >

                📋 Copiar oferta

            </button>


            <div
                id="ad_${id}"
                style="
                    display:none;
                    white-space:pre-wrap;
                    background:#f7f7f7;
                    padding:10px;
                    border-radius:9px;
                    margin-top:8px;
                    font-size:13px;
                "
            ></div>

        </div>

    `;
}


async function gerarAnuncio(
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
                offer.discount || 0,

            shipping_free:
                offer.free_shipping
                ?
                '1'
                :
                '0',

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
            +
            params.toString()
        );


    const data =
        await response.json();


    const ad =
        document.getElementById(
            'ad_' + id
        );

    ad.style.display =
        'block';

    ad.textContent =
        data.anuncio;

    document.getElementById(
        'copy_' + id
    ).style.display =
        'block';
}


async function copyAd(id){

    const element =
        document.getElementById(
            'ad_' + id
        );

    const text =
        element.textContent.trim();


    if(!text){

        alert(
            'Gere o anúncio primeiro.'
        );

        return;
    }


    try{

        if(
            navigator.clipboard
            &&
            window.isSecureContext
        ){

            await navigator.clipboard.writeText(
                text
            );

        }else{

            const textarea =
                document.createElement(
                    'textarea'
                );

            textarea.value =
                text;

            textarea.style.position =
                'fixed';

            textarea.style.opacity =
                '0';

            document.body.appendChild(
                textarea
            );

            textarea.focus();

            textarea.select();

            document.execCommand(
                'copy'
            );

            textarea.remove();
        }


        const button =
            document.getElementById(
                'copy_' + id
            );

        const old =
            button.textContent;

        button.textContent =
            '✅ Copiado!';


        setTimeout(
            () => {

                button.textContent =
                    old;

            },
            1500
        );

    }catch(error){

        alert(
            'Não foi possível copiar automaticamente.'
        );
    }
}


function brl(value){

    return 'R$ '
        +
        Number(
            value || 0
        ).toLocaleString(
            'pt-BR',
            {
                minimumFractionDigits:2,
                maximumFractionDigits:2
            }
        );
}


function esc(value){

    return String(
        value || ''
    ).replace(
        /[&<>"']/g,
        function(character){

            return {

                '&':'&amp;',
                '<':'&lt;',
                '>':'&gt;',
                '"':'&quot;',
                "'":'&#039;'

            }[character];

        }
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

            Primeiro vamos encontrar
            produtos reais no Mercado Livre.
            Os cupons serão adicionados
            depois.

        </p>


        {% if conectado %}

            <div class="status">

                🟢 Mercado Livre conectado

                {% if nickname %}

                    <br>

                    <b>
                        {{ nickname }}
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
            🛒 Procurar produtos
        </h2>

        <p class="small">

            Produtos a partir de
            R$ 69,90.

        </p>


        <div class="grid">

            {% for categoria in categorias %}

                <button
                    class="cat"
                    onclick="
                        cacar(
                            {{ categoria|tojson }}
                        )
                    "
                >

                    {{ categoria }}

                </button>

            {% endfor %}

        </div>


        <button
            onclick="cacar('')"
        >

            🚀 BUSCAR TODAS AS CATEGORIAS

        </button>


        <p
            id="status"
            class="small"
        >

            Escolha uma categoria
            para começar.

        </p>

    </div>


    <div class="card">

        <h2>
            🔎 Buscar produto
        </h2>


        <input
            id="q"
            placeholder="
                Ex: celular Samsung,
                perfume, furadeira...
            "
            onkeydown="
                if(event.key==='Enter')
                    buscar()
            "
        >


        <button
            onclick="buscar()"
        >

            🔎 Procurar produto

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
            🏆 Produtos encontrados
        </h2>


        <div class="search-info">

            💡 Nesta etapa mostramos
            somente os produtos.

            <br>

            🎟️ A parte dos cupons
            será feita depois.

        </div>


        <div id="results">

            <div class="empty">

                Faça uma busca para começar.

            </div>

        </div>

    </div>


    <div class="card">

        <a
            href="/mercadolivre/diagnostico"
            target="_blank"
        >

            🧪 Diagnóstico Mercado Livre

        </a>

        <br>
        <br>

        <a
            href="/health"
            target="_blank"
        >

            ❤️ Status da aplicação

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

    token = get_tokens()

    return render_template_string(

        HTML,

        conectado=
            bool(
                access_token()
            ),

        nickname=
            token.get(
                "nickname"
            )
            if token
            else None,

        categorias=
            list(
                CATALOG.keys()
            )
    )


# ============================================================
# BUSCA VIA URL
# ============================================================

@app.route(
    "/buscar"
)
def buscar_page():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:
        return redirect("/")

    result = run_search(
        manual_query=query
    )

    token = get_tokens()

    return render_template_string(

        HTML,

        conectado=
            bool(
                access_token()
            ),

        nickname=
            token.get(
                "nickname"
            )
            if token
            else None,

        categorias=
            list(
                CATALOG.keys()
            ),

        resultado=
            result
    )


# ============================================================
# CUPONS
# ============================================================

@app.route(
    "/api/cupons"
)
def api_cupons():

    return jsonify({

        "status":
            "aguardando segunda etapa",

        "mensagem":
            "Cupons estão fora do fluxo de busca de produtos.",

        "cupons":
            []
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

    port = int(
        os.getenv(
            "PORT",
            "8080"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )