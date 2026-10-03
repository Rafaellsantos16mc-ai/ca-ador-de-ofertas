import os
import re
import json
import time
import base64
import hashlib
import secrets
import sqlite3
from urllib.parse import (
    urlencode,
    urlparse,
    parse_qs,
    quote_plus,
)

import requests
from flask import (
    Flask,
    request,
    redirect,
    jsonify,
    render_template_string,
    send_file,
)


# ============================================================
# CONFIGURAÇÃO
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

DB_PATH = os.getenv("DB_PATH", "ofertas.db")

DEFAULT_LIMIT = int(os.getenv("DEFAULT_LIMIT", "20"))
MAX_LIMIT = int(os.getenv("MAX_LIMIT", "50"))

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

# Sessão simples em memória para OAuth.
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
    """
    Recupera o token salvo nas variáveis de ambiente.
    Também aceita ACCESS_TOKEN caso já exista no Railway.
    """
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
    """
    Guarda o token em memória durante a execução.
    """
    if not data:
        return

    oauth_sessions["token"] = data


def current_token():
    token_data = oauth_sessions.get("token")

    if isinstance(token_data, dict):
        access_token = token_data.get("access_token")

        if access_token:
            return access_token

    return get_saved_token()


def refresh_access_token():
    refresh_token = get_refresh_token()

    token_data = oauth_sessions.get("token")

    if isinstance(token_data, dict):
        refresh_token = token_data.get("refresh_token") or refresh_token

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

        return data.get("access_token")

    except Exception:
        return None


def api_get(path, params=None, retry=True):
    """
    GET autenticado na API do Mercado Livre.
    """
    token = current_token()

    if not token:
        return {
            "ok": False,
            "status": 401,
            "error": "Sem access token.",
            "data": None,
        }

    url = f"{ML_API}{path}"

    try:
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "CaçadorDeOfertas/1.0",
            },
            params=params or {},
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 401 and retry:
            new_token = refresh_access_token()

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
            "error": None if response.ok else data,
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
# OAUTH / PKCE
# ============================================================

def base64url(data):
    return (
        base64.urlsafe_b64encode(data)
        .decode()
        .rstrip("=")
    )


def create_pkce():
    verifier = base64url(secrets.token_bytes(32))

    challenge = base64url(
        hashlib.sha256(
            verifier.encode()
        ).digest()
    )

    return verifier, challenge


# ============================================================
# UTILITÁRIOS
# ============================================================

def safe_float(value):
    try:
        if value is None:
            return None

        return float(value)

    except Exception:
        return None


def calculate_discount(original, price):
    original = safe_float(original)
    price = safe_float(price)

    if not original or not price:
        return None

    if original <= 0:
        return None

    if price >= original:
        return 0.0

    return round(
        ((original - price) / original) * 100,
        2,
    )


def normalize_query(value):
    value = str(value or "").strip()

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value[:200]


def extract_item_ids(text):
    """
    Aceita:

    MLB123456789
    https://produto.mercadolivre.com.br/MLB-123456789...
    https://www.mercadolivre.com.br/...
    etc.

    O retorno é somente MLB + números.
    """

    if not text:
        return []

    found = re.findall(
        r"\bMLB[-_]?(\d{6,})\b",
        text,
        flags=re.IGNORECASE,
    )

    result = []

    for number in found:
        item_id = f"MLB{number}"

        if item_id not in result:
            result.append(item_id)

    return result


def money(value):
    value = safe_float(value)

    if value is None:
        return "-"

    return f"R$ {value:,.2f}".replace(
        ",",
        "X",
    ).replace(
        ".",
        ",",
    ).replace(
        "X",
        ".",
    )


# ============================================================
# PREÇO DO ITEM
# ============================================================

def get_item(item_id):
    return api_get(
        f"/items/{quote_plus(item_id)}"
    )


def get_sale_price(item_id):
    return api_get(
        f"/items/{quote_plus(item_id)}/sale_price",
        params={
            "context": "channel_marketplace"
        },
    )


def get_prices(item_id):
    return api_get(
        f"/items/{quote_plus(item_id)}/prices"
    )


