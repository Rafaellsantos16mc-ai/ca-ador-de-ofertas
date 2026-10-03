import os
import re
import time
import base64
import hashlib
import secrets
import sqlite3
from urllib.parse import urlencode, quote_plus

import requests

from flask import (
    Flask,
    request,
    redirect,
    jsonify,
    render_template_string,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

APP_NAME = "Caçador de Ofertas"

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

ML_SITE = "MLB"

CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback",
).strip()

DB_PATH = os.getenv(
    "DB_PATH",
    "ofertas.db",
)

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "20",
    )
)

MAX_RESULTS = 50

oauth_sessions = {}


# ============================================================
# BANCO
# ============================================================

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT UNIQUE,
            title TEXT,
            price REAL,
            original_price REAL,
            discount REAL,
            currency TEXT,
            permalink TEXT,
            thumbnail TEXT,
            seller_id TEXT,
            product_id TEXT,
            category_id TEXT,
            category_name TEXT,
            source TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# TOKEN
# ============================================================

def get_saved_token():

    token = (
        os.getenv("ML_ACCESS_TOKEN")
        or os.getenv("ML_TOKEN")
        or os.getenv("ACCESS_TOKEN")
    )

    if token:
        return token.strip()

    return None


def get_refresh_token():

    token = (
        os.getenv("ML_REFRESH_TOKEN")
        or os.getenv("REFRESH_TOKEN")
    )

    if token:
        return token.strip()

    return None


def save_token_memory(data):

    if not data:
        return

    oauth_sessions["token"] = data


def current_token():

    token_data = oauth_sessions.get(
        "token"
    )

    if isinstance(
        token_data,
        dict,
    ):

        token = token_data.get(
            "access_token"
        )

        if token:
            return token

    return get_saved_token()


def refresh_access_token():

    refresh_token = get_refresh_token()

    token_data = oauth_sessions.get(
        "token"
    )

    if isinstance(
        token_data,
        dict,
    ):

        refresh_token = (
            token_data.get(
                "refresh_token"
            )
            or refresh_token
        )

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
                "refresh_token": refresh_token,
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return None

        data = response.json()

        save_token_memory(data)

        return data.get(
            "access_token"
        )

    except Exception:

        return None


# ============================================================
# API GET
# ============================================================

def api_get(
    path,
    params=None,
    retry=True,
):

    token = current_token()

    if not token:

        return {
            "ok": False,
            "status": 401,
            "error": "Access token não encontrado.",
            "data": None,
        }

    try:

        response = requests.get(
            f"{ML_API}{path}",
            headers={
                "Authorization": (
                    f"Bearer {token}"
                ),
                "Accept": "application/json",
                "User-Agent": (
                    "CacadorDeOfertas/2.0"
                ),
            },
            params=params or {},
            timeout=REQUEST_TIMEOUT,
        )

        if (
            response.status_code == 401
            and retry
        ):

            new_token = (
                refresh_access_token()
            )

            if new_token:

                return api_get(
                    path,
                    params=params,
                    retry=False,
                )

        try:
            data = response.json()

        except Exception:

            data = {
                "raw": response.text[:5000]
            }

        return {
            "ok": response.ok,
            "status": response.status_code,
            "error": (
                None
                if response.ok
                else data
            ),
            "data": data,
        }

    except Exception as e:

        return {
            "ok": False,
            "status": 0,
            "error": str(e),
            "data": None,
        }


# ============================================================
# PKCE
# ============================================================

def base64url(data):

    return (
        base64.urlsafe_b64encode(
            data
        )
        .decode()
        .rstrip("=")
    )


def create_pkce():

    verifier = base64url(
        secrets.token_bytes(32)
    )

    challenge = base64url(
        hashlib.sha256(
            verifier.encode()
        ).digest()
    )

    return verifier, challenge


# ============================================================
# UTILITÁRIOS
# ============================================================

def normalize_query(value):

    value = str(
        value or ""
    ).strip()

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value[:200]


def safe_float(value):

    try:

        if value is None:
            return None

        return float(value)

    except Exception:

        return None


def calculate_discount(
    original,
    price,
):

    original = safe_float(
        original
    )

    price = safe_float(
        price
    )

    if not original:
        return None

    if not price:
        return None

    if original <= 0:
        return None

    if price >= original:
        return 0.0

    return round(
        (
            (original - price)
            / original
        )
        * 100,
        2,
    )


