import os
import re
import time
import sqlite3
import secrets
import hashlib
import base64
from urllib.parse import urlencode

import requests

from flask import (
    Flask,
    request,
    redirect,
    session,
    jsonify,
    render_template_string
)


# ============================================================
# CONFIG
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "cacador-ofertas-secret"
)

ML_API = "https://api.mercadolibre.com"

ML_AUTH = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN = (
    "https://api.mercadolibre.com/oauth/token"
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

SITE_ID = "MLB"

DB_FILE = "ofertas.db"

MIN_PRICE = 69.90

MAX_PRODUCTS = 60

PRODUCTS_PER_QUERY = 20

AUTO_REFRESH_MINUTES = 30


# ============================================================
# CATEGORIAS DE PRODUTOS DE ALTO GIRO
# ============================================================

CATEGORIES = {

    "📱 Celulares": [
        "celular",
        "smartphone",
        "iphone",
        "samsung galaxy",
        "motorola",
        "xiaomi"
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "headset",
        "smartwatch",
        "tablet",
        "caixa de som bluetooth",
        "power bank"
    ],

    "🏠 Casa": [
        "aspirador de pó",
        "liquidificador",
        "cafeteira",
        "ventilador",
        "ferro de passar",
        "secador de cabelo"
    ],

    "🍳 Cozinha": [
        "air fryer",
        "panela elétrica",
        "jogo de panelas",
        "cafeteira",
        "liquidificador",
        "sanduicheira"
    ],

    "🔧 Ferramentas": [
        "furadeira",
        "parafusadeira",
        "esmerilhadeira",
        "kit ferramentas",
        "chave de impacto",
        "maleta ferramentas"
    ],

    "🏋️ Academia": [
        "tenis corrida",
        "tenis academia",
        "roupa academia",
        "whey protein",
        "creatina",
        "legging"
    ],

    "🌸 Beleza": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "secador",
        "chapinha",
        "kit perfume"
    ],

    "🚗 Automotivo": [
        "compressor automotivo",
        "aspirador automotivo",
        "suporte celular carro",
        "carregador automotivo",
        "tapete automotivo",
        "ferramentas automotivas"
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "mochila",
        "bolsa feminina",
        "oculos de sol",
        "relogio masculino"
    ]
}


# ============================================================
# BANCO
# ============================================================

def db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db()

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
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            description TEXT,
            discount_percent REAL,
            fixed_discount REAL,
            min_purchase REAL,
            max_discount REAL,
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

