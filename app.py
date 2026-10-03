import os
import re
import json
import time
import sqlite3
import secrets
import hashlib
import base64
from urllib.parse import urlencode, quote

import requests
from flask import (
    Flask,
    request,
    redirect,
    session,
    render_template_string,
    jsonify,
)

# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "caçador-ofertas-chave-trocar-em-producao"
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

REQUEST_TIMEOUT = 25

DEFAULT_LIMIT_CATEGORIES = 8
DEFAULT_LIMIT_PRODUCTS = 20

# ============================================================
# SQLITE
# ============================================================


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

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


def clean_text(value):
    if value is None:
        return ""

    value = str(value)

    return re.sub(r"\s+", " ", value).strip()


def money(value):
    try:
        return float(value)
    except Exception:
        return None


def calculate_discount(price, original_price):
    try:
        price = float(price)
        original_price = float(original_price)

        if original_price <= 0:
            return None

        if price >= original_price:
            return 0.0

        return round(((original_price - price) / original_price) * 100, 2)

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
# OAUTH
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
        "SELECT refresh_token, user_id, nickname FROM oauth_tokens WHERE id = 1"
    ).fetchone()

    if old:
        if not refresh_token:
            refresh_token = old["refresh_token"]

        if not user_id:
            user_id = old["user_id"]

        if not nickname:
            nickname = old["nickname"]

    expires_at = now() + int(expires_in or 21600)

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


def get_access_token():
    row = get_tokens()

    if not row:
        return None

    if not row["access_token"]:
        return None

    # Renova com pequena margem
    if row["expires_at"] and row["expires_at"] < now() + 120:
        refreshed = refresh_access_token()

        if refreshed:
            return refreshed

    return row["access_token"]


def refresh_access_token():
    row = get_tokens()

    if not row or not row["refresh_token"]:
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
                "refresh_token": row["refresh_token"],
            },
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:
            print(
                "[REFRESH ERRO]",
                response.status_code,
                response.text[:1000]
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
        print("[REFRESH EXCEPTION]", e)
        return None


def require_token():
    token = get_access_token()

    if not token:
        return None

    return token


# ============================================================
# API MERCADO LIVRE
# ============================================================


def api_get(path, params=None, retry=True):
    token = require_token()

    if not token:
        return {
            "ok": False,
            "status": 401,
            "data": None,
            "error": "Aplicação não está autenticada."
        }

    url = ML_API + path

    try:
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params=params or {},
            timeout=REQUEST_TIMEOUT
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
                "error": clean_text(error)
            }

        return {
            "ok": True,
            "status": response.status_code,
            "data": data,
            "error": None
        }

    except requests.RequestException as e:
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
    return api_get("/users/me")


# ============================================================
# CATEGORIAS
# ============================================================


