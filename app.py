import os
import re
import time
import sqlite3
import secrets
import hashlib
import base64
from urllib.parse import urlencode

import requests
from flask import Flask, request, redirect, session, render_template_string, jsonify


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "troque-esta-chave-em-producao"
)

# ============================================================
# CONFIGURAÇÕES
# ============================================================

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

SITE_ID = "MLB"

CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

DB_FILE = "ofertas.db"

TIMEOUT = 25

MAX_CATEGORIES = 8
MAX_PRODUCTS_PER_CATEGORY = 20
MAX_PRODUCTS_TOTAL = 100


# ============================================================
# BANCO
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT,
            updated_at INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT UNIQUE,
            item_id TEXT,
            title TEXT,
            price REAL,
            original_price REAL,
            discount REAL,
            currency TEXT,
            permalink TEXT,
            category_id TEXT,
            category_name TEXT,
            image_url TEXT,
            affiliate_link TEXT,
            created_at INTEGER,
            updated_at INTEGER
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILIDADES
# ============================================================

def now():
    return int(time.time())


def clean(value):

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value)
    ).strip()


def money(value):

    try:
        if value is None:
            return None

        return float(value)

    except Exception:
        return None


def discount(price, original):

    try:

        price = float(price)
        original = float(original)

        if original <= 0:
            return None

        if price >= original:
            return 0.0

        return round(
            ((original - price) / original) * 100,
            2
        )

    except Exception:
        return None


def product_url(product_id, permalink=None):

    if permalink:
        return permalink

    if product_id:
        return f"https://www.mercadolivre.com.br/p/{product_id}"

    return ""


# ============================================================
# PKCE
# ============================================================

def generate_pkce():

    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(
        verifier.encode("ascii")
    ).digest()

    challenge = base64.urlsafe_b64encode(
        digest
    ).decode("ascii").rstrip("=")

    return verifier, challenge


# ============================================================
# TOKENS
# ============================================================

def save_tokens(
    access_token,
    refresh_token=None,
    expires_in=None,
    user_id=None,
    nickname=None
):

    conn = db()

    old = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id = 1"
    ).fetchone()

    if old:

        if not refresh_token:
            refresh_token = old["refresh_token"]

        if not user_id:
            user_id = old["user_id"]

        if not nickname:
            nickname = old["nickname"]

    expires_at = now() + int(
        expires_in or 21600
    )

    conn.execute("""
        INSERT INTO oauth_tokens
        (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname,
            updated_at
        )
        VALUES
        (1, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(id)
        DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            expires_at = excluded.expires_at,
            user_id = excluded.user_id,
            nickname = excluded.nickname,
            updated_at = excluded.updated_at
    """, (
        access_token,
        refresh_token,
        expires_at,
        user_id,
        nickname,
        now()
    ))

    conn.commit()
    conn.close()


def get_tokens():

    conn = db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id = 1"
    ).fetchone()

    conn.close()

    return row


def refresh_access_token():

    row = get_tokens()

    if not row:
        return None

    refresh_token = row["refresh_token"]

    if not refresh_token:
        return None

    if not CLIENT_ID or not CLIENT_SECRET:
        return None

    try:

        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "refresh_token": refresh_token
            },
            timeout=TIMEOUT
        )

        if response.status_code != 200:

            print(
                "[REFRESH ERRO]",
                response.status_code,
                response.text[:500]
            )

            return None

        data = response.json()

        save_tokens(
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
            user_id=row["user_id"],
            nickname=row["nickname"]
        )

        return data.get("access_token")

    except Exception as e:

        print(
            "[REFRESH EXCEPTION]",
            e
        )

        return None


def get_access_token():

    row = get_tokens()

    if not row:
        return None

    token = row["access_token"]

    if not token:
        return None

    expires_at = row["expires_at"] or 0

    if expires_at < now() + 120:

        new_token = refresh_access_token()

        if new_token:
            return new_token

    return token


# ============================================================
# API
# ============================================================

