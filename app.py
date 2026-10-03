import os
import re
import json
import time
import base64
import hashlib
import secrets
import sqlite3
import unicodedata

from urllib.parse import urlencode, quote_plus

import requests

from flask import (
    Flask,
    request,
    redirect,
    jsonify,
    render_template_string,
    session,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "troque-esta-chave-em-producao"
)

PORT = int(
    os.getenv(
        "PORT",
        "8080"
    )
)

DB_FILE = os.getenv(
    "DB_FILE",
    "ofertas.db"
)


# ============================================================
# MERCADO LIVRE
# ============================================================

ML_API = (
    "https://api.mercadolibre.com"
)

ML_AUTH = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN = (
    "https://api.mercadolibre.com/oauth/token"
)

ML_SITE = "MLB"

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


# ============================================================
# CONFIG
# ============================================================

REQUEST_TIMEOUT = 30

MAX_CATEGORIES = 8

MAX_OFFERS = 100

MAX_FAMILY_PRODUCTS = 40

DEFAULT_DISCOUNT = 10


# ============================================================
# HTTP
# ============================================================

http = requests.Session()

http.headers.update({
    "User-Agent": "CacadorDeOfertas/1.0",
    "Accept": "application/json",
})


# ============================================================
# BANCO
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY CHECK (id = 1),
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
            item_id TEXT UNIQUE,
            titulo TEXT,
            preco REAL,
            preco_original REAL,
            desconto REAL,
            url TEXT,
            imagem TEXT,
            seller_id TEXT,
            categoria_id TEXT,
            categoria_nome TEXT,
            fonte TEXT,
            criado_em INTEGER
        )
    """)

    conn.commit()

    conn.close()


init_db()


# ============================================================
# UTILITÁRIOS
# ============================================================

def now_ts():

    return int(
        time.time()
    )


def normalize_text(
    text
):

    if text is None:
        return ""

    text = str(text)

    text = unicodedata.normalize(
        "NFKD",
        text
    )

    text = (
        text
        .encode(
            "ascii",
            "ignore"
        )
        .decode(
            "ascii"
        )
    )

    text = text.lower()

    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def normalize_query(
    text
):

    return normalize_text(
        text
    )


def safe_float(
    value
):

    try:

        if value is None:
            return None

        return float(
            value
        )

    except Exception:

        return None


# ============================================================
# OAUTH
# ============================================================

def generate_code_verifier():

    return secrets.token_urlsafe(
        64
    )[:128]


def generate_code_challenge(
    verifier
):

    digest = hashlib.sha256(
        verifier.encode(
            "ascii"
        )
    ).digest()

    return (
        base64
        .urlsafe_b64encode(
            digest
        )
        .decode(
            "ascii"
        )
        .rstrip("=")
    )


def save_tokens(
    access_token,
    refresh_token=None,
    expires_in=None,
    user_id=None,
    nickname=None
):

    conn = get_db()

    old = conn.execute(
        """
        SELECT
            refresh_token,
            user_id,
            nickname
        FROM oauth_tokens
        WHERE id = 1
        """
    ).fetchone()

    if old:

        if refresh_token is None:
            refresh_token = old[
                "refresh_token"
            ]

        if user_id is None:
            user_id = old[
                "user_id"
            ]

        if nickname is None:
            nickname = old[
                "nickname"
            ]

    expires_at = (
        now_ts()
        + int(
            expires_in or 0
        )
    )

    conn.execute(
        """
        INSERT INTO oauth_tokens (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname,
            updated_at
        )
        VALUES (
            1, ?, ?, ?, ?, ?, ?
        )

        ON CONFLICT(id)
        DO UPDATE SET
            access_token =
                excluded.access_token,

            refresh_token =
                excluded.refresh_token,

            expires_at =
                excluded.expires_at,

            user_id =
                excluded.user_id,

            nickname =
                excluded.nickname,

            updated_at =
                excluded.updated_at
        """,
        (
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname,
            now_ts(),
        )
    )

    conn.commit()

    conn.close()


def get_tokens():

    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM oauth_tokens
        WHERE id = 1
        """
    ).fetchone()

    conn.close()

    if not row:
        return None

    return dict(
        row
    )


def refresh_access_token():

    tokens = get_tokens()

    if not tokens:
        return None

    refresh_token = tokens.get(
        "refresh_token"
    )

    if not refresh_token:
        return None

    if not ML_CLIENT_ID:
        return None

    if not ML_CLIENT_SECRET:
        return None

    data = {
        "grant_type":
            "refresh_token",

        "client_id":
            ML_CLIENT_ID,

        "client_secret":
            ML_CLIENT_SECRET,

        "refresh_token":
            refresh_token,
    }

    try:

        response = http.post(
            ML_TOKEN,
            data=data,
            timeout=REQUEST_TIMEOUT,
        )

    except Exception:

        return None

    try:

        payload = response.json()

    except Exception:

        payload = {}

    if response.status_code != 200:

        return None

    access_token = payload.get(
        "access_token"
    )

    if not access_token:

        return None

    save_tokens(
        access_token=
            access_token,

        refresh_token=
            payload.get(
                "refresh_token"
            ),

        expires_in=
            payload.get(
                "expires_in",
                21600
            ),

        user_id=
            tokens.get(
                "user_id"
            ),

        nickname=
            tokens.get(
                "nickname"
            ),
    )

    return access_token


