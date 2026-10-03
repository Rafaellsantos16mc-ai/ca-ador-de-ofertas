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

# Quantidade máxima exibida
MAX_PRODUCTS = 60

# Quantos produtos do ranking serão analisados por categoria
TOP_PRODUCTS_PER_CATEGORY = 8

# Quantas categorias descobertas por consulta serão analisadas
MAX_DISCOVERED_CATEGORIES = 3

# ============================================================
# CATEGORIAS DO CAÇADOR
# ============================================================

CATEGORIES = {
    "📱 Celulares": [
        "celular",
        "smartphone",
        "iphone",
        "samsung galaxy",
        "motorola"
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "headset",
        "smartwatch",
        "tablet",
        "caixa de som bluetooth"
    ],

    "🏠 Casa": [
        "aspirador de pó",
        "liquidificador",
        "cafeteira",
        "ventilador",
        "ferro de passar"
    ],

    "🍳 Cozinha": [
        "air fryer",
        "panela elétrica",
        "jogo de panelas",
        "cafeteira",
        "liquidificador"
    ],

    "🔧 Ferramentas": [
        "furadeira",
        "parafusadeira",
        "esmerilhadeira",
        "kit ferramentas",
        "chave de impacto"
    ],

    "🏋️ Academia": [
        "tenis corrida",
        "roupa academia",
        "whey protein",
        "creatina",
        "legging"
    ],

    "🌸 Beleza": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "secador de cabelo",
        "chapinha"
    ],

    "🚗 Automotivo": [
        "compressor automotivo",
        "aspirador automotivo",
        "suporte celular carro",
        "carregador automotivo",
        "tapete automotivo"
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "mochila",
        "bolsa feminina",
        "oculos de sol"
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
            relevance_score REAL DEFAULT 0,
            affiliate_link TEXT,
            extra_earnings REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            f"R$ {float(value):,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except Exception:
        return "R$ 0,00"


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


def safe_float(value):

    try:
        return float(value)
    except Exception:
        return None


# ============================================================
# OAUTH
# ============================================================

def generate_pkce():

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

            access_token = excluded.access_token,

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
        data.get("access_token"),

        data.get("refresh_token"),

        int(time.time()) +
        int(data.get("expires_in", 21600)),

        str(user.get("id"))
        if user and user.get("id")
        else old.get("user_id"),

        user.get("nickname")
        if user
        else old.get("nickname")
    ))

    conn.commit()
    conn.close()


def refresh_token():

    data = get_tokens()

    if not data:
        return None

    refresh = data.get("refresh_token")

    if not refresh:
        return None

    try:

        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": refresh
            },
            timeout=30
        )

        if response.status_code != 200:
            print(
                "[REFRESH TOKEN]",
                response.status_code,
                response.text[:500]
            )

            return None

        new_data = response.json()

        save_tokens(
            new_data,
            {
                "id": data.get("user_id"),
                "nickname": data.get("nickname")
            }
        )

        return new_data.get("access_token")

    except Exception as e:

        print(
            "[ERRO REFRESH]",
            repr(e)
        )

        return None


def access_token():

    data = get_tokens()

    if not data:
        return None

    token = data.get("access_token")

    expires = data.get("expires_at") or 0

    if token and time.time() < expires - 120:
        return token

    return refresh_token() or token


# ============================================================
# API MERCADO LIVRE
# ============================================================

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
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "CacadorDeOfertas/1.0"
            },
            params=params,
            timeout=30
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "message": response.text
            }

        return (
            data,
            response.status_code,
            dict(response.headers)
        )

    except requests.RequestException as e:

        return {
            "error": str(e)
        }, 500, {}


# ============================================================
# LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return jsonify({
            "erro": "ML_CLIENT_ID não configurado."
        }), 500

    verifier, challenge = generate_pkce()

    state = secrets.token_urlsafe(32)

    session["ml_state"] = state
    session["ml_code_verifier"] = verifier

    params = {

        "response_type": "code",

        "client_id": ML_CLIENT_ID,

        "redirect_uri": ML_REDIRECT_URI,

        "state": state,

        "code_challenge": challenge,

        "code_challenge_method": "S256"
    }

    return redirect(
        ML_AUTH + "?" + urlencode(params)
    )


