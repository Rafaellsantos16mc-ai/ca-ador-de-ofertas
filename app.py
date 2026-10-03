import os
import sqlite3
import secrets
import hashlib
import base64
import time
from urllib.parse import urlencode

import requests
from flask import (
    Flask,
    request,
    redirect,
    session,
    jsonify,
    render_template_string,
)

# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "chave-temporaria-caçador-ofertas"
)

# ============================================================
# MERCADO LIVRE
# ============================================================

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"

ML_AUTH = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN = (
    "https://api.mercadolibre.com/oauth/token"
)

SITE_ID = "MLB"

DB_FILE = "ofertas.db"


# ============================================================
# BANCO DE DADOS
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()

    conn.close()


init_db()


# ============================================================
# UTILIDADES
# ============================================================

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


def gerar_pkce():

    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()

    challenge = base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")

    return verifier, challenge


def calcular_desconto(preco, original):

    try:

        preco = float(preco)
        original = float(original)

        if original > preco > 0:

            return round(
                (1 - preco / original) * 100,
                2
            )

    except Exception:
        pass

    return 0


# ============================================================
# TOKEN
# ============================================================

def obter_tokens():

    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM oauth_tokens
        WHERE id = 1
    """).fetchone()

    conn.close()

    if row:
        return dict(row)

    return None


def salvar_tokens(data, user=None):

    access_token = data.get(
        "access_token"
    )

    refresh_token = data.get(
        "refresh_token"
    )

    expires_in = int(
        data.get(
            "expires_in",
            21600
        )
    )

    expires_at = int(
        time.time()
    ) + expires_in

    user_id = None
    nickname = None

    if user:

        if user.get("id") is not None:
            user_id = str(
                user.get("id")
            )

        nickname = user.get(
            "nickname"
        )

    conn = get_db()

    conn.execute("""
        INSERT INTO oauth_tokens
        (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname
        )
        VALUES
        (
            1,
            ?,
            ?,
            ?,
            ?,
            ?
        )

        ON CONFLICT(id) DO UPDATE SET

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
        access_token,
        refresh_token,
        expires_at,
        user_id,
        nickname
    ))

    conn.commit()

    conn.close()


def renovar_token(refresh_token):

    if not refresh_token:
        return None

    try:

        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": refresh_token
            },
            timeout=30
        )

        if response.status_code != 200:

            print(
                "[ERRO REFRESH]",
                response.status_code,
                response.text[:1000]
            )

            return None

        data = response.json()

        tokens_antigos = obter_tokens()

        user = None

        if tokens_antigos:

            user = {
                "id": tokens_antigos.get(
                    "user_id"
                ),
                "nickname": tokens_antigos.get(
                    "nickname"
                )
            }

        salvar_tokens(
            data,
            user
        )

        return data.get(
            "access_token"
        )

    except Exception as e:

        print(
            "[ERRO REFRESH EXCEPTION]",
            e
        )

        return None


def get_access_token():

    tokens = obter_tokens()

    if not tokens:
        return None

    access_token = tokens.get(
        "access_token"
    )

    expires_at = tokens.get(
        "expires_at"
    ) or 0

    if (
        access_token
        and time.time() < expires_at - 120
    ):
        return access_token

    refresh_token = tokens.get(
        "refresh_token"
    )

    if refresh_token:

        novo_token = renovar_token(
            refresh_token
        )

        if novo_token:
            return novo_token

    return access_token


# ============================================================
# REQUEST MERCADO LIVRE
# ============================================================

def ml_get(path, params=None):

    token = get_access_token()

    if not token:

        return (
            None,
            401,
            {}
        )

    if path.startswith("http"):

        url = path

    else:

        url = ML_API + path

    try:

        response = requests.get(
            url,
            headers={
                "Authorization":
                    f"Bearer {token}",

                "Accept":
                    "application/json"
            },
            params=params,
            timeout=30
        )

        try:

            data = response.json()

        except Exception:

            data = {
                "message":
                    response.text
            }

        return (
            data,
            response.status_code,
            dict(response.headers)
        )

    except requests.RequestException as e:

        return (
            {
                "error":
                    str(e)
            },
            500,
            {}
        )