def extract_item_ids(text):

    if not text:
        return []

    found = re.findall(
        r"\bMLB[-_]?(\d{6,})\b",
        str(text),
        flags=re.IGNORECASE,
    )

    result = []

    for number in found:

        item_id = (
            f"MLB{number}"
        )

        if item_id not in result:
            result.append(
                item_id
            )

    return result


# ============================================================
# CATEGORIA / DOMAIN DISCOVERY
# ============================================================

def discover_categories(query):

    query = normalize_query(
        query
    )

    if not query:
        return []

    response = api_get(
        f"/sites/{ML_SITE}/domain_discovery/search",
        params={
            "q": query,
            "limit": 8,
        },
    )

    if not response["ok"]:

        return []

    data = response["data"]

    if not isinstance(
        data,
        list,
    ):

        return []

    categories = []

    seen = set()

    for item in data:

        if not isinstance(
            item,
            dict,
        ):
            continue

        category_id = item.get(
            "category_id"
        )

        category_name = item.get(
            "category_name"
        )

        domain_id = item.get(
            "domain_id"
        )

        if not category_id:
            continue

        if category_id in seen:
            continue

        seen.add(
            category_id
        )

        categories.append({
            "category_id": category_id,
            "category_name": (
                category_name
                or category_id
            ),
            "domain_id": domain_id,
            "domain_name": item.get(
                "domain_name"
            ),
            "attributes": (
                item.get(
                    "attributes"
                )
                or []
            ),
        })

    return categories


# ============================================================
# MAIS VENDIDOS / HIGHLIGHTS
# ============================================================

def get_highlights(
    category_id,
):

    response = api_get(
        f"/highlights/{ML_SITE}/category/{quote_plus(category_id)}"
    )

    if not response["ok"]:

        return {
            "ok": False,
            "status": response["status"],
            "error": response["error"],
            "content": [],
        }

    data = (
        response["data"]
        or {}
    )

    content = data.get(
        "content",
        [],
    )

    if not isinstance(
        content,
        list,
    ):

        content = []

    return {
        "ok": True,
        "status": response["status"],
        "error": None,
        "query_data": data.get(
            "query_data"
        ),
        "content": content,
    }


# ============================================================
# ITEM
# ============================================================

def get_item(
    item_id,
):

    return api_get(
        f"/items/{quote_plus(item_id)}"
    )


def get_sale_price(
    item_id,
):

    return api_get(
        f"/items/{quote_plus(item_id)}/sale_price",
        params={
            "context": (
                "channel_marketplace"
            )
        },
    )


def get_prices(
    item_id,
):

    return api_get(
        f"/items/{quote_plus(item_id)}/prices"
    )


# ============================================================
# ANALISAR ITEM
# ============================================================