def discover_categories(query):
    query = clean_text(query)

    if not query:
        return {
            "ok": False,
            "categories": [],
            "error": "Digite um produto."
        }

    result = api_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        params={
            "q": query,
            "limit": DEFAULT_LIMIT_CATEGORIES
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

    if not isinstance(data, list):
        data = []

    categories = []

    for item in data:
        if not isinstance(item, dict):
            continue

        category_id = item.get("id")

        if not category_id:
            continue

        categories.append({
            "id": category_id,
            "name": item.get("name"),
            "domain_id": item.get("domain_id"),
            "domain_name": item.get("domain_name"),
            "score": item.get("score"),
            "source": "domain_discovery"
        })

    return {
        "ok": True,
        "categories": categories,
        "status": 200,
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

    if not isinstance(data, list):
        data = []

    products = []

    for entry in data:
        if not isinstance(entry, dict):
            continue

        entry_type = entry.get("type")
        entry_id = entry.get("id")

        if not entry_id:
            continue

        products.append({
            "id": entry_id,
            "type": entry_type,
            "position": entry.get("position")
        })

    return {
        "ok": True,
        "products": products,
        "status": 200,
        "error": None
    }


# ============================================================
# PRODUTO DE CATÁLOGO
# ============================================================


def get_product(product_id):
    return api_get(
        f"/products/{product_id}"
    )


def extract_product_data(product):
    if not isinstance(product, dict):
        return None

    product_id = product.get("id")

    if not product_id:
        return None

    buy_box = product.get("buy_box_winner")

    if not isinstance(buy_box, dict):
        buy_box = None

    price = None
    original_price = None
    item_id = None
    seller_id = None
    currency = "BRL"

    if buy_box:
        item_id = buy_box.get("item_id")
        seller_id = buy_box.get("seller_id")

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

    # Alguns produtos podem trazer faixa de preço
    # quando não há Buy Box.
    price_range = product.get(
        "buy_box_winner_price_range"
    )

    if price is None and isinstance(price_range, dict):
        minimum = price_range.get("min")

        if isinstance(minimum, dict):
            price = money(
                minimum.get("price")
            )

            currency = (
                minimum.get("currency_id")
                or currency
            )

    discount = calculate_discount(
        price,
        original_price
    )

    permalink = product.get("permalink")

    return {
        "product_id": product_id,
        "item_id": item_id,
        "seller_id": seller_id,
        "title": clean_text(
            product.get("name")
            or product.get("family_name")
            or ""
        ),
        "price": price,
        "original_price": original_price,
        "discount": discount,
        "currency": currency,
        "permalink": product_url(
            product_id,
            permalink
        ),
        "has_buy_box": bool(buy_box),
        "domain_id": product.get("domain_id"),
        "status": product.get("status"),
        "parent_id": product.get("parent_id"),
        "children_ids": product.get("children_ids") or [],
        "pictures": product.get("pictures") or [],
    }


# ============================================================
# PREÇOS
# ============================================================


def get_item_prices(item_id):
    if not item_id:
        return {
            "ok": False,
            "status": 0,
            "prices": [],
            "error": "Item ID não informado."
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
        prices = data.get("prices") or []
    elif isinstance(data, list):
        prices = data
    else:
        prices = []

    return {
        "ok": True,
        "status": 200,
        "prices": prices,
        "error": None
    }


def get_sale_price(item_id):
    if not item_id:
        return {
            "ok": False,
            "status": 0,
            "data": None,
            "error": "Item ID não informado."
        }

    result = api_get(
        f"/items/{item_id}/sale_price",
        params={
            "context": "channel_marketplace"
        }
    )

    return result


# ============================================================
# COMPLETAR PREÇO DO BUY BOX
# ============================================================


def enrich_buy_box_price(data):
    if not data:
        return data

    item_id = data.get("item_id")

    if not item_id:
        return data

    # Primeiro tenta sale_price.
    sale = get_sale_price(item_id)

    if sale["ok"] and isinstance(sale["data"], dict):
        sale_data = sale["data"]

        amount = money(
            sale_data.get("amount")
        )

        regular_amount = money(
            sale_data.get("regular_amount")
        )

        if amount is not None:
            data["price"] = amount

        if regular_amount is not None:
            data["original_price"] = regular_amount

        data["discount"] = calculate_discount(
            data.get("price"),
            data.get("original_price")
        )

    # Se ainda não temos preço original,
    # tenta /prices.
    if (
        data.get("price") is None
        or data.get("original_price") is None
    ):
        prices = get_item_prices(item_id)

        if prices["ok"]:
            candidates = prices["prices"]

            promotion = None
            standard = None

            for p in candidates:
                if not isinstance(p, dict):
                    continue

                if p.get("type") == "promotion":
                    promotion = p

                elif p.get("type") == "standard":
                    standard = p

            selected = promotion or standard

            if selected:
                amount = money(
                    selected.get("amount")
                )

                regular_amount = money(
                    selected.get("regular_amount")
                )

                if amount is not None:
                    data["price"] = amount

                if regular_amount is not None:
                    data["original_price"] = regular_amount

            data["discount"] = calculate_discount(
                data.get("price"),
                data.get("original_price")
            )

    return data


# ============================================================
# SALVAR OFERTA
# ============================================================


def save_product(data, category_id=None, category_name=None):
    if not data:
        return

    product_id = data.get("product_id")

    if not product_id:
        return

    pictures = data.get("pictures") or []

    image_url = None

    if pictures:
        first = pictures[0]

        if isinstance(first, dict):
            image_url = (
                first.get("url")
                or first.get("secure_url")
            )

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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)

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
        image_url,
        now(),
        now()
    ))

    conn.commit()
    conn.close()


# ============================================================
# BUSCA PRINCIPAL
# ============================================================