# ============================================================
# LOGIN MERCADO LIVRE
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return jsonify({
            "erro":
                "ML_CLIENT_ID não configurado."
        }), 500

    verifier, challenge = gerar_pkce()

    state = secrets.token_urlsafe(
        32
    )

    session[
        "ml_state"
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
            "S256"
    }

    url = (
        ML_AUTH
        + "?"
        + urlencode(params)
    )

    return redirect(url)


# ============================================================
# CALLBACK
# ============================================================

@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return jsonify({
            "erro":
                error,

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
                "Código de autorização não recebido."
        }), 400

    if state != session.get(
        "ml_state"
    ):

        return jsonify({
            "erro":
                "State inválido."
        }), 400

    verifier = session.get(
        "ml_code_verifier"
    )

    if not verifier:

        return jsonify({
            "erro":
                "Code verifier não encontrado."
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
                    verifier
            },
            timeout=30
        )

        if response.status_code != 200:

            return jsonify({

                "erro":
                    "Falha ao trocar código por token.",

                "status":
                    response.status_code,

                "resposta":
                    response.text
            }), response.status_code

        token_data = response.json()

        access_token = token_data.get(
            "access_token"
        )

        user = None

        if access_token:

            try:

                me = requests.get(
                    f"{ML_API}/users/me",
                    headers={
                        "Authorization":
                            f"Bearer {access_token}"
                    },
                    timeout=30
                )

                if me.status_code == 200:

                    user = me.json()

            except Exception as e:

                print(
                    "[AVISO USERS/ME]",
                    e
                )

        salvar_tokens(
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

        # CORRIGIDO:
        # agora "/" realmente existe
        return redirect(
            "/?conectado=1"
        )

    except Exception as e:

        return jsonify({
            "erro":
                str(e)
        }), 500


# ============================================================
# LOGOUT
# ============================================================

@app.route("/mercadolivre/logout")
def mercadolivre_logout():

    conn = get_db()

    conn.execute("""
        DELETE FROM oauth_tokens
        WHERE id = 1
    """)

    conn.commit()

    conn.close()

    session.clear()

    return redirect("/")


# ============================================================
# TESTE CATEGORIA
# ============================================================

@app.route(
    "/mercadolivre/teste-categoria"
)
def teste_categoria():

    q = request.args.get(
        "q",
        "fone"
    ).strip()

    data, status, _ = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        {
            "q": q
        }
    )

    return jsonify({

        "query":
            q,

        "status_http":
            status,

        "resposta":
            data
    }), status


# ============================================================
# TESTE HIGHLIGHTS
# ============================================================

@app.route(
    "/mercadolivre/teste-highlights"
)
def teste_highlights():

    category_id = request.args.get(
        "category_id",
        "MLB1664"
    ).strip()

    data, status, _ = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    return jsonify({

        "category_id":
            category_id,

        "status_http":
            status,

        "resposta":
            data
    }), status


# ============================================================
# TESTE PRODUTO
# ============================================================

@app.route(
    "/mercadolivre/teste-produto"
)
def teste_produto():

    product_id = request.args.get(
        "product_id",
        "MLB24117280"
    ).strip()

    data, status, _ = ml_get(
        f"/products/{product_id}"
    )

    return jsonify({

        "product_id":
            product_id,

        "status_http":
            status,

        "resposta":
            data
    }), status


# ============================================================
# TESTE PRODUTO -> ITENS
# ============================================================