def analyze_item(
    item_id,
    source="highlights",
    category_id=None,
    category_name=None,
):

    item_response = get_item(
        item_id
    )

    if not item_response["ok"]:

        return {
            "ok": False,
            "item_id": item_id,
            "source": source,
            "error": item_response[
                "error"
            ],
            "status": item_response[
                "status"
            ],
        }

    item = (
        item_response["data"]
        or {}
    )

    sale_response = get_sale_price(
        item_id
    )

    sale = (
        sale_response["data"]
        if sale_response["ok"]
        else {}
    )

    prices_response = get_prices(
        item_id
    )

    prices_data = (
        prices_response["data"]
        if prices_response["ok"]
        else {}
    )

    current_price = None
    original_price = None

    currency = (
        item.get(
            "currency_id"
        )
        or "BRL"
    )

    # --------------------------------------------------------
    # SALE PRICE
    # --------------------------------------------------------

    if isinstance(
        sale,
        dict,
    ):

        current_price = safe_float(
            sale.get(
                "amount"
            )
        )

        original_price = safe_float(
            sale.get(
                "regular_amount"
            )
        )

        currency = (
            sale.get(
                "currency_id"
            )
            or currency
        )

    # --------------------------------------------------------
    # PRICES
    # --------------------------------------------------------

    if isinstance(
        prices_data,
        dict,
    ):

        prices = prices_data.get(
            "prices",
            [],
        )

        if isinstance(
            prices,
            list,
        ):

            promotions = []
            standards = []

            for price in prices:

                if not isinstance(
                    price,
                    dict,
                ):
                    continue

                amount = safe_float(
                    price.get(
                        "amount"
                    )
                )

                regular = safe_float(
                    price.get(
                        "regular_amount"
                    )
                )

                price_type = str(
                    price.get(
                        "type"
                    )
                    or ""
                ).lower()

                if (
                    price_type
                    == "promotion"
                ):

                    promotions.append(
                        price
                    )

                elif (
                    price_type
                    == "standard"
                ):

                    standards.append(
                        price
                    )

                if (
                    current_price
                    is None
                    and amount is not None
                ):

                    current_price = amount

                if (
                    original_price
                    is None
                    and regular is not None
                ):

                    original_price = regular

                if price.get(
                    "currency_id"
                ):

                    currency = price.get(
                        "currency_id"
                    )

            if promotions:

                promotion = (
                    promotions[0]
                )

                amount = safe_float(
                    promotion.get(
                        "amount"
                    )
                )

                regular = safe_float(
                    promotion.get(
                        "regular_amount"
                    )
                )

                if amount is not None:
                    current_price = amount

                if regular is not None:
                    original_price = regular

            if (
                original_price is None
                and standards
            ):

                original_price = (
                    safe_float(
                        standards[0].get(
                            "amount"
                        )
                    )
                )

    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    if current_price is None:

        current_price = safe_float(
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

    discount = calculate_discount(
        original_price,
        current_price,
    )

    return {
        "ok": True,
        "item_id": item_id,
        "title": item.get(
            "title"
        ),
        "price": current_price,
        "original_price": original_price,
        "discount": discount,
        "currency": currency,
        "permalink": item.get(
            "permalink"
        ),
        "thumbnail": item.get(
            "thumbnail"
        ),
        "seller_id": item.get(
            "seller_id"
        ),
        "product_id": (
            item.get(
                "catalog_product_id"
            )
            or item.get(
                "product_id"
            )
        ),
        "category_id": category_id,
        "category_name": category_name,
        "source": source,
        "promotion": (
            sale.get(
                "metadata"
            )
            if isinstance(
                sale,
                dict,
            )
            else None
        ),
    }


# ============================================================
# PRODUTO DE CATÁLOGO
# ============================================================

def get_product(
    product_id,
):

    return api_get(
        f"/products/{quote_plus(product_id)}"
    )


def product_to_item(
    product_id,
    category_id,
    category_name,
):

    response = get_product(
        product_id
    )

    if not response["ok"]:

        return {
            "ok": False,
            "error": response[
                "error"
            ],
        }

    product = (
        response["data"]
        or {}
    )

    buy_box = product.get(
        "buy_box_winner"
    )

    if not isinstance(
        buy_box,
        dict,
    ):

        return {
            "ok": False,
            "reason": "sem_buy_box",
            "product_id": product_id,
        }

    item_id = buy_box.get(
        "item_id"
    )

    if not item_id:

        return {
            "ok": False,
            "reason": "sem_item_id",
            "product_id": product_id,
        }

    return analyze_item(
        item_id,
        source="highlights_product_buy_box",
        category_id=category_id,
        category_name=category_name,
    )


# ============================================================
# DESCOBRIR OFERTAS
# ============================================================

def discover_offers(
    query,
    min_discount=10,
    max_categories=8,
):

    query = normalize_query(
        query
    )

    categories = (
        discover_categories(
            query
        )
    )

    if not categories:

        return {
            "ok": False,
            "error": (
                "Não foi possível encontrar "
                "uma categoria para essa busca."
            ),
            "categories": [],
            "results": [],
            "stats": {},
        }

    results = []

    seen_items = set()

    stats = {
        "categorias_encontradas": len(
            categories
        ),
        "categorias_analisadas": 0,
        "itens_highlights": 0,
        "produtos_highlights": 0,
        "itens_analisados": 0,
        "buy_box_analisadas": 0,
        "sem_desconto": 0,
        "sem_preco_original": 0,
        "erros": 0,
        "ofertas_encontradas": 0,
    }

    category_results = []

    for category in categories[
        :max_categories
    ]:

        category_id = category[
            "category_id"
        ]

        category_name = category[
            "category_name"
        ]

        stats[
            "categorias_analisadas"
        ] += 1

        highlights = get_highlights(
            category_id
        )

        category_info = {
            "category_id": category_id,
            "category_name": category_name,
            "status": highlights.get(
                "status"
            ),
            "items": 0,
            "products": 0,
        }

        if not highlights["ok"]:

            stats["erros"] += 1

            category_results.append(
                category_info
            )

            continue

        content = highlights[
            "content"
        ]

        for entry in content:

            if not isinstance(
                entry,
                dict,
            ):
                continue

            entry_id = entry.get(
                "id"
            )

            entry_type = str(
                entry.get(
                    "type"
                )
                or ""
            ).upper()

            if not entry_id:
                continue

            # ------------------------------------------------
            # ITEM
            # ------------------------------------------------

            if entry_type == "ITEM":

                category_info[
                    "items"
                ] += 1

                stats[
                    "itens_highlights"
                ] += 1

                item_ids = (
                    extract_item_ids(
                        entry_id
                    )
                )

                if not item_ids:
                    continue

                item_id = item_ids[0]

                if item_id in seen_items:
                    continue

                seen_items.add(
                    item_id
                )

                offer = analyze_item(
                    item_id,
                    source="highlights_item",
                    category_id=category_id,
                    category_name=category_name,
                )

                if not offer.get(
                    "ok"
                ):

                    stats["erros"] += 1
                    continue

                stats[
                    "itens_analisados"
                ] += 1

                discount = offer.get(
                    "discount"
                )

                if discount is None:

                    stats[
                        "sem_preco_original"
                    ] += 1

                    continue

                if discount < min_discount:

                    stats[
                        "sem_desconto"
                    ] += 1

                    continue

                results.append(
                    offer
                )

            # ------------------------------------------------
            # PRODUCT
            # ------------------------------------------------

            elif entry_type == "PRODUCT":

                category_info[
                    "products"
                ] += 1

                stats[
                    "produtos_highlights"
                ] += 1

                product_result = (
                    product_to_item(
                        entry_id,
                        category_id,
                        category_name,
                    )
                )

                if not product_result.get(
                    "ok"
                ):

                    if product_result.get(
                        "reason"
                    ) in (
                        "sem_buy_box",
                        "sem_item_id",
                    ):

                        continue

                    stats["erros"] += 1
                    continue

                stats[
                    "buy_box_analisadas"
                ] += 1

                item_id = product_result.get(
                    "item_id"
                )

                if item_id in seen_items:
                    continue

                seen_items.add(
                    item_id
                )

                discount = (
                    product_result.get(
                        "discount"
                    )
                )

                if discount is None:

                    stats[
                        "sem_preco_original"
                    ] += 1

                    continue

                if discount < min_discount:

                    stats[
                        "sem_desconto"
                    ] += 1

                    continue

                results.append(
                    product_result
                )

            # ------------------------------------------------
            # USER PRODUCT
            # ------------------------------------------------

            elif entry_type == "USER_PRODUCT":

                # USER_PRODUCT não é um item MLB diretamente.
                # Não tentamos fazer scraping ou adivinhação.
                continue

        category_results.append(
            category_info
        )

    results.sort(
        key=lambda x: (
            x.get(
                "discount"
            )
            if x.get(
                "discount"
            ) is not None
            else -1
        ),
        reverse=True,
    )

    stats[
        "ofertas_encontradas"
    ] = len(
        results
    )

    return {
        "ok": True,
        "query": query,
        "categories": categories,
        "category_results": category_results,
        "results": results,
        "stats": stats,
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

    conn = db()

    conn.execute(
        """
        INSERT INTO ofertas (
            item_id,
            title,
            price,
            original_price,
            discount,
            currency,
            permalink,
            thumbnail,
            seller_id,
            product_id,
            category_id,
            category_name,
            source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(item_id)
        DO UPDATE SET
            title = excluded.title,
            price = excluded.price,
            original_price = excluded.original_price,
            discount = excluded.discount,
            currency = excluded.currency,
            permalink = excluded.permalink,
            thumbnail = excluded.thumbnail,
            seller_id = excluded.seller_id,
            product_id = excluded.product_id,
            category_id = excluded.category_id,
            category_name = excluded.category_name,
            source = excluded.source
        """,
        (
            offer.get(
                "item_id"
            ),
            offer.get(
                "title"
            ),
            offer.get(
                "price"
            ),
            offer.get(
                "original_price"
            ),
            offer.get(
                "discount"
            ),
            offer.get(
                "currency"
            ),
            offer.get(
                "permalink"
            ),
            offer.get(
                "thumbnail"
            ),
            str(
                offer.get(
                    "seller_id"
                )
                or ""
            ),
            offer.get(
                "product_id"
            ),
            offer.get(
                "category_id"
            ),
            offer.get(
                "category_name"
            ),
            offer.get(
                "source"
            ),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# HTML
# ============================================================

HTML = """
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>{{ app_name }}</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f5f6f8;
    color: #202124;
    font-family: Arial, sans-serif;
}

.container {
    width: min(1000px, 94%);
    margin: 25px auto 50px;
}

.header {
    background: #111827;
    color: white;
    border-radius: 18px;
    padding: 22px;
    margin-bottom: 18px;
}

.header h1 {
    margin: 0 0 8px;
    font-size: 25px;
}

.header p {
    margin: 0;
    opacity: .82;
}

.card {
    background: white;
    border-radius: 18px;
    padding: 20px;
    margin-bottom: 18px;
    box-shadow: 0 3px 15px rgba(0,0,0,.06);
}

label {
    display: block;
    font-weight: bold;
    margin-bottom: 7px;
}

input,
button {
    width: 100%;
    padding: 14px;
    border-radius: 11px;
    font-size: 16px;
}

input {
    border: 1px solid #d7dbe2;
    margin-bottom: 15px;
}

button {
    border: 0;
    background: #3483fa;
    color: white;
    font-weight: bold;
    cursor: pointer;
}

button:hover {
    opacity: .92;
}

.stats {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(145px, 1fr));
    gap: 10px;
}

.stat {
    background: #f3f4f6;
    border-radius: 13px;
    padding: 14px;
}

.stat strong {
    display: block;
    font-size: 24px;
    margin-bottom: 5px;
}

.category {
    background: #f8fafc;
    border: 1px solid #e4e7eb;
    padding: 12px;
    border-radius: 11px;
    margin-top: 8px;
}

.offer {
    border: 1px solid #e2e5e9;
    border-radius: 15px;
    padding: 15px;
    margin-top: 12px;
}

.offer h3 {
    margin: 0 0 9px;
    font-size: 17px;
}

.price {
    font-size: 23px;
    font-weight: bold;
}

.old {
    color: #777;
    text-decoration: line-through;
}

.discount {
    display: inline-block;
    margin-top: 8px;
    background: #00a650;
    color: white;
    border-radius: 8px;
    padding: 5px 9px;
    font-weight: bold;
}

.warning {
    background: #fff7e6;
    border: 1px solid #ffd591;
    padding: 14px;
    border-radius: 12px;
    margin-top: 12px;
}

.success {
    background: #e9f8ef;
    border: 1px solid #a8e0bb;
    padding: 14px;
    border-radius: 12px;
    margin-top: 12px;
}

.small {
    font-size: 13px;
    color: #666;
    line-height: 1.5;
}

a {
    color: #3483fa;
    text-decoration: none;
    font-weight: bold;
}

hr {
    border: 0;
    border-top: 1px solid #eee;
    margin: 18px 0;
}

</style>

</head>

<body>

<div class="container">

<div class="header">

<h1>
🛒 Caçador de Ofertas
</h1>

<p>
Busca por categoria + Mais Vendidos + análise de preço
</p>

</div>


<div class="card">

<form
method="get"
action="/buscar"
>

<label>
Produto
</label>

<input
name="q"
placeholder="Ex.: fone bluetooth"
value="{{ query }}"
>

<label>
Desconto mínimo (%)
</label>

<input
name="desconto"
type="number"
min="0"
max="99"
step="1"
value="{{ desconto }}"
>

<button type="submit">
🔎 Procurar ofertas
</button>

</form>

<p class="small">

Exemplos:
<strong>fone bluetooth</strong>,
<strong>celular</strong>,
<strong>TV</strong>,
<strong>notebook</strong>,
<strong>air fryer</strong>.

</p>

</div>


<div class="card">

<h2>
📌 Analisar anúncio diretamente
</h2>

<p class="small">
Cole um ou vários IDs de anúncios.
Exemplo: MLB123456789.
</p>

<form
method="get"
action="/buscar"
>

<input
name="ids"
placeholder="MLB123456789, MLB987654321"
>

<input
name="desconto"
type="number"
min="0"
max="99"
value="{{ desconto }}"
>

<button type="submit">
💰 Analisar anúncios
</button>

</form>

</div>


{% if searched %}

<div class="card">

<h2>
📊 Resultado
</h2>

<div class="stats">

<div class="stat">
<strong>
{{ stats.get("categorias_encontradas", 0) }}
</strong>
Categorias encontradas
</div>

<div class="stat">
<strong>
{{ stats.get("categorias_analisadas", 0) }}
</strong>
Categorias analisadas
</div>

<div class="stat">
<strong>
{{ stats.get("itens_highlights", 0) }}
</strong>
Itens encontrados
</div>

<div class="stat">
<strong>
{{ stats.get("produtos_highlights", 0) }}
</strong>
Produtos
</div>

<div class="stat">
<strong>
{{ stats.get("itens_analisados", 0) }}
</strong>
Itens analisados
</div>

<div class="stat">
<strong>
{{ stats.get("ofertas_encontradas", 0) }}
</strong>
Ofertas
</div>

<div class="stat">
<strong>
{{ stats.get("sem_desconto", 0) }}
</strong>
Sem desconto
</div>

<div class="stat">
<strong>
{{ stats.get("erros", 0) }}
</strong>
Erros
</div>

</div>

</div>


{% if categories %}

<div class="card">

<h2>
🗂️ Categorias encontradas
</h2>

{% for category in categories %}

<div class="category">

<strong>
{{ category.category_name }}
</strong>

<br>

<span class="small">
{{ category.category_id }}

{% if category.domain_name %}
<br>
Domínio: {{ category.domain_name }}
{% endif %}

</span>

</div>

{% endfor %}

</div>

{% endif %}


{% if category_results %}

<div class="card">

<h2>
🏆 Rankings analisados
</h2>

{% for category in category_results %}

<div class="category">

<strong>
{{ category.category_name }}
</strong>

<br>

<span class="small">

Categoria:
{{ category.category_id }}

<br>

Itens:
{{ category.items }}

&nbsp; • &nbsp;

Produtos:
{{ category.products }}

</span>

</div>

{% endfor %}

</div>

{% endif %}


{% if warning %}

<div class="card">

<div class="warning">

<strong>
ℹ️ Nenhuma oferta encontrada
</strong>

<p>
A API encontrou categorias e consultou os rankings,
mas nenhum anúncio analisado apresentou um desconto
igual ou superior ao limite escolhido ou o preço
original não estava disponível para essa consulta.
</p>

</div>

</div>

{% endif %}

{% endif %}


{% if results %}

<div class="card">

<h2>
🔥 Ofertas encontradas
</h2>

{% for item in results %}

<div class="offer">

<h3>
{{ item.title or item.item_id }}
</h3>

{% if item.original_price %}

<div class="old">

R$
{{ "%.2f"|format(item.original_price) }}

</div>

{% endif %}

<div class="price">

R$
{{ "%.2f"|format(item.price or 0) }}

</div>

{% if item.discount is not none %}

<div class="discount">

{{ "%.1f"|format(item.discount) }}% OFF

</div>

{% endif %}

<p class="small">

ID:
{{ item.item_id }}

<br>

Categoria:
{{ item.category_name or "-" }}

<br>

Fonte:
{{ item.source }}

</p>

{% if item.permalink %}

<a
href="{{ item.permalink }}"
target="_blank"
rel="noopener"
>

Abrir anúncio →

</a>

{% endif %}

</div>

{% endfor %}

</div>

{% endif %}


<div class="card">

<h2>
🔐 Mercado Livre
</h2>

{% if token_ok %}

<div class="success">

✅ Access Token disponível.

</div>

{% else %}

<div class="warning">

❌ Access Token não encontrado.

<br><br>

<a href="/mercadolivre/login">

Entrar com Mercado Livre →

</a>

</div>

{% endif %}

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

    return render_template_string(
        HTML,
        app_name=APP_NAME,
        query="",
        desconto=10,
        searched=False,
        results=[],
        categories=[],
        category_results=[],
        stats={},
        warning=False,
        token_ok=bool(
            current_token()
        ),
    )


# ============================================================
# BUSCAR
# ============================================================

@app.route("/buscar")
def buscar():

    query = normalize_query(
        request.args.get(
            "q",
            "",
        )
    )

    ids_text = request.args.get(
        "ids",
        "",
    )

    desconto = safe_float(
        request.args.get(
            "desconto",
            "10",
        )
    )

    if desconto is None:
        desconto = 10

    desconto = max(
        0,
        min(
            desconto,
            99,
        ),
    )

    results = []

    categories = []

    category_results = []

    stats = {
        "categorias_encontradas": 0,
        "categorias_analisadas": 0,
        "itens_highlights": 0,
        "produtos_highlights": 0,
        "itens_analisados": 0,
        "buy_box_analisadas": 0,
        "sem_desconto": 0,
        "sem_preco_original": 0,
        "erros": 0,
        "ofertas_encontradas": 0,
    }

    # ========================================================
    # IDS DIRETOS
    # ========================================================

    item_ids = extract_item_ids(
        ids_text
    )

    if item_ids:

        stats[
            "categorias_encontradas"
        ] = 0

        for item_id in item_ids[:50]:

            offer = analyze_item(
                item_id,
                source="item_direto",
            )

            if not offer.get(
                "ok"
            ):

                stats[
                    "erros"
                ] += 1

                continue

            stats[
                "itens_analisados"
            ] += 1

            discount = offer.get(
                "discount"
            )

            if discount is None:

                stats[
                    "sem_preco_original"
                ] += 1

                continue

            if discount < desconto:

                stats[
                    "sem_desconto"
                ] += 1

                continue

            results.append(
                offer
            )

            save_offer(
                offer
            )

    # ========================================================
    # BUSCA POR PRODUTO
    # ========================================================

    elif query:

        discovery = discover_offers(
            query=query,
            min_discount=desconto,
            max_categories=8,
        )

        if not discovery.get(
            "ok"
        ):

            return render_template_string(
                HTML,
                app_name=APP_NAME,
                query=query,
                desconto=desconto,
                searched=True,
                results=[],
                categories=[],
                category_results=[],
                stats={
                    "erros": 1
                },
                warning=True,
                token_ok=bool(
                    current_token()
                ),
            )

        results = discovery[
            "results"
        ]

        categories = discovery[
            "categories"
        ]

        category_results = (
            discovery[
                "category_results"
            ]
        )

        stats = discovery[
            "stats"
        ]

        for offer in results:

            save_offer(
                offer
            )

    results.sort(
        key=lambda x: (
            x.get(
                "discount"
            )
            if x.get(
                "discount"
            ) is not None
            else -1
        ),
        reverse=True,
    )

    stats[
        "ofertas_encontradas"
    ] = len(
        results
    )

    warning = (
        len(results) == 0
    )

    return render_template_string(
        HTML,
        app_name=APP_NAME,
        query=query,
        desconto=desconto,
        searched=True,
        results=results,
        categories=categories,
        category_results=category_results,
        stats=stats,
        warning=warning,
        token_ok=bool(
            current_token()
        ),
    )


# ============================================================
# OFERTAS SALVAS
# ============================================================

@app.route("/ofertas")
def ofertas():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM ofertas
        ORDER BY discount DESC, created_at DESC
        LIMIT 200
        """
    ).fetchall()

    conn.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "app": APP_NAME,
        "site": ML_SITE,
        "api": ML_API,
        "token_disponivel": bool(
            current_token()
        ),
        "playwright": False,
        "metodo_busca": (
            "domain_discovery + highlights"
        ),
    })


# ============================================================
# OAUTH LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not CLIENT_ID:

        return jsonify({
            "erro": (
                "ML_CLIENT_ID não configurado."
            )
        }), 500

    if not CLIENT_SECRET:

        return jsonify({
            "erro": (
                "ML_CLIENT_SECRET não configurado."
            )
        }), 500

    verifier, challenge = (
        create_pkce()
    )

    state = secrets.token_urlsafe(
        32
    )

    oauth_sessions[state] = {
        "code_verifier": verifier,
        "created_at": time.time(),
    }

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    return redirect(
        ML_AUTH
        + "?"
        + urlencode(params)
    )


# ============================================================
# OAUTH CALLBACK
# ============================================================

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
            "erro": error,
            "descricao": request.args.get(
                "error_description"
            ),
        }), 400

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    if not code:

        return jsonify({
            "erro": (
                "Código OAuth não recebido."
            )
        }), 400

    if not state:

        return jsonify({
            "erro": (
                "State não recebido."
            )
        }), 400

    session = oauth_sessions.get(
        state
    )

    if not session:

        return jsonify({
            "erro": (
                "State OAuth inválido "
                "ou expirado."
            )
        }), 400

    verifier = session.get(
        "code_verifier"
    )

    try:

        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type": (
                    "authorization_code"
                ),
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            },
            timeout=REQUEST_TIMEOUT,
        )

        try:

            data = response.json()

        except Exception:

            data = {
                "raw": response.text[:5000]
            }

        if response.status_code != 200:

            return jsonify({
                "ok": False,
                "status": response.status_code,
                "resposta": data,
            }), 400

        save_token_memory(
            data
        )

        oauth_sessions.pop(
            state,
            None,
        )

        return redirect("/")

    except Exception as e:

        return jsonify({
            "ok": False,
            "erro": str(e),
        }), 500