@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    if request.args.get("error"):

        return jsonify({
            "erro": request.args.get("error"),
            "descricao": request.args.get(
                "error_description"
            )
        }), 400

    code = request.args.get("code")

    state = request.args.get("state")

    if not code:

        return jsonify({
            "erro": "Código de autorização não recebido."
        }), 400

    if state != session.get("ml_state"):

        return jsonify({
            "erro": "State inválido."
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

        token_data = response.json()

        user = None

        if token_data.get("access_token"):

            me = requests.get(

                ML_API + "/users/me",

                headers={
                    "Authorization":
                        "Bearer " +
                        token_data["access_token"]
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
            "ml_code_verifier",
            None
        )

        return redirect(
            "/?conectado=1"
        )

    except Exception as e:

        return jsonify({
            "erro": str(e)
        }), 500


@app.route("/mercadolivre/logout")
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
# CATEGORIAS
# ============================================================

def discover_categories(query):

    data, status, _ = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        {
            "q": query
        }
    )

    if status != 200:
        return []

    if not isinstance(data, list):
        return []

    result = []

    seen = set()

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

        if not category_id:
            continue

        if category_id in seen:
            continue

        seen.add(category_id)

        result.append({

            "category_id":
                category_id,

            "category_name":
                category_name
        })

    return result


# ============================================================
# MAIS VENDIDOS
# ============================================================

def get_best_sellers(category_id):

    data, status, _ = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    if status != 200:

        print(
            "[BEST SELLER]",
            category_id,
            "HTTP",
            status
        )

        return []

    if not isinstance(data, dict):
        return []

    content = data.get(
        "content",
        []
    )

    if not isinstance(content, list):
        return []

    result = []

    for item in content:

        if not isinstance(item, dict):
            continue

        item_id = item.get("id")

        position = item.get(
            "position"
        )

        item_type = item.get(
            "type"
        )

        if not item_id:
            continue

        result.append({

            "id":
                item_id,

            "position":
                position,

            "type":
                item_type,

            "category_id":
                category_id
        })

    return result


# ============================================================
# PRODUTO
# ============================================================

def get_product(product_id):

    data, status, _ = ml_get(
        f"/products/{product_id}"
    )

    if status != 200:

        return None

    if not isinstance(data, dict):
        return None

    return data


def get_product_items(product_id):

    data, status, _ = ml_get(
        f"/products/{product_id}/items"
    )

    if status != 200:

        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        result = data.get(
            "results",
            []
        )

        if isinstance(result, list):
            return result

    return []


# ============================================================
# NORMALIZAÇÃO DE OFERTA
# ============================================================

def normalize_item(item):

    if not isinstance(item, dict):
        return None

    item_id = item.get(
        "item_id"
    ) or item.get(
        "id"
    )

    if not item_id:
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
        else safe_float(
            shipping.get("cost")
        )
    )

    price = safe_float(
        item.get("price")
    )

    original_price = safe_float(
        item.get("original_price")
    )

    return {

        "item_id":
            item_id,

        "seller_id":
            item.get("seller_id"),

        "price":
            price,

        "original_price":
            original_price,

        "condition":
            item.get("condition")
            or item.get("item_condition"),

        "listing_type_id":
            item.get("listing_type_id"),

        "free_shipping":
            free_shipping,

        "shipping_cost":
            shipping_cost,

        "permalink":
            item.get("permalink"),

        "user_product_id":
            item.get(
                "user_product_id"
            )
    }


# ============================================================
# BUY BOX
# ============================================================

def normalize_buy_box(
    buy_box,
    product_data
):

    if isinstance(
        buy_box,
        list
    ):

        buy_box = (
            buy_box[0]
            if buy_box
            else None
        )

    if not isinstance(
        buy_box,
        dict
    ):

        return None

    item_id = (
        buy_box.get("item_id")
        or buy_box.get("id")
    )

    price = safe_float(
        buy_box.get("price")
    )

    original_price = safe_float(
        buy_box.get(
            "original_price"
        )
    )

    seller_id = (
        buy_box.get(
            "seller_id"
        )
    )

    shipping = (
        buy_box.get("shipping")
        or {}
    )

    free_shipping = bool(
        shipping.get(
            "free_shipping"
        )
    )

    if not item_id and not price:
        return None

    return {

        "item_id":
            item_id,

        "seller_id":
            seller_id,

        "price":
            price,

        "original_price":
            original_price,

        "condition":
            buy_box.get(
                "condition"
            ),

        "listing_type_id":
            buy_box.get(
                "listing_type_id"
            ),

        "free_shipping":
            free_shipping,

        "shipping_cost":
            0
            if free_shipping
            else safe_float(
                shipping.get("cost")
            ),

        "permalink":
            buy_box.get(
                "permalink"
            )
            or product_data.get(
                "permalink"
            ),

        "user_product_id":
            buy_box.get(
                "user_product_id"
            )
    }


# ============================================================
# TENDÊNCIAS
# ============================================================

def get_trends():

    data, status, _ = ml_get(
        f"/trends/{SITE_ID}"
    )

    if status != 200:
        return []

    if not isinstance(
        data,
        list
    ):
        return []

    result = []

    for index, item in enumerate(
        data
    ):

        if not isinstance(
            item,
            dict
        ):
            continue

        keyword = item.get(
            "keyword"
        )

        if not keyword:
            continue

        result.append({

            "keyword":
                keyword,

            "position":
                index + 1,

            "url":
                item.get("url")
        })

    return result


# ============================================================
# BUSCA DE PRODUTO PELO TERMO
# ============================================================

def search_products(query):

    data, status, _ = ml_get(
        "/products/search",
        {
            "site_id":
                SITE_ID,

            "status":
                "active",

            "q":
                query,

            "limit":
                10
        }
    )

    if status != 200:
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

    if not isinstance(
        results,
        list
    ):
        return []

    return results


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    ranking_position,
    price,
    discount,
    free_shipping,
    trend_bonus=0
):

    score = 0

    # MAIS VENDIDO = maior peso
    if ranking_position:

        position = int(
            ranking_position
        )

        if position == 1:
            score += 100

        elif position <= 3:
            score += 90

        elif position <= 5:
            score += 80

        elif position <= 10:
            score += 65

        elif position <= 15:
            score += 50

        elif position <= 20:
            score += 40

    # Tendência
    score += trend_bonus

    # Desconto existente
    if discount > 0:

        score += min(
            discount * 0.8,
            25
        )

    # Frete grátis
    if free_shipping:
        score += 10

    # Faixa de preço com maior facilidade de giro
    if price:

        if 69.90 <= price <= 149.90:
            score += 20

        elif 150 <= price <= 299.90:
            score += 17

        elif 300 <= price <= 499.90:
            score += 12

        elif 500 <= price <= 999.90:
            score += 8

        elif price > 1500:
            score -= 5

    return round(
        score,
        2
    )


