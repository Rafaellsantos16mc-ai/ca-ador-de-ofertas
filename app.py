import os
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
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "cacador-ofertas-secret-2026"
)

DB_PATH = os.getenv(
    "DB_PATH",
    "ofertas.db"
)


# ============================================================
# MERCADO LIVRE
# ============================================================

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

ML_AUTH_URL = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN_URL = (
    "https://api.mercadolibre.com/oauth/token"
)

ML_API = (
    "https://api.mercadolibre.com"
)


# ============================================================
# CONFIGURAÇÕES DE BUSCA
# ============================================================

PRODUCT_SEARCH_LIMIT = 50

MAX_CHILDREN_PER_PRODUCT = 20

MAX_PRODUCTS_TO_PROCESS = 50


# ============================================================
# BANCO
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH
    )

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

    # --------------------------------------------------------
    # MIGRAÇÕES
    # --------------------------------------------------------

    migrations = {
        "tokens": [
            ("user_id", "TEXT"),
            ("nickname", "TEXT")
        ],
        "ofertas": [
            ("product_id", "TEXT"),
            ("seller_id", "TEXT"),
            ("category_id", "TEXT")
        ]
    }

    for table, columns in migrations.items():

        for column, datatype in columns:

            try:

                conn.execute(
                    f"""
                    ALTER TABLE {table}
                    ADD COLUMN {column} {datatype}
                    """
                )

            except Exception:

                pass

    conn.commit()

    conn.close()


init_db()


# ============================================================
# TOKEN
# ============================================================