def search_offers(query, min_discount=0):
    categories_result = discover_categories(query)

    if not categories_result["ok"]:
        return {
            "ok": False,
            "query": query,
            "error": categories_result["error"],
            "categories": [],
            "products": [],
            "offers": [],
            "stats": {}
        }

    categories = categories_result["categories"]

    all_products = []
    product_seen = set()

    category_debug = []

    for category in categories:
        category_id = category["id"]

        highlights = get_highlights(category_id)

        if not highlights["ok"]:
            category_debug.append({
                "id": category_id,
                "nome": category.get("name"),
                "status": highlights["status"],
                "erro": highlights["error"],
                "produtos": 0,
                "tipos": {}
            })
            continue

        entries = highlights["products"]

        type_counter = {}

        for entry in entries:
            entry_type = entry.get("type") or "UNKNOWN"

            type_counter[entry_type] = (
                type_counter.get(entry_type, 0) + 1
            )

            entry_id = entry.get("id")

            if not entry_id:
                continue

            # USER_PRODUCT não será consultado.
            # A API está retornando 403 para produtos
            # de terceiros com o nosso token.
            if entry_type == "USER_PRODUCT":
                continue

            if entry_type == "ITEM":
                # ITEM encontrado no ranking.
                # Não tentamos abrir automaticamente,
                # pois anúncios de terceiros podem retornar 403.
                continue

            if entry_type != "PRODUCT":
                continue

            if entry_id in product_seen:
                continue

            product_seen.add(entry_id)

            all_products.append({
                "product_id": entry_id,
                "category_id": category_id,
                "category_name": category.get("name"),
                "source": "highlights_product"
            })

        category_debug.append({
            "id": category_id,
            "nome": category.get("name"),
            "status": 200,
            "erro": None,
            "produtos": len(entries),
            "tipos": type_counter
        })

    analyzed = []
    offers = []

    stats = {
        "categorias_encontradas": len(categories),
        "categorias_analisadas": len(category_debug),
        "produtos_ranking": sum(
            x.get("produtos", 0)
            for x in category_debug
        ),
        "produtos_unicos": len(all_products),
        "produtos_consultados": 0,
        "buy_box_encontradas": 0,
        "produtos_sem_buy_box": 0,
        "produtos_com_preco": 0,
        "ofertas_encontradas": 0,
        "sem_desconto": 0,
        "erros": 0
    }

    # Limita a quantidade de detalhes para evitar
    # chamadas excessivas na API.
    for candidate in all_products[:100]:

        product_id = candidate["product_id"]

        result = get_product(product_id)

        stats["produtos_consultados"] += 1

        if not result["ok"]:
            stats["erros"] += 1

            analyzed.append({
                "product_id": product_id,
                "title": "",
                "status": result["status"],
                "error": result["error"],
                "has_buy_box": False,
                "price": None,
                "original_price": None,
                "discount": None,
                "permalink": product_url(product_id)
            })

            continue

        product = result["data"]

        data = extract_product_data(product)

        if not data:
            stats["erros"] += 1
            continue

        # Se houver Buy Box, tentamos complementar preço.
        if data["has_buy_box"]:
            stats["buy_box_encontradas"] += 1

            data = enrich_buy_box_price(data)

        else:
            stats["produtos_sem_buy_box"] += 1

        if data.get("price") is not None:
            stats["produtos_com_preco"] += 1

        # Produto que possui preço e preço original.
        discount = data.get("discount")

        if (
            discount is not None
            and discount >= float(min_discount)
            and data.get("price") is not None
            and data.get("original_price") is not None
            and data.get("original_price") > data.get("price")
        ):
            stats["ofertas_encontradas"] += 1

            offers.append({
                **data,
                "category_id": candidate["category_id"],
                "category_name": candidate["category_name"]
            })

        elif (
            data.get("price") is not None
            and data.get("original_price") is not None
            and data.get("original_price") <= data.get("price")
        ):
            stats["sem_desconto"] += 1

        data["category_id"] = candidate["category_id"]
        data["category_name"] = candidate["category_name"]

        analyzed.append(data)

        # Salva todos os produtos conhecidos.
        save_product(
            data,
            category_id=candidate["category_id"],
            category_name=candidate["category_name"]
        )

    # Maior desconto primeiro.
    offers.sort(
        key=lambda x: x.get("discount") or 0,
        reverse=True
    )

    return {
        "ok": True,
        "query": query,
        "categories": categories,
        "category_debug": category_debug,
        "products": analyzed,
        "offers": offers,
        "stats": stats
    }


# ============================================================
# ROTAS
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
        nickname=token["nickname"] if token else None,
        user_id=token["user_id"] if token else None
    )