def api_get(
    path,
    params=None,
    retry=True
):

    token = get_access_token()

    if not token:

        return {
            "ok": False,
            "status": 401,
            "data": None,
            "error": "Mercado Livre não conectado."
        }

    try:

        response = requests.get(
            ML_API + path,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            },
            params=params or {},
            timeout=TIMEOUT
        )

        if response.status_code == 401 and retry:

            new_token = refresh_access_token()

            if new_token:

                return api_get(
                    path,
                    params=params,
                    retry=False
                )

        try:
            data = response.json()
        except Exception:
            data = None

        if response.status_code >= 400:

            error = ""

            if isinstance(data, dict):

                error = (
                    data.get("message")
                    or data.get("error")
                    or data.get("cause")
                    or ""
                )

            if not error:

                error = response.text[:500]

            return {
                "ok": False,
                "status": response.status_code,
                "data": data,
                "error": clean(error)
            }

        return {
            "ok": True,
            "status": response.status_code,
            "data": data,
            "error": None
        }

    except Exception as e:

        return {
            "ok": False,
            "status": 0,
            "data": None,
            "error": str(e)
        }


# ============================================================
# USUÁRIO
# ============================================================

def get_me():

    return api_get(
        "/users/me"
    )


# ============================================================
# CATEGORIAS
# ============================================================

def discover_categories(query):

    query = clean(query)

    if not query:

        return {
            "ok": False,
            "categories": [],
            "error": "Produto não informado."
        }

    result = api_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        params={
            "q": query,
            "limit": MAX_CATEGORIES
        }
    )

    if not result["ok"]:

        return {
            "ok": False,
            "categories": [],
            "status": result["status"],
            "error": result["error"]
        }

    data = result["data"]

    # ========================================================
    # CORREÇÃO IMPORTANTE
    #
    # API atual:
    #
    # category_id
    # category_name
    #
    # Versões antigas:
    #
    # id
    # name
    # ========================================================

    if isinstance(data, dict):

        # Algumas respostas podem vir embrulhadas
        # em uma propriedade.
        data = (
            data.get("results")
            or data.get("categories")
            or data.get("data")
            or []
        )

    if not isinstance(data, list):

        data = []

    categories = []

    for item in data:

        if not isinstance(item, dict):
            continue

        category_id = (
            item.get("category_id")
            or item.get("id")
        )

        category_name = (
            item.get("category_name")
            or item.get("name")
            or ""
        )

        domain_id = item.get(
            "domain_id"
        )

        domain_name = item.get(
            "domain_name"
        )

        if not category_id:
            continue

        categories.append({
            "id": category_id,
            "name": category_name,
            "category_id": category_id,
            "category_name": category_name,
            "domain_id": domain_id,
            "domain_name": domain_name,
            "score": item.get("score"),
            "source": "domain_discovery"
        })

    return {
        "ok": True,
        "categories": categories,
        "status": result["status"],
        "error": None
    }


# ============================================================
# HIGHLIGHTS
# ============================================================

def get_highlights(category_id):

    if not category_id:

        return {
            "ok": False,
            "products": [],
            "error": "Categoria não informada."
        }

    result = api_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    if not result["ok"]:

        return {
            "ok": False,
            "products": [],
            "status": result["status"],
            "error": result["error"]
        }

    data = result["data"]

    if isinstance(data, dict):

        data = (
            data.get("content")
            or data.get("results")
            or data.get("products")
            or []
        )

    if not isinstance(data, list):

        data = []

    products = []

    for item in data:

        if not isinstance(item, dict):
            continue

        product_id = item.get("id")

        if not product_id:
            continue

        products.append({
            "id": product_id,
            "type": item.get("type"),
            "position": item.get("position")
        })

    return {
        "ok": True,
        "products": products,
        "status": result["status"],
        "error": None
    }


# ============================================================
# PRODUTO
# ============================================================

def get_product(product_id):

    return api_get(
        f"/products/{product_id}"
    )


def extract_product(product):

    if not isinstance(product, dict):
        return None

    product_id = product.get("id")

    if not product_id:
        return None

    buy_box = product.get(
        "buy_box_winner"
    )

    if not isinstance(buy_box, dict):
        buy_box = None

    price = None
    original_price = None
    item_id = None
    seller_id = None
    currency = "BRL"

    if buy_box:

        item_id = buy_box.get(
            "item_id"
        )

        seller_id = buy_box.get(
            "seller_id"
        )

        price = money(
            buy_box.get("price")
        )

        original_price = money(
            buy_box.get("original_price")
        )

        currency = (
            buy_box.get("currency_id")
            or currency
        )

    # Tenta outras estruturas de preço.
    if price is None:

        for key in [
            "price",
            "sale_price",
            "current_price"
        ]:

            if key in product:

                value = money(
                    product.get(key)
                )

                if value is not None:

                    price = value
                    break

    if original_price is None:

        for key in [
            "original_price",
            "regular_price",
            "list_price"
        ]:

            if key in product:

                value = money(
                    product.get(key)
                )

                if value is not None:

                    original_price = value
                    break

    pictures = product.get(
        "pictures"
    ) or []

    image_url = None

    if pictures:

        first = pictures[0]

        if isinstance(first, dict):

            image_url = (
                first.get("url")
                or first.get("secure_url")
            )

    permalink = product.get(
        "permalink"
    )

    return {
        "product_id": product_id,
        "item_id": item_id,
        "seller_id": seller_id,
        "title": clean(
            product.get("name")
            or product.get("family_name")
            or ""
        ),
        "price": price,
        "original_price": original_price,
        "discount": discount(
            price,
            original_price
        ),
        "currency": currency,
        "permalink": product_url(
            product_id,
            permalink
        ),
        "image_url": image_url,
        "has_buy_box": bool(
            buy_box
        ),
        "parent_id": product.get(
            "parent_id"
        ),
        "children_ids": product.get(
            "children_ids"
        ) or [],
        "domain_id": product.get(
            "domain_id"
        )
    }