def salvar_token(
    access_token,
    refresh_token,
    expires_in,
    user_id=None,
    nickname=None
):

    conn = get_db()

    expires_at = (
        int(time.time())
        + int(expires_in or 21600)
    )

    conn.execute(
        "DELETE FROM tokens"
    )

    conn.execute("""
        INSERT INTO tokens (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname
        )
        VALUES (
            1,
            ?,
            ?,
            ?,
            ?,
            ?
        )
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


def refresh_access_token():

    row = obter_token_db()

    if not row:
        return None

    refresh_token = row["refresh_token"]

    if not refresh_token:
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

            print(
                "[ERRO REFRESH]",
                data
            )

            return None

        access_token = data.get(
            "access_token"
        )

        if not access_token:
            return None

        salvar_token(
            access_token,
            data.get(
                "refresh_token",
                refresh_token
            ),
            data.get(
                "expires_in",
                21600
            ),
            row["user_id"],
            row["nickname"]
        )

        print(
            "[OK] TOKEN RENOVADO"
        )

        return access_token

    except Exception as e:

        print(
            "[ERRO REFRESH]",
            e
        )

        return None


def get_access_token():

    row = obter_token_db()

    if not row:
        return None

    access_token = row["access_token"]

    expires_at = (
        row["expires_at"]
        or 0
    )

    # --------------------------------------------------------
    # TOKEN AINDA VÁLIDO
    # --------------------------------------------------------

    if (
        access_token
        and int(time.time())
        < int(expires_at) - 120
    ):

        return access_token

    # --------------------------------------------------------
    # RENOVAR
    # --------------------------------------------------------

    return refresh_access_token()


# ============================================================
# PKCE
# ============================================================

def gerar_code_verifier():

    return secrets.token_urlsafe(
        64
    )


def gerar_code_challenge(
    verifier
):

    digest = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()

    return (
        base64.urlsafe_b64encode(
            digest
        )
        .decode("utf-8")
        .rstrip("=")
    )


# ============================================================
# HEADERS
# ============================================================

def ml_headers(
    token
):

    return {
        "Authorization": (
            f"Bearer {token}"
        ),
        "Accept": "application/json"
    }


# ============================================================
# GET MERCADO LIVRE
# ============================================================

def ml_get(
    url,
    token,
    **kwargs
):

    headers = ml_headers(
        token
    )

    custom_headers = kwargs.pop(
        "headers",
        {}
    )

    headers.update(
        custom_headers
    )

    return requests.get(
        url,
        headers=headers,
        timeout=30,
        **kwargs
    )


# ============================================================
# CONVERTER NÚMERO
# ============================================================

def numero(
    valor
):

    try:

        if valor is None:
            return None

        return float(
            valor
        )

    except Exception:

        return None


# ============================================================
# CALCULAR DESCONTO
# ============================================================

def calcular_desconto(
    preco,
    original
):

    preco = numero(
        preco
    )

    original = numero(
        original
    )

    if (
        preco is None
        or original is None
        or original <= 0
        or original <= preco
    ):

        return None

    return round(
        (
            (
                original
                - preco
            )
            / original
        )
        * 100,
        2
    )


# ============================================================
# OBTER PREÇOS DE ITEM
# ============================================================

def obter_precos_item(
    item_id,
    token
):

    resultado = {
        "price": None,
        "original_price": None,
        "currency_id": None,
        "item": {},
        "sale_price": {},
        "prices": []
    }

    # ========================================================
    # ITEM
    # ========================================================

    try:

        response = ml_get(
            f"{ML_API}/items/{item_id}",
            token
        )

        if response.ok:

            item = response.json()

            resultado["item"] = item

            resultado["price"] = (
                item.get(
                    "price"
                )
            )

            resultado[
                "original_price"
            ] = (
                item.get(
                    "original_price"
                )
            )

            resultado[
                "currency_id"
            ] = (
                item.get(
                    "currency_id"
                )
            )

    except Exception as e:

        print(
            "[ITEM ERRO]",
            item_id,
            e
        )

    # ========================================================
    # SALE PRICE
    # ========================================================

    try:

        response = ml_get(
            (
                f"{ML_API}/items/"
                f"{item_id}/sale_price"
            ),
            token,
            params={
                "context":
                    "channel_marketplace"
            }
        )

        if response.ok:

            sale = response.json()

            resultado[
                "sale_price"
            ] = sale

            if (
                resultado["price"]
                is None
            ):

                resultado[
                    "price"
                ] = sale.get(
                    "amount"
                )

            if (
                resultado[
                    "original_price"
                ]
                is None
            ):

                resultado[
                    "original_price"
                ] = sale.get(
                    "regular_amount"
                )

    except Exception as e:

        print(
            "[SALE PRICE ERRO]",
            item_id,
            e
        )

    # ========================================================
    # PRICES
    # ========================================================

    try:

        response = ml_get(
            (
                f"{ML_API}/items/"
                f"{item_id}/prices"
            ),
            token,
            headers={
                "show-all-prices":
                    "true"
            }
        )

        if response.ok:

            prices_data = (
                response.json()
            )

            lista = (
                prices_data.get(
                    "prices",
                    []
                )
            )

            resultado[
                "prices"
            ] = lista

            # ------------------------------------------------
            # PRIORIZA PROMOTION
            # ------------------------------------------------

            for p in lista:

                if (
                    p.get("type")
                    == "promotion"
                ):

                    if (
                        resultado[
                            "price"
                        ]
                        is None
                    ):

                        resultado[
                            "price"
                        ] = p.get(
                            "amount"
                        )

                    if (
                        resultado[
                            "original_price"
                        ]
                        is None
                    ):

                        resultado[
                            "original_price"
                        ] = p.get(
                            "regular_amount"
                        )

            # ------------------------------------------------
            # FALLBACK
            # ------------------------------------------------

            for p in lista:

                if (
                    resultado[
                        "price"
                    ]
                    is None
                ):

                    resultado[
                        "price"
                    ] = p.get(
                        "amount"
                    )

                if (
                    resultado[
                        "original_price"
                    ]
                    is None
                ):

                    regular = (
                        p.get(
                            "regular_amount"
                        )
                    )

                    if regular is not None:

                        resultado[
                            "original_price"
                        ] = regular

                if (
                    resultado[
                        "price"
                    ] is not None
                    and
                    resultado[
                        "original_price"
                    ] is not None
                ):

                    break

    except Exception as e:

        print(
            "[PRICES ERRO]",
            item_id,
            e
        )

    resultado[
        "discount"
    ] = calcular_desconto(
        resultado["price"],
        resultado[
            "original_price"
        ]
    )

    return resultado


# ============================================================
# ENCONTRAR ITEM
# ============================================================

def obter_item_do_produto(
    product,
    token,
    visitados=None,
    nivel=0
):

    if visitados is None:

        visitados = set()

    product_id = product.get(
        "id"
    )

    if not product_id:

        return None

    # --------------------------------------------------------
    # PROTEÇÃO CONTRA LOOP
    # --------------------------------------------------------

    if product_id in visitados:

        return None

    visitados.add(
        product_id
    )

    # --------------------------------------------------------
    # LIMITE
    # --------------------------------------------------------

    if nivel > 3:

        return None

    # ========================================================
    # 1. BUY BOX
    # ========================================================

    buy_box = (
        product.get(
            "buy_box_winner"
        )
        or {}
    )

    item_id = (
        buy_box.get(
            "item_id"
        )
    )

    if item_id:

        print(
            "[BUY BOX]",
            product_id,
            item_id
        )

        return {
            "item_id": item_id,
            "seller_id": (
                buy_box.get(
                    "seller_id"
                )
            ),
            "price": (
                buy_box.get(
                    "price"
                )
            ),
            "original_price": (
                buy_box.get(
                    "original_price"
                )
            ),
            "buy_box": True,
            "product_id": product_id
        }

    # ========================================================
    # 2. CHILDREN IDS
    # ========================================================

    children = (
        product.get(
            "children_ids"
        )
        or []
    )

    if children:

        print(
            "[PRODUTO PAI]",
            product_id,
            "children:",
            len(children)
        )

        children = children[
            :MAX_CHILDREN_PER_PRODUCT
        ]

        for child_id in children:

            if not child_id:
                continue

            if child_id in visitados:
                continue

            try:

                response = ml_get(
                    (
                        f"{ML_API}/products/"
                        f"{child_id}"
                    ),
                    token
                )

                if (
                    response.status_code
                    != 200
                ):

                    continue

                child = (
                    response.json()
                )

                # ------------------------------------------------
                # BUY BOX DO FILHO
                # ------------------------------------------------

                child_box = (
                    child.get(
                        "buy_box_winner"
                    )
                    or {}
                )

                child_item = (
                    child_box.get(
                        "item_id"
                    )
                )

                if child_item:

                    print(
                        "[BUY BOX FILHO]",
                        child_id,
                        child_item
                    )

                    return {
                        "item_id":
                            child_item,
                        "seller_id":
                            child_box.get(
                                "seller_id"
                            ),
                        "price":
                            child_box.get(
                                "price"
                            ),
                        "original_price":
                            child_box.get(
                                "original_price"
                            ),
                        "buy_box": True,
                        "product_id":
                            child_id,
                        "parent_product_id":
                            product_id
                    }

                # ------------------------------------------------
                # RECURSÃO
                # ------------------------------------------------

                encontrado = (
                    obter_item_do_produto(
                        child,
                        token,
                        visitados,
                        nivel + 1
                    )
                )

                if encontrado:

                    return encontrado

            except Exception as e:

                print(
                    "[CHILD ERRO]",
                    child_id,
                    e
                )

                continue

    # ========================================================
    # 3. ITEM_ID DIRETO, CASO EXISTENTE
    # ========================================================

    for campo in [
        "item_id",
        "listing_item_id"
    ]:

        possible = (
            product.get(
                campo
            )
        )

        if (
            possible
            and str(
                possible
            ).startswith(
                "MLB"
            )
        ):

            print(
                "[ITEM DIRETO]",
                possible
            )

            return {
                "item_id": possible,
                "seller_id": None,
                "price": None,
                "original_price": None,
                "buy_box": False,
                "product_id": product_id
            }

    return None


# ============================================================
# LOGIN MERCADO LIVRE
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return jsonify({
            "erro":
                "ML_CLIENT_ID não configurado."
        }), 500

    verifier = (
        gerar_code_verifier()
    )

    challenge = (
        gerar_code_challenge(
            verifier
        )
    )

    state = secrets.token_urlsafe(
        32
    )

    session[
        "oauth_state"
    ] = state

    session[
        "code_verifier"
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

    query = "&".join(
        (
            f"{key}="
            f"{requests.utils.quote(str(value), safe='')}"
        )
        for key, value
        in params.items()
    )

    return redirect(
        f"{ML_AUTH_URL}?{query}"
    )


# ============================================================
# CALLBACK
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
            "erro": error,
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

        return jsonify({
            "erro":
                "Código OAuth não recebido."
        }), 400

    saved_state = session.get(
        "oauth_state"
    )

    if (
        saved_state
        and state != saved_state
    ):

        return jsonify({
            "erro":
                "State OAuth inválido."
        }), 400

    verifier = session.get(
        "code_verifier"
    )

    if not verifier:

        return jsonify({
            "erro":
                "code_verifier não encontrado."
        }), 400

    try:

        response = requests.post(
            ML_TOKEN_URL,
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
                    verifier
            },
            timeout=30
        )

        data = response.json()

        if response.status_code != 200:

            return jsonify({
                "erro":
                    "Mercado Livre recusou OAuth.",
                "status":
                    response.status_code,
                "resposta":
                    data
            }), response.status_code

        access_token = data.get(
            "access_token"
        )

        if not access_token:

            return jsonify({
                "erro":
                    "Access token não recebido."
            }), 500

        user_id = data.get(
            "user_id"
        )

        nickname = None

        try:

            user_response = ml_get(
                f"{ML_API}/users/me",
                access_token
            )

            if user_response.ok:

                user_data = (
                    user_response.json()
                )

                if not user_id:

                    user_id = (
                        user_data.get(
                            "id"
                        )
                    )

                nickname = (
                    user_data.get(
                        "nickname"
                    )
                )

        except Exception:

            pass

        salvar_token(
            access_token,
            data.get(
                "refresh_token"
            ),
            data.get(
                "expires_in",
                21600
            ),
            user_id,
            nickname
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

        return jsonify({
            "erro": str(e)
        }), 500


@app.route(
    "/mercadolivre/callback2"
)
def callback2():

    return redirect(
        "/mercadolivre/callback"
    )


# ============================================================
# STATUS
# ============================================================

@app.route(
    "/mercadolivre/status"
)
def mercadolivre_status():

    token = (
        get_access_token()
    )

    if not token:

        return jsonify({
            "conectado": False
        })

    try:

        response = ml_get(
            f"{ML_API}/users/me",
            token
        )

        data = response.json()

        return jsonify({
            "conectado":
                response.status_code
                == 200,
            "status":
                response.status_code,
            "user_id":
                data.get("id"),
            "nickname":
                data.get("nickname"),
            "site_id":
                data.get("site_id"),
            "status_conta":
                data.get("status")
        })

    except Exception as e:

        return jsonify({
            "conectado": False,
            "erro": str(e)
        }), 500


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    token = (
        get_access_token()
    )

    if not token:

        return jsonify({
            "token": False,
            "erro":
                "Sem access token."
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

        d = r.json()

        resultado[
            "users_me"
        ] = {
            "status":
                r.status_code,
            "id":
                d.get("id"),
            "nickname":
                d.get("nickname"),
            "site_id":
                d.get("site_id"),
            "status":
                d.get("status")
        }

    except Exception as e:

        resultado[
            "users_me"
        ] = {
            "erro": str(e)
        }

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------

    try:

        r = ml_get(
            (
                f"{ML_API}/applications/"
                f"{ML_CLIENT_ID}"
            ),
            token
        )

        d = r.json()

        resultado[
            "application"
        ] = {
            "status":
                r.status_code,
            "id":
                d.get("id"),
            "name":
                d.get("name"),
            "active":
                d.get("active"),
            "blocked":
                d.get("blocked"),
            "certification_status":
                d.get(
                    "certification_status"
                ),
            "use_pkce":
                d.get("use_pkce"),
            "allow_flow":
                d.get("allow_flow")
        }

    except Exception as e:

        resultado[
            "application"
        ] = {
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
                "status":
                    "active",
                "site_id":
                    "MLB",
                "q":
                    "celular",
                "limit":
                    1
            }
        )

        d = r.json()

        resultado[
            "products_search"
        ] = {
            "status":
                r.status_code,
            "total":
                d.get(
                    "paging",
                    {}
                ).get(
                    "total"
                ),
            "results":
                len(
                    d.get(
                        "results",
                        []
                    )
                )
        }

    except Exception as e:

        resultado[
            "products_search"
        ] = {
            "erro": str(e)
        }

    return jsonify(
        resultado
    )


# ============================================================
# TESTE DE PREÇO
# ============================================================

@app.route(
    "/mercadolivre/teste-preco"
)
def teste_preco():

    token = (
        get_access_token()
    )

    if not token:

        return jsonify({
            "erro":
                "Sem access token."
        }), 401

    resultado = {}

    # ========================================================
    # BUSCAR PRODUTO
    # ========================================================

    try:

        response = ml_get(
            f"{ML_API}/products/search",
            token,
            params={
                "status":
                    "active",
                "site_id":
                    "MLB",
                "q":
                    "celular",
                "limit":
                    1
            }
        )

        if not response.ok:

            return jsonify({
                "status":
                    response.status_code,
                "erro":
                    "products/search falhou."
            }), response.status_code

        data = (
            response.json()
        )

    except Exception as e:

        return jsonify({
            "erro": str(e)
        }), 500

    if not data.get(
        "results"
    ):

        return jsonify({
            "erro":
                "Nenhum produto encontrado."
        })

    product_id = (
        data[
            "results"
        ][0].get(
            "id"
        )
    )

    resultado[
        "product_id"
    ] = product_id

    # ========================================================
    # PRODUTO
    # ========================================================

    response = ml_get(
        (
            f"{ML_API}/products/"
            f"{product_id}"
        ),
        token
    )

    if not response.ok:

        return jsonify({
            "erro":
                "Falha ao consultar produto.",
            "status":
                response.status_code
        })

    product = (
        response.json()
    )

    buy_box = (
        product.get(
            "buy_box_winner"
        )
        or {}
    )

    resultado[
        "produto"
    ] = {
        "status":
            response.status_code,
        "id":
            product.get("id"),
        "name":
            product.get("name"),
        "parent_id":
            product.get("parent_id"),
        "children_ids":
            product.get(
                "children_ids"
            )
    }

    resultado[
        "buy_box"
    ] = {
        "item_id":
            buy_box.get(
                "item_id"
            ),
        "seller_id":
            buy_box.get(
                "seller_id"
            ),
        "price":
            buy_box.get(
                "price"
            ),
        "original_price":
            buy_box.get(
                "original_price"
            ),
        "deal_ids":
            buy_box.get(
                "deal_ids"
            )
    }

    # ========================================================
    # TENTAR ITEM
    # ========================================================

    info_item = (
        obter_item_do_produto(
            product,
            token
        )
    )

    if not info_item:

        resultado[
            "resultado"
        ] = (
            "A API não forneceu um "
            "item_id para este produto."
        )

        resultado[
            "observacao"
        ] = (
            "O produto pode não ter uma "
            "publicação vencedora."
        )

        return jsonify(
            resultado
        )

    item_id = (
        info_item[
            "item_id"
        ]
    )

    resultado[
        "item_id"
    ] = item_id

    # ========================================================
    # PREÇOS
    # ========================================================

    precos = (
        obter_precos_item(
            item_id,
            token
        )
    )

    item = (
        precos.get(
            "item",
            {}
        )
    )

    resultado[
        "item"
    ] = {
        "id":
            item.get("id"),
        "title":
            item.get("title"),
        "price":
            item.get("price"),
        "base_price":
            item.get("base_price"),
        "original_price":
            item.get(
                "original_price"
            ),
        "currency_id":
            item.get(
                "currency_id"
            ),
        "seller_id":
            item.get(
                "seller_id"
            )
    }

    sale = (
        precos.get(
            "sale_price",
            {}
        )
    )

    resultado[
        "sale_price"
    ] = {
        "amount":
            sale.get(
                "amount"
            ),
        "regular_amount":
            sale.get(
                "regular_amount"
            ),
        "metadata":
            sale.get(
                "metadata"
            )
    }

    resultado[
        "prices"
    ] = []

    for p in precos.get(
        "prices",
        []
    ):

        resultado[
            "prices"
        ].append({
            "type":
                p.get(
                    "type"
                ),
            "amount":
                p.get(
                    "amount"
                ),
            "regular_amount":
                p.get(
                    "regular_amount"
                ),
            "currency_id":
                p.get(
                    "currency_id"
                )
        })

    resultado[
        "calculo"
    ] = {
        "preco":
            precos.get(
                "price"
            ),
        "preco_original":
            precos.get(
                "original_price"
            ),
        "desconto_percentual":
            precos.get(
                "discount"
            )
    }

    return jsonify(
        resultado
    )


# ============================================================
# BUSCAR OFERTAS
# ============================================================

@app.route(
    "/buscar"
)
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

    token = (
        get_access_token()
    )

    if not token:

        return jsonify({
            "erro":
                "Mercado Livre não conectado."
        }), 401

    # ========================================================
    # PRODUCTS SEARCH
    # ========================================================

    try:

        response = ml_get(
            f"{ML_API}/products/search",
            token,
            params={
                "status":
                    "active",
                "site_id":
                    "MLB",
                "q":
                    query,
                "limit":
                    PRODUCT_SEARCH_LIMIT
            }
        )

        if response.status_code != 200:

            return jsonify({
                "erro":
                    "Erro no products/search.",
                "status":
                    response.status_code,
                "resposta":
                    response.json()
            }), response.status_code

        search_data = (
            response.json()
        )

    except Exception as e:

        return jsonify({
            "erro":
                "Erro na busca.",
            "detalhes":
                str(e)
        }), 500

    products = (
        search_data.get(
            "results",
            []
        )
    )

    # ========================================================
    # CONTADORES
    # ========================================================

    ofertas = []

    sem_item = 0

    sem_desconto = 0

    com_erro = 0

    buy_box_encontradas = 0

    filhos_analisados = 0

    # ========================================================
    # PROCESSAMENTO
    # ========================================================

    for product_summary in products[
        :MAX_PRODUCTS_TO_PROCESS
    ]:

        product_id = (
            product_summary.get(
                "id"
            )
        )

        if not product_id:
            continue

        try:

            # ------------------------------------------------
            # DETALHE
            # ------------------------------------------------

            response = ml_get(
                (
                    f"{ML_API}/products/"
                    f"{product_id}"
                ),
                token
            )

            if not response.ok:

                com_erro += 1

                continue

            product = (
                response.json()
            )

            # ------------------------------------------------
            # CONTABILIZA FILHOS
            # ------------------------------------------------

            children = (
                product.get(
                    "children_ids"
                )
                or []
            )

            filhos_analisados += len(
                children
            )

            # ------------------------------------------------
            # ITEM
            # ------------------------------------------------

            info_item = (
                obter_item_do_produto(
                    product,
                    token
                )
            )

            if not info_item:

                sem_item += 1

                continue

            item_id = (
                info_item[
                    "item_id"
                ]
            )

            if info_item.get(
                "buy_box"
            ):

                buy_box_encontradas += 1

            # ------------------------------------------------
            # PREÇOS
            # ------------------------------------------------

            precos = (
                obter_precos_item(
                    item_id,
                    token
                )
            )

            price = (
                precos.get(
                    "price"
                )
            )

            original_price = (
                precos.get(
                    "original_price"
                )
            )

            # ------------------------------------------------
            # FALLBACK BUY BOX
            # ------------------------------------------------

            if price is None:

                price = (
                    info_item.get(
                        "price"
                    )
                )

            if (
                original_price
                is None
            ):

                original_price = (
                    info_item.get(
                        "original_price"
                    )
                )

            # ------------------------------------------------
            # DESCONTO
            # ------------------------------------------------

            discount = (
                calcular_desconto(
                    price,
                    original_price
                )
            )

            if (
                price is None
                or original_price is None
                or discount is None
            ):

                sem_desconto += 1

                continue

            if discount < min_discount:

                sem_desconto += 1

                continue

            # ------------------------------------------------
            # ITEM
            # ------------------------------------------------

            item = (
                precos.get(
                    "item",
                    {}
                )
            )

            title = (
                item.get(
                    "title"
                )
                or product.get(
                    "name"
                )
                or "Produto"
            )

            permalink = (
                item.get(
                    "permalink"
                )
            )

            if not permalink:

                permalink = (
                    product.get(
                        "permalink"
                    )
                )

            oferta = {
                "item_id":
                    item_id,

                "product_id":
                    product_id,

                "title":
                    title,

                "price":
                    float(price),

                "original_price":
                    float(
                        original_price
                    ),

                "discount":
                    float(
                        discount
                    ),

                "seller_id":
                    item.get(
                        "seller_id"
                    )
                    or info_item.get(
                        "seller_id"
                    ),

                "category_id":
                    item.get(
                        "category_id"
                    ),

                "permalink":
                    permalink,

                "buy_box":
                    bool(
                        info_item.get(
                            "buy_box"
                        )
                    )
            }

            ofertas.append(
                oferta
            )

        except Exception as e:

            com_erro += 1

            print(
                "[ERRO PRODUTO]",
                product_id,
                str(e)
            )

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
            oferta[
                "item_id"
            ],
            oferta[
                "product_id"
            ],
            oferta[
                "title"
            ],
            oferta[
                "price"
            ],
            oferta[
                "original_price"
            ],
            oferta[
                "discount"
            ],
            oferta[
                "seller_id"
            ],
            oferta[
                "category_id"
            ],
            oferta[
                "permalink"
            ],
            int(
                time.time()
            )
        ))

    conn.commit()

    conn.close()

    # ========================================================
    # TELA
    # ========================================================

    return render_template_string("""
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
    background: #f5f5f5;
    font-family: Arial, sans-serif;
    color: #222;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 20px;
}