def brl(value):

    try:

        return (
            "R$ "
            +
            f"{float(value):,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except Exception:

        return "R$ 0,00"


def number(value):

    try:
        return float(value)
    except Exception:
        return None


def normalize(text):

    if not text:
        return ""

    text = str(text).lower()

    text = (
        text
        .replace("á", "a")
        .replace("à", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("ä", "a")
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("ë", "e")
        .replace("í", "i")
        .replace("ì", "i")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ó", "o")
        .replace("ò", "o")
        .replace("õ", "o")
        .replace("ô", "o")
        .replace("ö", "o")
        .replace("ú", "u")
        .replace("ù", "u")
        .replace("û", "u")
        .replace("ü", "u")
        .replace("ç", "c")
    )

    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def json_safe(value):

    if isinstance(value, dict):

        return {
            k: json_safe(v)
            for k, v in value.items()
        }

    if isinstance(value, list):

        return [
            json_safe(v)
            for v in value
        ]

    if isinstance(
        value,
        (
            str,
            int,
            float,
            bool
        )
    ) or value is None:

        return value

    return str(value)


# ============================================================
# OAUTH
# ============================================================

def get_tokens():

    conn = db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id=1"
    ).fetchone()

    conn.close()

    return dict(row) if row else None


def save_tokens(data, user=None):

    old = get_tokens() or {}

    conn = db()

    conn.execute("""
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

        (
            user.get("id")
            if user
            else old.get("user_id")
        ),

        (
            user.get("nickname")
            if user
            else old.get("nickname")
        )
    ))

    conn.commit()
    conn.close()


def pkce():

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


def refresh_access_token():

    tokens = get_tokens()

    if not tokens:
        return None

    refresh = tokens.get(
        "refresh_token"
    )

    if not refresh:
        return None

    try:

        r = requests.post(

            ML_TOKEN,

            data={

                "grant_type":
                    "refresh_token",

                "client_id":
                    ML_CLIENT_ID,

                "client_secret":
                    ML_CLIENT_SECRET,

                "refresh_token":
                    refresh
            },

            timeout=30
        )

        if r.status_code != 200:

            print(
                "[REFRESH]",
                r.status_code,
                r.text[:500]
            )

            return None

        data = r.json()

        save_tokens(
            data,
            {
                "id":
                    tokens.get(
                        "user_id"
                    ),

                "nickname":
                    tokens.get(
                        "nickname"
                    )
            }
        )

        return data.get(
            "access_token"
        )

    except Exception as e:

        print(
            "[REFRESH ERRO]",
            repr(e)
        )

        return None


def access_token():

    tokens = get_tokens()

    if not tokens:
        return None

    token = tokens.get(
        "access_token"
    )

    expires = (
        tokens.get(
            "expires_at"
        )
        or 0
    )

    if (
        token
        and
        time.time()
        <
        expires - 120
    ):

        return token

    return (
        refresh_access_token()
        or
        token
    )


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def ml_login():

    verifier, challenge = pkce()

    state = secrets.token_urlsafe(
        32
    )

    session[
        "ml_state"
    ] = state

    session[
        "ml_verifier"
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
        +
        "?"
        +
        urlencode(params)
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

    if not code:
        return "Código não recebido.", 400

    if state != session.get(
        "ml_state"
    ):

        return "State inválido.", 400

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
                        "ml_verifier"
                    )
            },

            timeout=30
        )

        if r.status_code != 200:

            return jsonify({

                "erro":
                    "Erro ao obter token.",

                "status":
                    r.status_code,

                "resposta":
                    r.text

            }), r.status_code

        token_data = r.json()

        user = None

        if token_data.get(
            "access_token"
        ):

            me = requests.get(

                ML_API +
                "/users/me",

                headers={

                    "Authorization":
                        "Bearer "
                        +
                        token_data[
                            "access_token"
                        ]
                },

                timeout=30
            )

            if me.status_code == 200:

                user = me.json()

        save_tokens(
            token_data,
            user
        )

        session.pop(
            "ml_state",
            None
        )

        session.pop(
            "ml_verifier",
            None
        )

        return redirect(
            "/"
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

    conn = db()

    conn.execute(
        "DELETE FROM oauth_tokens WHERE id=1"
    )

    conn.commit()
    conn.close()

    session.clear()

    return redirect("/")


# ============================================================
# REQUEST MERCADO LIVRE
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

    try:

        r = requests.get(

            url,

            headers={

                "Authorization":
                    "Bearer " +
                    token,

                "Accept":
                    "application/json",

                "User-Agent":
                    "CacadorDeOfertas/3.0"
            },

            params=params,

            timeout=30
        )

        try:
            data = r.json()
        except Exception:
            data = {}

        return data, r.status_code

    except Exception as e:

        print(
            "[ML GET]",
            repr(e)
        )

        return {}, 500


# ============================================================
# BUSCA CATÁLOGO
# ============================================================

def search_products(
    query
):

    data, status = ml_get(

        "/products/search",

        {

            "site_id":
                SITE_ID,

            "status":
                "active",

            "q":
                query,

            "limit":
                PRODUCTS_PER_QUERY
        }
    )

    if status != 200:
        return []

    return (
        data.get(
            "results",
            []
        )
        if isinstance(
            data,
            dict
        )
        else []
    )


def product_detail(
    product_id
):

    data, status = ml_get(
        f"/products/{product_id}"
    )

    if status != 200:
        return None

    return data


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

        return data.get(
            "results",
            []
        )

    return []


# ============================================================
# MAIS VENDIDOS
# ============================================================

def domain_discovery(
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

        if not isinstance(
            item,
            dict
        ):
            continue

        cid = (
            item.get(
                "category_id"
            )
            or
            item.get(
                "id"
            )
        )

        if cid:
            result.append(
                cid
            )

    return list(
        dict.fromkeys(
            result
        )
    )


def category_highlights(
    category_id
):

    data, status = ml_get(

        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    if status != 200:
        return []

    if not isinstance(
        data,
        dict
    ):
        return []

    content = data.get(
        "content",
        []
    )

    if not isinstance(
        content,
        list
    ):
        return []

    result = []

    for item in content:

        if not isinstance(
            item,
            dict
        ):
            continue

        pid = item.get(
            "id"
        )

        if not pid:
            continue

        result.append({

            "product_id":
                pid,

            "position":
                item.get(
                    "position"
                )
        })

    return result


# ============================================================
# NORMALIZA ITEM
# ============================================================

def normalize_item(
    item
):

    if not isinstance(
        item,
        dict
    ):
        return None

    item_id = (
        item.get(
            "item_id"
        )
        or
        item.get(
            "id"
        )
    )

    price = number(
        item.get(
            "price"
        )
    )

    if not item_id or price is None:
        return None

    original = number(
        item.get(
            "original_price"
        )
    )

    shipping = (
        item.get(
            "shipping"
        )
        or
        {}
    )

    free_shipping = bool(
        shipping.get(
            "free_shipping"
        )
    )

    return {

        "item_id":
            item_id,

        "seller_id":
            item.get(
                "seller_id"
            ),

        "price":
            price,

        "original_price":
            original,

        "free_shipping":
            free_shipping,

        "shipping_cost":
            (
                0
                if free_shipping
                else
                number(
                    shipping.get(
                        "cost"
                    )
                )
                or
                0
            ),

        "permalink":
            item.get(
                "permalink"
            )
    }


# ============================================================
# PONTUAÇÃO
# ============================================================

def product_score(
    position,
    price,
    discount,
    free_shipping
):

    score = 0

    # --------------------------------
    # Mais vendido
    # --------------------------------

    if position:

        position = int(
            position
        )

        if position == 1:
            score += 100

        elif position <= 3:
            score += 90

        elif position <= 5:
            score += 80

        elif position <= 10:
            score += 70

        elif position <= 20:
            score += 55

        elif position <= 50:
            score += 40

        else:
            score += 25

    # --------------------------------
    # Preço de giro
    # --------------------------------

    if 69.90 <= price <= 149.90:
        score += 25

    elif price <= 249.90:
        score += 20

    elif price <= 399.90:
        score += 15

    elif price <= 699.90:
        score += 10

    else:
        score += 5

    # --------------------------------
    # Desconto
    # --------------------------------

    if discount >= 50:
        score += 25

    elif discount >= 40:
        score += 22

    elif discount >= 30:
        score += 18

    elif discount >= 20:
        score += 14

    elif discount >= 10:
        score += 8

    # --------------------------------
    # Frete
    # --------------------------------

    if free_shipping:
        score += 15

    return score


# ============================================================
# PROCESSA PRODUTO
# ============================================================

def process_product(
    candidate
):

    pid = candidate[
        "product_id"
    ]

    data = product_detail(
        pid
    )

    if not data:
        return None

    title = (
        data.get(
            "name"
        )
        or
        data.get(
            "title"
        )
        or
        pid
    )

    # --------------------------------
    # IMAGEM
    # --------------------------------

    image = None

    pictures = (
        data.get(
            "pictures"
        )
        or
        []
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
                or
                first.get(
                    "secure_url"
                )
            )

    # --------------------------------
    # ITENS
    # --------------------------------

    raw_items = product_items(
        pid
    )

    items = []

    for raw in raw_items:

        item = normalize_item(
            raw
        )

        if item:
            items.append(
                item
            )

    # --------------------------------
    # BUY BOX
    # --------------------------------

    if not items:

        winner = data.get(
            "buy_box_winner"
        )

        if isinstance(
            winner,
            dict
        ):

            normalized = normalize_item(
                winner
            )

            if normalized:
                items.append(
                    normalized
                )

    if not items:
        return None

    # --------------------------------
    # FILTRO
    # --------------------------------

    items = [

        x
        for x in items

        if x.get(
            "price"
        ) is not None

        and
        x.get(
            "price"
        ) >= MIN_PRICE
    ]

    if not items:
        return None

    # --------------------------------
    # MELHOR OFERTA
    # --------------------------------

    items.sort(
        key=lambda x: (

            x["price"],

            0
            if x.get(
                "free_shipping"
            )
            else
            1
        )
    )

    item = items[0]

    price = item[
        "price"
    ]

    original = item.get(
        "original_price"
    )

    discount = 0

    if (
        original
        and
        original > price
    ):

        discount = round(
            (
                1 -
                price /
                original
            )
            *
            100,
            1
        )

    shipping = item.get(
        "shipping_cost"
    ) or 0

    total = round(
        price +
        shipping,
        2
    )

    score = product_score(

        candidate.get(
            "position"
        ),

        price,

        discount,

        item.get(
            "free_shipping"
        )
    )

    return {

        "product_id":
            pid,

        "item_id":
            item.get(
                "item_id"
            ),

        "seller_id":
            item.get(
                "seller_id"
            ),

        "title":
            title,

        "image":
            image,

        "category_name":
            candidate[
                "category_name"
            ],

        "category_id":
            candidate.get(
                "category_id"
            ),

        "query":
            candidate.get(
                "query"
            ),

        "position":
            candidate.get(
                "position"
            ),

        "price":
            price,

        "original_price":
            original,

        "discount":
            discount,

        "free_shipping":
            item.get(
                "free_shipping"
            ),

        "shipping_cost":
            shipping,

        "total_price":
            total,

        "score":
            score,

        "permalink":
            item.get(
                "permalink"
            )
            or
            data.get(
                "permalink"
            )
            or
            (
                "https://www.mercadolivre.com.br/p/"
                +
                pid
            ),

        "cupom":
            None,

        "desconto_cupom":
            0,

        "preco_com_cupom":
            None
    }


# ============================================================
# COLETA DE CANDIDATOS
# ============================================================

def collect_candidates():

    all_categories = {}

    diagnostics = {}

    for category_name, queries in CATEGORIES.items():

        candidates = {}

        total_search = 0

        total_best = 0

        # --------------------------------
        # 1. BUSCA POR MAIS VENDIDOS
        # --------------------------------

        for query in queries:

            domains = domain_discovery(
                query
            )

            for category_id in domains[:2]:

                highlights = category_highlights(
                    category_id
                )

                for h in highlights:

                    pid = h.get(
                        "product_id"
                    )

                    if not pid:
                        continue

                    if pid in candidates:
                        continue

                    candidates[
                        pid
                    ] = {

                        "product_id":
                            pid,

                        "category_name":
                            category_name,

                        "category_id":
                            category_id,

                        "query":
                            query,

                        "position":
                            h.get(
                                "position"
                            ),

                        "source":
                            "mais_vendidos"
                    }

                    total_best += 1

                    if len(
                        candidates
                    ) >= 14:

                        break

                if len(
                    candidates
                ) >= 14:

                    break

            if len(
                candidates
            ) >= 14:

                break

        # --------------------------------
        # 2. COMPLEMENTO DE CATÁLOGO
        # --------------------------------

        if len(
            candidates
        ) < 14:

            for query in queries:

                results = search_products(
                    query
                )

                total_search += len(
                    results
                )

                for result in results:

                    pid = result.get(
                        "id"
                    )

                    if not pid:
                        continue

                    if pid in candidates:
                        continue

                    candidates[
                        pid
                    ] = {

                        "product_id":
                            pid,

                        "category_name":
                            category_name,

                        "category_id":
                            result.get(
                                "category_id"
                            ),

                        "query":
                            query,

                        "position":
                            None,

                        "source":
                            "catalogo"
                    }

                    if len(
                        candidates
                    ) >= 18:

                        break

                if len(
                    candidates
                ) >= 18:

                    break

        all_categories[
            category_name
        ] = list(
            candidates.values()
        )

        diagnostics[
            category_name
        ] = {

            "candidatos":
                len(
                    candidates
                ),

            "mais_vendidos":
                total_best,

            "catalogo":
                total_search,

            "validos":
                0
        }

    return (
        all_categories,
        diagnostics
    )


# ============================================================
# CAÇADOR
# ============================================================

def run_scan():

    started = time.time()

    print(
        "\n"
        + "=" * 60
    )

    print(
        "🔥 CAÇADOR DE OFERTAS"
    )

    print(
        "🏆 PRIORIZANDO PRODUTOS DE ALTO GIRO"
    )

    print(
        "=" * 60
    )

    candidates_by_category, diagnostics = (
        collect_candidates()
    )

    final = []

    global_seen = set()

    # --------------------------------
    # PROCESSA CADA CATEGORIA
    # --------------------------------

    for category_name, candidates in (
        candidates_by_category.items()
    ):

        category_products = []

        for candidate in candidates:

            try:

                product = process_product(
                    candidate
                )

                if not product:
                    continue

                pid = product[
                    "product_id"
                ]

                if pid in global_seen:
                    continue

                category_products.append(
                    product
                )

            except Exception as e:

                print(
                    "[PRODUTO ERRO]",
                    candidate.get(
                        "product_id"
                    ),
                    repr(e)
                )

        # --------------------------------
        # RANKING DA CATEGORIA
        # --------------------------------

        category_products.sort(

            key=lambda x: (

                -x.get(
                    "score",
                    0
                ),

                -x.get(
                    "position",
                    999999
                )
                if x.get(
                    "position"
                )
                else
                999999,

                x.get(
                    "price",
                    999999
                )
            )
        )

        category_products = (
            category_products[:8]
        )

        for product in category_products:

            pid = product[
                "product_id"
            ]

            if pid in global_seen:
                continue

            global_seen.add(
                pid
            )

            final.append(
                product
            )

        diagnostics[
            category_name
        ][
            "validos"
        ] = len(
            category_products
        )

    # --------------------------------
    # RANKING GLOBAL
    # --------------------------------

    final.sort(

        key=lambda x: (

            -x.get(
                "score",
                0
            ),

            -x.get(
                "discount",
                0
            ),

            x.get(
                "price",
                999999
            )
        )
    )

    # --------------------------------
    # INTERCALA CATEGORIAS
    # --------------------------------

    grouped = {}

    for product in final:

        grouped.setdefault(
            product[
                "category_name"
            ],
            []
        ).append(
            product
        )

    interleaved = []

    categories = list(
        grouped.keys()
    )

    position = 0

    while (
        len(interleaved)
        <
        MAX_PRODUCTS
    ):

        added = False

        for category in categories:

            products = grouped[
                category
            ]

            if position >= len(
                products
            ):
                continue

            interleaved.append(
                products[
                    position
                ]
            )

            added = True

            if len(interleaved) >= MAX_PRODUCTS:
                break

        if not added:
            break

        position += 1

    final = interleaved

    # --------------------------------
    # ESTATÍSTICAS
    # --------------------------------

    prices = [

        p["price"]
        for p in final
        if p.get(
            "price"
        ) is not None
    ]

    stats = {

        "categorias":
            len(
                set(
                    p[
                        "category_name"
                    ]
                    for p in final
                )
            ),

        "com desconto":
            sum(
                1
                for p in final
                if p.get(
                    "discount",
                    0
                ) > 0
            ),

        "em tendência":
            sum(
                1
                for p in final
                if not p.get(
                    "position"
                )
            ),

        "frete grátis":
            sum(
                1
                for p in final
                if p.get(
                    "free_shipping"
                )
            ),

        "mais vendidos":
            sum(
                1
                for p in final
                if p.get(
                    "position"
                )
            ),

        "menor preço":
            brl(
                min(
                    prices,
                    default=0
                )
            ),

        "produtos":
            len(
                final
            ),

        "tempo":
            f"{round(time.time()-started,2)}s"
    }

    print(
        "[OK]",
        len(final),
        "produtos encontrados"
    )

    return {

        "stats":
            stats,

        "produtos":
            final,

        "diagnostico":
            diagnostics,

        "atualizado_em":
            time.strftime(
                "%d/%m/%Y %H:%M:%S"
            ),

        "auto_refresh_minutos":
            AUTO_REFRESH_MINUTES
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
                "Conecte sua conta do Mercado Livre primeiro."
        }), 401

    try:

        result = run_scan()

        return jsonify(
            json_safe(
                result
            )
        )

    except Exception as e:

        print(
            "[CAÇA ERRO]",
            repr(e)
        )

        return jsonify({

            "erro":
                "Erro durante a busca.",

            "detalhes":
                str(e)

        }), 500


# ============================================================
# BUSCA MANUAL
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

    if not access_token():

        return jsonify({
            "erro":
                "Conecte o Mercado Livre."
        }), 401

    candidates = search_products(
        query
    )

    products = []

    seen = set()

    for item in candidates:

        pid = item.get(
            "id"
        )

        if not pid or pid in seen:
            continue

        seen.add(
            pid
        )

        candidate = {

            "product_id":
                pid,

            "category_name":
                "🔎 Busca manual",

            "category_id":
                item.get(
                    "category_id"
                ),

            "query":
                query,

            "position":
                None
        }

        product = process_product(
            candidate
        )

        if product:

            products.append(
                product
            )

    products.sort(
        key=lambda x: (
            -x.get(
                "score",
                0
            ),
            x.get(
                "price",
                999999
            )
        )
    )

    products = products[
        :MAX_PRODUCTS
    ]

    return jsonify(
        json_safe({

            "stats": {

                "categorias":
                    1,

                "com desconto":
                    sum(
                        1
                        for p in products
                        if p.get(
                            "discount",
                            0
                        ) > 0
                    ),

                "em tendência":
                    len(products),

                "frete grátis":
                    sum(
                        1
                        for p in products
                        if p.get(
                            "free_shipping"
                        )
                    ),

                "mais vendidos":
                    0,

                "menor preço":
                    brl(
                        min(
                            [
                                p["price"]
                                for p in products
                            ],
                            default=0
                        )
                    ),

                "produtos":
                    len(products)
            },

            "produtos":
                products,

            "atualizado_em":
                time.strftime(
                    "%d/%m/%Y %H:%M:%S"
                )
        })
    )


# ============================================================
# GERADOR DE ANÚNCIO
# ============================================================

def generate_ad(
    product
):

    title = product.get(
        "title",
        "Produto"
    )

    price = number(
        product.get(
            "price"
        )
    ) or 0

    original = number(
        product.get(
            "original_price"
        )
    )

    discount = number(
        product.get(
            "discount"
        )
    ) or 0

    link = (
        product.get(
            "affiliate_link"
        )
        or
        product.get(
            "permalink"
        )
        or
        ""
    )

    lines = [

        "🔥 ACHADO DO MELI!",

        ""
    ]

    if product.get(
        "position"
    ):

        lines.append(
            "🏆 #"
            +
            str(
                product[
                    "position"
                ]
            )
            +
            " MAIS VENDIDO"
        )

    lines += [

        "",

        title,

        ""
    ]

    if original and original > price:

        lines.append(
            "De "
            +
            brl(original)
        )

    lines.append(
        "💰 Por "
        +
        brl(price)
    )

    if discount > 0:

        lines.append(
            "🔥 "
            +
            str(discount)
            +
            "% OFF"
        )

    if product.get(
        "free_shipping"
    ):

        lines.append(
            "🚚 Frete grátis"
        )

    lines += [

        "",

        "👉 Pegar promoção:",

        link,

        "",

        "Agora, no MELI! 🔥"
    ]

    return "\n".join(
        lines
    )


# ============================================================
# ÚNICA ROTA GERAR ANÚNCIO
# ============================================================

@app.route(
    "/api/gerar-anuncio",
    methods=[
        "GET",
        "POST"
    ]
)
def api_gerar_anuncio():

    if request.method == "POST":

        product = (
            request.get_json(
                silent=True
            )
            or
            {}
        )

    else:

        product = {

            "title":
                request.args.get(
                    "title",
                    "Produto"
                ),

            "price":
                number(
                    request.args.get(
                        "price"
                    )
                )
                or
                0,

            "original_price":
                number(
                    request.args.get(
                        "original_price"
                    )
                ),

            "discount":
                number(
                    request.args.get(
                        "discount"
                    )
                )
                or
                0,

            "free_shipping":
                request.args.get(
                    "shipping_free"
                ) == "1",

            "affiliate_link":
                request.args.get(
                    "affiliate_link",
                    ""
                )
        }

    return jsonify({

        "anuncio":
            generate_ad(
                product
            )
    })


# ============================================================
# CUPONS
# ============================================================

@app.route(
    "/api/cupons"
)
def api_cupons():

    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM cupons
        WHERE active=1
        ORDER BY updated_at DESC
    """).fetchall()

    conn.close()

    return jsonify({

        "cupons":
            [
                dict(row)
                for row in rows
            ]
    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    tokens = get_tokens()

    token = access_token()

    result = {

        "conectado":
            bool(token),

        "client_id":
            bool(
                ML_CLIENT_ID
            ),

        "user_id":
            (
                tokens.get(
                    "user_id"
                )
                if tokens
                else None
            ),

        "nickname":
            (
                tokens.get(
                    "nickname"
                )
                if tokens
                else None
            )
    }

    if token:

        me, status = ml_get(
            "/users/me"
        )

        result[
            "users_me"
        ] = {

            "status":
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
# HEALTH
# ============================================================

@app.route(
    "/health"
)
def health():

    return jsonify({

        "status":
            "ok",

        "mercado_livre":
            bool(
                access_token()
            ),

        "categorias":
            len(
                CATEGORIES
            ),

        "atualizacao_automatica":
            f"{AUTO_REFRESH_MINUTES} minutos"
    })


# ============================================================
# HTML
# ============================================================

HTML = """

<!DOCTYPE html>

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

*{
    box-sizing:border-box;
}

body{
    margin:0;
    background:#f4f5f7;
    color:#222;
    font-family:Arial,sans-serif;
}

.container{
    max-width:1100px;
    margin:auto;
    padding:15px;
}

.card{
    background:#fff;
    border-radius:18px;
    padding:18px;
    margin-bottom:15px;
    box-shadow:0 5px 20px #0000000d;
}

button{
    width:100%;
    border:0;
    padding:14px;
    border-radius:12px;
    background:#3483fa;
    color:#fff;
    font-size:16px;
    font-weight:bold;
    margin-top:7px;
}

.login{
    background:#ffe600;
    color:#222;
}

input{
    width:100%;
    padding:14px;
    border:1px solid #ddd;
    border-radius:12px;
    font-size:16px;
}

.stats{
    display:grid;
    grid-template-columns:
        repeat(2,1fr);
    gap:9px;
}

.stat{
    background:#f2f3f5;
    padding:14px;
    border-radius:13px;
}

.stat b{
    display:block;
    font-size:23px;
    margin-top:6px;
}

.product{
    border:2px solid #eee;
    border-radius:18px;
    padding:14px;
    margin-top:14px;
}

.product-top{
    display:flex;
    gap:12px;
    align-items:flex-start;
}

.product-img{
    width:100px;
    height:100px;
    object-fit:contain;
    border-radius:12px;
    background:#fafafa;
}

.title{
    font-size:19px;
    font-weight:bold;
    line-height:1.25;
}

.badge{
    display:inline-block;
    padding:6px 9px;
    border-radius:9px;
    background:#edf3ff;
    color:#3478f6;
    margin:2px;
    font-size:12px;
}

.badge-green{
    background:#45aa60;
    color:white;
}

.price{
    font-size:26px;
    font-weight:bold;
    margin-top:12px;
}

.old{
    color:#777;
    text-decoration:line-through;
    font-size:17px;
}

.discount{
    color:#298b43;
    font-size:20px;
    font-weight:bold;
    margin-top:8px;
}

.score{
    background:#f4f4f4;
    border-radius:10px;
    padding:10px;
    margin-top:12px;
}

.loading{
    background:#eef4ff;
    padding:13px;
    border-radius:12px;
}

.small{
    color:#666;
    font-size:13px;
}

.ad{
    display:none;
    white-space:pre-wrap;
    background:#f5f5f5;
    padding:12px;
    border-radius:10px;
    margin-top:10px;
}

.diag{
    font-size:13px;
    line-height:1.6;
}

a{
    color:#1769e0;
}

</style>


<script>

let automatico = null;

const AUTO_MINUTES = {{ auto_minutes }};


function money(value){

    return "R$ " +
        Number(
            value || 0
        ).toLocaleString(
            "pt-BR",
            {
                minimumFractionDigits:2,
                maximumFractionDigits:2
            }
        );
}


function esc(value){

    return String(
        value || ""
    )
    .replace(
        /[&<>"']/g,
        function(c){

            return {
                "&":"&amp;",
                "<":"&lt;",
                ">":"&gt;",
                '"':"&quot;",
                "'":"&#039;"
            }[c];

        }
    );

}


function iniciarAutomatico(){

    if(automatico){
        clearInterval(
            automatico
        );
    }

    automatico =
        setInterval(
            function(){

                if(
                    document.visibilityState
                    ===
                    "visible"
                ){

                    cacar(
                        true
                    );

                }

            },
            AUTO_MINUTES
            *
            60
            *
            1000
        );
}


async function cacar(
    automatico=false
){

    const status =
        document.getElementById(
            "status"
        );

    status.innerHTML =
        `
        <div class="loading">

            🔄
            ${
                automatico
                ?
                "Atualizando automaticamente..."
                :
                "Buscando os melhores produtos..."
            }

            <br>

            🏆 Priorizando mais vendidos,
            preço, desconto e frete.

        </div>
        `;

    try{

        const r =
            await fetch(
                "/api/cacar?" +
                Date.now()
            );

        const data =
            await r.json();

        if(!r.ok){

            status.innerHTML =
                "❌ " +
                esc(
                    data.erro ||
                    "Erro"
                );

            return;

        }

        render(
            data
        );

        status.innerHTML =
            `
            ✅ Lista atualizada:
            ${esc(
                data.atualizado_em
            )}

            <br>

            🔄 Próxima atualização
            automática em
            ${AUTO_MINUTES} minutos.
            `;

    }catch(e){

        console.error(e);

        status.innerHTML =
            "❌ Erro ao atualizar.";

    }

}


async function buscar(){

    const q =
        document.getElementById(
            "q"
        ).value.trim();

    if(!q){
        return;
    }

    document.getElementById(
        "status"
    ).textContent =
        "🔎 Procurando...";

    try{

        const r =
            await fetch(
                "/api/buscar?q=" +
                encodeURIComponent(
                    q
                )
            );

        const data =
            await r.json();

        render(
            data
        );

        document.getElementById(
            "status"
        ).textContent =
            "✅ Busca finalizada.";

    }catch(e){

        document.getElementById(
            "status"
        ).textContent =
            "❌ Erro.";

    }

}


function render(
    data
){

    const stats =
        document.getElementById(
            "stats"
        );

    stats.innerHTML =
        Object.entries(
            data.stats || {}
        )
        .map(
            function(item){

                return `

                <div class="stat">

                    ${esc(
                        item[0]
                    )}

                    <b>

                        ${esc(
                            item[1]
                        )}

                    </b>

                </div>

                `;

            }
        )
        .join("");


    const results =
        document.getElementById(
            "results"
        );

    const products =
        data.produtos || [];


    if(!products.length){

        results.innerHTML =
            `
            <p>
                Nenhum produto encontrado.
            </p>
            `;

    }else{

        results.innerHTML =
            products
            .map(
                renderProduct
            )
            .join("");

    }


    const diag =
        document.getElementById(
            "diagnostico"
        );

    if(data.diagnostico){

        diag.innerHTML =
            Object.entries(
                data.diagnostico
            )
            .map(
                function(item){

                    const name =
                        item[0];

                    const d =
                        item[1];

                    return `

                    <div>

                        <b>
                            ${esc(name)}
                        </b>

                        <br>

                        Candidatos:
                        ${d.candidatos || 0}

                        · Mais vendidos:
                        ${d.mais_vendidos || 0}

                        · Válidos:
                        ${d.validos || 0}

                    </div>

                    <hr>

                    `;

                }
            )
            .join("");

    }

}


function renderProduct(
    p,
    index
){

    const image =
        p.image
        ?
        `
        <img
            class="product-img"
            src="${esc(p.image)}"
        >
        `
        :
        "";


    let badges = `

        <span class="badge">

            #${index + 1}

        </span>

    `;


    if(p.position){

        badges += `

        <span class="badge badge-green">

            🏆 #${esc(
                p.position
            )}
            mais vendido

        </span>

        `;

    }else{

        badges += `

        <span class="badge">

            🔥 Em tendência

        </span>

        `;

    }


    badges += `

        <span class="badge">

            ${esc(
                p.category_name
            )}

        </span>

    `;


    const oldPrice =
        p.original_price
        ?
        `
        <div class="old">

            De:
            ${money(
                p.original_price
            )}

        </div>
        `
        :
        "";


    const discount =
        p.discount > 0
        ?
        `
        <div class="discount">

            🔥
            ${esc(
                p.discount
            )}% OFF

        </div>
        `
        :
        "";


    const shipping =
        p.free_shipping
        ?
        `
        <div class="discount">

            🚚 Frete grátis

        </div>
        `
        :
        "";


    const id =
        "p" +
        index;


    return `

    <div class="product">

        <div class="product-top">

            ${image}

            <div>

                ${badges}

                <div class="title">

                    ${esc(
                        p.title
                    )}

                </div>

            </div>

        </div>


        <div class="price">

            ${money(
                p.price
            )}

        </div>


        ${oldPrice}

        ${discount}

        ${shipping}


        <div class="score">

            ⭐ Pontuação:
            <b>
                ${esc(
                    p.score
                )}
            </b>

        </div>


        <br>


        <a
            href="${esc(
                p.permalink
            )}"
            target="_blank"
        >

            🛒 Ver produto

        </a>


        <br>
        <br>


        <input
            id="link_${id}"
            placeholder="Cole seu link de afiliado"
        >


        <button
            onclick='gerar(
                "${id}",
                ${JSON.stringify(p)}
            )'
        >

            📢 Gerar anúncio

        </button>


        <div
            id="ad_${id}"
            class="ad"
        ></div>


        <button
            id="copy_${id}"
            style="
                display:none;
                background:#ff8a00;
            "
            onclick="copiar('${id}')"
        >

            📋 Copiar oferta

        </button>


    </div>

    `;

}


async function gerar(
    id,
    product
){

    const link =
        document.getElementById(
            "link_" + id
        ).value.trim();

    product.affiliate_link =
        link;


    try{

        const r =
            await fetch(
                "/api/gerar-anuncio",
                {

                    method:
                        "POST",

                    headers:{
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            product
                        )

                }
            );

        const data =
            await r.json();


        const ad =
            document.getElementById(
                "ad_" + id
            );

        ad.style.display =
            "block";

        ad.textContent =
            data.anuncio;


        document.getElementById(
            "copy_" + id
        ).style.display =
            "block";

    }catch(e){

        alert(
            "Erro ao gerar anúncio."
        );

    }

}


async function copiar(
    id
){

    const ad =
        document.getElementById(
            "ad_" + id
        );

    try{

        await navigator.clipboard.writeText(
            ad.textContent
        );

        const button =
            document.getElementById(
                "copy_" + id
            );

        button.textContent =
            "✅ Copiado!";

        setTimeout(
            function(){

                button.textContent =
                    "📋 Copiar oferta";

            },
            1500
        );

    }catch(e){

        alert(
            "Não foi possível copiar."
        );

    }

}


window.addEventListener(
    "load",
    function(){

        iniciarAutomatico();

    }
);

</script>

</head>


<body>


<div class="container">


<div class="card">

    <h1>
        🛒 Caçador de Ofertas
    </h1>

    <p>

        Encontramos automaticamente
        produtos com maior potencial
        de giro no Mercado Livre.

    </p>


    {% if conectado %}

        <div
            style="
                background:#eaf8ef;
                padding:12px;
                border-radius:10px;
            "
        >

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
        🏆 Caçador automático
    </h2>

    <p class="small">

        O sistema prioriza produtos
        que aparecem entre os mais vendidos,
        depois considera preço, desconto
        e frete grátis.

        <br><br>

        🔄 A lista será atualizada
        automaticamente a cada
        {{ auto_minutes }} minutos.

    </p>


    <button
        onclick="cacar(false)"
    >

        🚀 BUSCAR MELHORES OFERTAS

    </button>


    <p
        id="status"
        class="small"
    >

        Aguardando busca...

    </p>

</div>


<div class="card">

    <h2>
        🔎 Busca manual
    </h2>


    <input
        id="q"
        placeholder="Ex: air fryer, perfume, celular..."
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
        🏆 Produtos encontrados
    </h2>


    <p class="small">

        O ranking combina popularidade,
        preço, desconto e frete.

        <br>

        Produtos repetidos são eliminados.

    </p>


    <div id="results">

        Faça uma busca para começar.

    </div>

</div>


<div class="card">

    <h3>
        🧪 Cobertura das categorias
    </h3>


    <div
        id="diagnostico"
        class="diag"
    >

        Aguardando busca...

    </div>

</div>


<div class="card">

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
# HOME
# ============================================================

@app.route("/")
def home():

    tokens = get_tokens()

    return render_template_string(

        HTML,

        conectado=
            bool(
                access_token()
            ),

        nickname=
            (
                tokens.get(
                    "nickname"
                )
                if tokens
                else None
            ),

        auto_minutes=
            AUTO_REFRESH_MINUTES
    )


# ============================================================
# ERROS
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({

        "erro":
            "Rota não encontrada.",

        "rota":
            request.path

    }), 404


@app.errorhandler(500)
def server_error(error):

    return jsonify({

        "erro":
            "Erro interno.",

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