@app.route("/mercadolivre/login")
def ml_login():

    if not CLIENT_ID:
        return "ML_CLIENT_ID não configurado.", 500

    verifier, challenge = generate_pkce()

    state = secrets.token_urlsafe(32)

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

    url = (
        ML_AUTH
        + "?"
        + urlencode(params)
    )

    return redirect(url)


@app.route("/mercadolivre/callback")
def ml_callback():

    error = request.args.get("error")

    if error:
        return f"""
        <h2>Erro no Mercado Livre</h2>
        <pre>{clean_text(error)}</pre>
        """

    state = request.args.get("state")

    if state != session.get("oauth_state"):
        return "State OAuth inválido.", 400

    code = request.args.get("code")

    if not code:
        return "Código OAuth não recebido.", 400

    verifier = session.get("code_verifier")

    if not verifier:
        return "Code verifier não encontrado.", 400

    try:
        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier
            },
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:
            return f"""
            <h2>Erro ao obter token</h2>
            <pre>{response.text}</pre>
            """, 400

        data = response.json()

        access_token = data.get("access_token")

        if not access_token:
            return "Mercado Livre não retornou access_token.", 400

        save_tokens(
            access_token=access_token,
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in")
        )

        me = get_me()

        user_id = None
        nickname = None

        if me["ok"] and isinstance(me["data"], dict):
            user_id = me["data"].get("id")
            nickname = me["data"].get("nickname")

            save_tokens(
                access_token=access_token,
                refresh_token=data.get("refresh_token"),
                expires_in=data.get("expires_in"),
                user_id=user_id,
                nickname=nickname
            )

        session.pop("oauth_state", None)
        session.pop("code_verifier", None)

        return redirect("/")

    except Exception as e:
        return f"""
        <h2>Erro OAuth</h2>
        <pre>{clean_text(e)}</pre>
        """, 500


@app.route("/mercadolivre/logout")
def ml_logout():

    conn = db()

    conn.execute(
        "DELETE FROM oauth_tokens WHERE id = 1"
    )

    conn.commit()
    conn.close()

    return redirect("/")


# ============================================================
# BUSCA WEB
# ============================================================


@app.route("/buscar")
def buscar():

    query = clean_text(
        request.args.get("q", "")
    )

    try:
        min_discount = float(
            request.args.get(
                "desconto",
                0
            )
        )
    except Exception:
        min_discount = 0

    if not query:
        return redirect("/")

    result = search_offers(
        query,
        min_discount
    )

    return render_template_string(
        RESULTS_HTML,
        result=result,
        query=query,
        min_discount=min_discount
    )


# ============================================================
# API JSON
# ============================================================


@app.route("/api/buscar")
def api_buscar():

    query = clean_text(
        request.args.get("q", "")
    )

    try:
        min_discount = float(
            request.args.get("desconto", 0)
        )
    except Exception:
        min_discount = 0

    if not query:
        return jsonify({
            "ok": False,
            "erro": "Informe q."
        }), 400

    return jsonify(
        search_offers(
            query,
            min_discount
        )
    )


# ============================================================
# TESTE CATEGORIA
# ============================================================


@app.route("/mercadolivre/teste-categoria")
def teste_categoria():

    query = clean_text(
        request.args.get("q", "fone")
    )

    result = discover_categories(query)

    return jsonify({
        "consulta": query,
        "categorias": result.get("categories", []),
        "total": len(
            result.get("categories", [])
        ),
        "status": result.get("status"),
        "erro": result.get("error")
    })


# ============================================================
# TESTE HIGHLIGHTS
# ============================================================


@app.route("/mercadolivre/teste-highlights")
def teste_highlights():

    category_id = clean_text(
        request.args.get(
            "category_id",
            ""
        )
    )

    if not category_id:
        return jsonify({
            "ok": False,
            "erro": "Informe category_id."
        }), 400

    result = get_highlights(
        category_id
    )

    return jsonify({
        "category_id": category_id,
        "status": result.get("status"),
        "erro": result.get("error"),
        "total": len(
            result.get("products", [])
        ),
        "produtos": result.get("products", [])
    })


# ============================================================
# TESTE PRODUTO
# ============================================================


@app.route("/mercadolivre/teste-produto")
def teste_produto():

    product_id = clean_text(
        request.args.get(
            "product_id",
            ""
        )
    )

    if not product_id:
        return jsonify({
            "ok": False,
            "erro": "Informe product_id."
        }), 400

    result = get_product(
        product_id
    )

    if not result["ok"]:
        return jsonify({
            "ok": False,
            "product_id": product_id,
            "status": result["status"],
            "erro": result["error"]
        }), result["status"] or 500

    data = extract_product_data(
        result["data"]
    )

    return jsonify({
        "ok": True,
        "product_id": product_id,
        "produto": data,
        "raw": result["data"]
    })