def get_access_token():

    tokens = get_tokens()

    if not tokens:
        return None

    token = tokens.get(
        "access_token"
    )

    expires_at = int(
        tokens.get(
            "expires_at"
        ) or 0
    )

    if (
        token
        and expires_at
        > now_ts() + 120
    ):

        return token

    return refresh_access_token()


# ============================================================
# API
# ============================================================

def api_get(
    path,
    params=None,
    retry_refresh=True,
    timeout=REQUEST_TIMEOUT
):

    token = get_access_token()

    if not token:

        return {
            "ok": False,
            "status": 401,
            "data": None,
            "error":
                "Mercado Livre não autorizado.",
            "url":
                ML_API + path,
        }

    url = (
        ML_API + path
    )

    headers = {
        "Authorization":
            f"Bearer {token}",

        "Accept":
            "application/json",
    }

    try:

        response = http.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    except requests.RequestException as e:

        return {
            "ok": False,
            "status": 0,
            "data": None,
            "error": str(e),
            "url": url,
        }

    if (
        response.status_code == 401
        and retry_refresh
    ):

        new_token = (
            refresh_access_token()
        )

        if new_token:

            return api_get(
                path,
                params=params,
                retry_refresh=False,
                timeout=timeout,
            )

    try:

        data = response.json()

    except Exception:

        data = None

    if (
        200
        <= response.status_code
        < 300
    ):

        return {
            "ok": True,
            "status":
                response.status_code,
            "data": data,
            "error": None,
            "url":
                response.url,
        }

    error_message = None

    if isinstance(
        data,
        dict
    ):

        error_message = (
            data.get(
                "message"
            )
            or data.get(
                "error"
            )
            or data.get(
                "cause"
            )
        )

    if isinstance(
        error_message,
        list
    ):

        error_message = json.dumps(
            error_message,
            ensure_ascii=False
        )

    if not error_message:

        error_message = (
            response.text[:500]
        )

    return {
        "ok": False,
        "status":
            response.status_code,
        "data": data,
        "error":
            error_message,
        "url":
            response.url,
    }


# ============================================================
# OAUTH LOGIN
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return (
            "<h2>"
            "ML_CLIENT_ID não configurado."
            "</h2>"
        )

    state = secrets.token_urlsafe(
        32
    )

    verifier = (
        generate_code_verifier()
    )

    challenge = (
        generate_code_challenge(
            verifier
        )
    )

    session[
        "ml_oauth_state"
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
        + urlencode(
            params
        )
    )


@app.route(
    "/mercadolivre/callback"
)
def mercadolivre_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return jsonify({
            "ok": False,
            "erro":
                error,

            "descricao":
                request.args.get(
                    "error_description"
                ),
        }), 400

    state = request.args.get(
        "state"
    )

    code = request.args.get(
        "code"
    )

    expected = session.get(
        "ml_oauth_state"
    )

    verifier = session.get(
        "ml_code_verifier"
    )

    if (
        not state
        or state != expected
    ):

        return jsonify({
            "ok": False,
            "erro":
                "state inválido",
        }), 400

    if not code:

        return jsonify({
            "ok": False,
            "erro":
                "code não recebido",
        }), 400

    data = {
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
            verifier,
    }

    try:

        response = http.post(
            ML_TOKEN,
            data=data,
            timeout=REQUEST_TIMEOUT,
        )

    except Exception as e:

        return jsonify({
            "ok": False,
            "erro":
                str(e),
        }), 500

    try:

        payload = response.json()

    except Exception:

        payload = {}

    if response.status_code != 200:

        return jsonify({
            "ok": False,
            "status":
                response.status_code,
            "resposta":
                payload,
        }), response.status_code

    access_token = payload.get(
        "access_token"
    )

    if not access_token:

        return jsonify({
            "ok": False,
            "erro":
                "access_token não recebido",
        }), 500

    save_tokens(
        access_token=
            access_token,

        refresh_token=
            payload.get(
                "refresh_token"
            ),

        expires_in=
            payload.get(
                "expires_in",
                21600
            ),
    )

    me = api_get(
        "/users/me"
    )

    if (
        me["ok"]
        and isinstance(
            me["data"],
            dict
        )
    ):

        save_tokens(
            access_token=
                access_token,

            refresh_token=
                payload.get(
                    "refresh_token"
                ),

            expires_in=
                payload.get(
                    "expires_in",
                    21600
                ),

            user_id=
                me["data"].get(
                    "id"
                ),

            nickname=
                me["data"].get(
                    "nickname"
                ),
        )

    session.pop(
        "ml_oauth_state",
        None
    )

    session.pop(
        "ml_code_verifier",
        None
    )

    return redirect("/")


# ============================================================
# CATEGORIA
# ============================================================