# ============================================================
# STATUS
# ============================================================

@app.route(
    "/mercadolivre/status"
)
def mercadolivre_status():

    token = current_token()

    if not token:

        return jsonify({
            "autenticado": False,
            "mensagem": (
                "Nenhum token disponível."
            ),
        })

    me = api_get(
        "/users/me"
    )

    return jsonify({
        "autenticado": me["ok"],
        "status": me["status"],
        "usuario": (
            me["data"]
            if me["ok"]
            else me["error"]
        ),
    })


# ============================================================
# TESTE DE CATEGORIA
# ============================================================

@app.route(
    "/mercadolivre/teste-categoria"
)
def teste_categoria():

    query = normalize_query(
        request.args.get(
            "q",
            "fone",
        )
    )

    categories = (
        discover_categories(
            query
        )
    )

    return jsonify({
        "consulta": query,
        "categorias": categories,
        "total": len(
            categories
        ),
    })


# ============================================================
# TESTE HIGHLIGHTS
# ============================================================

@app.route(
    "/mercadolivre/teste-highlights"
)
def teste_highlights():

    category_id = (
        request.args.get(
            "category_id",
            "",
        )
        .strip()
        .upper()
    )

    if not category_id:

        query = normalize_query(
            request.args.get(
                "q",
                "fone",
            )
        )

        categories = (
            discover_categories(
                query
            )
        )

        if not categories:

            return jsonify({
                "ok": False,
                "erro": (
                    "Nenhuma categoria encontrada."
                ),
            }), 404

        category = categories[0]

        category_id = category[
            "category_id"
        ]

    highlights = get_highlights(
        category_id
    )

    return jsonify({
        "category_id": category_id,
        "resultado": highlights,
    })