# ============================================================
# TESTE BUSCA
# ============================================================


@app.route("/mercadolivre/teste-busca")
def teste_busca():

    query = clean_text(
        request.args.get(
            "q",
            "fone"
        )
    )

    try:
        desconto = float(
            request.args.get(
                "desconto",
                0
            )
        )
    except Exception:
        desconto = 0

    result = search_offers(
        query,
        desconto
    )

    return jsonify({
        "ok": result["ok"],
        "consulta": query,
        "ofertas": result["offers"],
        "produtos": result["products"],
        "categorias": result["categories"],
        "categorias_debug": result["category_debug"],
        "stats": result["stats"],
        "erro": result.get("error")
    })


# ============================================================
# TESTE ITEM
# ============================================================


@app.route("/mercadolivre/item")
def teste_item():

    item_id = clean_text(
        request.args.get(
            "item_id",
            ""
        )
    )

    if not item_id:
        return jsonify({
            "ok": False,
            "erro": "Informe item_id."
        }), 400

    result = api_get(
        f"/items/{item_id}"
    )

    return jsonify({
        "item_id": item_id,
        "status": result["status"],
        "ok": result["ok"],
        "erro": result["error"],
        "item": result["data"]
    })


# ============================================================
# TESTE PREÇO
# ============================================================


@app.route("/mercadolivre/teste-preco")
def teste_preco():

    item_id = clean_text(
        request.args.get(
            "item_id",
            ""
        )
    )

    if not item_id:
        return jsonify({
            "ok": False,
            "erro": "Informe item_id."
        }), 400

    sale = get_sale_price(
        item_id
    )

    prices = get_item_prices(
        item_id
    )

    return jsonify({
        "item_id": item_id,
        "sale_price": {
            "status": sale["status"],
            "ok": sale["ok"],
            "erro": sale["error"],
            "dados": sale["data"]
        },
        "prices": {
            "status": prices["status"],
            "ok": prices["ok"],
            "erro": prices["error"],
            "dados": prices["prices"]
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
            "client_id_configurado": bool(CLIENT_ID),
            "client_secret_configurado": bool(CLIENT_SECRET),
            "redirect_uri": REDIRECT_URI
        },
        "oauth": {
            "token_salvo": bool(token),
            "user_id": token["user_id"] if token else None,
            "nickname": token["nickname"] if token else None,
            "expires_at": token["expires_at"] if token else None
        },
        "mercadolivre": {
            "me_status": me["status"],
            "me_ok": me["ok"],
            "me": me["data"] if me["ok"] else None,
            "erro": me["error"]
        }
    })


# ============================================================
# LISTAR SALVOS
# ============================================================


@app.route("/api/salvos")
def salvos():

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
# HTML PRINCIPAL
# ============================================================


HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, Helvetica, sans-serif;
    background: #f3f4f6;
    color: #111827;
}