def analyze_item(item_id, source="manual"):
    """
    Consulta:

    /items/{id}
    /items/{id}/sale_price
    /items/{id}/prices

    Tenta encontrar preço atual + preço original.
    """

    item_response = get_item(item_id)

    if not item_response["ok"]:
        return {
            "ok": False,
            "item_id": item_id,
            "source": source,
            "error": item_response["error"],
            "status": item_response["status"],
        }

    item = item_response["data"] or {}

    sale_response = get_sale_price(item_id)

    sale = (
        sale_response["data"]
        if sale_response["ok"]
        else {}
    )

    prices_response = get_prices(item_id)

    prices_data = (
        prices_response["data"]
        if prices_response["ok"]
        else {}
    )

    current_price = None
    original_price = None
    currency = (
        item.get("currency_id")
        or sale.get("currency_id")
        or "BRL"
    )

    # --------------------------------------------------------
    # PREÇO DO /sale_price
    # --------------------------------------------------------

    if isinstance(sale, dict):
        current_price = safe_float(
            sale.get("amount")
        )

        original_price = safe_float(
            sale.get("regular_amount")
        )

        currency = (
            sale.get("currency_id")
            or currency
        )

    # --------------------------------------------------------
    # FALLBACK PARA /prices
    # --------------------------------------------------------

    if isinstance(prices_data, dict):

        prices_list = prices_data.get(
            "prices",
            []
        )

        if isinstance(prices_list, list):

            promotions = []

            standards = []

            for p in prices_list:

                if not isinstance(p, dict):
                    continue

                amount = safe_float(
                    p.get("amount")
                )

                regular = safe_float(
                    p.get("regular_amount")
                )

                ptype = str(
                    p.get("type") or ""
                ).lower()

                if ptype == "promotion":
                    promotions.append(p)

                elif ptype == "standard":
                    standards.append(p)

                if (
                    current_price is None
                    and amount is not None
                ):
                    current_price = amount

                if (
                    original_price is None
                    and regular is not None
                ):
                    original_price = regular

                if p.get("currency_id"):
                    currency = p.get(
                        "currency_id"
                    )

            # Prefer promotion currently available
            if promotions:

                selected = promotions[0]

                amount = safe_float(
                    selected.get("amount")
                )

                regular = safe_float(
                    selected.get("regular_amount")
                )

                if amount is not None:
                    current_price = amount

                if regular is not None:
                    original_price = regular

            # Se não há original, tenta standard
            if (
                original_price is None
                and standards
            ):

                standard = standards[0]

                original_price = safe_float(
                    standard.get("amount")
                )

    # --------------------------------------------------------
    # FALLBACK PARA /items
    # --------------------------------------------------------

    if current_price is None:
        current_price = safe_float(
            item.get("price")
        )

    if original_price is None:
        original_price = safe_float(
            item.get("original_price")
        )

    discount = calculate_discount(
        original_price,
        current_price,
    )

    return {
        "ok": True,
        "item_id": item_id,
        "source": source,
        "title": item.get("title"),
        "price": current_price,
        "original_price": original_price,
        "discount": discount,
        "currency": currency,
        "permalink": item.get("permalink"),
        "thumbnail": (
            item.get("thumbnail")
            or (
                item.get("pictures", [{}])[0].get("url")
                if item.get("pictures")
                else None
            )
        ),
        "seller_id": item.get("seller_id"),
        "product_id": (
            item.get("catalog_product_id")
            or item.get("product_id")
        ),
        "available_quantity": item.get(
            "available_quantity"
        ),
        "condition": item.get("condition"),
        "listing_type_id": item.get(
            "listing_type_id"
        ),
        "tags": item.get("tags") or [],
    }


# ============================================================
# DESCOBERTA POR CATÁLOGO
# ============================================================

def search_catalog(query, limit=20):
    """
    Busca produtos de catálogo.

    IMPORTANTE:
    Isso NÃO é busca de anúncios.
    O anúncio MLB só aparece quando o catálogo fornece
    buy_box_winner.item_id.
    """

    query = normalize_query(query)

    limit = max(
        1,
        min(
            int(limit or DEFAULT_LIMIT),
            MAX_LIMIT,
        ),
    )

    response = api_get(
        "/products/search",
        params={
            "status": "active",
            "site_id": ML_SITE,
            "q": query,
            "limit": limit,
            "offset": 0,
        },
    )

    if not response["ok"]:
        return {
            "ok": False,
            "status": response["status"],
            "error": response["error"],
            "products": [],
        }

    data = response["data"] or {}

    return {
        "ok": True,
        "status": response["status"],
        "keywords": data.get("keywords"),
        "paging": data.get("paging") or {},
        "products": data.get("results") or [],
    }


def get_product(product_id):
    return api_get(
        f"/products/{quote_plus(product_id)}"
    )