# ============================================================
# PREÇOS
# ============================================================

def get_sale_price(item_id):

    if not item_id:

        return {
            "ok": False,
            "status": 0,
            "data": None,
            "error": "Item não informado."
        }

    return api_get(
        f"/items/{item_id}/sale_price",
        params={
            "context": "channel_marketplace"
        }
    )


def get_prices(item_id):

    if not item_id:

        return {
            "ok": False,
            "status": 0,
            "prices": [],
            "error": "Item não informado."
        }

    result = api_get(
        f"/items/{item_id}/prices"
    )

    if not result["ok"]:

        return {
            "ok": False,
            "status": result["status"],
            "prices": [],
            "error": result["error"]
        }

    data = result["data"]

    if isinstance(data, dict):

        prices = (
            data.get("prices")
            or []
        )

    elif isinstance(data, list):

        prices = data

    else:

        prices = []

    return {
        "ok": True,
        "status": result["status"],
        "prices": prices,
        "error": None
    }


def enrich_price(data):

    if not data:
        return data

    item_id = data.get(
        "item_id"
    )

    if not item_id:
        return data

    sale = get_sale_price(
        item_id
    )

    if sale["ok"] and isinstance(
        sale["data"],
        dict
    ):

        sale_data = sale["data"]

        amount = money(
            sale_data.get("amount")
        )

        regular = money(
            sale_data.get("regular_amount")
        )

        if amount is not None:
            data["price"] = amount

        if regular is not None:
            data["original_price"] = regular

    if (
        data.get("price") is None
        or data.get("original_price") is None
    ):

        prices = get_prices(
            item_id
        )

        if prices["ok"]:

            promotion = None
            standard = None

            for item in prices["prices"]:

                if not isinstance(item, dict):
                    continue

                if item.get("type") == "promotion":
                    promotion = item

                if item.get("type") == "standard":
                    standard = item

            selected = (
                promotion
                or standard
            )

            if selected:

                amount = money(
                    selected.get("amount")
                )

                regular = money(
                    selected.get("regular_amount")
                )

                if amount is not None:
                    data["price"] = amount

                if regular is not None:
                    data["original_price"] = regular

    data["discount"] = discount(
        data.get("price"),
        data.get("original_price")
    )

    return data


# ============================================================
# SALVAR
# ============================================================

def save_product(
    data,
    category_id=None,
    category_name=None
):

    if not data:
        return

    product_id = data.get(
        "product_id"
    )

    if not product_id:
        return

    conn = db()

    conn.execute("""
        INSERT INTO ofertas
        (
            product_id,
            item_id,
            title,
            price,
            original_price,
            discount,
            currency,
            permalink,
            category_id,
            category_name,
            image_url,
            affiliate_link,
            created_at,
            updated_at
        )
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)

        ON CONFLICT(product_id)
        DO UPDATE SET

            item_id = excluded.item_id,
            title = excluded.title,
            price = excluded.price,
            original_price = excluded.original_price,
            discount = excluded.discount,
            currency = excluded.currency,
            permalink = excluded.permalink,
            category_id = excluded.category_id,
            category_name = excluded.category_name,
            image_url = excluded.image_url,
            updated_at = excluded.updated_at
    """, (
        product_id,
        data.get("item_id"),
        data.get("title"),
        data.get("price"),
        data.get("original_price"),
        data.get("discount"),
        data.get("currency"),
        data.get("permalink"),
        category_id,
        category_name,
        data.get("image_url"),
        now(),
        now()
    ))

    conn.commit()
    conn.close()