# ============================================================
# TESTE BUSCA COMPLETA
# ============================================================

@app.route(
    "/mercadolivre/teste-busca"
)
def teste_busca():

    query = normalize_query(
        request.args.get(
            "q",
            "fone",
        )
    )

    resultado = discover_offers(
        query=query,
        min_discount=0,
        max_categories=8,
    )

    return jsonify({
        "consulta": query,
        "resultado": resultado,
    })


# ============================================================
# TESTE DE PREÇO
# ============================================================

@app.route(
    "/mercadolivre/teste-preco"
)
def teste_preco():

    item_id = request.args.get(
        "item_id",
        "",
    )

    ids = extract_item_ids(
        item_id
    )

    if not ids:

        return jsonify({
            "erro": (
                "Informe ?item_id=MLB123456789"
            )
        }), 400

    item_id = ids[0]

    item = get_item(
        item_id
    )

    sale = get_sale_price(
        item_id
    )

    prices = get_prices(
        item_id
    )

    return jsonify({
        "item_id": item_id,

        "item": {
            "status": item["status"],
            "ok": item["ok"],
            "data": (
                item["data"]
                if item["ok"]
                else item["error"]
            ),
        },

        "sale_price": {
            "status": sale["status"],
            "ok": sale["ok"],
            "data": (
                sale["data"]
                if sale["ok"]
                else sale["error"]
            ),
        },

        "prices": {
            "status": prices["status"],
            "ok": prices["ok"],
            "data": (
                prices["data"]
                if prices["ok"]
                else prices["error"]
            ),
        },
    })