def extract_buy_box_item(product):
    """
    Retorna o item_id do Buy Box, se existir.
    """

    if not isinstance(product, dict):
        return None

    buy_box = product.get(
        "buy_box_winner"
    )

    if isinstance(buy_box, dict):

        item_id = buy_box.get(
            "item_id"
        )

        if item_id:
            return item_id

    # Campos alternativos defensivos
    for key in (
        "item_id",
        "listing_item_id",
        "seller_item_id",
    ):

        value = product.get(key)

        if isinstance(value, str):
            if re.fullmatch(
                r"MLB\d+",
                value,
                flags=re.IGNORECASE,
            ):
                return value.upper()

    return None


def discover_from_catalog(
    query,
    limit=20,
    min_discount=0,
):
    """
    Pesquisa catálogo e tenta descobrir anúncios reais.

    Também analisa children_ids quando existirem.
    """

    catalog = search_catalog(
        query,
        limit,
    )

    if not catalog["ok"]:
        return {
            "ok": False,
            "error": catalog["error"],
            "status": catalog["status"],
            "results": [],
            "stats": {},
        }

    products = catalog["products"]

    results = []

    analyzed_products = 0
    buy_box_found = 0
    children_analyzed = 0
    no_item = 0
    no_discount = 0
    errors = 0

    seen_items = set()
    seen_products = set()

    # --------------------------------------------------------
    # PRIMEIRA PASSADA
    # --------------------------------------------------------

    for product in products:

        if not isinstance(product, dict):
            continue

        product_id = product.get("id")

        if not product_id:
            continue

        if product_id in seen_products:
            continue

        seen_products.add(product_id)

        analyzed_products += 1

        detail_response = get_product(
            product_id
        )

        if not detail_response["ok"]:
            errors += 1
            continue

        detail = detail_response["data"] or {}

        item_id = extract_buy_box_item(
            detail
        )

        if item_id:
            buy_box_found += 1

            if item_id not in seen_items:
                seen_items.add(item_id)

                offer = analyze_item(
                    item_id,
                    source="catalog_buy_box",
                )

                if not offer.get("ok"):
                    errors += 1
                    continue

                discount = offer.get(
                    "discount"
                )

                if (
                    discount is not None
                    and discount >= min_discount
                ):
                    results.append(offer)

                else:
                    no_discount += 1

        # ----------------------------------------------------
        # CHILDREN
        # ----------------------------------------------------

        children = detail.get(
            "children_ids"
        )

        if isinstance(children, list):

            for child_id in children:

                if not child_id:
                    continue

                children_analyzed += 1

                child_response = get_product(
                    child_id
                )

                if not child_response["ok"]:
                    errors += 1
                    continue

                child = (
                    child_response["data"]
                    or {}
                )

                child_item_id = (
                    extract_buy_box_item(
                        child
                    )
                )

                if not child_item_id:
                    continue

                buy_box_found += 1

                if child_item_id in seen_items:
                    continue

                seen_items.add(
                    child_item_id
                )

                offer = analyze_item(
                    child_item_id,
                    source="catalog_child",
                )

                if not offer.get("ok"):
                    errors += 1
                    continue

                discount = offer.get(
                    "discount"
                )

                if (
                    discount is not None
                    and discount >= min_discount
                ):
                    results.append(
                        offer
                    )
                else:
                    no_discount += 1

    results.sort(
        key=lambda x: (
            x.get("discount")
            if x.get("discount") is not None
            else -1
        ),
        reverse=True,
    )

    return {
        "ok": True,
        "query": query,
        "results": results,
        "stats": {
            "produtos_analisados": analyzed_products,
            "buy_box_encontradas": buy_box_found,
            "produtos_filhos_analisados": children_analyzed,
            "sem_publicacao_item": no_item,
            "sem_desconto_suficiente": no_discount,
            "com_erro": errors,
            "ofertas_encontradas": len(results),
        },
        "catalog_paging": catalog.get(
            "paging",
            {},
        ),
    }


# ============================================================
# SALVAR OFERTAS
# ============================================================