# ============================================================
# COLETA INTELIGENTE
# ============================================================

def collect_ranked_candidates():

    candidates = {}

    diagnostics = {}

    trends = get_trends()

    trend_words = [
        norm(x["keyword"])
        for x in trends[:50]
    ]

    for category_name, queries in CATEGORIES.items():

        category_candidates = {}

        discovered_count = 0

        ranking_count = 0

        trend_count = 0

        # ----------------------------------------------------
        # 1. MAIS VENDIDOS
        # ----------------------------------------------------

        for query in queries:

            discovered = discover_categories(
                query
            )

            for discovered_category in discovered[
                :MAX_DISCOVERED_CATEGORIES
            ]:

                discovered_count += 1

                category_id = discovered_category[
                    "category_id"
                ]

                best_sellers = get_best_sellers(
                    category_id
                )

                for best in best_sellers:

                    product_id = best[
                        "id"
                    ]

                    item_type = best.get(
                        "type"
                    )

                    # Nosso fluxo principal trabalha
                    # com produtos de catálogo.
                    if item_type != "PRODUCT":
                        continue

                    if product_id in category_candidates:
                        continue

                    category_candidates[
                        product_id
                    ] = {

                        "product_id":
                            product_id,

                        "category_name":
                            category_name,

                        "category_id":
                            category_id,

                        "query":
                            query,

                        "ranking_position":
                            best.get(
                                "position"
                            ),

                        "source":
                            "mais_vendidos",

                        "trend_bonus":
                            0
                    }

                    ranking_count += 1

                    # Evita fazer centenas de chamadas
                    if len(category_candidates) >= TOP_PRODUCTS_PER_CATEGORY:
                        break

                if len(category_candidates) >= TOP_PRODUCTS_PER_CATEGORY:
                    break

            if len(category_candidates) >= TOP_PRODUCTS_PER_CATEGORY:
                break

        # ----------------------------------------------------
        # 2. COMPLEMENTO COM TENDÊNCIAS
        # ----------------------------------------------------

        if len(category_candidates) < TOP_PRODUCTS_PER_CATEGORY:

            for trend in trends:

                keyword = trend.get(
                    "keyword"
                )

                if not keyword:
                    continue

                normalized_keyword = norm(
                    keyword
                )

                # Verifica se a tendência tem relação
                # com alguma busca da categoria.
                related = False

                for q in queries:

                    nq = norm(q)

                    words = [
                        w
                        for w in nq.split()
                        if len(w) >= 4
                    ]

                    if any(
                        w in normalized_keyword
                        for w in words
                    ):
                        related = True
                        break

                if not related:
                    continue

                products = search_products(
                    keyword
                )

                for product_result in products:

                    product_id = (
                        product_result.get(
                            "id"
                        )
                    )

                    if not product_id:
                        continue

                    if product_id in category_candidates:
                        continue

                    category_candidates[
                        product_id
                    ] = {

                        "product_id":
                            product_id,

                        "category_name":
                            category_name,

                        "category_id":
                            product_result.get(
                                "category_id"
                            ),

                        "query":
                            keyword,

                        "ranking_position":
                            None,

                        "source":
                            "tendencia",

                        "trend_bonus":
                            max(
                                5,
                                35 - (
                                    trend.get(
                                        "position",
                                        50
                                    ) * 0.5
                                )
                            )
                    }

                    trend_count += 1

                    if len(category_candidates) >= TOP_PRODUCTS_PER_CATEGORY:
                        break

                if len(category_candidates) >= TOP_PRODUCTS_PER_CATEGORY:
                    break

        candidates[
            category_name
        ] = list(
            category_candidates.values()
        )

        diagnostics[
            category_name
        ] = {

            "categorias_consultadas":
                discovered_count,

            "mais_vendidos":
                ranking_count,

            "tendencias":
                trend_count,

            "candidatos":
                len(category_candidates)
        }

    return candidates, diagnostics