.card {
    background: white;
    border-radius: 18px;
    padding: 20px;
    margin-bottom: 18px;
    box-shadow:
        0 4px 20px rgba(0,0,0,.07);
}

h1 {
    margin-top: 0;
    font-size: 32px;
}

h2 {
    font-size: 25px;
}

.offer {
    border: 1px solid #eee;
    border-radius: 15px;
    padding: 16px;
    margin-top: 15px;
}

.title {
    font-size: 18px;
    font-weight: bold;
    line-height: 1.4;
}

.price {
    font-size: 29px;
    font-weight: bold;
    margin-top: 12px;
}

.old {
    color: #888;
    text-decoration: line-through;
    margin-top: 5px;
}

.discount {
    color: #0a8f35;
    font-size: 19px;
    font-weight: bold;
    margin-top: 8px;
}

.badge {
    display: inline-block;
    background: #eee;
    padding: 7px 10px;
    border-radius: 8px;
    font-size: 13px;
    margin-top: 10px;
}

button {
    width: 100%;
    border: 0;
    padding: 14px;
    border-radius: 11px;
    background: #3483fa;
    color: white;
    font-weight: bold;
    font-size: 16px;
    margin-top: 10px;
}

.dark {
    background: #222;
}

.green {
    background: #25d366;
}

