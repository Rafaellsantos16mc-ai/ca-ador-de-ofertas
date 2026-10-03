import os
import json
import time
import sqlite3
import secrets
import hashlib
import base64
import requests

from flask import (
    Flask,
    request,
    redirect,
    jsonify,
    render_template_string,
    session
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "caçador-ofertas-secret-key-2026"
)

DB_PATH = os.getenv("DB_PATH", "ofertas.db")

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_API = "https://api.mercadolibre.com"

PRODUCT_SEARCH_LIMIT = 50


# ============================================================
# BANCO DE DADOS
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY,
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
            item_id TEXT,
            product_id TEXT,
            title TEXT,
            price REAL,
            original_price REAL,
            discount REAL,
            seller_id TEXT,
            category_id TEXT,
            permalink TEXT,
            created_at INTEGER
        )
    """)

    conn.commit()

    # Migração caso banco antigo não tenha alguma coluna
    try:
        conn.execute("ALTER TABLE tokens ADD COLUMN user_id TEXT")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE tokens ADD COLUMN nickname TEXT")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE ofertas ADD COLUMN product_id TEXT")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE ofertas ADD COLUMN seller_id TEXT")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE ofertas ADD COLUMN category_id TEXT")
    except Exception:
        pass

    conn.commit()
    conn.close()


init_db()


# ============================================================
# FUNÇÕES DE BANCO
# ============================================================

def salvar_token(
    access_token,
    refresh_token,
    expires_in,
    user_id=None,
    nickname=None
):
    conn = get_db()

    expires_at = int(time.time()) + int(expires_in or 0)

    conn.execute("""
        DELETE FROM tokens
    """)

    conn.execute("""
        INSERT INTO tokens (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname
        )
        VALUES (1, ?, ?, ?, ?, ?)
    """, (
        access_token,
        refresh_token,
        expires_at,
        user_id,
        nickname
    ))

    conn.commit()
    conn.close()


def obter_token_db():
    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM tokens
        WHERE id = 1
    """).fetchone()

    conn.close()

    return row


# ============================================================
# REFRESH TOKEN
# ============================================================

def refresh_access_token():
    row = obter_token_db()

    if not row:
        return None

    refresh_token = row["refresh_token"]

    if not refresh_token:
        return None

    if not ML_CLIENT_ID or not ML_CLIENT_SECRET:
        return None

    try:
        response = requests.post(
            ML_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": refresh_token
            },
            timeout=30
        )

        data = response.json()

        if response.status_code != 200:
            print("[REFRESH TOKEN ERRO]")
            print(data)
            return None

        access_token = data.get("access_token")
        new_refresh_token = data.get(
            "refresh_token",
            refresh_token
        )

        expires_in = data.get("expires_in", 21600)

        if not access_token:
            return None

        salvar_token(
            access_token,
            new_refresh_token,
            expires_in,
            row["user_id"],
            row["nickname"]
        )

        print("[OK] Access token renovado")

        return access_token

    except Exception as e:
        print("[REFRESH TOKEN EXCEPTION]", e)
        return None


# ============================================================
# ACCESS TOKEN
# ============================================================

def get_access_token():
    row = obter_token_db()

    if not row:
        return None

    access_token = row["access_token"]
    expires_at = row["expires_at"] or 0

    # Ainda válido
    if access_token and int(time.time()) < int(expires_at) - 120:
        return access_token

    # Tenta renovar
    return refresh_access_token()


# ============================================================
# PKCE
# ============================================================

def gerar_code_verifier():
    return secrets.token_urlsafe(64)


def gerar_code_challenge(code_verifier):
    digest = hashlib.sha256(
        code_verifier.encode("utf-8")
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")


# ============================================================
# REQUISIÇÃO ML
# ============================================================

def ml_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }


def ml_get(url, token, **kwargs):
    headers = kwargs.pop("headers", {})

    final_headers = ml_headers(token)
    final_headers.update(headers)

    return requests.get(
        url,
        headers=final_headers,
        timeout=30,
        **kwargs
    )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    row = obter_token_db()

    conectado = bool(row and row["access_token"])

    return render_template_string("""
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
    font-family: Arial, sans-serif;
    background: #f5f5f5;
    color: #222;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 20px;
}

.card {
    background: white;
    border-radius: 16px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 0 4px 20px rgba(0,0,0,.08);
}

h1 {
    margin-top: 0;
}

input,
button {
    width: 100%;
    padding: 14px;
    margin-top: 8px;
    margin-bottom: 12px;
    border-radius: 10px;
    border: 1px solid #ddd;
    font-size: 16px;
}

button {
    background: #3483fa;
    color: white;
    border: none;
    font-weight: bold;
    cursor: pointer;
}

button:hover {
    opacity: .9;
}

.btn-green {
    background: #25d366;
}

.btn-dark {
    background: #222;
}

.status {
    padding: 12px;
    border-radius: 10px;
    margin-bottom: 15px;
    background: #eee;
}

.ok {
    background: #d9f7df;
    color: #126b24;
}

.no {
    background: #ffe0e0;
    color: #9b111e;
}

.offer {
    border: 1px solid #eee;
    border-radius: 14px;
    padding: 15px;
    margin-top: 15px;
}

.price {
    font-size: 25px;
    font-weight: bold;
}

.old {
    text-decoration: line-through;
    color: #888;
}

.discount {
    color: #0a8f2f;
    font-weight: bold;
}

a {
    color: #3483fa;
    text-decoration: none;
}

.small {
    font-size: 13px;
    color: #666;
}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>🛒 Caçador de Ofertas</h1>

<div class="status {{ 'ok' if conectado else 'no' }}">

{% if conectado %}
✅ Mercado Livre conectado
{% else %}
❌ Mercado Livre não conectado
{% endif %}

</div>

{% if not conectado %}

<a href="/mercadolivre/login">
<button>
🔐 Conectar Mercado Livre
</button>
</a>

{% else %}

<form action="/buscar" method="get">

<label>
Produto para procurar
</label>

<input
    name="q"
    value="celular"
    placeholder="Ex: celular, TV, air fryer..."
>

<label>
Desconto mínimo (%)
</label>

<input
    name="min_discount"
    type="number"
    value="10"
    min="0"
    max="99"
>

<button>
🔎 Procurar ofertas
</button>

</form>

{% endif %}

</div>


<div class="card">

<h3>🔧 Ferramentas</h3>

<a href="/mercadolivre/status">
<button class="btn-dark">
📡 Status Mercado Livre
</button>
</a>

<a href="/mercadolivre/diagnostico">
<button class="btn-dark">
🩺 Diagnóstico
</button>
</a>

<a href="/mercadolivre/teste-preco">
<button class="btn-dark">
💰 Testar preços
</button>
</a>

<a href="/mercadolivre/notificacoes">
<button class="btn-dark">
🔔 Notificações
</button>
</a>

</div>

</div>

</body>
</html>
""", conectado=conectado)


# ============================================================
# LOGIN MERCADO LIVRE
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:
        return jsonify({
            "erro": "ML_CLIENT_ID não configurado no Railway."
        }), 500

    code_verifier = gerar_code_verifier()
    code_challenge = gerar_code_challenge(code_verifier)

    state = secrets.token_urlsafe(32)

    session["oauth_state"] = state
    session["code_verifier"] = code_verifier

    params = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }

    query = "&".join(
        f"{k}={requests.utils.quote(str(v), safe='')}"
        for k, v in params.items()
    )

    url = f"{ML_AUTH_URL}?{query}"

    return redirect(url)


# ============================================================
# CALLBACK
# ============================================================

@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    error = request.args.get("error")

    if error:
        return jsonify({
            "erro": error,
            "descricao": request.args.get("error_description")
        }), 400

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return jsonify({
            "erro": "Código de autorização não recebido."
        }), 400

    saved_state = session.get("oauth_state")

    if saved_state and state != saved_state:
        return jsonify({
            "erro": "State OAuth inválido."
        }), 400

    code_verifier = session.get("code_verifier")

    if not code_verifier:
        return jsonify({
            "erro": "code_verifier não encontrado na sessão."
        }), 400

    try:

        response = requests.post(
            ML_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "code": code,
                "redirect_uri": ML_REDIRECT_URI,
                "code_verifier": code_verifier
            },
            timeout=30
        )

        data = response.json()

        if response.status_code != 200:

            return jsonify({
                "erro": "Mercado Livre recusou o token.",
                "status": response.status_code,
                "resposta": data
            }), response.status_code

        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 21600)
        user_id = data.get("user_id")

        if not access_token:
            return jsonify({
                "erro": "Access token não recebido.",
                "resposta": data
            }), 500

        # Busca nickname
        nickname = None

        try:

            user_response = requests.get(
                f"{ML_API}/users/me",
                headers=ml_headers(access_token),
                timeout=20
            )

            if user_response.ok:
                user_data = user_response.json()
                nickname = user_data.get("nickname")

                if not user_id:
                    user_id = user_data.get("id")

        except Exception:
            pass

        salvar_token(
            access_token,
            refresh_token,
            expires_in,
            user_id,
            nickname
        )

        session.pop("oauth_state", None)
        session.pop("code_verifier", None)

        return redirect("/")

    except Exception as e:

        return jsonify({
            "erro": "Erro durante OAuth.",
            "detalhes": str(e)
        }), 500


# ============================================================
# CALLBACK2
# ============================================================

@app.route("/mercadolivre/callback2")
def mercadolivre_callback2():
    return redirect("/mercadolivre/callback")


# ============================================================
# STATUS
# ============================================================

@app.route("/mercadolivre/status")
def mercadolivre_status():

    token = get_access_token()

    if not token:
        return jsonify({
            "conectado": False,
            "mensagem": "Nenhum access token válido."
        })

    try:

        response = ml_get(
            f"{ML_API}/users/me",
            token
        )

        data = response.json()

        return jsonify({
            "conectado": response.status_code == 200,
            "status": response.status_code,
            "user_id": data.get("id"),
            "nickname": data.get("nickname"),
            "site_id": data.get("site_id"),
            "status_account": data.get("status"),
            "sell": data.get("sell"),
            "buy": data.get("buy")
        })

    except Exception as e:

        return jsonify({
            "conectado": False,
            "erro": str(e)
        }), 500


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def mercadolivre_diagnostico():

    token = get_access_token()

    if not token:

        return jsonify({
            "token": False,
            "erro": "Não existe access token válido."
        }), 401

    resultado = {}

    # --------------------------------------------------------
    # USERS ME
    # --------------------------------------------------------

    try:

        r = ml_get(
            f"{ML_API}/users/me",
            token
        )

        data = r.json()

        resultado["users_me"] = {
            "status": r.status_code,
            "id": data.get("id"),
            "nickname": data.get("nickname"),
            "site_id": data.get("site_id"),
            "status": data.get("status"),
            "sell": data.get("sell"),
            "buy": data.get("buy")
        }

    except Exception as e:

        resultado["users_me"] = {
            "erro": str(e)
        }

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------

    try:

        r = ml_get(
            f"{ML_API}/applications/{ML_CLIENT_ID}",
            token
        )

        data = r.json()

        resultado["application"] = {
            "status": r.status_code,
            "id": data.get("id"),
            "name": data.get("name"),
            "active": data.get("active"),
            "blocked": data.get("blocked"),
            "site_id": data.get("site_id"),
            "certification_status": data.get(
                "certification_status"
            ),
            "use_pkce": data.get("use_pkce"),
            "allow_flow": data.get("allow_flow")
        }

    except Exception as e:

        resultado["application"] = {
            "erro": str(e)
        }

    # --------------------------------------------------------
    # GRANTS
    # --------------------------------------------------------

    try:

        r = ml_get(
            f"{ML_API}/users/{resultado.get('users_me', {}).get('id')}/applications/{ML_CLIENT_ID}/grants",
            token
        )

        resultado["grants"] = {
            "status": r.status_code
        }

        if r.ok:
            data = r.json()

            if isinstance(data, dict):

                resultado["grants"]["scopes"] = data.get(
                    "scopes"
                )

                resultado["grants"]["application_id"] = data.get(
                    "application_id"
                )

    except Exception as e:

        resultado["grants"] = {
            "erro": str(e)
        }

    # --------------------------------------------------------
    # PRODUCTS SEARCH
    # --------------------------------------------------------

    try:

        r = ml_get(
            f"{ML_API}/products/search",
            token,
            params={
                "status": "active",
                "site_id": "MLB",
                "q": "celular",
                "limit": 1
            }
        )

        data = r.json()

        resultado["products_search"] = {
            "status": r.status_code,
            "total": data.get("paging", {}).get("total"),
            "results": len(data.get("results", []))
        }

    except Exception as e:

        resultado["products_search"] = {
            "erro": str(e)
        }

    return jsonify(resultado)