# ============================================================
# BUSCA
# ============================================================

def search_offers(
    query,
    minimum_discount=0
):

    categories_result = discover_categories(
        query
    )

    if not categories_result["ok"]:

        return {
            "ok": False,
            "query": query,
            "error": categories_result["error"],
            "categories": [],
            "category_debug": [],
            "products": [],
            "offers": [],
            "stats": {}
        }

    categories = (
        categories_result["categories"]
    )

    all_products = []
    seen = set()

    category_debug = []

    for category in categories:

        category_id = category["id"]

        highlights = get_highlights(
            category_id
        )

        if not highlights["ok"]:

            category_debug.append({
                "id": category_id,
                "nome": category.get("name"),
                "status": highlights.get(
                    "status"
                ),
                "erro": highlights.get(
                    "error"
                ),
                "produtos": 0,
                "tipos": {}
            })

            continue

        entries = highlights[
            "products"
        ]

        types = {}

        for entry in entries:

            entry_type = (
                entry.get("type")
                or "UNKNOWN"
            )

            types[entry_type] = (
                types.get(
                    entry_type,
                    0
                ) + 1
            )

            entry_id = entry.get(
                "id"
            )

            if not entry_id:
                continue

            # USER_PRODUCT continua ignorado
            # porque já comprovamos 403.
            if entry_type == "USER_PRODUCT":
                continue

            # ITEM de terceiros também não
            # será aberto automaticamente.
            if entry_type == "ITEM":
                continue

            if entry_type != "PRODUCT":
                continue

            if entry_id in seen:
                continue

            seen.add(entry_id)

            all_products.append({
                "product_id": entry_id,
                "category_id": category_id,
                "category_name": category.get(
                    "name"
                ),
                "source": "highlights_product"
            })

        category_debug.append({
            "id": category_id,
            "nome": category.get(
                "name"
            ),
            "status": 200,
            "erro": None,
            "produtos": len(entries),
            "tipos": types
        })

    products = []
    offers = []

    stats = {
        "categorias_encontradas": len(
            categories
        ),
        "categorias_analisadas": len(
            category_debug
        ),
        "produtos_ranking": sum(
            x["produtos"]
            for x in category_debug
        ),
        "produtos_unicos": len(
            all_products
        ),
        "produtos_consultados": 0,
        "buy_box_encontradas": 0,
        "produtos_sem_buy_box": 0,
        "produtos_com_preco": 0,
        "ofertas_encontradas": 0,
        "sem_desconto": 0,
        "erros": 0
    }

    for candidate in all_products[
        :MAX_PRODUCTS_TOTAL
    ]:

        product_id = candidate[
            "product_id"
        ]

        result = get_product(
            product_id
        )

        stats[
            "produtos_consultados"
        ] += 1

        if not result["ok"]:

            stats["erros"] += 1

            products.append({
                "product_id": product_id,
                "title": "",
                "price": None,
                "original_price": None,
                "discount": None,
                "permalink": product_url(
                    product_id
                ),
                "has_buy_box": False,
                "error": result["error"],
                "status": result["status"],
                "category_id": candidate[
                    "category_id"
                ],
                "category_name": candidate[
                    "category_name"
                ]
            })

            continue

        data = extract_product(
            result["data"]
        )

        if not data:

            stats["erros"] += 1
            continue

        if data["has_buy_box"]:

            stats[
                "buy_box_encontradas"
            ] += 1

            data = enrich_price(
                data
            )

        else:

            stats[
                "produtos_sem_buy_box"
            ] += 1

        data["category_id"] = (
            candidate["category_id"]
        )

        data["category_name"] = (
            candidate["category_name"]
        )

        if data.get("price") is not None:

            stats[
                "produtos_com_preco"
            ] += 1

        if (
            data.get("discount") is not None
            and data.get("discount") >= float(
                minimum_discount
            )
            and data.get("price") is not None
            and data.get("original_price") is not None
            and data.get("original_price")
            > data.get("price")
        ):

            stats[
                "ofertas_encontradas"
            ] += 1

            offers.append(
                data.copy()
            )

        elif (
            data.get("price") is not None
            and data.get("original_price")
            is not None
            and data.get("original_price")
            <= data.get("price")
        ):

            stats[
                "sem_desconto"
            ] += 1

        products.append(
            data.copy()
        )

        save_product(
            data,
            category_id=candidate[
                "category_id"
            ],
            category_name=candidate[
                "category_name"
            ]
        )

    offers.sort(
        key=lambda x: x.get(
            "discount"
        ) or 0,
        reverse=True
    )

    return {
        "ok": True,
        "query": query,
        "categories": categories,
        "category_debug": category_debug,
        "products": products,
        "offers": offers,
        "stats": stats
    }


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    token = get_tokens()

    connected = bool(
        token and token["access_token"]
    )

    return render_template_string(
        HTML,
        connected=connected,
        nickname=(
            token["nickname"]
            if token else None
        ),
        user_id=(
            token["user_id"]
            if token else None
        )
    )