# ============================================================
# ITEM DIRETO
# ============================================================

@app.route(
    "/mercadolivre/item"
)
def mercadolivre_item():

    item_id = request.args.get(
        "item_id",
        "",
    )

    ids = extract_item_ids(
        item_id
    )

    if not ids:

        return jsonify({
            "erro": (
                "Informe um item_id válido."
            )
        }), 400

    result = analyze_item(
        ids[0],
        source="item_direto",
    )

    return jsonify(
        result
    )


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    result = {
        "app": APP_NAME,

        "api": ML_API,

        "site": ML_SITE,

        "metodo_busca": [
            "domain_discovery",
            "highlights",
            "items",
            "sale_price",
            "prices",
        ],

        "playwright": False,

        "oauth": {
            "client_id_configurado": bool(
                CLIENT_ID
            ),
            "client_secret_configurado": bool(
                CLIENT_SECRET
            ),
            "redirect_uri": REDIRECT_URI,
            "pkce": True,
        },

        "token_disponivel": bool(
            current_token()
        ),
    }

    if current_token():

        me = api_get(
            "/users/me"
        )

        result[
            "users_me"
        ] = {
            "ok": me["ok"],
            "status": me["status"],
            "data": (
                me["data"]
                if me["ok"]
                else me["error"]
            ),
        }

    return jsonify(
        result
    )


# ============================================================
# 404
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "ok": False,
        "erro": (
            "Rota não encontrada."
        ),
        "path": request.path,
    }), 404


# ============================================================
# 500
# ============================================================

@app.errorhandler(500)
def server_error(error):

    return jsonify({
        "ok": False,
        "erro": (
            "Erro interno."
        ),
        "detalhes": str(error),
    }), 500


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "8080",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )