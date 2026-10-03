import os
import re
import json
import time
import base64
import hashlib
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote_plus

import requests
from flask import (
    Flask,
    request,
    redirect,
    jsonify,
    render_template_string,
    session,
    send_file,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "troque-esta-chave-em-producao"
)

PORT = int(os.getenv("PORT", "8080"))

DB_FILE = os.getenv("DB_FILE", "ofertas.db")

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

ML_SITE = "MLB"

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()


# ============================================================
# CONFIGURAÇÕES
# ============================================================

REQUEST_TIMEOUT = 25

CATEGORY_CACHE_SECONDS = 60 * 60 * 12

MAX_CATEGORIES = 8
MAX_ITEMS_PER_CATEGORY = 20
MAX_OFFERS = 100

DEFAULT_DISCOUNT = 10


# ============================================================
# SESSÃO HTTP
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
    conn = sqlite3.connect(DB_FILE)
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

def normalize_text(text):
    if text is None:
        return ""

    text = str(text)

    text = unicodedata.normalize(
        "NFKD",
        text
    ).encode(
        "ascii",
        "ignore"
    ).decode(
        "ascii"
    )

    text = text.lower()

    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_query(text):
    return normalize_text(text)


def safe_float(value):
    try:
        if value is None:
            return None

        return float(value)

    except Exception:
        return None


def now_ts():
    return int(time.time())


def json_safe(value):
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


# ============================================================
# TOKEN OAUTH
# ============================================================