# ============================================================
# LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def ml_login():

    if not CLIENT_ID:

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

    session["oauth_state"] = state
    session["code_verifier"] = verifier

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256"
    }

    return redirect(
        ML_AUTH
        + "?"
        + urlencode(params)
    )


# ============================================================
# CALLBACK
# ============================================================

@app.route("/mercadolivre/callback")
def ml_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return f"""
        <h2>Erro Mercado Livre</h2>
        <pre>{clean(error)}</pre>
        """

    state = request.args.get(
        "state"
    )

    if state != session.get(
        "oauth_state"
    ):

        return (
            "State OAuth inválido.",
            400
        )

    code = request.args.get(
        "code"
    )

    if not code:

        return (
            "Código OAuth não recebido.",
            400
        )

    verifier = session.get(
        "code_verifier"
    )

    if not verifier:

        return (
            "Code verifier não encontrado.",
            400
        )

    try:

        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type":
                    "authorization_code",
                "client_id":
                    CLIENT_ID,
                "client_secret":
                    CLIENT_SECRET,
                "code":
                    code,
                "redirect_uri":
                    REDIRECT_URI,
                "code_verifier":
                    verifier
            },
            timeout=TIMEOUT
        )

        if response.status_code != 200:

            return f"""
            <h2>Erro ao obter token</h2>
            <pre>{response.text}</pre>
            """, 400

        data = response.json()

        access_token = data.get(
            "access_token"
        )

        if not access_token:

            return (
                "Access token não recebido.",
                400
            )

        save_tokens(
            access_token=access_token,
            refresh_token=data.get(
                "refresh_token"
            ),
            expires_in=data.get(
                "expires_in"
            )
        )

        me = get_me()

        if me["ok"]:

            user = me["data"]

            save_tokens(
                access_token=access_token,
                refresh_token=data.get(
                    "refresh_token"
                ),
                expires_in=data.get(
                    "expires_in"
                ),
                user_id=user.get("id"),
                nickname=user.get(
                    "nickname"
                )
            )

        session.pop(
            "oauth_state",
            None
        )

        session.pop(
            "code_verifier",
            None
        )

        return redirect("/")

    except Exception as e:

        return f"""
        <h2>Erro OAuth</h2>
        <pre>{clean(e)}</pre>
        """, 500


# ============================================================
# LOGOUT
# ============================================================

@app.route("/mercadolivre/logout")
def logout():

    conn = db()

    conn.execute(
        "DELETE FROM oauth_tokens WHERE id = 1"
    )

    conn.commit()
    conn.close()

    return redirect("/")


# ============================================================
# BUSCA HTML
# ============================================================

@app.route("/buscar")
def buscar():

    query = clean(
        request.args.get(
            "q",
            ""
        )
    )

    try:

        minimum = float(
            request.args.get(
                "desconto",
                10
            )
        )

    except Exception:

        minimum = 10

    if not query:

        return redirect("/")

    result = search_offers(
        query,
        minimum
    )

    return render_template_string(
        RESULTS_HTML,
        result=result,
        query=query,
        minimum=minimum
    )


# ============================================================
# API BUSCA
# ============================================================

@app.route("/api/buscar")
def api_buscar():

    query = clean(
        request.args.get(
            "q",
            ""
        )
    )

    try:

        minimum = float(
            request.args.get(
                "desconto",
                10
            )
        )

    except Exception:

        minimum = 10

    if not query:

        return jsonify({
            "ok": False,
            "erro": "Informe q."
        }), 400

    return jsonify(
        search_offers(
            query,
            minimum
        )
    )


# ============================================================
# TESTE CATEGORIA
# ============================================================

@app.route("/mercadolivre/teste-categoria")
def teste_categoria():

    query = clean(
        request.args.get(
            "q",
            "fone"
        )
    )

    result = discover_categories(
        query
    )

    return jsonify({
        "consulta": query,
        "status": result.get(
            "status"
        ),
        "erro": result.get(
            "error"
        ),
        "categorias": result.get(
            "categories",
            []
        ),
        "total": len(
            result.get(
                "categories",
                []
            )
        )
    })