def discover_categories(
    query,
    max_results=MAX_CATEGORIES
):

    query = normalize_query(
        query
    )

    if not query:

        return {
            "categories": [],
            "attempts": [],
        }

    result = api_get(
        f"/sites/{ML_SITE}/domain_discovery/search",
        params={
            "q":
                query,

            "limit":
                8,
        }
    )

    categories = []

    if (
        result["ok"]
        and isinstance(
            result["data"],
            list
        )
    ):

        for index, item in enumerate(
            result["data"]
        ):

            category_id = item.get(
                "category_id"
            )

            if not category_id:
                continue

            categories.append({
                "id":
                    category_id,

                "name":
                    item.get(
                        "category_name"
                    ),

                "domain_id":
                    item.get(
                        "domain_id"
                    ),

                "domain_name":
                    item.get(
                        "domain_name"
                    ),

                "score":
                    1000 - index,

                "source":
                    "domain_discovery",
            })

            if len(
                categories
            ) >= max_results:

                break

    return {
        "categories":
            categories,

        "attempts": [
            {
                "metodo":
                    "domain_discovery",

                "q":
                    query,

                "status":
                    result["status"],

                "total":
                    len(
                        categories
                    ),

                "erro":
                    result["error"],
            }
        ],
    }


# ============================================================
# HIGHLIGHTS
# ============================================================

def get_highlights(
    category_id
):

    return api_get(
        f"/highlights/"
        f"{ML_SITE}/category/"
        f"{quote_plus(category_id)}"
    )


# ============================================================
# PRODUTO
# ============================================================

def get_product(
    product_id
):

    return api_get(
        f"/products/"
        f"{quote_plus(product_id)}"
    )


# ============================================================
# ITEM
# ============================================================

def get_item(
    item_id
):

    return api_get(
        f"/items/"
        f"{quote_plus(item_id)}"
    )


# ============================================================
# USER PRODUCT
# ============================================================

def get_user_product(
    user_product_id
):

    return api_get(
        f"/user-products/"
        f"{quote_plus(user_product_id)}"
    )


def get_items_by_user_product(
    seller_id,
    user_product_id
):

    return api_get(
        f"/users/"
        f"{quote_plus(str(seller_id))}"
        f"/items/search",

        params={
            "user_product_id":
                user_product_id,

            "limit":
                50,
        }
    )


# ============================================================
# PREÇOS
# ============================================================

def get_sale_price(
    item_id
):

    return api_get(
        f"/items/"
        f"{quote_plus(item_id)}"
        f"/sale_price",

        params={
            "context":
                "channel_marketplace"
        }
    )


def get_prices(
    item_id
):

    return api_get(
        f"/items/"
        f"{quote_plus(item_id)}"
        f"/prices"
    )


# ============================================================
# DESCONTO
# ============================================================

def calculate_discount(
    price,
    original_price
):

    price = safe_float(
        price
    )

    original_price = safe_float(
        original_price
    )

    if (
        price is None
        or original_price is None
    ):

        return None

    if original_price <= price:

        return 0.0

    return round(
        (
            (
                original_price
                - price
            )
            / original_price
        )
        * 100,
        2
    )


# ============================================================
# PREÇO DO ITEM
# ============================================================

def extract_price_data(
    item_id,
    item
):

    price = None

    original_price = None

    source = None

    sale = get_sale_price(
        item_id
    )

    if (
        sale["ok"]
        and isinstance(
            sale["data"],
            dict
        )
    ):

        data = sale[
            "data"
        ]

        price = safe_float(
            data.get(
                "amount"
            )
        )

        original_price = (
            safe_float(
                data.get(
                    "regular_amount"
                )
            )
        )

        if price is not None:

            source = (
                "sale_price"
            )

    if price is None:

        prices = get_prices(
            item_id
        )

        if prices["ok"]:

            data = prices[
                "data"
            ]

            entries = []

            if isinstance(
                data,
                list
            ):

                entries = data

            elif isinstance(
                data,
                dict
            ):

                entries = (
                    data.get(
                        "prices"
                    )
                    or data.get(
                        "results"
                    )
                    or []
                )

            for entry in entries:

                if not isinstance(
                    entry,
                    dict
                ):
                    continue

                amount = safe_float(
                    entry.get(
                        "amount"
                    )
                )

                regular = safe_float(
                    entry.get(
                        "regular_amount"
                    )
                )

                if amount is None:
                    continue

                price = amount

                if regular:

                    original_price = (
                        regular
                    )

                source = (
                    "prices"
                )

                break

    # fallback
    if price is None:

        price = safe_float(
            item.get(
                "price"
            )
        )

    if original_price is None:

        original_price = safe_float(
            item.get(
                "original_price"
            )
        )

    if original_price is None:

        original_price = safe_float(
            item.get(
                "base_price"
            )
        )

    discount = calculate_discount(
        price,
        original_price
    )

    return {
        "price":
            price,

        "original_price":
            original_price,

        "discount":
            discount,

        "source":
            source,
    }


# ============================================================
# ANALISAR ITEM
# ============================================================