a {
    text-decoration: none;
}

.small {
    color: #666;
    font-size: 13px;
    line-height: 1.5;
}

.stat {
    padding: 12px;
    background: #f3f3f3;
    border-radius: 10px;
    margin-top: 8px;
}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>🔥 Caçador de Ofertas</h1>

<p>
Busca:
<strong>
{{ query }}
</strong>
</p>

<p>
Desconto mínimo:
<strong>
{{ min_discount }}%
</strong>
</p>

<div class="stat">
📦 Produtos analisados:
<strong>
{{ total }}
</strong>
</div>

<div class="stat">
🔥 Ofertas encontradas:
<strong>
{{ ofertas|length }}
</strong>
</div>

<div class="stat">
🏆 Buy Box encontradas:
<strong>
{{ buy_box }}
</strong>
</div>

<div class="stat">
👶 Produtos-filhos analisados:
<strong>
{{ filhos }}
</strong>
</div>

<a href="/">
<button class="dark">
⬅️ Voltar
</button>
</a>

</div>


{% if not ofertas %}

<div class="card">

<h2>
😕 Nenhuma oferta encontrada
</h2>

<p>
Foram analisados
<strong>
{{ total }}
</strong>
produtos.
</p>

<div class="stat">
❌ Sem publicação/item:
<strong>
{{ sem_item }}
</strong>
</div>