# ============================================================
# TESTE HIGHLIGHTS
# ============================================================

@app.route("/mercadolivre/teste-highlights")
def teste_highlights():

    category_id = clean(
        request.args.get(
            "category_id",
            ""
        )
    )

    if not category_id:

        return jsonify({
            "ok": False,
            "erro":
                "Informe category_id."
        }), 400

    result = get_highlights(
        category_id
    )

    return jsonify({
        "ok": result["ok"],
        "category_id": category_id,
        "status": result.get(
            "status"
        ),
        "erro": result.get(
            "error"
        ),
        "total": len(
            result.get(
                "products",
                []
            )
        ),
        "produtos": result.get(
            "products",
            []
        )
    })


# ============================================================
# TESTE BUSCA
# ============================================================

@app.route("/mercadolivre/teste-busca")
def teste_busca():

    query = clean(
        request.args.get(
            "q",
            "fone"
        )
    )

    try:

        minimum = float(
            request.args.get(
                "desconto",
                10
            )
        )

    except Exception:

        minimum = 10

    result = search_offers(
        query,
        minimum
    )

    return jsonify({
        "ok": result["ok"],
        "consulta": query,
        "categorias":
            result.get(
                "categories",
                []
            ),
        "categorias_debug":
            result.get(
                "category_debug",
                []
            ),
        "produtos":
            result.get(
                "products",
                []
            ),
        "ofertas":
            result.get(
                "offers",
                []
            ),
        "stats":
            result.get(
                "stats",
                {}
            ),
        "erro":
            result.get(
                "error"
            )
    })


# ============================================================
# TESTE PRODUTO
# ============================================================

@app.route("/mercadolivre/teste-produto")
def teste_produto():

    product_id = clean(
        request.args.get(
            "product_id",
            ""
        )
    )

    if not product_id:

        return jsonify({
            "ok": False,
            "erro":
                "Informe product_id."
        }), 400

    result = get_product(
        product_id
    )

    if not result["ok"]:

        return jsonify({
            "ok": False,
            "product_id": product_id,
            "status":
                result["status"],
            "erro":
                result["error"]
        }), result["status"] or 500

    data = extract_product(
        result["data"]
    )

    return jsonify({
        "ok": True,
        "product_id":
            product_id,
        "produto":
            data,
        "raw":
            result["data"]
    })


# ============================================================
# TESTE ITEM
# ============================================================

@app.route("/mercadolivre/item")
def teste_item():

    item_id = clean(
        request.args.get(
            "item_id",
            ""
        )
    )

    if not item_id:

        return jsonify({
            "ok": False,
            "erro":
                "Informe item_id."
        }), 400

    result = api_get(
        f"/items/{item_id}"
    )

    return jsonify({
        "ok":
            result["ok"],
        "item_id":
            item_id,
        "status":
            result["status"],
        "erro":
            result["error"],
        "item":
            result["data"]
    })


# ============================================================
# TESTE PREÇO
# ============================================================