def analyze_item(
    item_id,
    category_id=None,
    category_name=None,
    source="unknown",
    position=None
):

    result = get_item(
        item_id
    )

    if not result["ok"]:

        return {
            "ok": False,

            "item_id":
                item_id,

            "status":
                result["status"],

            "erro":
                result["error"],

            "url_api":
                result.get(
                    "url"
                ),

            "fonte":
                source,
        }

    item = result[
        "data"
    ]

    if not isinstance(
        item,
        dict
    ):

        return {
            "ok": False,

            "item_id":
                item_id,

            "status":
                result["status"],

            "erro":
                "Resposta inválida.",

            "fonte":
                source,
        }

    prices = extract_price_data(
        item_id,
        item
    )

    return {
        "ok": True,

        "item_id":
            item_id,

        "titulo":
            item.get(
                "title"
            ),

        "preco":
            prices[
                "price"
            ],

        "preco_original":
            prices[
                "original_price"
            ],

        "desconto":
            prices[
                "discount"
            ],

        "url":
            item.get(
                "permalink"
            ),

        "imagem":
            item.get(
                "thumbnail"
            ),

        "seller_id":
            item.get(
                "seller_id"
            ),

        "categoria_id":
            item.get(
                "category_id"
            )
            or category_id,

        "categoria_nome":
            category_name,

        "fonte":
            source,

        "position":
            position,

        "price_source":
            prices[
                "source"
            ],
    }


# ============================================================
# SALVAR
# ============================================================