def save_offer(offer):
    if not offer:
        return

    item_id = offer.get("item_id")

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
            source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            source = excluded.source
        """,
        (
            offer.get("item_id"),
            offer.get("title"),
            offer.get("price"),
            offer.get("original_price"),
            offer.get("discount"),
            offer.get("currency"),
            offer.get("permalink"),
            offer.get("thumbnail"),
            str(
                offer.get("seller_id")
                or ""
            ),
            offer.get("product_id"),
            offer.get("source"),
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
<meta name="viewport"
      content="width=device-width,initial-scale=1">

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
    opacity: .8;
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
    padding: 13px;
    border-radius: 10px;
    font-size: 16px;
}

input {
    border: 1px solid #d7dbe2;
    margin-bottom: 14px;
}

button {
    border: 0;
    background: #3483fa;
    color: white;
    font-weight: bold;
    cursor: pointer;
}

button:hover {
    opacity: .9;
}

.secondary {
    background: #111827;
    margin-top: 10px;
}

.stats {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(145px, 1fr));
    gap: 10px;
}

.stat {
    background: #f3f4f6;
    border-radius: 12px;
    padding: 14px;
}

.stat strong {
    display: block;
    font-size: 23px;
    margin-bottom: 4px;
}

.offer {
    border: 1px solid #e2e5e9;
    border-radius: 15px;
    padding: 15px;
    margin-top: 12px;
}

.offer h3 {
    margin: 0 0 10px;
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

a {
    color: #3483fa;
    text-decoration: none;
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

</style>
</head>

<body>

<div class="container">

<div class="header">
    <h1>🛒 Caçador de Ofertas</h1>
    <p>
        Mercado Livre • Catálogo + análise de anúncios
    </p>
</div>


<div class="card">

<form method="get" action="/buscar">

<label>Produto</label>

<input
    name="q"
    placeholder="Ex.: fone bluetooth"
    value="{{ query }}"
>

<label>Desconto mínimo (%)</label>

<input
    name="desconto"
    type="number"
    min="0"
    max="99"
    step="1"
    value="{{ desconto }}"
>

<label>Quantidade de produtos do catálogo</label>

<input
    name="limite"
    type="number"
    min="1"
    max="50"
    value="{{ limite }}"
>

<button type="submit">
    🔎 Procurar ofertas
</button>

</form>

</div>


<div class="card">

<h2>📌 Analisar anúncios diretamente</h2>

<p class="small">
Cole um ou vários IDs/links de anúncios do Mercado Livre.
Exemplo: MLB123456789.
</p>

<form method="get" action="/buscar">

<input
    name="ids"
    placeholder="MLB123456789, MLB987654321"
>

<input
    name="desconto"
    type="number"
    min="0"
    max="99"
    step="1"
    value="{{ desconto }}"
>

<button type="submit">
    💰 Analisar anúncios
</button>

</form>

</div>


{% if searched %}

<div class="card">

<h2>📊 Resultado</h2>

<div class="stats">

<div class="stat">
<strong>{{ stats.get("produtos_analisados", 0) }}</strong>
Produtos analisados
</div>

<div class="stat">
<strong>{{ stats.get("buy_box_encontradas", 0) }}</strong>
Buy Box/item
</div>

<div class="stat">
<strong>{{ stats.get("produtos_filhos_analisados", 0) }}</strong>
Filhos analisados
</div>

<div class="stat">
<strong>{{ stats.get("ofertas_encontradas", 0) }}</strong>
Ofertas
</div>

<div class="stat">
<strong>{{ stats.get("sem_desconto_suficiente", 0) }}</strong>
Sem desconto
</div>

<div class="stat">
<strong>{{ stats.get("com_erro", 0) }}</strong>
Erros
</div>

</div>

{% if catalog_warning %}

<div class="warning">

<strong>ℹ️ Atenção</strong>

<p>
A API encontrou produtos de catálogo, mas eles não
possuem uma publicação vencedora disponível para esta
consulta. Nesse caso não existe um MLB de anúncio para
analisar automaticamente.
</p>

</div>

{% endif %}

</div>

{% endif %}


{% if results %}

<div class="card">

<h2>🔥 Ofertas encontradas</h2>

{% for item in results %}

<div class="offer">

<h3>
{{ item.title or item.item_id }}
</h3>

{% if item.original_price %}
<div class="old">
{{ item.currency or "BRL" }}
{{ "%.2f"|format(item.original_price) }}
</div>
{% endif %}

<div class="price">
{{ item.currency or "BRL" }}
{{ "%.2f"|format(item.price or 0) }}
</div>

{% if item.discount is not none %}
<div class="discount">
{{ "%.1f"|format(item.discount) }}% OFF
</div>
{% endif %}

<p class="small">
ID: {{ item.item_id }}<br>
Fonte: {{ item.source }}
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

<h2>🔐 Mercado Livre</h2>

<p class="small">
Status da autenticação:
</p>

{% if token_ok %}

<div class="success">
✅ Access Token disponível.
</div>

{% else %}

<div class="warning">
❌ Nenhum Access Token disponível.
</div>

<a href="/mercadolivre/login">
    Entrar com Mercado Livre
</a>

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

    token_ok = bool(
        current_token()
    )

    return render_template_string(
        HTML,
        app_name=APP_NAME,
        query="",
        desconto=10,
        limite=20,
        searched=False,
        stats={},
        results=[],
        catalog_warning=False,
        token_ok=token_ok,
    )


# ============================================================
# BUSCAR
# ============================================================

@app.route("/buscar")
def buscar():

    query = normalize_query(
        request.args.get("q", "")
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

    try:
        limite = int(
            request.args.get(
                "limite",
                DEFAULT_LIMIT,
            )
        )

    except Exception:
        limite = DEFAULT_LIMIT

    limite = max(
        1,
        min(
            limite,
            MAX_LIMIT,
        ),
    )

    results = []

    stats = {
        "produtos_analisados": 0,
        "buy_box_encontradas": 0,
        "produtos_filhos_analisados": 0,
        "sem_publicacao_item": 0,
        "sem_desconto_suficiente": 0,
        "com_erro": 0,
        "ofertas_encontradas": 0,
    }

    catalog_warning = False

    # --------------------------------------------------------
    # 1. IDS DIRETOS
    # --------------------------------------------------------

    item_ids = extract_item_ids(
        ids_text
    )

    if item_ids:

        for item_id in item_ids[:50]:

            offer = analyze_item(
                item_id,
                source="item_direto",
            )

            if not offer.get("ok"):

                stats["com_erro"] += 1
                continue

            discount = offer.get(
                "discount"
            )

            if (
                discount is not None
                and discount >= desconto
            ):

                results.append(
                    offer
                )

                save_offer(
                    offer
                )

            else:

                stats[
                    "sem_desconto_suficiente"
                ] += 1

        stats[
            "ofertas_encontradas"
        ] = len(results)

    # --------------------------------------------------------
    # 2. CATÁLOGO
    # --------------------------------------------------------

    elif query:

        discovery = discover_from_catalog(
            query=query,
            limit=limite,
            min_discount=desconto,
        )

        if not discovery["ok"]:

            return jsonify({
                "ok": False,
                "erro": discovery["error"],
                "status": discovery["status"],
            }), 500

        results = discovery[
            "results"
        ]

        stats = discovery[
            "stats"
        ]

        for offer in results:
            save_offer(
                offer
            )

        if (
            stats[
                "produtos_analisados"
            ] > 0
            and stats[
                "buy_box_encontradas"
            ] == 0
        ):
            catalog_warning = True

    results.sort(
        key=lambda x: (
            x.get("discount")
            if x.get("discount") is not None
            else -1
        ),
        reverse=True,
    )

    return render_template_string(
        HTML,
        app_name=APP_NAME,
        query=query,
        desconto=desconto,
        limite=limite,
        searched=True,
        stats=stats,
        results=results,
        catalog_warning=catalog_warning,
        token_ok=bool(
            current_token()
        ),
    )


# ============================================================
# GERAR
# ============================================================

@app.route("/gerar", methods=["GET", "POST"])
def gerar():

    if request.method == "POST":

        query = request.form.get(
            "q",
            "",
        )

        desconto = request.form.get(
            "desconto",
            "10",
        )

        limite = request.form.get(
            "limite",
            "20",
        )

        url = (
            "/buscar?"
            + urlencode({
                "q": query,
                "desconto": desconto,
                "limite": limite,
            })
        )

        return redirect(url)

    return redirect("/")


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
        "oauth_configurado": bool(
            CLIENT_ID
            and CLIENT_SECRET
            and REDIRECT_URI
        ),
        "token_disponivel": bool(
            current_token()
        ),
        "api": ML_API,
        "site": ML_SITE,
    })


# ============================================================
# OAUTH LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not CLIENT_ID:
        return jsonify({
            "erro": "ML_CLIENT_ID não configurado."
        }), 500

    if not CLIENT_SECRET:
        return jsonify({
            "erro": "ML_CLIENT_SECRET não configurado."
        }), 500

    verifier, challenge = create_pkce()

    state = secrets.token_urlsafe(32)

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

@app.route("/mercadolivre/callback")
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
            "erro": "Código OAuth não recebido."
        }), 400

    if not state:
        return jsonify({
            "erro": "State não recebido."
        }), 400

    session = oauth_sessions.get(
        state
    )

    if not session:
        return jsonify({
            "erro": "State OAuth inválido ou expirado."
        }), 400

    verifier = session.get(
        "code_verifier"
    )

    try:

        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type": "authorization_code",
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
# STATUS MERCADO LIVRE
# ============================================================

@app.route("/mercadolivre/status")
def mercadolivre_status():

    token = current_token()

    if not token:

        return jsonify({
            "autenticado": False,
            "mensagem": "Nenhum token disponível.",
        })

    me = api_get(
        "/users/me"
    )

    return jsonify({
        "autenticado": me["ok"],
        "status": me["status"],
        "usuario": me["data"]
        if me["ok"]
        else me["error"],
    })


# ============================================================
# DIAGNÓSTICO DE BUSCA
# ============================================================

@app.route("/mercadolivre/teste-busca")
def teste_busca():

    query = normalize_query(
        request.args.get(
            "q",
            "fone",
        )
    )

    catalog = search_catalog(
        query,
        limit=10,
    )

    if not catalog["ok"]:

        return jsonify({
            "consulta": query,
            "ok": False,
            "status": catalog["status"],
            "erro": catalog["error"],
        })

    diagnostics = []

    for product in catalog[
        "products"
    ][:10]:

        product_id = product.get(
            "id"
        )

        if not product_id:
            continue

        detail_response = get_product(
            product_id
        )

        if not detail_response["ok"]:

            diagnostics.append({
                "product_id": product_id,
                "erro": detail_response[
                    "error"
                ],
            })

            continue

        detail = (
            detail_response["data"]
            or {}
        )

        buy_box = detail.get(
            "buy_box_winner"
        )

        diagnostics.append({
            "product_id": product_id,
            "nome": detail.get(
                "name"
            ),
            "status": detail.get(
                "status"
            ),
            "buy_box_item_id": (
                buy_box.get("item_id")
                if isinstance(
                    buy_box,
                    dict
                )
                else None
            ),
            "buy_box": buy_box,
            "children_ids": detail.get(
                "children_ids",
                [],
            ),
            "permalink": detail.get(
                "permalink"
            ),
        })

    return jsonify({
        "consulta": query,
        "ok": True,
        "total_catalogo": len(
            catalog["products"]
        ),
        "paging": catalog[
            "paging"
        ],
        "resultados": diagnostics,
        "observacao": (
            "A busca de catálogo pode retornar "
            "produtos sem publicação vencedora. "
            "Nesse caso buy_box_item_id fica null."
        ),
    })


# ============================================================
# DIAGNÓSTICO DE PREÇO
# ============================================================

@app.route("/mercadolivre/teste-preco")
def teste_preco():

    item_id = request.args.get(
        "item_id",
        "",
    ).strip().upper()

    if not item_id:

        return jsonify({
            "erro": (
                "Informe ?item_id=MLB123456789"
            )
        }), 400

    ids = extract_item_ids(
        item_id
    )

    if not ids:

        return jsonify({
            "erro": (
                "ID inválido. Use MLB + números."
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
            "data": item["data"]
            if item["ok"]
            else item["error"],
        },

        "sale_price": {
            "status": sale["status"],
            "ok": sale["ok"],
            "data": sale["data"]
            if sale["ok"]
            else sale["error"],
        },

        "prices": {
            "status": prices["status"],
            "ok": prices["ok"],
            "data": prices["data"]
            if prices["ok"]
            else prices["error"],
        },
    })


# ============================================================
# ANALISAR ITEM DIRETO — JSON
# ============================================================

@app.route("/mercadolivre/item")
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
# DIAGNÓSTICO GERAL
# ============================================================

@app.route("/mercadolivre/diagnostico")
def diagnostico():

    token = current_token()

    result = {
        "app": APP_NAME,
        "api": ML_API,
        "site": ML_SITE,
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
            token
        ),
    }

    if token:

        me = api_get(
            "/users/me"
        )

        result["users_me"] = {
            "status": me["status"],
            "ok": me["ok"],
            "data": me["data"]
            if me["ok"]
            else me["error"],
        }

    return jsonify(
        result
    )


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "ok": False,
        "erro": "Rota não encontrada.",
        "path": request.path,
    }), 404


@app.errorhandler(500)
def server_error(error):

    return jsonify({
        "ok": False,
        "erro": "Erro interno.",
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