<div class="stat">
📉 Sem desconto suficiente:
<strong>
{{ sem_desconto }}
</strong>
</div>

<div class="stat">
⚠️ Com erro:
<strong>
{{ erros }}
</strong>
</div>

<p class="small">

A busca atual utiliza o catálogo oficial
do Mercado Livre.

Quando o catálogo não fornece um
<strong>item_id</strong>, não é possível
inventar ou montar um anúncio de vendedor.

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

{% if oferta.buy_box %}

<div class="badge">
🏆 Buy Box
</div>

{% else %}

<div class="badge">
🛒 Publicação
</div>

{% endif %}

{% if oferta.permalink %}

<a
href="{{ oferta.permalink }}"
target="_blank"
>

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
        query=query,
        min_discount=min_discount,
        total=len(
            products
        ),
        ofertas=ofertas,
        buy_box=buy_box_encontradas,
        filhos=filhos_analisados,
        sem_item=sem_item,
        sem_desconto=sem_desconto,
        erros=com_erro
    )


# ============================================================
# GERAR PUBLICAÇÃO
# ============================================================

@app.route(
    "/gerar",
    methods=[
        "GET",
        "POST"
    ]
)
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

        preco_original = (
            request.form.get(
                "preco_original",
                ""
            )
        )

        desconto = request.form.get(
            "desconto",
            ""
        )

        link = request.form.get(
            "link",
            ""
        )

        texto = f"""🔥 OFERTA ENCONTRADA! 🔥

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

<h2>
📢 Publicação pronta
</h2>

<textarea id="texto">{{ texto }}</textarea>

<button onclick="copiar()">
📋 Copiar publicação
</button>

</div>

<script>

function copiar() {

    const texto =
        document.getElementById(
            "texto"
        );

    texto.select();

    document.execCommand(
        "copy"
    );

    alert(
        "Publicação copiada!"
    );
}

</script>

</body>

</html>
""",
            texto=texto
        )

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