@app.route(
    "/mercadolivre/teste-produto-itens"
)
def teste_produto_itens():

    product_id = request.args.get(
        "product_id",
        "MLB58793248"
    ).strip()

    if not product_id:

        return jsonify({

            "erro":
                "Informe o product_id.",

            "exemplo":
                "/mercadolivre/teste-produto-itens?product_id=MLB58793248"
        }), 400

    token = get_access_token()

    if not token:

        return jsonify({

            "endpoint":
                f"/products/{product_id}/items",

            "product_id":
                product_id,

            "status_http":
                401,

            "resposta":
                None,

            "erro":
                "Mercado Livre não conectado. Faça login novamente."
        }), 401

    data, status, _ = ml_get(
        f"/products/{product_id}/items"
    )

    return jsonify({

        "endpoint":
            f"/products/{product_id}/items",

        "product_id":
            product_id,

        "resposta":
            data,

        "status_http":
            status
    }), status


# ============================================================
# TESTE PREÇO
# ============================================================

@app.route(
    "/mercadolivre/teste-preco"
)
def teste_preco():

    item_id = request.args.get(
        "item_id",
        ""
    ).strip()

    if not item_id:

        return jsonify({

            "erro":
                "Informe item_id.",

            "exemplo":
                "/mercadolivre/teste-preco?item_id=MLB123456789"
        }), 400

    token = get_access_token()

    if not token:

        return jsonify({

            "erro":
                "Mercado Livre não conectado."
        }), 401

    resultados = {}

    endpoints = [

        f"/items/{item_id}/sale_price",

        f"/items/{item_id}/prices"
    ]

    for endpoint in endpoints:

        data, status, _ = ml_get(
            endpoint
        )

        resultados[
            endpoint
        ] = {

            "status_http":
                status,

            "resposta":
                data
        }

    return jsonify({

        "item_id":
            item_id,

        "resultados":
            resultados
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
        ""
    ).strip()

    if not item_id:

        return jsonify({
            "erro":
                "Informe item_id."
        }), 400

    data, status, _ = ml_get(
        f"/items/{item_id}"
    )

    return jsonify({

        "item_id":
            item_id,

        "status_http":
            status,

        "resposta":
            data
    }), status


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    token = get_access_token()

    resultado = {

        "configuracao": {

            "client_id_configurado":
                bool(ML_CLIENT_ID),

            "client_secret_configurado":
                bool(ML_CLIENT_SECRET),

            "redirect_uri":
                ML_REDIRECT_URI
        },

        "token": {

            "disponivel":
                bool(token)
        }
    }

    if not token:

        resultado[
            "usuario"
        ] = {

            "status":
                "não conectado"
        }

        return jsonify(resultado)

    me, status_me, _ = ml_get(
        "/users/me"
    )

    resultado[
        "users_me"
    ] = {

        "status_http":
            status_me,

        "resposta":
            me
    }

    tokens = obter_tokens()

    if tokens:

        resultado[
            "token_local"
        ] = {

            "user_id":
                tokens.get("user_id"),

            "nickname":
                tokens.get("nickname"),

            "tem_access_token":
                bool(
                    tokens.get(
                        "access_token"
                    )
                ),

            "tem_refresh_token":
                bool(
                    tokens.get(
                        "refresh_token"
                    )
                ),

            "expires_at":
                tokens.get(
                    "expires_at"
                )
        }

    return jsonify(resultado)


# ============================================================
# DESCOBRIR CATEGORIAS
# ============================================================

def descobrir_categorias(query):

    data, status, _ = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        {
            "q":
                query
        }
    )

    if status != 200:

        return [], {

            "status":
                status,

            "resposta":
                data
        }

    categorias = []

    if not isinstance(
        data,
        list
    ):

        return categorias, {

            "status":
                status,

            "resposta":
                data
        }

    for item in data:

        category_id = (
            item.get(
                "category_id"
            )
            or
            item.get(
                "id"
            )
        )

        category_name = (
            item.get(
                "category_name"
            )
            or
            item.get(
                "name"
            )
        )

        if not category_id:
            continue

        categorias.append({

            "category_id":
                category_id,

            "category_name":
                category_name
                or category_id
        })

    return categorias, {

        "status":
            status
    }