.container {
    width: min(1100px, 94%);
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

h2 {
    margin-top: 0;
}

.subtitle {
    color: #6b7280;
}

.status {
    padding: 10px 14px;
    border-radius: 10px;
    background: #ecfdf5;
    color: #047857;
    display: inline-block;
    margin-bottom: 15px;
}

.status.off {
    background: #fef2f2;
    color: #b91c1c;
}

form {
    display: grid;
    grid-template-columns: 1fr 160px 130px;
    gap: 10px;
}

input,
button {
    border-radius: 10px;
    padding: 13px;
    font-size: 16px;
}

input {
    border: 1px solid #d1d5db;
}

button {
    border: 0;
    cursor: pointer;
    background: #3483fa;
    color: white;
    font-weight: bold;
}

button:hover {
    opacity: .9;
}

a {
    color: #2563eb;
    text-decoration: none;
}

.links {
    margin-top: 15px;
    display: flex;
    gap: 15px;
    flex-wrap: wrap;
}

.info {
    background: #eff6ff;
    border-left: 4px solid #2563eb;
    padding: 14px;
    border-radius: 8px;
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

<form action="/buscar" method="get">

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
    step="1"
    value="10"
    placeholder="% desconto"
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

<a href="/mercadolivre/teste-busca?q=fone">
Teste API
</a>

</div>

</div>

<div class="card">

<h2>Como funciona</h2>

<div class="info">

O sistema encontra produtos específicos do catálogo,
analisa a publicação vencedora quando a API disponibiliza
o Buy Box e calcula o desconto somente quando existe
preço atual e preço original verificáveis.

</div>

<br>

<div class="info">

Depois de encontrar um produto, use o
<strong>ID do produto</strong> ou a página específica
do produto na Central de Afiliados para gerar seu link
de afiliado.

</div>

</div>

</div>

</body>
</html>
"""


# ============================================================
# HTML RESULTADOS
# ============================================================


RESULTS_HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>Resultados - Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f3f4f6;
    font-family: Arial, Helvetica, sans-serif;
    color: #111827;
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

.back {
    display: inline-block;
    margin-bottom: 15px;
}

.stats {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(150px, 1fr));
    gap: 10px;
}

.stat {
    background: #f9fafb;
    border-radius: 12px;
    padding: 15px;
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
    border: 1px solid #e5e7eb;
    border-radius: 15px;
    padding: 16px;
    background: white;
}

.product h3 {
    font-size: 16px;
    line-height: 1.4;
    margin-top: 0;
}

.price {
    font-size: 24px;
    font-weight: bold;
}

.old {
    color: #6b7280;
    text-decoration: line-through;
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

.no-discount {
    color: #6b7280;
    font-size: 14px;
}

.actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
}

.actions a {
    background: #3483fa;
    color: white;
    padding: 9px 11px;
    border-radius: 8px;
    font-size: 14px;
}

.actions a.secondary {
    background: #111827;
}

.warning {
    background: #fff7ed;
    border-left: 4px solid #f97316;
    padding: 14px;
    border-radius: 8px;
}

.success {
    background: #ecfdf5;
    border-left: 4px solid #10b981;
    padding: 14px;
    border-radius: 8px;
}

.muted {
    color: #6b7280;
    font-size: 14px;
}

details {
    margin-top: 20px;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: #111827;
    color: #f9fafb;
    padding: 15px;
    border-radius: 10px;
    overflow-x: auto;
}

@media(max-width:600px) {

    .products {
        grid-template-columns: 1fr;
    }

}

</style>

</head>

<body>

<div class="container">

<a class="back" href="/">
← Nova busca
</a>

<div class="card">

<h1>Resultados: {{ query }}</h1>

<p class="muted">
Filtro mínimo: {{ min_discount }}%
</p>

<div class="stats">

<div class="stat">
Categorias
<strong>
{{ result.stats.categorias_encontradas }}
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
Buy Box
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

<h2>🔥 Ofertas encontradas</h2>

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

{% if item.item_id %}

<p class="muted">
Anúncio: {{ item.item_id }}
</p>

{% endif %}

<div class="actions">

<a
    href="{{ item.permalink }}"
    target="_blank"
>
Ver produto
</a>

<a
    class="secondary"
    href="{{ item.permalink }}"
    target="_blank"
>
Gerar afiliado
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
Nenhum desconto verificável encontrado.
</strong>

<br><br>

Isso não significa que não existam ofertas.

A API encontrou produtos, mas para muitos deles o
Mercado Livre não disponibilizou uma publicação vencedora
ou preço original para este token.

</div>

</div>

{% endif %}


<div class="card">

<h2>📦 Produtos encontrados</h2>

<div class="products">

{% for item in result.products %}

<div class="product">

<h3>
{{ item.title or "Produto " ~ item.product_id }}
</h3>

{% if item.price is not none %}

<div class="price">
R$ {{ "%.2f"|format(item.price) }}
</div>

{% else %}

<div class="muted">
Preço não disponibilizado pela API
</div>

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

{% else %}

<div class="no-discount">

{% if item.has_buy_box %}
Buy Box encontrada, mas sem desconto verificável.
{% else %}
Produto de catálogo sem Buy Box disponível.
{% endif %}

</div>

{% endif %}

<p class="muted">
ID: {{ item.product_id }}
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


<div class="card">

<h2>📊 Diagnóstico</h2>

<details>

<summary>
Ver categorias analisadas
</summary>

<pre>{{ result.category_debug | tojson(indent=2) }}</pre>

</details>

</div>

<div class="card">

<div class="success">

<strong>
💡 Próximo passo para afiliado
</strong>

<br><br>

Abra a página específica do produto pelo botão
<strong>“Abrir produto”</strong>.

Depois use a Central/Barra de Afiliados do Mercado Livre
para gerar o seu link de afiliado.

</div>

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