# ============================================================
# NOVO TESTE DE PREÇO
# ============================================================

@app.route("/mercadolivre/teste-preco")
def teste_preco():

    token = get_access_token()

    if not token:

        return jsonify({
            "erro": "Sem access_token. Faça login novamente no Mercado Livre."
        }), 401

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    resultado = {}

    # ========================================================
    # 1. BUSCAR PRODUTO
    # ========================================================

    try:

        r1 = requests.get(
            f"{ML_API}/products/search",
            params={
                "status": "active",
                "site_id": "MLB",
                "q": "celular",
                "limit": 1
            },
            headers=headers,
            timeout=20
        )

        if r1.status_code != 200:

            return jsonify({
                "etapa": "products/search",
                "status": r1.status_code,
                "resposta": r1.json()
            }), r1.status_code

        data = r1.json()

    except Exception as e:

        return jsonify({
            "etapa": "products/search",
            "erro": str(e)
        }), 500

    if not data.get("results"):

        return jsonify({
            "erro": "Nenhum produto encontrado."
        })

    product_id = data["results"][0].get("id")

    resultado["product_id"] = product_id

    # ========================================================
    # 2. DETALHES DO PRODUTO
    # ========================================================

    try:

        r2 = requests.get(
            f"{ML_API}/products/{product_id}",
            headers=headers,
            timeout=20
        )

        produto = r2.json() if r2.content else {}

    except Exception as e:

        return jsonify({
            "product_id": product_id,
            "erro": f"Erro ao consultar produto: {e}"
        }), 500

    buy_box = produto.get("buy_box_winner") or {}

    resultado["produto"] = {
        "status": r2.status_code,
        "id": produto.get("id"),
        "name": produto.get("name")
    }

    resultado["buy_box"] = {
        "item_id": buy_box.get("item_id"),
        "seller_id": buy_box.get("seller_id"),
        "price": buy_box.get("price"),
        "original_price": buy_box.get("original_price"),
        "deal_ids": buy_box.get("deal_ids"),
        "tier": buy_box.get("tier"),
        "product_id": buy_box.get("product_id"),
        "site_id": buy_box.get("site_id")
    }

    item_id = buy_box.get("item_id")

    # ========================================================
    # SEM BUY BOX
    # ========================================================

    if not item_id:

        resultado["erro"] = (
            "Produto encontrado, mas não possui "
            "Buy Box/item_id."
        )

        return jsonify(resultado)

    # ========================================================
    # 3. ITEM
    # ========================================================

    try:

        r3 = requests.get(
            f"{ML_API}/items/{item_id}",
            headers=headers,
            timeout=20
        )

        item = r3.json() if r3.content else {}

        resultado["item"] = {
            "status": r3.status_code,
            "id": item.get("id"),
            "title": item.get("title"),
            "price": item.get("price"),
            "base_price": item.get("base_price"),
            "original_price": item.get("original_price"),
            "currency_id": item.get("currency_id"),
            "seller_id": item.get("seller_id"),
            "category_id": item.get("category_id")
        }

    except Exception as e:

        resultado["item"] = {
            "erro": str(e)
        }

    # ========================================================
    # 4. SALE PRICE
    # ========================================================

    try:

        r4 = requests.get(
            f"{ML_API}/items/{item_id}/sale_price",
            params={
                "context": "channel_marketplace"
            },
            headers=headers,
            timeout=20
        )

        sale = r4.json() if r4.content else {}

        resultado["sale_price"] = {
            "status": r4.status_code,
            "amount": sale.get("amount"),
            "regular_amount": sale.get("regular_amount"),
            "currency_id": sale.get("currency_id"),
            "metadata": sale.get("metadata")
        }

    except Exception as e:

        resultado["sale_price"] = {
            "erro": str(e)
        }

    # ========================================================
    # 5. PRICES
    # ========================================================

    try:

        r5 = requests.get(
            f"{ML_API}/items/{item_id}/prices",
            headers={
                **headers,
                "show-all-prices": "true"
            },
            timeout=20
        )

        prices_data = (
            r5.json()
            if r5.content
            else {}
        )

        prices = []

        for p in prices_data.get("prices", []):

            prices.append({
                "type": p.get("type"),
                "amount": p.get("amount"),
                "regular_amount": p.get("regular_amount"),
                "currency_id": p.get("currency_id"),
                "conditions": p.get("conditions")
            })

        resultado["prices"] = {
            "status": r5.status_code,
            "prices": prices
        }

    except Exception as e:

        resultado["prices"] = {
            "erro": str(e)
        }

    # ========================================================
    # RESULTADO DO DESCONTO
    # ========================================================

    preco = None
    preco_original = None

    # Primeiro tenta Buy Box
    if buy_box.get("price") is not None:
        preco = buy_box.get("price")

    if buy_box.get("original_price") is not None:
        preco_original = buy_box.get(
            "original_price"
        )

    # Depois tenta item
    if preco is None:
        preco = item.get("price")

    if preco_original is None:
        preco_original = item.get(
            "original_price"
        )

    # Depois sale_price
    sale = resultado.get("sale_price", {})

    if preco is None:
        preco = sale.get("amount")

    if preco_original is None:
        preco_original = sale.get(
            "regular_amount"
        )

    # Depois prices
    prices_result = resultado.get(
        "prices",
        {}
    )

    for p in prices_result.get("prices", []):

        if preco is None and p.get("amount") is not None:
            preco = p.get("amount")

        if (
            preco_original is None
            and p.get("regular_amount") is not None
        ):
            preco_original = p.get(
                "regular_amount"
            )

    desconto = None

    try:

        if (
            preco is not None
            and preco_original is not None
            and float(preco_original) > float(preco)
        ):

            desconto = round(
                (
                    (
                        float(preco_original)
                        - float(preco)
                    )
                    / float(preco_original)
                )
                * 100,
                2
            )

    except Exception:
        desconto = None

    resultado["calculo"] = {
        "preco": preco,
        "preco_original": preco_original,
        "desconto_percentual": desconto
    }

    return jsonify(resultado)