# ============================================================
# HIGHLIGHTS
# ============================================================

def buscar_highlights(
    category_id
):

    data, status, _ = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
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

        content = data.get(
            "content"
        )

        if isinstance(
            content,
            list
        ):
            return content

        results = data.get(
            "results"
        )

        if isinstance(
            results,
            list
        ):
            return results

    return []


# ============================================================
# PRODUTO
# ============================================================

def obter_produto(
    product_id
):

    data, status, _ = ml_get(
        f"/products/{product_id}"
    )

    if (
        status != 200
        or
        not isinstance(
            data,
            dict
        )
    ):
        return None

    return data


# ============================================================
# PRODUTO -> ITENS
# ============================================================

def obter_itens_do_produto(
    product_id
):

    data, status, _ = ml_get(
        f"/products/{product_id}/items"
    )

    if status != 200:

        print(
            "[PRODUTO-ITENS]",
            product_id,
            "HTTP",
            status
        )

        return (
            [],
            status,
            data
        )

    if isinstance(
        data,
        list
    ):

        return (
            data,
            status,
            data
        )

    if isinstance(
        data,
        dict
    ):

        results = data.get(
            "results"
        )

        if isinstance(
            results,
            list
        ):

            return (
                results,
                status,
                data
            )

        items = data.get(
            "items"
        )

        if isinstance(
            items,
            list
        ):

            return (
                items,
                status,
                data
            )

    return (
        [],
        status,
        data
    )


# ============================================================
# NORMALIZAR ITEM
# ============================================================