@app.route("/mercadolivre/teste-preco")
def teste_preco():

    item_id = clean(
        request.args.get(
            "item_id",
            ""
        )
    )

    if not item_id:

        return jsonify({
            "ok": False,
            "erro":
                "Informe item_id."
        }), 400

    sale = get_sale_price(
        item_id
    )

    prices = get_prices(
        item_id
    )

    return jsonify({
        "item_id":
            item_id,
        "sale_price": {
            "ok":
                sale["ok"],
            "status":
                sale["status"],
            "erro":
                sale["error"],
            "dados":
                sale["data"]
        },
        "prices": {
            "ok":
                prices["ok"],
            "status":
                prices["status"],
            "erro":
                prices["error"],
            "dados":
                prices["prices"]
        }
    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def diagnostico():

    token = get_tokens()

    me = get_me()

    return jsonify({

        "app": {

            "client_id":
                bool(CLIENT_ID),

            "client_secret":
                bool(CLIENT_SECRET),

            "redirect_uri":
                REDIRECT_URI

        },

        "oauth": {

            "token_salvo":
                bool(token),

            "user_id":
                token["user_id"]
                if token else None,

            "nickname":
                token["nickname"]
                if token else None,

            "expires_at":
                token["expires_at"]
                if token else None

        },

        "mercadolivre": {

            "me_ok":
                me["ok"],

            "me_status":
                me["status"],

            "me":
                me["data"]
                if me["ok"]
                else None,

            "erro":
                me["error"]

        }

    })


# ============================================================
# SALVOS
# ============================================================

@app.route("/api/salvos")
def api_salvos():

    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY
            COALESCE(discount, 0) DESC,
            updated_at DESC
        LIMIT 100
    """).fetchall()

    conn.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# ============================================================
# HTML
# ============================================================

HTML = r"""
<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f3f4f6;
    font-family: Arial, sans-serif;
    color: #111827;
}

.container {
    width: min(1050px, 94%);
    margin: 30px auto;
}

.card {
    background: white;
    border-radius: 18px;
    padding: 22px;
    margin-bottom: 20px;
    box-shadow: 0 8px 30px rgba(0,0,0,.07);
}

h1 {
    margin-top: 0;
}

.subtitle {
    color: #6b7280;
}

.status {
    display: inline-block;
    padding: 10px 14px;
    border-radius: 10px;
    background: #ecfdf5;
    color: #047857;
    margin-bottom: 15px;
}

.off {
    background: #fef2f2;
    color: #b91c1c;
}

form {
    display: grid;
    grid-template-columns: 1fr 150px 120px;
    gap: 10px;
}

input,
button {
    padding: 13px;
    border-radius: 10px;
    font-size: 16px;
}

input {
    border: 1px solid #d1d5db;
}

button {
    border: 0;
    background: #3483fa;
    color: white;
    font-weight: bold;
}

.links {
    margin-top: 15px;
    display: flex;
    gap: 15px;
    flex-wrap: wrap;
}

a {
    color: #2563eb;
    text-decoration: none;
}

.info {
    padding: 14px;
    border-radius: 10px;
    background: #eff6ff;
    color: #1e3a8a;
}

@media(max-width:700px) {

    form {
        grid-template-columns: 1fr;
    }

}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>🛒 Caçador de Ofertas</h1>

<p class="subtitle">
Mercado Livre • Produtos • Preços • Descontos
</p>

{% if connected %}

<div class="status">
🟢 Mercado Livre conectado
{% if nickname %}
— {{ nickname }}
{% endif %}
</div>

{% else %}

<div class="status off">
🔴 Mercado Livre não conectado
</div>

{% endif %}

<form action="/buscar">

<input
name="q"
placeholder="Ex.: fone bluetooth"
required
>

<input
name="desconto"
type="number"
min="0"
max="100"
value="10"
>

<button>
🔎 Buscar
</button>

</form>

<div class="links">

{% if connected %}

<a href="/mercadolivre/logout">
Desconectar
</a>

{% else %}

<a href="/mercadolivre/login">
🔐 Conectar Mercado Livre
</a>

{% endif %}

<a href="/mercadolivre/diagnostico">
Diagnóstico
</a>

<a href="/mercadolivre/teste-busca?q=fone&desconto=10">
Teste Fone
</a>

</div>

</div>

<div class="card">

<h2>Como funciona</h2>

<div class="info">

Digite um produto. O sistema encontra categorias,
consulta os produtos em destaque e analisa os dados
disponíveis no catálogo do Mercado Livre.

</div>

<br>

<div class="info">

Quando existir preço atual + preço original,
o sistema calcula automaticamente o desconto.

</div>

</div>

</div>

</body>

</html>
"""


# ============================================================
# RESULTADOS
# ============================================================

RESULTS_HTML = r"""
<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Resultados</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f3f4f6;
    font-family: Arial, sans-serif;
}

.container {
    width: min(1200px, 94%);
    margin: 25px auto;
}

.card {
    background: white;
    border-radius: 18px;
    padding: 20px;
    margin-bottom: 18px;
    box-shadow: 0 7px 25px rgba(0,0,0,.07);
}

.stats {
    display: grid;
    grid-template-columns:
    repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
}

.stat {
    background: #f9fafb;
    padding: 15px;
    border-radius: 12px;
}

.stat strong {
    display: block;
    font-size: 25px;
    margin-top: 5px;
}

.products {
    display: grid;
    grid-template-columns:
    repeat(auto-fill, minmax(280px, 1fr));
    gap: 15px;
}

.product {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 15px;
    padding: 16px;
}

.product h3 {
    font-size: 16px;
    line-height: 1.4;
}

.price {
    font-size: 24px;
    font-weight: bold;
}

.old {
    text-decoration: line-through;
    color: #6b7280;
}