<h2>
📢 Gerar publicação
</h2>

<form method="post">

<label>
Título
</label>

<input
name="titulo"
placeholder="Ex: Smartphone Samsung Galaxy"
required
>

<label>
Preço atual
</label>

<input
name="preco"
placeholder="999,90"
required
>

<label>
Preço original
</label>

<input
name="preco_original"
placeholder="1299,90"
required
>

<label>
Desconto
</label>

<input
name="desconto"
placeholder="23"
required
>

<label>
Link
</label>

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

@app.route(
    "/mercadolivre/notificacoes"
)
def notificacoes():

    return jsonify({
        "ok": True,
        "endpoint":
            "/mercadolivre/notificacoes",
        "metodo":
            "POST"
    })


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    token = (
        get_access_token()
    )

    conectado = bool(
        token
    )

    return render_template_string("""
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
    font-family: Arial;
    background: #f5f5f5;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 20px;
}

.card {
    background: white;
    padding: 20px;
    border-radius: 18px;
    margin-bottom: 20px;
    box-shadow:
        0 4px 20px rgba(0,0,0,.08);
}

h1 {
    font-size: 32px;
}

input,
button {
    width: 100%;
    padding: 14px;
    border-radius: 10px;
    font-size: 16px;
}

input {
    border: 1px solid #ddd;
    margin-top: 8px;
    margin-bottom: 14px;
}

button {
    border: 0;
    background: #3483fa;
    color: white;
    font-weight: bold;
    margin-bottom: 10px;
}

.dark {
    background: #222;
}

.green {
    background: #25d366;
}

.status {
    padding: 12px;
    border-radius: 10px;
    margin-bottom: 15px;
}

.ok {
    background: #d8f5df;
    color: #126b27;
}

.no {
    background: #ffe0e0;
    color: #9c1515;
}

a {
    text-decoration: none;
}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>
🔥 Caçador de Ofertas
</h1>

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

<form
action="/buscar"
method="get"
>

<label>
Produto
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

<h3>
🔧 Ferramentas
</h3>

<a href="/mercadolivre/status">

<button class="dark">
📡 Status Mercado Livre
</button>

</a>

<a href="/mercadolivre/diagnostico">

<button class="dark">
🩺 Diagnóstico
</button>

</a>

<a href="/mercadolivre/teste-preco">

<button class="dark">
💰 Testar preços
</button>

</a>

<a href="/gerar">

<button class="green">
📢 Gerar publicação
</button>

</a>

</div>

</div>

</body>

</html>
""",
        conectado=conectado
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
            "Caçador de Ofertas",
        "timestamp":
            int(time.time())
    })


# ============================================================
# 404
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "erro":
            "Página não encontrada.",
        "status":
            404
    }), 404


# ============================================================
# 500
# ============================================================

@app.errorhandler(500)
def internal_error(error):

    return jsonify({
        "erro":
            "Erro interno.",
        "status":
            500
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