def normalizar_item_produto(
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

    if not item_id:
        return None

    seller_id = item.get(
        "seller_id"
    )

    price = item.get(
        "price"
    )

    original_price = (
        item.get(
            "original_price"
        )
        or
        item.get(
            "regular_amount"
        )
    )

    permalink = (
        item.get(
            "permalink"
        )
        or
        item.get(
            "url"
        )
    )

    return {

        "item_id":
            item_id,

        "seller_id":
            seller_id,

        "price":
            price,

        "original_price":
            original_price,

        "permalink":
            permalink,

        "raw":
            item
    }


# ============================================================
# SALVAR OFERTA
# ============================================================

def salvar_oferta(
    oferta
):

    conn = get_db()

    conn.execute("""
        INSERT INTO ofertas
        (
            product_id,
            item_id,
            title,
            permalink,
            price,
            original_price,
            discount,
            seller_id,
            image,
            category_id,
            category_name
        )

        VALUES
        (
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
        )
    """, (

        oferta.get(
            "product_id"
        ),

        oferta.get(
            "item_id"
        ),

        oferta.get(
            "title"
        ),

        oferta.get(
            "permalink"
        ),

        oferta.get(
            "price"
        ),

        oferta.get(
            "original_price"
        ),

        oferta.get(
            "discount"
        ),

        oferta.get(
            "seller_id"
        ),

        oferta.get(
            "image"
        ),

        oferta.get(
            "category_id"
        ),

        oferta.get(
            "category_name"
        )
    ))

    conn.commit()

    conn.close()


# ============================================================
# BUSCAR OFERTAS
# ============================================================

def search_offers(
    query,
    desconto_minimo=0
):

    try:

        desconto_minimo = float(
            desconto_minimo
        )

    except Exception:

        desconto_minimo = 0

    categorias, categoria_info = (
        descobrir_categorias(
            query
        )
    )

    stats = {

        "query":
            query,

        "categorias_encontradas":
            len(categorias),

        "produtos_highlights":
            0,

        "produtos_unicos":
            0,

        "produtos_consultados":
            0,

        "produtos_com_buy_box":
            0,

        "produtos_sem_buy_box":
            0,

        "produtos_com_preco":
            0,

        "ofertas":
            0,

        "erros":
            0,

        "itens_encontrados":
            0
    }

    produtos = {}

    # --------------------------------------------------------
    # CATEGORIAS / HIGHLIGHTS
    # --------------------------------------------------------

    for categoria in categorias:

        category_id = categoria[
            "category_id"
        ]

        highlights = (
            buscar_highlights(
                category_id
            )
        )

        stats[
            "produtos_highlights"
        ] += len(
            highlights
        )

        for h in highlights:

            h_type = h.get(
                "type"
            )

            # Só PRODUCT neste fluxo
            if h_type != "PRODUCT":
                continue

            product_id = (
                h.get(
                    "id"
                )
                or
                h.get(
                    "product_id"
                )
            )

            if not product_id:
                continue

            if product_id not in produtos:

                produtos[
                    product_id
                ] = {

                    "product_id":
                        product_id,

                    "category_id":
                        category_id,

                    "category_name":
                        categoria[
                            "category_name"
                        ]
                }

    stats[
        "produtos_unicos"
    ] = len(
        produtos
    )

    ofertas = []

    itens_processados = set()

    # --------------------------------------------------------
    # PRODUTOS
    # --------------------------------------------------------

    for product_id, base in produtos.items():

        produto = obter_produto(
            product_id
        )

        stats[
            "produtos_consultados"
        ] += 1

        if not produto:

            stats[
                "erros"
            ] += 1

            continue

        title = (
            produto.get(
                "name"
            )
            or
            produto.get(
                "title"
            )
            or
            product_id
        )

        permalink = (
            produto.get(
                "permalink"
            )
            or
            f"https://www.mercadolivre.com.br/p/{product_id}"
        )

        pictures = (
            produto.get(
                "pictures"
            )
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
                    first.get(
                        "url"
                    )
                    or
                    first.get(
                        "secure_url"
                    )
                )

        buy_box = produto.get(
            "buy_box_winner"
        )

        if buy_box:

            stats[
                "produtos_com_buy_box"
            ] += 1

        else:

            stats[
                "produtos_sem_buy_box"
            ] += 1

        # ----------------------------------------------------
        # NOVO CAMINHO
        #
        # PRODUCT
        #    ↓
        # /products/{PRODUCT_ID}/items
        #    ↓
        # ITEM REAL
        # ----------------------------------------------------

        itens, item_status, raw = (
            obter_itens_do_produto(
                product_id
            )
        )

        if item_status != 200:
            continue

        for item_raw in itens:

            item = (
                normalizar_item_produto(
                    item_raw
                )
            )

            if not item:
                continue

            item_id = item[
                "item_id"
            ]

            if item_id in itens_processados:
                continue

            itens_processados.add(
                item_id
            )

            stats[
                "itens_encontrados"
            ] += 1

            price = item.get(
                "price"
            )

            original_price = item.get(
                "original_price"
            )

            if isinstance(
                price,
                dict
            ):

                price = (
                    price.get(
                        "amount"
                    )
                    or
                    price.get(
                        "value"
                    )
                )

            if isinstance(
                original_price,
                dict
            ):

                original_price = (
                    original_price.get(
                        "amount"
                    )
                    or
                    original_price.get(
                        "value"
                    )
                )

            if price is None:
                continue

            try:

                price = float(
                    price
                )

            except Exception:

                continue

            if original_price is not None:

                try:

                    original_price = float(
                        original_price
                    )

                except Exception:

                    original_price = None

            desconto = (
                calcular_desconto(
                    price,
                    original_price
                )
            )

            stats[
                "produtos_com_preco"
            ] += 1

            if desconto < desconto_minimo:
                continue

            oferta = {

                "product_id":
                    product_id,

                "item_id":
                    item_id,

                "title":
                    title,

                "permalink":
                    (
                        item.get(
                            "permalink"
                        )
                        or
                        permalink
                    ),

                "price":
                    price,

                "original_price":
                    original_price,

                "discount":
                    desconto,

                "seller_id":
                    item.get(
                        "seller_id"
                    ),

                "image":
                    image,

                "category_id":
                    base[
                        "category_id"
                    ],

                "category_name":
                    base[
                        "category_name"
                    ]
            }

            ofertas.append(
                oferta
            )

            salvar_oferta(
                oferta
            )

    stats[
        "ofertas"
    ] = len(
        ofertas
    )

    return {

        "stats":
            stats,

        "categorias":
            categorias,

        "ofertas":
            ofertas
    }


# ============================================================
# API BUSCAR
# ============================================================

@app.route("/api/buscar")
def api_buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    desconto = request.args.get(
        "desconto",
        "0"
    ).strip()

    if not query:

        return jsonify({

            "erro":
                "Informe uma busca.",

            "exemplo":
                "/api/buscar?q=fone&desconto=10"
        }), 400

    resultado = search_offers(
        query,
        desconto
    )

    return jsonify(
        json_safe(
            resultado
        )
    )


# ============================================================
# TESTE BUSCA
# ============================================================

@app.route(
    "/mercadolivre/teste-busca"
)
def teste_busca():

    query = request.args.get(
        "q",
        "fone"
    ).strip()

    desconto = request.args.get(
        "desconto",
        "10"
    ).strip()

    resultado = search_offers(
        query,
        desconto
    )

    return jsonify(
        json_safe(
            resultado
        )
    )


# ============================================================
# OFERTAS SALVAS
# ============================================================

@app.route("/api/salvos")
def api_salvos():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY id DESC
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

HTML = """
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

    background: #f5f6f8;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    color: #222;
}

.container {

    max-width: 1100px;

    margin: auto;

    padding: 20px;
}

.card {

    background: white;

    border-radius: 16px;

    padding: 20px;

    margin-bottom: 20px;

    box-shadow:
        0 5px 20px
        rgba(0,0,0,.07);
}

h1 {

    margin-top: 0;
}

input,
button {

    width: 100%;

    padding: 13px;

    border-radius: 10px;

    border: 1px solid #ddd;

    font-size: 16px;
}

input {

    margin-bottom: 10px;
}

button {

    background: #3483fa;

    color: white;

    border: 0;

    cursor: pointer;

    margin-top: 5px;
}

button:hover {

    opacity: .9;
}

.login {

    background: #ffe600;

    color: #222;
}

.logout {

    background: #555;
}

.stats {

    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(150px, 1fr)
        );

    gap: 10px;
}

.stat {

    background: #f5f5f5;

    border-radius: 12px;

    padding: 15px;
}

.stat strong {

    display: block;

    font-size: 24px;

    margin-top: 5px;
}

.produto {

    display: flex;

    gap: 15px;

    padding: 15px 0;

    border-bottom:
        1px solid #eee;
}

.produto img {

    width: 100px;

    height: 100px;

    object-fit: contain;

    border-radius: 10px;

    background: #fafafa;
}

.preco {

    font-size: 22px;

    font-weight: bold;
}

.desconto {

    color: #00a650;

    font-weight: bold;
}

a {

    color: #3483fa;

    text-decoration: none;
}

.small {

    color: #666;

    font-size: 13px;
}

.status {

    padding: 10px;

    border-radius: 10px;

    background: #f2f2f2;

    margin-bottom: 10px;
}

</style>

</head>

<body>

<div class="container">


<div class="card">

<h1>
🛒 Caçador de Ofertas
</h1>

<p>
Encontre produtos e possíveis ofertas do Mercado Livre.
</p>

{% if conectado %}

<div class="status">

🟢 Mercado Livre conectado

{% if nickname %}

<br>

<strong>
{{ nickname }}
</strong>

{% endif %}

</div>

<a href="/mercadolivre/logout">

<button class="logout">
Desconectar Mercado Livre
</button>

</a>

{% else %}

<a href="/mercadolivre/login">

<button class="login">
🔗 Conectar Mercado Livre
</button>

</a>

{% endif %}

</div>


<div class="card">

<h2>
🔎 Pesquisar
</h2>

<form
    action="/buscar"
    method="get"
>

<input
    name="q"
    placeholder="Ex: fone, celular, televisão..."
    required
>

<input
    name="desconto"
    type="number"
    min="0"
    value="10"
    placeholder="Desconto mínimo %"
>

<button type="submit">
Buscar ofertas
</button>

</form>

</div>


{% if resultado %}

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

Produtos consultados

<strong>
{{ resultado.stats.produtos_consultados }}
</strong>

</div>


<div class="stat">

Itens encontrados

<strong>
{{ resultado.stats.itens_encontrados }}
</strong>

</div>


<div class="stat">

Com preço

<strong>
{{ resultado.stats.produtos_com_preco }}
</strong>

</div>


<div class="stat">

Ofertas

<strong>
{{ resultado.stats.ofertas }}
</strong>

</div>

</div>

</div>


<div class="card">

<h2>
🔥 Ofertas encontradas
</h2>

{% if resultado.ofertas %}

{% for oferta in resultado.ofertas %}

<div class="produto">

{% if oferta.image %}

<img
    src="{{ oferta.image }}"
>

{% endif %}

<div>

<strong>
{{ oferta.title }}
</strong>

<br><br>

<div class="preco">

R$
{{ "%.2f"|format(oferta.price)|replace(".", ",") }}

</div>

{% if oferta.original_price %}

<div>

De:
R$
{{ "%.2f"|format(oferta.original_price)|replace(".", ",") }}

</div>

{% endif %}

{% if oferta.discount > 0 %}

<div class="desconto">

🔥 {{ oferta.discount }}% OFF

</div>

{% endif %}

<br>

<a
    href="{{ oferta.permalink }}"
    target="_blank"
>

Ver produto

</a>

</div>

</div>

{% endfor %}

{% else %}

<p>
Nenhuma oferta encontrada com esse desconto.
</p>

{% endif %}

</div>

{% endif %}


<div class="card">

<h3>
🧪 Testes técnicos
</h3>

<p class="small">
Produto usado no teste:
MLB58793248
</p>

<a
href="/mercadolivre/teste-produto-itens?product_id=MLB58793248"
target="_blank"
>

Testar Produto → Itens

</a>

<br><br>

<a
href="/mercadolivre/diagnostico"
target="_blank"
>

Diagnóstico Mercado Livre

</a>

<br><br>

<a
href="/mercadolivre/teste-produto?product_id=MLB58793248"
target="_blank"
>

Testar Produto

</a>

</div>


</div>

</body>

</html>
"""


# ============================================================
# ROTA PRINCIPAL
# ============================================================

@app.route("/")
def index():

    tokens = obter_tokens()

    conectado = bool(
        get_access_token()
    )

    nickname = None

    if tokens:

        nickname = tokens.get(
            "nickname"
        )

    return render_template_string(

        HTML,

        resultado=None,

        conectado=conectado,

        nickname=nickname
    )


# ============================================================
# BUSCA PELA INTERFACE
# ============================================================

@app.route("/buscar")
def buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    desconto = request.args.get(
        "desconto",
        "10"
    ).strip()

    resultado = None

    if query:

        resultado = search_offers(
            query,
            desconto
        )

    tokens = obter_tokens()

    conectado = bool(
        get_access_token()
    )

    nickname = None

    if tokens:

        nickname = tokens.get(
            "nickname"
        )

    return render_template_string(

        HTML,

        resultado=resultado,

        conectado=conectado,

        nickname=nickname
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

        "mercado_livre_configurado":
            bool(ML_CLIENT_ID),

        "mercado_livre_conectado":
            bool(get_access_token())
    })


# ============================================================
# 404
# ============================================================

@app.errorhandler(404)
def pagina_nao_encontrada(error):

    return jsonify({

        "erro":
            "Rota não encontrada.",

        "rota":
            request.path
    }), 404


# ============================================================
# 500
# ============================================================

@app.errorhandler(500)
def erro_interno(error):

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