.discount {
    display: inline-block;
    background: #dcfce7;
    color: #166534;
    padding: 6px 9px;
    border-radius: 8px;
    font-weight: bold;
    margin-top: 7px;
}

.muted {
    color: #6b7280;
    font-size: 14px;
}

.actions {
    margin-top: 12px;
}

.actions a {
    display: inline-block;
    background: #3483fa;
    color: white;
    padding: 9px 12px;
    border-radius: 8px;
}

.warning {
    background: #fff7ed;
    border-left: 4px solid #f97316;
    padding: 15px;
    border-radius: 8px;
}

.success {
    background: #ecfdf5;
    border-left: 4px solid #10b981;
    padding: 15px;
    border-radius: 8px;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: #111827;
    color: white;
    padding: 15px;
    border-radius: 10px;
}

</style>

</head>

<body>

<div class="container">

<a href="/">← Nova busca</a>

<div class="card">

<h1>
Resultados: {{ query }}
</h1>

<p class="muted">
Desconto mínimo: {{ minimum }}%
</p>

<div class="stats">

<div class="stat">
Categorias
<strong>
{{ result.stats.categorias_encontradas }}
</strong>
</div>

<div class="stat">
Produtos no ranking
<strong>
{{ result.stats.produtos_ranking }}
</strong>
</div>

<div class="stat">
Produtos únicos
<strong>
{{ result.stats.produtos_unicos }}
</strong>
</div>

<div class="stat">
Consultados
<strong>
{{ result.stats.produtos_consultados }}
</strong>
</div>

<div class="stat">
Com Buy Box
<strong>
{{ result.stats.buy_box_encontradas }}
</strong>
</div>

<div class="stat">
Com preço
<strong>
{{ result.stats.produtos_com_preco }}
</strong>
</div>

<div class="stat">
Ofertas
<strong>
{{ result.stats.ofertas_encontradas }}
</strong>
</div>

</div>

</div>


{% if result.offers %}

<div class="card">

<h2>
🔥 Ofertas encontradas
</h2>

<div class="products">

{% for item in result.offers %}

<div class="product">

<h3>
{{ item.title }}
</h3>

{% if item.original_price %}

<div class="old">
R$ {{ "%.2f"|format(item.original_price) }}
</div>

{% endif %}

{% if item.price %}

<div class="price">
R$ {{ "%.2f"|format(item.price) }}
</div>

{% endif %}

{% if item.discount is not none %}

<div class="discount">
{{ "%.2f"|format(item.discount) }}% OFF
</div>

{% endif %}

<p class="muted">
Produto: {{ item.product_id }}
</p>

<div class="actions">

<a
href="{{ item.permalink }}"
target="_blank"
>
Abrir produto
</a>

</div>

</div>

{% endfor %}

</div>

</div>

{% else %}

<div class="card">

<div class="warning">

<strong>
Nenhuma oferta com desconto verificável.
</strong>

<br><br>

Mas isso não significa que nenhum produto foi
encontrado. Veja os produtos abaixo.

</div>

</div>

{% endif %}


<div class="card">

<h2>
📦 Produtos encontrados
</h2>

<div class="products">

{% for item in result.products %}

<div class="product">

<h3>
{{ item.title or item.product_id }}
</h3>

{% if item.price is not none %}

<div class="price">
R$ {{ "%.2f"|format(item.price) }}
</div>

{% else %}

<p class="muted">
Preço não disponibilizado pela API.
</p>

{% endif %}

{% if item.original_price is not none %}

<div class="old">
R$ {{ "%.2f"|format(item.original_price) }}
</div>

{% endif %}

{% if item.discount is not none and item.discount > 0 %}

<div class="discount">
{{ "%.2f"|format(item.discount) }}% OFF
</div>

{% endif %}

<p class="muted">
ID: {{ item.product_id }}
</p>

{% if item.has_buy_box %}

<p class="muted">
🟢 Buy Box encontrada
</p>

{% else %}

<p class="muted">
Catálogo sem Buy Box disponível.
</p>

{% endif %}

<div class="actions">

<a
href="{{ item.permalink }}"
target="_blank"
>
Ver produto
</a>

</div>

</div>

{% endfor %}

</div>

</div>


<div class="card">

<h2>
📊 Diagnóstico
</h2>

<details>

<summary>
Categorias
</summary>

<pre>{{ result.category_debug | tojson(indent=2) }}</pre>

</details>

</div>

</div>

</body>

</html>
"""


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