# ============================================================
# BUSCAR OFERTAS
# ============================================================

@app.route("/buscar")
def buscar():

    query = request.args.get(
        "q",
        "celular"
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

    token = get_access_token()

    if not token:

        return jsonify({
            "erro": "Mercado Livre não conectado.",
            "login": "/mercadolivre/login"
        }), 401

    try:

        response = ml_get(
            f"{ML_API}/products/search",
            token,
            params={
                "status": "active",
                "site_id": "MLB",
                "q": query,
                "limit": PRODUCT_SEARCH_LIMIT
            }
        )

        if response.status_code != 200:

            return jsonify({
                "erro": "Erro na busca de produtos.",
                "status": response.status_code,
                "resposta": response.json()
            }), response.status_code

        data = response.json()

    except Exception as e:

        return jsonify({
            "erro": "Falha na busca.",
            "detalhes": str(e)
        }), 500

    products = data.get("results", [])

    ofertas = []

    # ========================================================
    # PROCESSAR PRODUTOS
    # ========================================================

    for product in products:

        product_id = product.get("id")

        if not product_id:
            continue

        try:

            product_response = ml_get(
                f"{ML_API}/products/{product_id}",
                token
            )

            if product_response.status_code != 200:
                continue

            product_data = product_response.json()

            buy_box = (
                product_data.get(
                    "buy_box_winner"
                )
                or {}
            )

            item_id = buy_box.get("item_id")

            if not item_id:
                continue

            # ------------------------------------------------
            # DADOS DO ITEM
            # ------------------------------------------------

            item_response = ml_get(
                f"{ML_API}/items/{item_id}",
                token
            )

            if item_response.status_code != 200:
                continue

            item = item_response.json()

            # ------------------------------------------------
            # PREÇOS
            # ------------------------------------------------

            price = None
            original_price = None

            # Buy Box
            if buy_box.get("price") is not None:
                price = buy_box.get("price")

            if buy_box.get("original_price") is not None:
                original_price = buy_box.get(
                    "original_price"
                )

            # Item
            if price is None:
                price = item.get("price")

            if original_price is None:
                original_price = item.get(
                    "original_price"
                )

            # ------------------------------------------------
            # SALE PRICE
            # ------------------------------------------------

            if price is None or original_price is None:

                try:

                    sale_response = ml_get(
                        f"{ML_API}/items/{item_id}/sale_price",
                        token,
                        params={
                            "context": "channel_marketplace"
                        }
                    )

                    if sale_response.status_code == 200:

                        sale = sale_response.json()

                        if price is None:
                            price = sale.get(
                                "amount"
                            )

                        if original_price is None:
                            original_price = sale.get(
                                "regular_amount"
                            )

                except Exception:
                    pass

            # ------------------------------------------------
            # PRICES
            # ------------------------------------------------

            if price is None or original_price is None:

                try:

                    prices_response = ml_get(
                        f"{ML_API}/items/{item_id}/prices",
                        token,
                        headers={
                            "show-all-prices": "true"
                        }
                    )

                    if prices_response.status_code == 200:

                        prices_data = (
                            prices_response.json()
                        )

                        for p in prices_data.get(
                            "prices",
                            []
                        ):

                            if price is None:
                                price = p.get(
                                    "amount"
                                )

                            if (
                                original_price is None
                                and p.get(
                                    "regular_amount"
                                ) is not None
                            ):

                                original_price = p.get(
                                    "regular_amount"
                                )

                            if (
                                price is not None
                                and original_price is not None
                            ):
                                break

                except Exception:
                    pass

            # ------------------------------------------------
            # CALCULAR DESCONTO
            # ------------------------------------------------

            if price is None:
                continue

            try:
                price = float(price)
            except Exception:
                continue

            if original_price is None:
                continue

            try:
                original_price = float(
                    original_price
                )
            except Exception:
                continue

            if original_price <= price:
                continue

            discount = round(
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

            if discount < min_discount:
                continue

            # ------------------------------------------------
            # OFERTA
            # ------------------------------------------------

            oferta = {
                "item_id": item_id,
                "product_id": product_id,
                "title": (
                    item.get("title")
                    or product_data.get("name")
                    or "Produto"
                ),
                "price": price,
                "original_price": original_price,
                "discount": discount,
                "seller_id": item.get(
                    "seller_id"
                ),
                "category_id": item.get(
                    "category_id"
                ),
                "permalink": item.get(
                    "permalink"
                ),
                "buy_box": True
            }

            ofertas.append(oferta)

        except Exception as e:

            print(
                "[ERRO PROCESSANDO PRODUTO]",
                product_id,
                e
            )

            continue

    # ========================================================
    # SALVAR
    # ========================================================

    conn = get_db()

    for oferta in ofertas:

        conn.execute("""
            INSERT INTO ofertas (
                item_id,
                product_id,
                title,
                price,
                original_price,
                discount,
                seller_id,
                category_id,
                permalink,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            oferta["item_id"],
            oferta["product_id"],
            oferta["title"],
            oferta["price"],
            oferta["original_price"],
            oferta["discount"],
            oferta["seller_id"],
            oferta["category_id"],
            oferta["permalink"],
            int(time.time())
        ))

    conn.commit()
    conn.close()

    # ========================================================
    # RESULTADO
    # ========================================================

    return render_template_string("""
<!DOCTYPE html>
<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>Ofertas encontradas</title>

<style>

body {
    font-family: Arial;
    background: #f5f5f5;
    margin: 0;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 20px;
}

.card {
    background: white;
    padding: 20px;
    border-radius: 16px;
    margin-bottom: 15px;
    box-shadow: 0 3px 15px rgba(0,0,0,.08);
}

.offer {
    border: 1px solid #eee;
    padding: 15px;
    border-radius: 14px;
    margin-top: 15px;
}

.title {
    font-size: 18px;
    font-weight: bold;
}

.price {
    font-size: 27px;
    font-weight: bold;
    margin-top: 10px;
}

.old {
    color: #888;
    text-decoration: line-through;
}

.discount {
    color: #0b8f35;
    font-weight: bold;
    font-size: 18px;
}

button {
    width: 100%;
    padding: 13px;
    border: 0;
    border-radius: 10px;
    background: #3483fa;
    color: white;
    font-weight: bold;
    margin-top: 10px;
}

a {
    text-decoration: none;
}

.back {
    background: #222;
}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>🔥 Ofertas encontradas</h1>

<p>
Busca:
<strong>{{ query }}</strong>
</p>

<p>
Desconto mínimo:
<strong>{{ min_discount }}%</strong>
</p>

<p>
Encontradas:
<strong>{{ ofertas|length }}</strong>
</p>

<a href="/">
<button class="back">
⬅️ Voltar
</button>
</a>

</div>

{% if not ofertas %}

<div class="card">

<h3>
😕 Nenhuma oferta encontrada
</h3>

<p>
A busca encontrou produtos, mas nenhum deles
possui publicação vencedora com desconto de
{{ min_discount }}% ou mais.
</p>

<p>
Use o botão <strong>Testar preços</strong>
na página inicial para verificar exatamente
o que a API do Mercado Livre está retornando.
</p>

</div>

{% endif %}


{% for oferta in ofertas %}

<div class="card offer">

<div class="title">
{{ oferta.title }}
</div>

<div class="price">
R$ {{ "%.2f"|format(oferta.price) }}
</div>

<div class="old">
De:
R$ {{ "%.2f"|format(oferta.original_price) }}
</div>

<div class="discount">
🔥 {{ oferta.discount }}% OFF
</div>

<p>
🏆 Buy Box
</p>

{% if oferta.permalink %}

<a href="{{ oferta.permalink }}"
   target="_blank">

<button>
🛒 Abrir produto
</button>

</a>

{% endif %}

</div>

{% endfor %}

</div>

</body>
</html>
""",
        ofertas=ofertas,
        query=query,
        min_discount=min_discount
    )


# ============================================================
# GERAR PUBLICAÇÃO
# ============================================================

@app.route("/gerar", methods=["GET", "POST"])
def gerar():

    if request.method == "POST":

        titulo = request.form.get(
            "titulo",
            "Oferta"
        )

        preco = request.form.get(
            "preco",
            ""
        )

        preco_original = request.form.get(
            "preco_original",
            ""
        )

        desconto = request.form.get(
            "desconto",
            ""
        )

        link = request.form.get(
            "link",
            ""
        )

        texto = f"""
🔥 OFERTA ENCONTRADA! 🔥

🛒 {titulo}

💰 Por apenas: R$ {preco}

🏷️ De: R$ {preco_original}

🔥 Desconto: {desconto}%

👉 COMPRE AQUI:
{link}

⚠️ Preço e disponibilidade podem mudar.
"""

        return render_template_string("""
<!DOCTYPE html>
<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Publicação</title>

<style>

body {
    font-family: Arial;
    background: #f5f5f5;
    padding: 20px;
}

.card {
    max-width: 700px;
    margin: auto;
    background: white;
    padding: 20px;
    border-radius: 16px;
}

textarea {
    width: 100%;
    height: 350px;
    padding: 15px;
    font-size: 16px;
    border-radius: 10px;
}

button {
    width: 100%;
    padding: 15px;
    margin-top: 10px;
    border: 0;
    border-radius: 10px;
    background: #25d366;
    color: white;
    font-weight: bold;
}

</style>

</head>

<body>

<div class="card">

<h2>📢 Publicação pronta</h2>

<textarea id="texto">{{ texto }}</textarea>

<button onclick="copiar()">
📋 Copiar publicação
</button>

</div>

<script>

function copiar() {

    const texto =
        document.getElementById("texto");

    texto.select();

    document.execCommand("copy");

    alert("Publicação copiada!");

}

</script>

</body>

</html>
""", texto=texto)

    return render_template_string("""
<!DOCTYPE html>
<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Gerar publicação</title>

<style>

body {
    font-family: Arial;
    background: #f5f5f5;
    padding: 20px;
}

.card {
    max-width: 700px;
    margin: auto;
    background: white;
    padding: 20px;
    border-radius: 16px;
}

input {
    width: 100%;
    padding: 14px;
    margin: 7px 0 14px;
    border: 1px solid #ddd;
    border-radius: 10px;
}

button {
    width: 100%;
    padding: 15px;
    border: 0;
    border-radius: 10px;
    background: #3483fa;
    color: white;
    font-weight: bold;
}

</style>

</head>

<body>

<div class="card">

<h2>📢 Gerar publicação</h2>

<form method="post">

<label>Título</label>

<input
name="titulo"
placeholder="Ex: Smartphone Samsung Galaxy"
required
>

<label>Preço atual</label>

<input
name="preco"
placeholder="999,90"
required
>

<label>Preço original</label>

<input
name="preco_original"
placeholder="1299,90"
required
>

<label>Desconto</label>

<input
name="desconto"
placeholder="23"
required
>

<label>Link</label>

<input
name="link"
placeholder="Cole seu link de afiliado"
required
>

<button>
🚀 Gerar publicação
</button>

</form>

</div>

</body>

</html>
""")


# ============================================================
# NOTIFICAÇÕES
# ============================================================

@app.route("/mercadolivre/notificacoes")
def mercadolivre_notificacoes():

    return jsonify({
        "ok": True,
        "mensagem": "Endpoint de notificações ativo.",
        "metodo": "POST",
        "endpoint": "/mercadolivre/notificacoes"
    })


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "app": "Caçador de Ofertas",
        "mercadolivre": bool(ML_CLIENT_ID),
        "timestamp": int(time.time())
    })


# ============================================================
# ERROR HANDLER
# ============================================================

@app.errorhandler(404)
def pagina_nao_encontrada(error):

    return jsonify({
        "erro": "Página não encontrada.",
        "status": 404
    }), 404


@app.errorhandler(500)
def erro_interno(error):

    return jsonify({
        "erro": "Erro interno do servidor.",
        "status": 500
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
        port=port
    )