# ============================================================
# PROCESSAMENTO DO PRODUTO
# ============================================================

def process_candidate(candidate):

    product_id = candidate[
        "product_id"
    ]

    data = get_product(
        product_id
    )

    if not data:
        return None

    title = (
        data.get("name")
        or data.get("title")
        or product_id
    )

    pictures = (
        data.get("pictures")
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
                first.get("url")
                or first.get(
                    "secure_url"
                )
            )

    # --------------------------------------------------------
    # Primeiro tenta /products/{id}/items
    # --------------------------------------------------------

    raw_items = get_product_items(
        product_id
    )

    items = []

    for raw in raw_items:

        normalized = normalize_item(
            raw
        )

        if normalized:
            items.append(
                normalized
            )

    # --------------------------------------------------------
    # Fallback Buy Box
    # --------------------------------------------------------

    if not items:

        buy_box = data.get(
            "buy_box_winner"
        )

        normalized_buy_box = normalize_buy_box(
            buy_box,
            data
        )

        if normalized_buy_box:

            items.append(
                normalized_buy_box
            )

    if not items:
        return None

    # --------------------------------------------------------
    # Filtra preços
    # --------------------------------------------------------

    valid_items = []

    for item in items:

        price = item.get(
            "price"
        )

        if price is None:
            continue

        if price < MIN_PRODUCT_PRICE:
            continue

        valid_items.append(
            item
        )

    if not valid_items:
        return None

    # --------------------------------------------------------
    # Escolhe o melhor vendedor
    # --------------------------------------------------------

    def seller_key(item):

        return (

            item.get(
                "price"
            )
            if item.get(
                "price"
            ) is not None
            else 999999,

            0
            if item.get(
                "free_shipping"
            )
            else 1
        )

    valid_items.sort(
        key=seller_key
    )

    best_item = valid_items[0]

    price = best_item[
        "price"
    ]

    original_price = best_item.get(
        "original_price"
    )

    discount = calc_discount(
        price,
        original_price
    )

    shipping_cost = best_item.get(
        "shipping_cost"
    )

    if shipping_cost is None:
        total_price = price
    else:
        total_price = (
            price +
            shipping_cost
        )

    ranking_position = candidate.get(
        "ranking_position"
    )

    trend_bonus = candidate.get(
        "trend_bonus",
        0
    )

    score = calculate_score(

        ranking_position,

        price,

        discount,

        best_item.get(
            "free_shipping"
        ),

        trend_bonus
    )

    if ranking_position:

        ranking_text = (
            f"#{ranking_position} "
            "mais vendido"
        )

    else:

        ranking_text = (
            "🔥 Em tendência"
        )

    return {

        "product_id":
            product_id,

        "item_id":
            best_item.get(
                "item_id"
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

        "ranking_position":
            ranking_position,

        "ranking_text":
            ranking_text,

        "source":
            candidate.get(
                "source"
            ),

        "price":
            price,

        "original_price":
            original_price,

        "discount":
            discount,

        "seller_id":
            best_item.get(
                "seller_id"
            ),

        "free_shipping":
            bool(
                best_item.get(
                    "free_shipping"
                )
            ),

        "shipping_cost":
            shipping_cost,

        "total_price":
            total_price,

        "condition":
            best_item.get(
                "condition"
            ),

        "permalink":
            best_item.get(
                "permalink"
            )
            or data.get(
                "permalink"
            )
            or (
                "https://www.mercadolivre.com.br/p/"
                + product_id
            ),

        "score":
            score,

        "affiliate_link":
            ""
    }


# ============================================================
# BUSCA COMPLETA
# ============================================================

def run_smart_scan():

    started = time.time()

    print("")
    print("=" * 60)
    print("🔥 CAÇADOR DE OFERTAS - BUSCA INTELIGENTE")
    print("=" * 60)

    candidates_by_category, diagnostics = (
        collect_ranked_candidates()
    )

    all_products = []

    seen_products = set()

    category_valid = {}

    # --------------------------------------------------------
    # Processa categoria por categoria
    # --------------------------------------------------------

    for category_name, candidates in (
        candidates_by_category.items()
    ):

        valid = []

        for candidate in candidates:

            try:

                result = process_candidate(
                    candidate
                )

                if not result:
                    continue

                product_id = result[
                    "product_id"
                ]

                if product_id in seen_products:
                    continue

                seen_products.add(
                    product_id
                )

                valid.append(
                    result
                )

            except Exception as e:

                print(
                    "[ERRO PRODUTO]",
                    candidate.get(
                        "product_id"
                    ),
                    repr(e)
                )

        # Ordena por score
        valid.sort(
            key=lambda x: (
                -x.get(
                    "score",
                    0
                ),

                x.get(
                    "ranking_position"
                )
                if x.get(
                    "ranking_position"
                )
                else 999,

                x.get(
                    "price",
                    999999
                )
            )
        )

        # Limita quantidade por categoria
        valid = valid[:10]

        category_valid[
            category_name
        ] = len(valid)

        all_products.extend(
            valid
        )

    # --------------------------------------------------------
    # Ranking global
    # --------------------------------------------------------

    all_products.sort(
        key=lambda x: (

            -x.get(
                "score",
                0
            ),

            x.get(
                "ranking_position"
            )
            if x.get(
                "ranking_position"
            )
            else 999,

            x.get(
                "price",
                999999
            )
        )
    )

    # --------------------------------------------------------
    # Tenta distribuir as categorias
    # --------------------------------------------------------

    final_products = []

    category_lists = {}

    for product in all_products:

        category = product[
            "category_name"
        ]

        category_lists.setdefault(
            category,
            []
        ).append(
            product
        )

    category_order = list(
        category_lists.keys()
    )

    index = 0

    while len(final_products) < MAX_PRODUCTS:

        added = False

        for category in category_order:

            products = category_lists[
                category
            ]

            if index >= len(products):
                continue

            product = products[
                index
            ]

            final_products.append(
                product
            )

            added = True

            if len(final_products) >= MAX_PRODUCTS:
                break

        if not added:
            break

        index += 1

    # --------------------------------------------------------
    # Estatísticas
    # --------------------------------------------------------

    prices = [
        p["price"]
        for p in final_products
        if p.get("price") is not None
    ]

    discounts = [
        p
        for p in final_products
        if p.get(
            "discount",
            0
        ) > 0
    ]

    free_shipping = [
        p
        for p in final_products
        if p.get(
            "free_shipping"
        )
    ]

    categories_found = list({
        p["category_name"]
        for p in final_products
    })

    elapsed = round(
        time.time() - started,
        2
    )

    stats = {

        "produtos":
            len(final_products),

        "categorias":
            len(categories_found),

        "mais vendidos":
            sum(
                1
                for p in final_products
                if p.get(
                    "ranking_position"
                )
            ),

        "em tendência":
            sum(
                1
                for p in final_products
                if p.get(
                    "source"
                ) == "tendencia"
            ),

        "com desconto":
            len(discounts),

        "frete grátis":
            len(free_shipping),

        "menor preço":
            brl(
                min(
                    prices,
                    default=0
                )
            ),

        "tempo":
            f"{elapsed}s"
    }

    # Atualiza diagnóstico
    for category in diagnostics:

        diagnostics[
            category
        ]["produtos_validos"] = (
            category_valid.get(
                category,
                0
            )
        )

    print("")
    print(
        "[RESULTADO]",
        len(final_products),
        "produtos"
    )

    print(
        "[CATEGORIAS]",
        len(categories_found)
    )

    print(
        "[TEMPO]",
        elapsed,
        "segundos"
    )

    print("=" * 60)
    print("")

    return {

        "stats":
            stats,

        "produtos":
            final_products,

        "diagnostico":
            diagnostics,

        "atualizado_em":
            time.strftime(
                "%d/%m/%Y %H:%M:%S"
            )
    }


# ============================================================
# API PRINCIPAL
# ============================================================

@app.route("/api/cacar")
def api_cacar():

    if not access_token():

        return jsonify({
            "erro":
                "Conecte sua conta do Mercado Livre primeiro."
        }), 401

    try:

        result = run_smart_scan()

        return jsonify(
            json_safe(
                result
            )
        )

    except Exception as e:

        print(
            "[ERRO CAÇA]",
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

    if not access_token():

        return jsonify({
            "erro":
                "Conecte sua conta do Mercado Livre primeiro."
        }), 401

    products = search_products(
        query
    )

    result = []

    seen = set()

    for item in products:

        product_id = item.get(
            "id"
        )

        if not product_id:
            continue

        if product_id in seen:
            continue

        seen.add(
            product_id
        )

        candidate = {

            "product_id":
                product_id,

            "category_name":
                "🔎 Busca manual",

            "category_id":
                item.get(
                    "category_id"
                ),

            "query":
                query,

            "ranking_position":
                None,

            "source":
                "busca",

            "trend_bonus":
                0
        }

        product_result = process_candidate(
            candidate
        )

        if product_result:

            result.append(
                product_result
            )

    result.sort(
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

    result = result[:MAX_PRODUCTS]

    return jsonify(
        json_safe({

            "stats": {

                "produtos":
                    len(result),

                "categorias":
                    1,

                "com desconto":
                    sum(
                        1
                        for p in result
                        if p.get(
                            "discount",
                            0
                        ) > 0
                    ),

                "frete grátis":
                    sum(
                        1
                        for p in result
                        if p.get(
                            "free_shipping"
                        )
                    ),

                "menor preço":
                    brl(
                        min(
                            [
                                p["price"]
                                for p in result
                                if p.get("price") is not None
                            ],
                            default=0
                        )
                    )
            },

            "produtos":
                result,

            "atualizado_em":
                time.strftime(
                    "%d/%m/%Y %H:%M:%S"
                )
        })
    )


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def diagnostico():

    token = access_token()

    result = {

        "configurado":
            bool(
                ML_CLIENT_ID
            ),

        "conectado":
            bool(token),

        "categorias":
            len(
                CATEGORIES
            )
    }

    if token:

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

    local = get_tokens()

    if local:

        result[
            "token_local"
        ] = {

            "user_id":
                local.get(
                    "user_id"
                ),

            "nickname":
                local.get(
                    "nickname"
                ),

            "expires_at":
                local.get(
                    "expires_at"
                )
        }

    return jsonify(
        json_safe(
            result
        )
    )


# ============================================================
# TESTE BEST SELLERS
# ============================================================

@app.route("/mercadolivre/teste-mais-vendidos")
def teste_mais_vendidos():

    category_id = request.args.get(
        "category_id",
        ""
    ).strip()

    if not category_id:

        return jsonify({

            "erro":
                "Informe category_id.",

            "exemplo":
                "/mercadolivre/teste-mais-vendidos?category_id=MLB432825"
        }), 400

    data, status, _ = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    return jsonify({

        "status_http":
            status,

        "category_id":
            category_id,

        "resposta":
            data
    }), status


# ============================================================
# GERAR ANÚNCIO
# ============================================================

@app.route("/api/gerar-anuncio")
def gerar_anuncio():

    title = request.args.get(
        "title",
        "Produto"
    )

    price = safe_float(
        request.args.get(
            "price"
        )
    ) or 0

    original_price = safe_float(
        request.args.get(
            "original_price"
        )
    )

    discount = safe_float(
        request.args.get(
            "discount"
        )
    ) or 0

    shipping_free = (
        request.args.get(
            "shipping_free"
        ) == "1"
    )

    affiliate_link = request.args.get(
        "affiliate_link",
        ""
    ).strip()

    lines = [

        "🔥 OFERTA ENCONTRADA!",

        "",

        f"🛍️ {title}"
    ]

    if original_price:

        lines.append(
            f"💸 De: {brl(original_price)}"
        )

    lines.append(
        f"🔥 Por: {brl(price)}"
    )

    if discount > 0:

        lines.append(
            f"🏷️ {discount}% OFF"
        )

    if shipping_free:

        lines.append(
            "🚚 Frete grátis"
        )

    lines += [

        "",

        "⚠️ Preço sujeito a alteração.",

        "",

        "🛒 PEGAR OFERTA:",

        affiliate_link
        or "Cole aqui seu link de afiliado."
    ]

    return jsonify({

        "anuncio":
            "\n".join(lines)
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
    max-width:1100px;
    margin:auto;
    padding:16px;
}

.card{
    background:#fff;
    border-radius:16px;
    padding:18px;
    margin-bottom:16px;
    box-shadow:0 5px 20px #0000000d;
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
    color:white;
    cursor:pointer;
    margin-top:7px;
    font-weight:bold;
}

.login{
    background:#ffe600;
    color:#222;
}

.stats{
    display:grid;
    grid-template-columns:
        repeat(auto-fit,minmax(130px,1fr));
    gap:8px;
}

.stat{
    background:#f3f4f6;
    padding:13px;
    border-radius:11px;
}

.stat b{
    display:block;
    font-size:21px;
    margin-top:5px;
}

.grid{
    display:grid;
    grid-template-columns:
        repeat(auto-fit,minmax(150px,1fr));
    gap:8px;
}

.cat{
    background:white;
    border:1px solid #ddd;
    color:#222;
    text-align:left;
}

.product{
    border:2px solid #eee;
    border-radius:15px;
    padding:13px;
    margin-top:12px;
}

.product-head{
    display:flex;
    gap:12px;
    align-items:center;
}

.product-head img{
    width:90px;
    height:90px;
    object-fit:contain;
    border-radius:10px;
    background:#fafafa;
}

.title{
    font-size:17px;
    font-weight:bold;
}

.badge{
    display:inline-block;
    background:#eef4ff;
    color:#3483fa;
    border-radius:7px;
    padding:5px 8px;
    font-size:11px;
    margin:3px 3px 3px 0;
}

.badge-green{
    background:#00a650;
    color:white;
}

.badge-orange{
    background:#fff1dc;
    color:#b86b00;
}

.price{
    font-size:23px;
    font-weight:bold;
    margin-top:8px;
}

.old{
    color:#777;
    text-decoration:line-through;
}

.green{
    color:#008a3e;
    font-weight:bold;
    margin-top:4px;
}

.score{
    background:#f5f5f5;
    border-radius:8px;
    padding:7px;
    margin-top:8px;
    font-size:12px;
}

.small{
    font-size:12px;
    color:#666;
}

.ad{
    display:none;
    white-space:pre-wrap;
    background:#f7f7f7;
    padding:10px;
    border-radius:9px;
    margin-top:8px;
    font-size:13px;
}

.diagnostico{
    font-size:12px;
    background:#fafafa;
    padding:10px;
    border-radius:10px;
    margin-top:8px;
}

</style>


<script>

let ultimoResultado = null;


async function cacar(){

    const status =
        document.getElementById(
            "status"
        );

    status.textContent =
        "🔄 Buscando os produtos que mais vendem no Mercado Livre...";

    try{

        const response =
            await fetch(
                "/api/cacar"
            );

        const data =
            await response.json();

        if(!response.ok){

            status.textContent =
                "❌ " +
                (data.erro ||
                "Erro na busca.");

            return;
        }

        ultimoResultado = data;

        render(data);

        status.textContent =
            "✅ Atualizado em " +
            data.atualizado_em;

    }catch(error){

        status.textContent =
            "❌ Erro de conexão.";

        console.error(error);
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

    const status =
        document.getElementById(
            "status"
        );

    status.textContent =
        "🔎 Procurando...";

    try{

        const response =
            await fetch(
                "/api/buscar?q=" +
                encodeURIComponent(q)
            );

        const data =
            await response.json();

        if(!response.ok){

            status.textContent =
                "❌ " +
                (data.erro ||
                "Erro.");

            return;
        }

        ultimoResultado = data;

        render(data);

        status.textContent =
            "✅ Busca concluída.";

    }catch(error){

        status.textContent =
            "❌ Erro de conexão.";
    }
}


function render(data){

    const stats =
        document.getElementById(
            "stats"
        );

    stats.innerHTML =
        Object.entries(
            data.stats || {}
        )
        .map(
            ([key,value]) => `

                <div class="stat">

                    ${escapeHtml(key)}

                    <b>
                        ${escapeHtml(value)}
                    </b>

                </div>

            `
        )
        .join("");


    const results =
        document.getElementById(
            "results"
        );

    const products =
        data.produtos || [];


    results.innerHTML =
        products
        .map(
            (product,index) =>
                renderProduct(
                    product,
                    index
                )
        )
        .join("");


    if(!products.length){

        results.innerHTML =
            `
            <p>
                Nenhum produto encontrado.
            </p>
            `;
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
                ([category,value]) => `

                    <div>

                        <b>
                            ${escapeHtml(category)}
                        </b>

                        <br>

                        Candidatos:
                        ${value.candidatos || 0}

                        · Mais vendidos:
                        ${value.mais_vendidos || 0}

                        · Tendências:
                        ${value.tendencias || 0}

                        · Válidos:
                        ${value.produtos_validos || 0}

                    </div>

                `
            )
            .join("<hr>");
    }
}


function renderProduct(
    product,
    index
){

    const id =
        "product_" +
        index;


    let badgeRanking = "";

    if(product.ranking_position){

        badgeRanking =
            `
            <span class="badge badge-green">

                🏆
                ${escapeHtml(
                    product.ranking_text
                )}

            </span>
            `;

    }else if(
        product.source ===
        "tendencia"
    ){

        badgeRanking =
            `
            <span class="badge badge-orange">

                📈 Em tendência

            </span>
            `;

    }


    const image =
        product.image
        ?
        `
        <img
            src="${escapeAttribute(
                product.image
            )}"
        >
        `
        :
        "";


    return `

    <div class="product">

        <div class="product-head">

            ${image}

            <div>

                <span class="badge">

                    #${index + 1}

                </span>

                ${badgeRanking}

                <span class="badge">

                    ${escapeHtml(
                        product.category_name
                    )}

                </span>

                <div class="title">

                    ${escapeHtml(
                        product.title
                    )}

                </div>

            </div>

        </div>


        <div class="price">

            ${brl(
                product.price
            )}

        </div>


        ${
            product.original_price
            ?
            `
            <div class="old">

                De:
                ${brl(
                    product.original_price
                )}

            </div>
            `
            :
            ""
        }


        ${
            product.discount > 0
            ?
            `
            <div class="green">

                🔥
                ${product.discount}%
                OFF

            </div>
            `
            :
            ""
        }


        ${
            product.free_shipping
            ?
            `
            <div class="green">

                🚚 Frete grátis

            </div>
            `
            :
            ""
        }


        <div class="score">

            ⭐ Pontuação:
            <b>
                ${product.score}
            </b>

            ${
                product.ranking_position
                ?
                `
                · Ranking:
                #${product.ranking_position}
                `
                :
                ""
            }

        </div>


        <div class="small">

            👤 Vendedor:
            ${escapeHtml(
                product.seller_id ||
                "N/A"
            )}

        </div>


        <br>


        <a
            href="${escapeAttribute(
                product.permalink
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
                ${JSON.stringify(
                    id
                )},
                ${JSON.stringify(
                    product
                )}
            )'
        >

            📢 Gerar anúncio

        </button>


        <button
            id="copy_${id}"
            style="display:none;background:#ff8a00"
            onclick="copiar('${id}')"
        >

            📋 Copiar oferta

        </button>


        <div
            id="ad_${id}"
            class="ad"
        ></div>

    </div>

    `;
}


async function gerarAnuncio(
    id,
    product
){

    const link =
        document.getElementById(
            "link_" + id
        ).value;


    const params =
        new URLSearchParams({

            title:
                product.title,

            price:
                product.price,

            original_price:
                product.original_price || "",

            discount:
                product.discount || 0,

            shipping_free:
                product.free_shipping
                ? "1"
                : "0",

            affiliate_link:
                link
        });


    const response =
        await fetch(
            "/api/gerar-anuncio?" +
            params.toString()
        );


    const data =
        await response.json();


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
}


async function copiar(id){

    const element =
        document.getElementById(
            "ad_" + id
        );

    const text =
        element.textContent.trim();


    if(!text){

        return;
    }


    try{

        await navigator.clipboard.writeText(
            text
        );

        const button =
            document.getElementById(
                "copy_" + id
            );

        const old =
            button.textContent;

        button.textContent =
            "✅ Copiado!";

        setTimeout(
            () =>
                button.textContent =
                    old,
            1500
        );

    }catch(error){

        alert(
            "Selecione o texto e copie."
        );
    }
}


function brl(value){

    return (
        "R$ " +
        Number(
            value || 0
        ).toLocaleString(
            "pt-BR",
            {
                minimumFractionDigits:2,
                maximumFractionDigits:2
            }
        )
    );
}


function escapeHtml(value){

    return String(
        value || ""
    )
    .replace(
        /[&<>"']/g,
        function(char){

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


function escapeAttribute(value){

    return String(
        value || ""
    )
    .replace(
        /"/g,
        "&quot;"
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

        O aplicativo procura automaticamente
        produtos que estão entre os mais vendidos
        e produtos em tendência no Mercado Livre.

        Depois verifica preço, desconto e frete
        para montar um ranking de oportunidades.

    </p>


    {% if conectado %}

        <div
            style="
            background:#eaf8ef;
            padding:10px;
            border-radius:9px;
            "
        >

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
        🔥 Produtos de maior giro
    </h2>


    <button
        onclick="cacar()"
    >

        🚀 BUSCAR MELHORES PRODUTOS AGORA

    </button>


    <p
        id="status"
        class="small"
    >

        Clique para atualizar
        os produtos.

    </p>

</div>


<div class="card">

    <h2>
        🔎 Busca manual
    </h2>


    <input
        id="q"
        placeholder="Ex: celular, air fryer, perfume..."
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

        O ranking prioriza produtos
        que aparecem entre os mais vendidos,
        depois considera tendências,
        preço, desconto e frete grátis.

    </p>


    <div
        id="results"
    >

        <p>
            Faça uma busca para começar.
        </p>

    </div>

</div>


<div class="card">

    <h2>
        🧪 Cobertura da busca
    </h2>


    <div
        id="diagnostico"
        class="diagnostico"
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


    <br>
    <br>


    <a
        href="/mercadolivre/teste-mais-vendidos?category_id=MLB432825"
        target="_blank"
    >

        🏆 Testar Mais Vendidos

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
            else None
    )


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

        "categorias":
            len(
                CATEGORIES
            ),

        "minimo":
            MIN_PRODUCT_PRICE,

        "estrategia":
            "mais vendidos + tendencias",

        "cupons":
            "segunda etapa"
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