def save_tokens(
    access_token,
    refresh_token=None,
    expires_in=None,
    user_id=None,
    nickname=None
):
    conn = get_db()

    expires_at = now_ts() + int(expires_in or 0)

    old = conn.execute(
        "SELECT refresh_token, user_id, nickname FROM oauth_tokens WHERE id = 1"
    ).fetchone()

    if refresh_token is None and old:
        refresh_token = old["refresh_token"]

    if user_id is None and old:
        user_id = old["user_id"]

    if nickname is None and old:
        nickname = old["nickname"]

    conn.execute("""
        INSERT INTO oauth_tokens (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname,
            updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
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
        now_ts(),
    ))

    conn.commit()
    conn.close()


def get_tokens():
    conn = get_db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id = 1"
    ).fetchone()

    conn.close()

    if not row:
        return None

    return dict(row)


def refresh_access_token():
    tokens = get_tokens()

    if not tokens:
        return None

    refresh_token = tokens.get("refresh_token")

    if not refresh_token:
        return None

    if not ML_CLIENT_ID or not ML_CLIENT_SECRET:
        return None

    data = {
        "grant_type": "refresh_token",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "refresh_token": refresh_token,
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

    access_token = payload.get("access_token")

    if not access_token:
        return None

    save_tokens(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_in=payload.get("expires_in", 21600),
        user_id=tokens.get("user_id"),
        nickname=tokens.get("nickname"),
    )

    return access_token


def get_access_token():
    tokens = get_tokens()

    if not tokens:
        return None

    access_token = tokens.get("access_token")
    expires_at = int(tokens.get("expires_at") or 0)

    # Renova antes de expirar
    if access_token and expires_at > now_ts() + 120:
        return access_token

    return refresh_access_token()


# ============================================================
# API MERCADO LIVRE
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
            "error": "Mercado Livre não autorizado.",
            "url": ML_API + path,
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    url = ML_API + path

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

    if response.status_code == 401 and retry_refresh:
        new_token = refresh_access_token()

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

    if response.status_code >= 200 and response.status_code < 300:
        return {
            "ok": True,
            "status": response.status_code,
            "data": data,
            "error": None,
            "url": response.url,
        }

    error_message = None

    if isinstance(data, dict):
        error_message = (
            data.get("message")
            or data.get("error")
            or data.get("cause")
        )

    if isinstance(error_message, list):
        error_message = json.dumps(
            error_message,
            ensure_ascii=False
        )

    if not error_message:
        error_message = response.text[:500]

    return {
        "ok": False,
        "status": response.status_code,
        "data": data,
        "error": error_message,
        "url": response.url,
    }


# ============================================================
# OAUTH PKCE
# ============================================================

def generate_code_verifier():
    return secrets.token_urlsafe(64)[:128]


def generate_code_challenge(verifier):
    digest = hashlib.sha256(
        verifier.encode("ascii")
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).decode("ascii").rstrip("=")


@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:
        return """
        <h2>ML_CLIENT_ID não configurado.</h2>
        """

    state = secrets.token_urlsafe(32)

    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)

    session["ml_oauth_state"] = state
    session["ml_code_verifier"] = verifier

    params = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    url = ML_AUTH + "?" + urlencode(params)

    return redirect(url)


@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    error = request.args.get("error")

    if error:
        return jsonify({
            "ok": False,
            "erro": error,
            "descricao": request.args.get("error_description"),
        }), 400

    state = request.args.get("state")
    code = request.args.get("code")

    expected_state = session.get("ml_oauth_state")
    verifier = session.get("ml_code_verifier")

    if not state or state != expected_state:
        return jsonify({
            "ok": False,
            "erro": "state inválido",
        }), 400

    if not code:
        return jsonify({
            "ok": False,
            "erro": "code não recebido",
        }), 400

    data = {
        "grant_type": "authorization_code",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "code": code,
        "redirect_uri": ML_REDIRECT_URI,
        "code_verifier": verifier,
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
            "erro": str(e),
        }), 500

    try:
        payload = response.json()
    except Exception:
        payload = {}

    if response.status_code != 200:
        return jsonify({
            "ok": False,
            "status": response.status_code,
            "resposta": payload,
        }), response.status_code

    access_token = payload.get("access_token")

    if not access_token:
        return jsonify({
            "ok": False,
            "erro": "access_token não recebido",
            "resposta": payload,
        }), 500

    save_tokens(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_in=payload.get("expires_in", 21600),
    )

    # Busca usuário autorizado
    me = api_get("/users/me")

    user_id = None
    nickname = None

    if me["ok"] and isinstance(me["data"], dict):
        user_id = me["data"].get("id")
        nickname = me["data"].get("nickname")

        save_tokens(
            access_token=access_token,
            refresh_token=payload.get("refresh_token"),
            expires_in=payload.get("expires_in", 21600),
            user_id=user_id,
            nickname=nickname,
        )

    session.pop("ml_oauth_state", None)
    session.pop("ml_code_verifier", None)

    return redirect("/")


# ============================================================
# CATEGORIAS
# ============================================================

_category_cache = {
    "timestamp": 0,
    "categories": None,
    "status": None,
    "error": None,
}


def load_all_categories(force=False):
    global _category_cache

    if (
        not force
        and _category_cache["categories"] is not None
        and now_ts() - _category_cache["timestamp"] < CATEGORY_CACHE_SECONDS
    ):
        return {
            "ok": True,
            "status": _category_cache["status"],
            "categories": _category_cache["categories"],
            "error": _category_cache["error"],
            "cached": True,
        }

    result = api_get(
        f"/sites/{ML_SITE}/categories/all",
        timeout=60,
    )

    if not result["ok"]:
        _category_cache = {
            "timestamp": now_ts(),
            "categories": None,
            "status": result["status"],
            "error": result["error"],
        }

        return {
            "ok": False,
            "status": result["status"],
            "categories": None,
            "error": result["error"],
            "cached": False,
        }

    data = result["data"]

    categories = []

    if isinstance(data, list):
        categories = data

    elif isinstance(data, dict):
        if isinstance(data.get("categories"), list):
            categories = data["categories"]

        elif isinstance(data.get("results"), list):
            categories = data["results"]

    if not categories:
        return {
            "ok": False,
            "status": result["status"],
            "categories": [],
            "error": "API retornou a árvore, mas o formato não foi reconhecido.",
            "cached": False,
        }

    _category_cache = {
        "timestamp": now_ts(),
        "categories": categories,
        "status": result["status"],
        "error": None,
    }

    return {
        "ok": True,
        "status": result["status"],
        "categories": categories,
        "error": None,
        "cached": False,
    }


def flatten_categories(data):
    """
    Aceita tanto árvore aninhada quanto lista plana.
    """

    output = []
    seen = set()

    def walk(node, parents=None):

        if parents is None:
            parents = []

        if isinstance(node, list):
            for child in node:
                walk(child, parents)
            return

        if not isinstance(node, dict):
            return

        category_id = node.get("id")
        name = node.get("name")

        if category_id and name:

            path = parents + [{
                "id": category_id,
                "name": name,
            }]

            children = node.get("children_categories")

            if not isinstance(children, list):
                children = []

            record = {
                "id": category_id,
                "name": name,
                "path": path,
                "children": len(children),
                "leaf": len(children) == 0,
                "total_items": node.get(
                    "total_items_in_this_category"
                ),
            }

            if category_id not in seen:
                seen.add(category_id)
                output.append(record)

            for child in children:
                walk(child, path)

        else:
            children = node.get("children_categories")

            if isinstance(children, list):
                for child in children:
                    walk(child, parents)

    walk(data)

    return output


def category_score(category, query):
    query_norm = normalize_text(query)

    if not query_norm:
        return 0

    query_tokens = set(query_norm.split())

    name_norm = normalize_text(
        category.get("name", "")
    )

    path_names = " ".join(
        normalize_text(x.get("name", ""))
        for x in category.get("path", [])
    )

    full_text = f"{name_norm} {path_names}"

    score = 0

    # Nome contém a expressão completa
    if query_norm in name_norm:
        score += 100

    # Nome contém expressão completa no caminho
    if query_norm in path_names:
        score += 50

    name_tokens = set(name_norm.split())

    overlap = query_tokens.intersection(name_tokens)

    score += len(overlap) * 25

    # Também procura os termos no caminho
    path_tokens = set(full_text.split())

    score += len(query_tokens.intersection(path_tokens)) * 8

    # Categoria folha é importante para highlights
    if category.get("leaf"):
        score += 30

    # Categorias com produtos têm prioridade
    total_items = safe_float(
        category.get("total_items")
    )

    if total_items:
        if total_items > 100:
            score += 5

    # Penaliza categorias genéricas demais
    generic = {
        "outros",
        "diversos",
        "servicos",
        "servicos diversos",
    }

    if name_norm in generic:
        score -= 50

    return score


def discover_categories_from_tree(query, limit=8):
    result = load_all_categories()

    if not result["ok"]:
        return {
            "ok": False,
            "status": result["status"],
            "error": result["error"],
            "categories": [],
        }

    flat = flatten_categories(
        result["categories"]
    )

    scored = []

    for category in flat:

        score = category_score(
            category,
            query
        )

        if score <= 0:
            continue

        scored.append({
            "id": category["id"],
            "name": category["name"],
            "score": score,
            "leaf": category["leaf"],
            "children": category["children"],
            "total_items": category["total_items"],
            "path": category["path"],
            "source": "category_tree",
        })

    scored.sort(
        key=lambda x: (
            -x["score"],
            not x["leaf"],
            -(safe_float(x["total_items"]) or 0),
        )
    )

    return {
        "ok": True,
        "status": 200,
        "error": None,
        "categories": scored[:limit],
    }


# ============================================================
# PREDITOR + FALLBACK DE CATEGORIAS
# ============================================================

def discover_categories(query, max_results=MAX_CATEGORIES):
    query = normalize_query(query)

    if not query:
        return {
            "categories": [],
            "attempts": [],
        }

    attempts = []
    categories = []
    seen = set()

    # --------------------------------------------------------
    # 1. Preditor oficial
    # --------------------------------------------------------

    predictor = api_get(
        f"/sites/{ML_SITE}/domain_discovery/search",
        params={
            "q": query,
            "limit": 8,
        },
    )

    predictor_total = 0

    if predictor["ok"] and isinstance(
        predictor["data"],
        list
    ):

        predictor_total = len(
            predictor["data"]
        )

        for item in predictor["data"]:

            category_id = item.get(
                "category_id"
            )

            category_name = item.get(
                "category_name"
            )

            if not category_id:
                continue

            if category_id in seen:
                continue

            seen.add(category_id)

            categories.append({
                "id": category_id,
                "name": category_name,
                "score": 1000 - len(categories),
                "leaf": None,
                "children": None,
                "total_items": None,
                "path": [],
                "source": "domain_discovery",
                "domain_id": item.get("domain_id"),
                "domain_name": item.get("domain_name"),
            })

    attempts.append({
        "metodo": "domain_discovery",
        "q": query,
        "status": predictor["status"],
        "total": predictor_total,
        "erro": predictor["error"],
    })

    # --------------------------------------------------------
    # 2. Fallback árvore completa
    # --------------------------------------------------------

    if len(categories) < max_results:

        tree = discover_categories_from_tree(
            query,
            limit=max_results,
        )

        attempts.append({
            "metodo": "category_tree",
            "q": query,
            "status": tree["status"],
            "total": len(tree["categories"]),
            "erro": tree["error"],
        })

        if tree["ok"]:

            for item in tree["categories"]:

                category_id = item["id"]

                if category_id in seen:
                    continue

                seen.add(category_id)

                categories.append(item)

                if len(categories) >= max_results:
                    break

    return {
        "categories": categories[:max_results],
        "attempts": attempts,
    }


# ============================================================
# HIGHLIGHTS
# ============================================================

def get_highlights(category_id):
    return api_get(
        f"/highlights/{ML_SITE}/category/{quote_plus(category_id)}"
    )


# ============================================================
# PRODUTOS
# ============================================================

def get_product(product_id):
    return api_get(
        f"/products/{quote_plus(product_id)}"
    )


def get_item(item_id):
    return api_get(
        f"/items/{quote_plus(item_id)}"
    )


# ============================================================
# USER PRODUCT
# ============================================================

def get_user_product(user_product_id):
    return api_get(
        f"/user-products/{quote_plus(user_product_id)}"
    )


def get_items_from_user_product(
    user_product_id,
    seller_id=None
):
    """
    USER_PRODUCT (MLBU...) pode estar associado a um ou mais
    ITEMs. A API oficial permite procurar os itens do vendedor
    filtrando pelo user_product_id.
    """

    if not seller_id:
        return {
            "ok": False,
            "status": 0,
            "data": None,
            "error": "seller_id não informado.",
        }

    return api_get(
        f"/users/{seller_id}/items/search",
        params={
            "user_product_id": user_product_id,
            "limit": 50,
        },
    )


# ============================================================
# PREÇOS
# ============================================================

def get_sale_price(item_id):
    return api_get(
        f"/items/{quote_plus(item_id)}/sale_price",
        params={
            "context": "channel_marketplace",
        },
    )


def get_prices(item_id):
    return api_get(
        f"/items/{quote_plus(item_id)}/prices"
    )


# ============================================================
# DESCONTO
# ============================================================

def calculate_discount(
    price,
    original_price
):
    price = safe_float(price)
    original_price = safe_float(
        original_price
    )

    if not price or not original_price:
        return None

    if original_price <= price:
        return 0.0

    discount = (
        (original_price - price)
        / original_price
    ) * 100

    return round(
        discount,
        2
    )


def extract_price_data(
    item_id,
    item_data
):
    """
    Tenta obter:
    - preço atual
    - preço original
    - desconto
    """

    price = None
    original_price = None
    source = None

    # --------------------------------------------------------
    # 1. Sale Price
    # --------------------------------------------------------

    sale = get_sale_price(item_id)

    if sale["ok"] and isinstance(
        sale["data"],
        dict
    ):

        data = sale["data"]

        price = safe_float(
            data.get("amount")
        )

        original_price = safe_float(
            data.get("regular_amount")
        )

        if price is not None:
            source = "sale_price"

    # --------------------------------------------------------
    # 2. Prices
    # --------------------------------------------------------

    if price is None:

        prices = get_prices(item_id)

        if prices["ok"]:

            data = prices["data"]

            entries = []

            if isinstance(data, dict):

                if isinstance(
                    data.get("prices"),
                    list
                ):
                    entries = data["prices"]

                elif isinstance(
                    data.get("results"),
                    list
                ):
                    entries = data["results"]

            elif isinstance(data, list):
                entries = data

            for entry in entries:

                amount = safe_float(
                    entry.get("amount")
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
                    original_price = regular

                source = "prices"

                if original_price:
                    break

    # --------------------------------------------------------
    # 3. Fallback /items
    # --------------------------------------------------------

    if price is None:
        price = safe_float(
            item_data.get("price")
        )

    if original_price is None:

        original_price = safe_float(
            item_data.get(
                "original_price"
            )
        )

    if original_price is None:

        original_price = safe_float(
            item_data.get(
                "base_price"
            )
        )

    discount = calculate_discount(
        price,
        original_price
    )

    return {
        "price": price,
        "original_price": original_price,
        "discount": discount,
        "source": source,
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
    if not item_id:
        return {
            "ok": False,
            "reason": "item_id vazio",
        }

    if not str(item_id).startswith("MLB"):
        return {
            "ok": False,
            "reason": "item_id não parece ser MLB",
        }

    item_result = get_item(
        item_id
    )

    if not item_result["ok"]:
        return {
            "ok": False,
            "item_id": item_id,
            "status": item_result["status"],
            "reason": item_result["error"],
            "source": source,
        }

    item = item_result["data"]

    if not isinstance(item, dict):
        return {
            "ok": False,
            "item_id": item_id,
            "reason": "Resposta /items inválida.",
        }

    prices = extract_price_data(
        item_id,
        item
    )

    price = prices["price"]
    original_price = prices["original_price"]
    discount = prices["discount"]

    if discount is None:

        return {
            "ok": True,
            "oferta": False,
            "item_id": item_id,
            "titulo": item.get("title"),
            "preco": price,
            "preco_original": original_price,
            "desconto": None,
            "fonte": source,
            "position": position,
            "motivo": "Preço original/desconto não disponível.",
        }

    return {
        "ok": True,
        "oferta": discount > 0,
        "item_id": item_id,
        "titulo": item.get("title"),
        "preco": price,
        "preco_original": original_price,
        "desconto": discount,
        "url": item.get(
            "permalink"
        ) or (
            f"https://www.mercadolivre.com.br/"
            f"{item_id}"
        ),
        "imagem": item.get(
            "thumbnail"
        ),
        "seller_id": item.get(
            "seller_id"
        ),
        "categoria_id": item.get(
            "category_id"
        ) or category_id,
        "categoria_nome": category_name,
        "fonte": source,
        "position": position,
    }


# ============================================================
# SALVAR OFERTA
# ============================================================

def save_offer(offer):
    if not offer.get("item_id"):
        return

    conn = get_db()

    conn.execute("""
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            titulo = excluded.titulo,
            preco = excluded.preco,
            preco_original = excluded.preco_original,
            desconto = excluded.desconto,
            url = excluded.url,
            imagem = excluded.imagem,
            seller_id = excluded.seller_id,
            categoria_id = excluded.categoria_id,
            categoria_nome = excluded.categoria_nome,
            fonte = excluded.fonte,
            criado_em = excluded.criado_em
    """, (
        offer.get("item_id"),
        offer.get("titulo"),
        offer.get("preco"),
        offer.get("preco_original"),
        offer.get("desconto"),
        offer.get("url"),
        offer.get("imagem"),
        offer.get("seller_id"),
        offer.get("categoria_id"),
        offer.get("categoria_nome"),
        offer.get("fonte"),
        now_ts(),
    ))

    conn.commit()
    conn.close()


# ============================================================
# DESCOBRIR OFERTAS
# ============================================================

def discover_offers(
    query,
    min_discount=10,
    max_categories=MAX_CATEGORIES
):
    query = normalize_query(query)

    if not query:
        return {
            "ok": False,
            "erro": "Digite um produto.",
        }

    category_result = discover_categories(
        query,
        max_results=max_categories,
    )

    categories = category_result["categories"]

    stats = {
        "categorias_encontradas": len(categories),
        "categorias_analisadas": 0,
        "produtos_ranking": 0,
        "items_analisados": 0,
        "user_products": 0,
        "buy_box_encontradas": 0,
        "ofertas_encontradas": 0,
        "sem_desconto": 0,
        "sem_preco_original": 0,
        "sem_item": 0,
        "erros": 0,
    }

    offers = []

    seen_items = set()
    seen_user_products = set()

    category_debug = []

    for category in categories:

        if len(offers) >= MAX_OFFERS:
            break

        category_id = category.get(
            "id"
        )

        category_name = category.get(
            "name"
        )

        if not category_id:
            continue

        stats["categorias_analisadas"] += 1

        highlight = get_highlights(
            category_id
        )

        category_info = {
            "id": category_id,
            "nome": category_name,
            "fonte_categoria": category.get(
                "source"
            ),
            "status": highlight["status"],
            "erro": highlight["error"],
            "items": 0,
        }

        if not highlight["ok"]:
            category_debug.append(
                category_info
            )

            stats["erros"] += 1

            continue

        data = highlight["data"]

        content = []

        if isinstance(data, dict):
            content = data.get(
                "content"
            ) or []

        if not isinstance(content, list):
            content = []

        category_info["items"] = len(
            content
        )

        stats["produtos_ranking"] += len(
            content
        )

        category_debug.append(
            category_info
        )

        # ----------------------------------------------------
        # Ranking
        # ----------------------------------------------------

        for ranked in content:

            if len(offers) >= MAX_OFFERS:
                break

            ranked_id = ranked.get(
                "id"
            )

            ranked_type = (
                ranked.get("type")
                or ""
            ).upper()

            position = ranked.get(
                "position"
            )

            if not ranked_id:
                continue

            # =================================================
            # ITEM
            # =================================================

            if ranked_type == "ITEM":

                if ranked_id in seen_items:
                    continue

                seen_items.add(
                    ranked_id
                )

                stats["items_analisados"] += 1

                result = analyze_item(
                    ranked_id,
                    category_id=category_id,
                    category_name=category_name,
                    source="highlights_item",
                    position=position,
                )

                if not result.get("ok"):
                    stats["erros"] += 1
                    continue

                if result.get(
                    "desconto"
                ) is None:
                    stats["sem_preco_original"] += 1
                    continue

                if result.get(
                    "desconto",
                    0
                ) < min_discount:
                    stats["sem_desconto"] += 1
                    continue

                offers.append(
                    result
                )

                stats["ofertas_encontradas"] += 1

                save_offer(
                    result
                )

            # =================================================
            # PRODUCT
            # =================================================

            elif ranked_type == "PRODUCT":

                product = get_product(
                    ranked_id
                )

                if not product["ok"]:
                    stats["erros"] += 1
                    continue

                pdata = product["data"]

                if not isinstance(
                    pdata,
                    dict
                ):
                    continue

                buy_box = pdata.get(
                    "buy_box_winner"
                )

                item_id = None

                if isinstance(
                    buy_box,
                    dict
                ):
                    item_id = buy_box.get(
                        "item_id"
                    )

                # Algumas respostas podem trazer
                # um produto com children_ids.
                if not item_id:

                    children = (
                        pdata.get(
                            "children_ids"
                        )
                        or []
                    )

                    if isinstance(
                        children,
                        list
                    ):

                        for child_id in children:

                            if not str(
                                child_id
                            ).startswith("MLB"):
                                continue

                            if child_id in seen_items:
                                continue

                            seen_items.add(
                                child_id
                            )

                            stats[
                                "items_analisados"
                            ] += 1

                            child_result = analyze_item(
                                child_id,
                                category_id=category_id,
                                category_name=category_name,
                                source="product_child",
                                position=position,
                            )

                            if not child_result.get(
                                "ok"
                            ):
                                stats["erros"] += 1
                                continue

                            discount = child_result.get(
                                "desconto"
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

                            offers.append(
                                child_result
                            )

                            stats[
                                "ofertas_encontradas"
                            ] += 1

                            save_offer(
                                child_result
                            )

                            break

                if not item_id:
                    stats["sem_item"] += 1
                    continue

                stats[
                    "buy_box_encontradas"
                ] += 1

                if item_id in seen_items:
                    continue

                seen_items.add(
                    item_id
                )

                stats[
                    "items_analisados"
                ] += 1

                result = analyze_item(
                    item_id,
                    category_id=category_id,
                    category_name=category_name,
                    source="highlights_product_buy_box",
                    position=position,
                )

                if not result.get("ok"):
                    stats["erros"] += 1
                    continue

                discount = result.get(
                    "desconto"
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

                offers.append(
                    result
                )

                stats[
                    "ofertas_encontradas"
                ] += 1

                save_offer(
                    result
                )

            # =================================================
            # USER PRODUCT
            # =================================================

            elif ranked_type == "USER_PRODUCT":

                if ranked_id in seen_user_products:
                    continue

                seen_user_products.add(
                    ranked_id
                )

                stats[
                    "user_products"
                ] += 1

                up_result = get_user_product(
                    ranked_id
                )

                if not up_result["ok"]:
                    stats["erros"] += 1
                    continue

                up = up_result["data"]

                if not isinstance(
                    up,
                    dict
                ):
                    continue

                # A resposta pode disponibilizar
                # user_id/seller_id.
                seller_id = (
                    up.get("user_id")
                    or up.get("seller_id")
                )

                if not seller_id:
                    stats["sem_item"] += 1
                    continue

                items_result = (
                    get_items_from_user_product(
                        ranked_id,
                        seller_id,
                    )
                )

                if not items_result["ok"]:
                    stats["erros"] += 1
                    continue

                items_data = (
                    items_result["data"]
                )

                item_ids = []

                if isinstance(
                    items_data,
                    dict
                ):
                    item_ids = (
                        items_data.get(
                            "results"
                        )
                        or []
                    )

                if not isinstance(
                    item_ids,
                    list
                ):
                    item_ids = []

                if not item_ids:
                    stats["sem_item"] += 1
                    continue

                for item_id in item_ids:

                    if len(offers) >= MAX_OFFERS:
                        break

                    if item_id in seen_items:
                        continue

                    seen_items.add(
                        item_id
                    )

                    stats[
                        "items_analisados"
                    ] += 1

                    result = analyze_item(
                        item_id,
                        category_id=category_id,
                        category_name=category_name,
                        source="user_product_item",
                        position=position,
                    )

                    if not result.get("ok"):
                        stats["erros"] += 1
                        continue

                    discount = result.get(
                        "desconto"
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

                    offers.append(
                        result
                    )

                    stats[
                        "ofertas_encontradas"
                    ] += 1

                    save_offer(
                        result
                    )

    # Ordena maior desconto primeiro
    offers.sort(
        key=lambda x: (
            -(safe_float(
                x.get("desconto")
            ) or 0),
            safe_float(
                x.get("preco")
            ) or 999999999,
        )
    )

    return {
        "ok": True,
        "consulta": query,
        "categorias": categories,
        "tentativas_categorias": category_result[
            "attempts"
        ],
        "categorias_debug": category_debug,
        "stats": stats,
        "ofertas": offers[:MAX_OFFERS],
    }


# ============================================================
# ROTAS DE DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/teste-categoria")
def teste_categoria():

    query = request.args.get(
        "q",
        ""
    ).strip()

    result = discover_categories(
        query
    )

    return jsonify({
        "consulta": query,
        "tentativas": result[
            "attempts"
        ],
        "categorias": result[
            "categories"
        ],
        "total": len(
            result["categories"]
        ),
    })


@app.route("/mercadolivre/teste-arvore-categorias")
def teste_arvore_categorias():

    query = request.args.get(
        "q",
        ""
    ).strip()

    result = discover_categories_from_tree(
        query,
        limit=20,
    )

    return jsonify({
        "consulta": query,
        "status": result["status"],
        "erro": result["error"],
        "categorias": result["categories"],
        "total": len(
            result["categories"]
        ),
    })


@app.route("/mercadolivre/teste-highlights")
def teste_highlights():

    category_id = request.args.get(
        "category_id",
        ""
    ).strip()

    if not category_id:
        return jsonify({
            "ok": False,
            "erro": "Informe ?category_id=MLB123"
        }), 400

    result = get_highlights(
        category_id
    )

    return jsonify({
        "ok": result["ok"],
        "status": result["status"],
        "erro": result["error"],
        "url": result.get("url"),
        "resposta": result["data"],
    })


@app.route("/mercadolivre/teste-busca")
def teste_busca():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:
        return jsonify({
            "ok": False,
            "erro": "Informe ?q=produto"
        }), 400

    result = discover_offers(
        query,
        min_discount=0,
    )

    return jsonify(
        result
    )


@app.route("/mercadolivre/teste-preco")
def teste_preco():

    item_id = request.args.get(
        "item_id",
        ""
    ).strip()

    product_id = request.args.get(
        "product_id",
        ""
    ).strip()

    response = {
        "item_id": item_id or None,
        "product_id": product_id or None,
    }

    if product_id:

        product = get_product(
            product_id
        )

        response["produto"] = {
            "status": product["status"],
            "erro": product["error"],
            "data": product["data"],
        }

        if product["ok"]:

            pdata = product["data"]

            if isinstance(
                pdata,
                dict
            ):

                response["buy_box"] = (
                    pdata.get(
                        "buy_box_winner"
                    )
                )

                response[
                    "children_ids"
                ] = pdata.get(
                    "children_ids"
                )

        return jsonify(
            response
        )

    if not item_id:

        return jsonify({
            "ok": False,
            "erro": "Informe ?item_id=MLB... ou ?product_id=MLB..."
        }), 400

    item = get_item(
        item_id
    )

    response["item"] = {
        "status": item["status"],
        "erro": item["error"],
        "data": item["data"],
    }

    sale = get_sale_price(
        item_id
    )

    response["sale_price"] = {
        "status": sale["status"],
        "erro": sale["error"],
        "data": sale["data"],
    }

    prices = get_prices(
        item_id
    )

    response["prices"] = {
        "status": prices["status"],
        "erro": prices["error"],
        "data": prices["data"],
    }

    return jsonify(
        response
    )


@app.route("/mercadolivre/item")
def teste_item():

    item_id = request.args.get(
        "item_id",
        ""
    ).strip()

    if not item_id:
        return jsonify({
            "ok": False,
            "erro": "Informe ?item_id=MLB..."
        }), 400

    result = analyze_item(
        item_id,
        source="teste_manual",
    )

    return jsonify(
        result
    )


@app.route("/mercadolivre/diagnostico")
def diagnostico():

    token = get_access_token()

    response = {
        "app": {
            "client_id_configurado": bool(
                ML_CLIENT_ID
            ),
            "client_secret_configurado": bool(
                ML_CLIENT_SECRET
            ),
            "redirect_uri": ML_REDIRECT_URI,
            "site": ML_SITE,
        },
        "oauth": {
            "token_disponivel": bool(
                token
            ),
        },
    }

    if token:

        me = api_get(
            "/users/me"
        )

        response["usuario"] = {
            "ok": me["ok"],
            "status": me["status"],
            "erro": me["error"],
            "data": me["data"],
        }

    return jsonify(
        response
    )


# ============================================================
# BUSCA PRINCIPAL
# ============================================================

@app.route("/buscar", methods=["GET", "POST"])
def buscar():

    query = ""

    if request.method == "POST":
        query = request.form.get(
            "q",
            ""
        ).strip()

        min_discount = request.form.get(
            "min_discount",
            "10"
        )

        try:
            min_discount = float(
                min_discount
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
        min_discount=min_discount,
    )

    return render_template_string(
        HTML,
        resultado=result,
        query=query,
        min_discount=min_discount,
    )


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #0f1115;
    color: #f5f5f5;
    font-family: Arial, Helvetica, sans-serif;
}

.container {
    width: min(1100px, 94%);
    margin: 0 auto;
    padding: 24px 0 60px;
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
    grid-template-columns: 1fr 160px 130px;
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
    outline: none;
}

button {
    background: #3483fa;
    color: white;
    font-weight: bold;
    cursor: pointer;
}

button:hover {
    opacity: .9;
}

.stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
}

.stat {
    background: #111318;
    padding: 15px;
    border-radius: 12px;
}

.stat strong {
    display: block;
    font-size: 25px;
    margin-top: 5px;
}

.muted {
    color: #9ca3af;
    font-size: 13px;
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
    text-decoration: none;
}

.category {
    background: #111318;
    padding: 12px;
    border-radius: 10px;
    margin-top: 8px;
}

.warning {
    background: #3a2d0a;
    border: 1px solid #69520c;
    color: #ffd76a;
    padding: 14px;
    border-radius: 10px;
    margin-bottom: 15px;
}

.success {
    background: #0b3020;
    border: 1px solid #11633d;
    color: #79e2ad;
    padding: 14px;
    border-radius: 10px;
    margin-bottom: 15px;
}

.error {
    background: #3b1418;
    border: 1px solid #6d2028;
    color: #ff9da6;
    padding: 14px;
    border-radius: 10px;
    margin-bottom: 15px;
}

pre {
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    color: #cbd5e1;
    font-size: 12px;
}

@media (max-width: 750px) {

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

    .offer > div:last-child {
        grid-column: 2;
    }

}

</style>

</head>

<body>

<div class="container">

<h1>🛒 Caçador de Ofertas</h1>

<div class="subtitle">
Mercado Livre • busca automática • descontos
</div>

<div class="card">

<form action="/buscar" method="POST">

<div class="form-grid">

<input
    type="text"
    name="q"
    value="{{ query or '' }}"
    placeholder="Digite: fone, celular, air fryer, TV..."
    required
>

<input
    type="number"
    name="min_discount"
    value="{{ min_discount or 10 }}"
    min="0"
    max="99"
    step="1"
    placeholder="% desconto"
>

<button type="submit">
    🔎 Procurar ofertas
</button>

</div>

</form>

</div>


{% if resultado %}

{% if resultado.get("erro") %}

<div class="error">
    {{ resultado.get("erro") }}
</div>

{% endif %}


{% if resultado.get("stats") %}

<div class="card">

<h2>📊 Resultado</h2>

<div class="stats">

<div class="stat">
Categorias
<strong>
{{ resultado.stats.categorias_encontradas }}
</strong>
</div>

<div class="stat">
Rankings
<strong>
{{ resultado.stats.produtos_ranking }}
</strong>
</div>

<div class="stat">
Itens analisados
<strong>
{{ resultado.stats.items_analisados }}
</strong>
</div>

<div class="stat">
Ofertas
<strong>
{{ resultado.stats.ofertas_encontradas }}
</strong>
</div>

</div>

<br>

<div class="stats">

<div class="stat">
Buy Box
<strong>
{{ resultado.stats.buy_box_encontradas }}
</strong>
</div>

<div class="stat">
User Products
<strong>
{{ resultado.stats.user_products }}
</strong>
</div>

<div class="stat">
Sem desconto
<strong>
{{ resultado.stats.sem_desconto }}
</strong>
</div>

<div class="stat">
Erros
<strong>
{{ resultado.stats.erros }}
</strong>
</div>

</div>

</div>

{% endif %}


{% if resultado.get("ofertas") %}

<div class="card">

<h2>🔥 Ofertas encontradas</h2>

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
R$ {{ "%.2f"|format(oferta.preco_original) }}
</span>

<br>

{% endif %}

<span class="price">
R$ {{ "%.2f"|format(oferta.preco) }}
</span>

<br>

<span class="discount">
{{ "%.0f"|format(oferta.desconto) }}% OFF
</span>

<br>

<span class="muted">
{{ oferta.categoria_nome or "Categoria não informada" }}
</span>

<br>

<a
    class="link"
    href="{{ oferta.url }}"
    target="_blank"
>
    Ver oferta →
</a>

</div>

<div>

<span class="muted">
Ranking: {{ oferta.position or "-" }}
</span>

</div>

</div>

{% endfor %}

</div>

{% elif resultado.get("stats") %}

<div class="warning">

A busca encontrou produtos/rankings, mas nenhum item atingiu
{{ min_discount }}% de desconto com preço original disponível.

</div>

{% endif %}


{% if resultado.get("categorias") %}

<div class="card">

<h2>📂 Categorias utilizadas</h2>

{% for categoria in resultado.categorias %}

<div class="category">

<strong>
{{ categoria.name or "Sem nome" }}
</strong>

<br>

<span class="muted">
ID: {{ categoria.id }}
•
Fonte: {{ categoria.source }}
{% if categoria.leaf %}
•
Categoria folha
{% endif %}
</span>

</div>

{% endfor %}

</div>

{% endif %}


{% if resultado.get("tentativas_categorias") %}

<div class="card">

<h2>🔧 Diagnóstico da categoria</h2>

{% for tentativa in resultado.tentativas_categorias %}

<div class="category">

<strong>
{{ tentativa.metodo }}
</strong>

<br>

<span class="muted">
Consulta: {{ tentativa.q }}
•
HTTP {{ tentativa.status }}
•
Resultados: {{ tentativa.total }}
</span>

{% if tentativa.erro %}

<br>

<span class="muted">
Erro: {{ tentativa.erro }}
</span>

{% endif %}

</div>

{% endfor %}

</div>

{% endif %}


{% if resultado.get("categorias_debug") %}

<div class="card">

<h2>🔍 Diagnóstico dos rankings</h2>

<pre>{{ resultado.categorias_debug | tojson(indent=2) }}</pre>

</div>

{% endif %}

{% endif %}


<div class="card">

<h3>🔗 Diagnóstico rápido</h3>

<p class="muted">
Depois desta atualização, você pode testar diretamente:
</p>

<p>
<a
    class="link"
    href="/mercadolivre/teste-categoria?q=fone"
>
teste categoria — fone
</a>
</p>

<p>
<a
    class="link"
    href="/mercadolivre/teste-arvore-categorias?q=fone"
>
teste árvore — fone
</a>
</p>

<p>
<a
    class="link"
    href="/mercadolivre/diagnostico"
>
diagnóstico OAuth
</a>
</p>

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

    conectado = bool(
        tokens
        and tokens.get("access_token")
    )

    nickname = (
        tokens.get("nickname")
        if tokens
        else None
    )

    if not conectado:

        return """
        <html>
        <head>
            <meta charset="UTF-8">
            <meta
                name="viewport"
                content="width=device-width, initial-scale=1"
            >
            <title>Caçador de Ofertas</title>
            <style>
                body {
                    background:#101216;
                    color:white;
                    font-family:Arial;
                    padding:30px;
                    text-align:center;
                }
                a {
                    display:inline-block;
                    padding:14px 20px;
                    background:#3483fa;
                    color:white;
                    text-decoration:none;
                    border-radius:10px;
                    margin-top:20px;
                }
            </style>
        </head>

        <body>

        <h1>🛒 Caçador de Ofertas</h1>

        <p>
        Conecte sua conta do Mercado Livre para começar.
        </p>

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
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "app": "cacador-de-ofertas",
        "timestamp": now_ts(),
    })


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
    )