def save_offer(
    offer
):

    item_id = offer.get(
        "item_id"
    )

    if not item_id:
        return

    conn = get_db()

    conn.execute(
        """
        INSERT INTO ofertas (
            item_id,
            titulo,
            preco,
            preco_original,
            desconto,
            url,
            imagem,
            seller_id,
            categoria_id,
            categoria_nome,
            fonte,
            criado_em
        )
        VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )

        ON CONFLICT(item_id)
        DO UPDATE SET
            titulo =
                excluded.titulo,

            preco =
                excluded.preco,

            preco_original =
                excluded.preco_original,

            desconto =
                excluded.desconto,

            url =
                excluded.url,

            imagem =
                excluded.imagem,

            seller_id =
                excluded.seller_id,

            categoria_id =
                excluded.categoria_id,

            categoria_nome =
                excluded.categoria_nome,

            fonte =
                excluded.fonte,

            criado_em =
                excluded.criado_em
        """,
        (
            item_id,
            offer.get(
                "titulo"
            ),
            offer.get(
                "preco"
            ),
            offer.get(
                "preco_original"
            ),
            offer.get(
                "desconto"
            ),
            offer.get(
                "url"
            ),
            offer.get(
                "imagem"
            ),
            offer.get(
                "seller_id"
            ),
            offer.get(
                "categoria_id"
            ),
            offer.get(
                "categoria_nome"
            ),
            offer.get(
                "fonte"
            ),
            now_ts(),
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# ANALISAR ITEM E REGISTRAR DEBUG
# ============================================================

def process_item(
    item_id,
    category_id,
    category_name,
    position,
    source,
    state,
    offers,
    min_discount,
    debug
):

    if not item_id:
        return

    if item_id in (
        state[
            "visited_items"
        ]
    ):

        return

    state[
        "visited_items"
    ].add(
        item_id
    )

    state[
        "items_found"
    ] += 1

    result = analyze_item(
        item_id,
        category_id=
            category_id,
        category_name=
            category_name,
        source=
            source,
        position=
            position,
    )

    item_debug = {
        "item_id":
            item_id,

        "source":
            source,

        "ok":
            result.get(
                "ok"
            ),

        "status":
            result.get(
                "status"
            ),

        "erro":
            result.get(
                "erro"
            ),

        "titulo":
            result.get(
                "titulo"
            ),

        "preco":
            result.get(
                "preco"
            ),

        "preco_original":
            result.get(
                "preco_original"
            ),

        "desconto":
            result.get(
                "desconto"
            ),
    }

    debug.append(
        item_debug
    )

    if not result.get(
        "ok"
    ):

        state[
            "errors"
        ] += 1

        return

    discount = result.get(
        "desconto"
    )

    if discount is None:

        state[
            "without_original_price"
        ] += 1

        return

    if discount >= min_discount:

        offers.append(
            result
        )

        state[
            "offers_found"
        ] += 1

        save_offer(
            result
        )

    else:

        state[
            "without_discount"
        ] += 1


# ============================================================
# USER PRODUCT
# ============================================================

def process_user_product(
    user_product_id,
    category_id,
    category_name,
    position,
    state,
    offers,
    min_discount,
    debug
):

    if not user_product_id:
        return

    state[
        "user_products_found"
    ] += 1

    up = get_user_product(
        user_product_id
    )

    up_debug = {
        "user_product_id":
            user_product_id,

        "status":
            up["status"],

        "erro":
            up["error"],
    }

    if not up["ok"]:

        state[
            "errors"
        ] += 1

        debug.append({
            **up_debug,
            "etapa":
                "user_product"
        })

        return

    data = up[
        "data"
    ]

    if not isinstance(
        data,
        dict
    ):

        debug.append({
            **up_debug,
            "etapa":
                "user_product",
            "observacao":
                "Resposta não é objeto."
        })

        return

    # O endpoint pode disponibilizar
    # user_id/seller_id dependendo
    # do modelo/resposta.

    seller_id = (
        data.get(
            "user_id"
        )
        or data.get(
            "seller_id"
        )
    )

    up_debug[
        "seller_id"
    ] = seller_id

    if not seller_id:

        debug.append({
            **up_debug,
            "etapa":
                "user_product",

            "observacao":
                "User Product não retornou seller_id/user_id."
        })

        return

    items = get_items_by_user_product(
        seller_id,
        user_product_id
    )

    up_debug[
        "items_status"
    ] = items[
        "status"
    ]

    up_debug[
        "items_erro"
    ] = items[
        "error"
    ]

    if not items["ok"]:

        state[
            "errors"
        ] += 1

        debug.append({
            **up_debug,
            "etapa":
                "items_by_user_product"
        })

        return

    item_data = items[
        "data"
    ]

    item_ids = []

    if isinstance(
        item_data,
        dict
    ):

        item_ids = (
            item_data.get(
                "results"
            )
            or []
        )

    if not isinstance(
        item_ids,
        list
    ):

        item_ids = []

    up_debug[
        "item_ids"
    ] = item_ids

    debug.append({
        **up_debug,
        "etapa":
            "items_by_user_product"
    })

    for item_id in item_ids:

        if len(
            offers
        ) >= MAX_OFFERS:

            break

        process_item(
            item_id,
            category_id,
            category_name,
            position,
            "user_product",
            state,
            offers,
            min_discount,
            debug,
        )


# ============================================================
# PRODUTO / FAMÍLIA
# ============================================================

def inspect_product(
    product_id,
    category_id,
    category_name,
    position,
    state,
    offers,
    min_discount,
    product_debug,
    item_debug,
    depth=0
):

    if depth > 3:
        return

    if len(
        state[
            "visited_products"
        ]
    ) >= MAX_FAMILY_PRODUCTS:

        return

    if product_id in (
        state[
            "visited_products"
        ]
    ):

        return

    state[
        "visited_products"
    ].add(
        product_id
    )

    product_result = get_product(
        product_id
    )

    state[
        "products_consulted"
    ] += 1

    debug = {
        "product_id":
            product_id,

        "status":
            product_result[
                "status"
            ],

        "erro":
            product_result[
                "error"
            ],

        "depth":
            depth,
    }

    if not product_result["ok"]:

        state[
            "errors"
        ] += 1

        product_debug.append(
            debug
        )

        return

    product = (
        product_result[
            "data"
        ]
    )

    if not isinstance(
        product,
        dict
    ):

        state[
            "errors"
        ] += 1

        debug[
            "erro"
        ] = (
            "Resposta de produto inválida."
        )

        product_debug.append(
            debug
        )

        return

    buy_box = product.get(
        "buy_box_winner"
    )

    debug[
        "tem_buy_box"
    ] = bool(
        isinstance(
            buy_box,
            dict
        )
        and buy_box.get(
            "item_id"
        )
    )

    # --------------------------------------------------------
    # BUY BOX
    # --------------------------------------------------------

    if isinstance(
        buy_box,
        dict
    ):

        item_id = buy_box.get(
            "item_id"
        )

        if item_id:

            state[
                "buy_box_found"
            ] += 1

            process_item(
                item_id,
                category_id,
                category_name,
                position,
                "catalog_buy_box",
                state,
                offers,
                min_discount,
                item_debug,
            )

    # --------------------------------------------------------
    # PAI
    # --------------------------------------------------------

    parent_id = product.get(
        "parent_id"
    )

    debug[
        "parent_id"
    ] = parent_id

    # --------------------------------------------------------
    # FILHOS
    # --------------------------------------------------------

    children = product.get(
        "children_ids"
    )

    if not isinstance(
        children,
        list
    ):

        children = []

    debug[
        "children"
    ] = children

    state[
        "children_discovered"
    ] += len(
        children
    )

    product_debug.append(
        debug
    )

    # Primeiro filhos
    for child_id in children:

        if len(
            offers
        ) >= MAX_OFFERS:

            break

        inspect_product(
            child_id,
            category_id,
            category_name,
            position,
            state,
            offers,
            min_discount,
            product_debug,
            item_debug,
            depth + 1,
        )

    # Depois pai
    if (
        parent_id
        and parent_id != product_id
    ):

        inspect_product(
            parent_id,
            category_id,
            category_name,
            position,
            state,
            offers,
            min_discount,
            product_debug,
            item_debug,
            depth + 1,
        )


# ============================================================
# BUSCA COMPLETA
# ============================================================

def discover_offers(
    query,
    min_discount=10,
    max_categories=MAX_CATEGORIES
):

    query = normalize_query(
        query
    )

    if not query:

        return {
            "ok": False,
            "erro":
                "Digite um produto."
        }

    category_result = (
        discover_categories(
            query,
            max_categories
        )
    )

    categories = (
        category_result[
            "categories"
        ]
    )

    offers = []

    state = {
        "visited_products":
            set(),

        "visited_items":
            set(),

        "products_consulted":
            0,

        "children_discovered":
            0,

        "buy_box_found":
            0,

        "without_buy_box":
            0,

        "items_found":
            0,

        "offers_found":
            0,

        "without_discount":
            0,

        "without_original_price":
            0,

        "errors":
            0,

        "ranking_products":
            0,

        "categories_analyzed":
            0,

        "user_products_found":
            0,
    }

    category_debug = []

    product_debug = []

    item_debug = []

    for category in categories:

        if len(
            offers
        ) >= MAX_OFFERS:

            break

        category_id = category.get(
            "id"
        )

        category_name = category.get(
            "name"
        )

        if not category_id:
            continue

        state[
            "categories_analyzed"
        ] += 1

        highlights = get_highlights(
            category_id
        )

        debug = {
            "id":
                category_id,

            "nome":
                category_name,

            "status":
                highlights[
                    "status"
                ],

            "erro":
                highlights[
                    "error"
                ],

            "produtos":
                0,

            "tipos":
                {},
        }

        if not highlights["ok"]:

            state[
                "errors"
            ] += 1

            category_debug.append(
                debug
            )

            continue

        data = highlights[
            "data"
        ]

        content = []

        if isinstance(
            data,
            dict
        ):

            content = (
                data.get(
                    "content"
                )
                or []
            )

        if not isinstance(
            content,
            list
        ):

            content = []

        debug[
            "produtos"
        ] = len(
            content
        )

        state[
            "ranking_products"
        ] += len(
            content
        )

        # ----------------------------------------------------
        # RANKING
        # ----------------------------------------------------

        for ranked in content:

            if len(
                offers
            ) >= MAX_OFFERS:

                break

            element_id = ranked.get(
                "id"
            )

            element_type = (
                ranked.get(
                    "type"
                )
                or ""
            ).upper()

            position = ranked.get(
                "position"
            )

            if not element_id:
                continue

            debug[
                "tipos"
            ][
                element_type
            ] = (
                debug[
                    "tipos"
                ].get(
                    element_type,
                    0
                )
                + 1
            )

            # =================================================
            # ITEM
            # =================================================

            if element_type == "ITEM":

                process_item(
                    element_id,
                    category_id,
                    category_name,
                    position,
                    "highlights_item",
                    state,
                    offers,
                    min_discount,
                    item_debug,
                )

            # =================================================
            # PRODUCT
            # =================================================

            elif element_type == "PRODUCT":

                inspect_product(
                    element_id,
                    category_id,
                    category_name,
                    position,
                    state,
                    offers,
                    min_discount,
                    product_debug,
                    item_debug,
                )

            # =================================================
            # USER PRODUCT
            # =================================================

            elif (
                element_type
                == "USER_PRODUCT"
            ):

                process_user_product(
                    element_id,
                    category_id,
                    category_name,
                    position,
                    state,
                    offers,
                    min_discount,
                    item_debug,
                )

        category_debug.append(
            debug
        )

    offers.sort(
        key=lambda x: (
            -(
                safe_float(
                    x.get(
                        "desconto"
                    )
                )
                or 0
            ),

            safe_float(
                x.get(
                    "preco"
                )
            )
            or 999999999,
        )
    )

    return {
        "ok":
            True,

        "consulta":
            query,

        "categorias":
            categories,

        "tentativas_categorias":
            category_result[
                "attempts"
            ],

        "categorias_debug":
            category_debug,

        "product_debug":
            product_debug,

        "item_debug":
            item_debug,

        "stats": {
            "categorias_encontradas":
                len(
                    categories
                ),

            "categorias_analisadas":
                state[
                    "categories_analyzed"
                ],

            "produtos_ranking":
                state[
                    "ranking_products"
                ],

            "produtos_consultados":
                state[
                    "products_consulted"
                ],

            "filhos_descobertos":
                state[
                    "children_discovered"
                ],

            "buy_box_encontradas":
                state[
                    "buy_box_found"
                ],

            "produtos_sem_buy_box":
                state[
                    "ranking_products"
                ]
                - state[
                    "buy_box_found"
                ],

            "itens_encontrados":
                state[
                    "items_found"
                ],

            "user_products":
                state[
                    "user_products_found"
                ],

            "ofertas_encontradas":
                state[
                    "offers_found"
                ],

            "sem_desconto":
                state[
                    "without_discount"
                ],

            "sem_preco_original":
                state[
                    "without_original_price"
                ],

            "erros":
                state[
                    "errors"
                ],
        },

        "ofertas":
            offers[
                :MAX_OFFERS
            ],
    }


# ============================================================
# TESTE CATEGORIA
# ============================================================

@app.route(
    "/mercadolivre/teste-categoria"
)
def teste_categoria():

    query = request.args.get(
        "q",
        ""
    ).strip()

    result = discover_categories(
        query
    )

    return jsonify({
        "consulta":
            query,

        "tentativas":
            result[
                "attempts"
            ],

        "categorias":
            result[
                "categories"
            ],

        "total":
            len(
                result[
                    "categories"
                ]
            ),
    })


# ============================================================
# TESTE HIGHLIGHTS
# ============================================================

@app.route(
    "/mercadolivre/teste-highlights"
)
def teste_highlights():

    category_id = request.args.get(
        "category_id",
        ""
    ).strip()

    if not category_id:

        return jsonify({
            "ok": False,
            "erro":
                "Informe ?category_id=MLB..."
        }), 400

    result = get_highlights(
        category_id
    )

    return jsonify({
        "ok":
            result["ok"],

        "status":
            result["status"],

        "erro":
            result["error"],

        "url":
            result.get(
                "url"
            ),

        "resposta":
            result["data"],
    })


# ============================================================
# TESTE PRODUTO
# ============================================================

@app.route(
    "/mercadolivre/teste-preco"
)
def teste_preco():

    product_id = request.args.get(
        "product_id",
        ""
    ).strip()

    item_id = request.args.get(
        "item_id",
        ""
    ).strip()

    if product_id:

        result = get_product(
            product_id
        )

        return jsonify({
            "ok":
                result["ok"],

            "status":
                result["status"],

            "erro":
                result["error"],

            "product_id":
                product_id,

            "produto":
                result["data"],
        })

    if item_id:

        result = analyze_item(
            item_id,
            source=
                "teste_manual"
        )

        return jsonify(
            result
        )

    return jsonify({
        "ok": False,
        "erro":
            "Informe ?product_id=MLB... ou ?item_id=MLB..."
    }), 400


# ============================================================
# TESTE BUSCA
# ============================================================

@app.route(
    "/mercadolivre/teste-busca"
)
def teste_busca():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:

        return jsonify({
            "ok": False,
            "erro":
                "Informe ?q=fone"
        }), 400

    result = discover_offers(
        query,
        min_discount=0,
    )

    return jsonify(
        result
    )


# ============================================================
# TESTE ITEM
# ============================================================

@app.route(
    "/mercadolivre/item"
)
def teste_item():

    item_id = request.args.get(
        "item_id",
        ""
    ).strip()

    if not item_id:

        return jsonify({
            "ok": False,
            "erro":
                "Informe ?item_id=MLB..."
        }), 400

    return jsonify(
        analyze_item(
            item_id,
            source=
                "teste_manual"
        )
    )


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    token = get_access_token()

    result = {
        "token_disponivel":
            bool(
                token
            ),

        "redirect_uri":
            ML_REDIRECT_URI,

        "site":
            ML_SITE,
    }

    if token:

        me = api_get(
            "/users/me"
        )

        result[
            "usuario"
        ] = {
            "ok":
                me["ok"],

            "status":
                me["status"],

            "erro":
                me["error"],

            "data":
                me["data"],
        }

    return jsonify(
        result
    )


# ============================================================
# BUSCA WEB
# ============================================================

@app.route(
    "/buscar",
    methods=[
        "GET",
        "POST"
    ]
)
def buscar():

    if request.method == "POST":

        query = request.form.get(
            "q",
            ""
        ).strip()

        try:

            min_discount = float(
                request.form.get(
                    "min_discount",
                    "10"
                )
            )

        except Exception:

            min_discount = 10

    else:

        query = request.args.get(
            "q",
            ""
        ).strip()

        try:

            min_discount = float(
                request.args.get(
                    "min_discount",
                    "10"
                )
            )

        except Exception:

            min_discount = 10

    if not query:

        return redirect("/")

    result = discover_offers(
        query,
        min_discount=
            min_discount
    )

    return render_template_string(
        HTML,
        resultado=
            result,

        query=
            query,

        min_discount=
            min_discount,
    )


# ============================================================
# HTML
# ============================================================

HTML = r"""
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

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #0f1115;
    color: #f5f5f5;
    font-family: Arial, sans-serif;
}

.container {
    width: min(1100px, 94%);
    margin: auto;
    padding: 25px 0 60px;
}

h1 {
    margin-bottom: 5px;
}

.subtitle {
    color: #9ca3af;
    margin-bottom: 25px;
}

.card {
    background: #181b21;
    border: 1px solid #282d36;
    border-radius: 16px;
    padding: 18px;
    margin-bottom: 18px;
}

.form-grid {
    display: grid;
    grid-template-columns: 1fr 150px 160px;
    gap: 10px;
}

input,
button {
    border: 0;
    border-radius: 10px;
    padding: 13px;
    font-size: 15px;
}

input {
    background: #242832;
    color: white;
}

button {
    background: #3483fa;
    color: white;
    font-weight: bold;
}

.stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
}

.stat {
    background: #111318;
    padding: 14px;
    border-radius: 12px;
}

.stat strong {
    display: block;
    font-size: 25px;
    margin-top: 5px;
}

.offer {
    display: grid;
    grid-template-columns: 110px 1fr auto;
    gap: 15px;
    align-items: center;
    background: #111318;
    border-radius: 14px;
    padding: 12px;
    margin-top: 10px;
}

.offer img {
    width: 110px;
    height: 110px;
    object-fit: contain;
    background: white;
    border-radius: 10px;
}

.price {
    font-size: 22px;
    font-weight: bold;
}

.old-price {
    color: #777;
    text-decoration: line-through;
}

.discount {
    display: inline-block;
    background: #00a650;
    padding: 6px 9px;
    border-radius: 7px;
    font-weight: bold;
    margin-top: 5px;
}

.link {
    display: inline-block;
    margin-top: 10px;
    color: #66a6ff;
}

.warning {
    background: #3a2d0a;
    border: 1px solid #69520c;
    color: #ffd76a;
    padding: 14px;
    border-radius: 10px;
}

.debug {
    background: #111318;
    border-radius: 10px;
    padding: 12px;
    margin-top: 8px;
}

.muted {
    color: #9ca3af;
    font-size: 13px;
}

pre {
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    color: #cbd5e1;
    font-size: 12px;
}

@media(max-width:750px) {

    .form-grid {
        grid-template-columns: 1fr;
    }

    .stats {
        grid-template-columns: repeat(2, 1fr);
    }

    .offer {
        grid-template-columns: 80px 1fr;
    }

    .offer img {
        width: 80px;
        height: 80px;
    }

}

</style>

</head>

<body>

<div class="container">

<h1>
🛒 Caçador de Ofertas
</h1>

<div class="subtitle">
Mercado Livre • ranking • produtos • anúncios • descontos
</div>

<div class="card">

<form
action="/buscar"
method="POST"
>

<div class="form-grid">

<input
type="text"
name="q"
value="{{ query or '' }}"
placeholder="Ex.: fone, celular, TV, air fryer..."
required
>

<input
type="number"
name="min_discount"
value="{{ min_discount or 10 }}"
min="0"
max="99"
step="1"
>

<button type="submit">
🔎 Procurar
</button>

</div>

</form>

</div>


{% if resultado and resultado.get("stats") %}

<div class="card">

<h2>
📊 Resultado
</h2>

<div class="stats">

<div class="stat">
Categorias
<strong>
{{ resultado.stats.categorias_encontradas }}
</strong>
</div>

<div class="stat">
Ranking
<strong>
{{ resultado.stats.produtos_ranking }}
</strong>
</div>

<div class="stat">
Produtos consultados
<strong>
{{ resultado.stats.produtos_consultados }}
</strong>
</div>

<div class="stat">
Buy Box
<strong>
{{ resultado.stats.buy_box_encontradas }}
</strong>
</div>

</div>

<br>

<div class="stats">

<div class="stat">
Filhos
<strong>
{{ resultado.stats.filhos_descobertos }}
</strong>
</div>

<div class="stat">
Itens
<strong>
{{ resultado.stats.itens_encontrados }}
</strong>
</div>

<div class="stat">
User Products
<strong>
{{ resultado.stats.user_products }}
</strong>
</div>

<div class="stat">
Ofertas
<strong>
{{ resultado.stats.ofertas_encontradas }}
</strong>
</div>

</div>

</div>


{% if resultado.ofertas %}

<div class="card">

<h2>
🔥 Ofertas
</h2>

{% for oferta in resultado.ofertas %}

<div class="offer">

<div>

{% if oferta.imagem %}

<img
src="{{ oferta.imagem }}"
loading="lazy"
>

{% endif %}

</div>

<div>

<strong>
{{ oferta.titulo }}
</strong>

<br><br>

{% if oferta.preco_original %}

<span class="old-price">
R$
{{ "%.2f"|format(
oferta.preco_original
) }}
</span>

<br>

{% endif %}

<span class="price">
R$
{{ "%.2f"|format(
oferta.preco
) }}
</span>

<br>

{% if oferta.desconto is not none %}

<span class="discount">
{{ "%.0f"|format(
oferta.desconto
) }}% OFF
</span>

{% endif %}

<br>

<a
class="link"
href="{{ oferta.url }}"
target="_blank"
>
Ver oferta →
</a>

</div>

</div>

{% endfor %}

</div>

{% else %}

<div class="warning">

Nenhuma oferta encontrada.

<br><br>

Itens reais encontrados:
{{ resultado.stats.itens_encontrados }}

<br>

Produtos sem Buy Box:
{{ resultado.stats.produtos_sem_buy_box }}

<br>

Sem preço original:
{{ resultado.stats.sem_preco_original }}

<br>

Erros:
{{ resultado.stats.erros }}

</div>

{% endif %}


<div class="card">

<h2>
🔍 Itens encontrados
</h2>

{% if resultado.item_debug %}

{% for item in resultado.item_debug %}

<div class="debug">

<strong>
{{ item.item_id }}
</strong>

<br>

<span class="muted">
Fonte: {{ item.source }}
</span>

<br>

Status:
{{ item.status }}

{% if item.erro %}

<br>

Erro:
{{ item.erro }}

{% endif %}

{% if item.titulo %}

<br>

Título:
{{ item.titulo }}

{% endif %}

{% if item.preco %}

<br>

Preço:
R$ {{ "%.2f"|format(item.preco) }}

{% endif %}

{% if item.desconto is not none %}

<br>

Desconto:
{{ "%.2f"|format(item.desconto) }}%

{% endif %}

</div>

{% endfor %}

{% else %}

<span class="muted">
Nenhum ITEM foi encontrado.
</span>

{% endif %}

</div>


<div class="card">

<h2>
📦 Produtos de catálogo
</h2>

<pre>
{{ resultado.product_debug | tojson(indent=2) }}
</pre>

</div>


<div class="card">

<h2>
📂 Categorias
</h2>

<pre>
{{ resultado.categorias_debug | tojson(indent=2) }}
</pre>

</div>

{% endif %}

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

    conectado = bool(
        tokens
        and tokens.get(
            "access_token"
        )
    )

    if not conectado:

        return """
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

        body {
            background:#101216;
            color:white;
            font-family:Arial;
            text-align:center;
            padding:40px;
        }

        a {
            display:inline-block;
            padding:15px 22px;
            background:#3483fa;
            color:white;
            text-decoration:none;
            border-radius:10px;
        }

        </style>

        </head>

        <body>

        <h1>
        🛒 Caçador de Ofertas
        </h1>

        <p>
        Conecte sua conta do Mercado Livre.
        </p>

        <br>

        <a href="/mercadolivre/login">
        🔐 Conectar Mercado Livre
        </a>

        </body>

        </html>
        """

    return render_template_string(
        HTML,
        resultado=None,
        query="",
        min_discount=10,
    )


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health"
)
def health():

    return jsonify({
        "ok": True,

        "app":
            "cacador-de-ofertas",

        "timestamp":
            now_ts(),
    })


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
    )