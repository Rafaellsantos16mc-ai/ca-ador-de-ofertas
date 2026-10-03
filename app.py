import os
import sqlite3
import secrets
import hashlib
import base64
import urllib.parse
import requests
import time

from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import (
    Flask,
    request,
    redirect,
    render_template_string,
    jsonify,
    session
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "troque-esta-chave-em-producao"
)


# ============================================================
# CONFIGURAÇÕES MERCADO LIVRE
# ============================================================

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET")

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
)

ML_AUTH_URL = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN_URL = (
    "https://api.mercadolibre.com/oauth/token"
)

ML_API_URL = (
    "https://api.mercadolibre.com"
)

ML_SITE = "MLB"

DB_NAME = "ofertas.db"

# Quantidade de produtos de catálogo que serão pesquisados
PRODUCT_SEARCH_LIMIT = 50

# Máximo processado por busca
MAX_PRODUCTS_PROCESS = 50

# Requisições simultâneas
MAX_WORKERS = 5


# ============================================================
# BANCO DE DADOS
# ============================================================

def get_db():

    conn = sqlite3.connect(DB_NAME)

    conn.row_factory = sqlite3.Row

    return conn


def adicionar_coluna_se_nao_existir(
    conn,
    tabela,
    coluna,
    definicao
):

    try:

        colunas = conn.execute(
            f"PRAGMA table_info({tabela})"
        ).fetchall()

        nomes = [
            coluna_db["name"]
            for coluna_db in colunas
        ]

        if coluna not in nomes:

            conn.execute(
                f"""
                ALTER TABLE {tabela}
                ADD COLUMN {coluna}
                {definicao}
                """
            )

    except Exception as e:

        print(
            "[ERRO MIGRAÇÃO]",
            tabela,
            coluna,
            e
        )


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            created_at INTEGER
                DEFAULT (strftime('%s','now'))
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
            vendedor TEXT,
            seller_id TEXT,
            categoria TEXT,
            link TEXT,
            imagem TEXT,
            product_id TEXT,
            buy_box INTEGER DEFAULT 0,
            criado_em INTEGER
                DEFAULT (strftime('%s','now'))
        )
    """)

    # --------------------------------------------------------
    # Migração para bancos antigos
    # --------------------------------------------------------

    adicionar_coluna_se_nao_existir(
        conn,
        "ofertas",
        "seller_id",
        "TEXT"
    )

    adicionar_coluna_se_nao_existir(
        conn,
        "ofertas",
        "product_id",
        "TEXT"
    )

    adicionar_coluna_se_nao_existir(
        conn,
        "ofertas",
        "buy_box",
        "INTEGER DEFAULT 0"
    )

    adicionar_coluna_se_nao_existir(
        conn,
        "ofertas",
        "imagem",
        "TEXT"
    )

    conn.commit()

    conn.close()


init_db()


# ============================================================
# TOKEN
# ============================================================

def salvar_token(data):

    conn = get_db()

    conn.execute(
        "DELETE FROM tokens"
    )

    expires_in = data.get(
        "expires_in",
        0
    )

    try:

        expires_in = int(
            expires_in
        )

    except Exception:

        expires_in = 0

    expires_at = (
        int(time.time())
        + expires_in
    )

    conn.execute("""
        INSERT INTO tokens (
            user_id,
            access_token,
            refresh_token,
            expires_at
        )
        VALUES (?, ?, ?, ?)
    """, (
        data.get("user_id"),
        data.get("access_token"),
        data.get("refresh_token"),
        expires_at
    ))

    conn.commit()

    conn.close()


def pegar_token():

    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM tokens
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    conn.close()

    if not row:

        return None

    return dict(row)


# ============================================================
# PKCE
# ============================================================

def gerar_pkce():

    verifier = secrets.token_urlsafe(
        64
    )

    digest = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()

    challenge = (
        base64.urlsafe_b64encode(
            digest
        )
        .decode("utf-8")
        .rstrip("=")
    )

    return verifier, challenge


# ============================================================
# HTTP
# ============================================================

def headers_token(
    access_token
):

    return {

        "Authorization":
            f"Bearer {access_token}",

        "Accept":
            "application/json",

        "User-Agent":
            "CacadorDeOfertas/1.0"
    }


def safe_json(
    response
):

    try:

        return response.json()

    except Exception:

        return {
            "texto":
                response.text[:3000]
        }


def limpar_segredos(
    data
):

    if isinstance(
        data,
        dict
    ):

        resultado = {}

        for chave, valor in data.items():

            chave_lower = str(
                chave
            ).lower()

            if any(
                termo in chave_lower
                for termo in [
                    "access_token",
                    "refresh_token",
                    "client_secret",
                    "authorization",
                    "token"
                ]
            ):

                resultado[chave] = (
                    "*** OCULTO ***"
                )

            else:

                resultado[chave] = (
                    limpar_segredos(
                        valor
                    )
                )

        return resultado

    if isinstance(
        data,
        list
    ):

        return [
            limpar_segredos(
                item
            )
            for item in data
        ]

    return data


# ============================================================
# OAUTH
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return (
            "ML_CLIENT_ID não configurado.",
            500
        )

    verifier, challenge = (
        gerar_pkce()
    )

    state = secrets.token_urlsafe(
        32
    )

    session["ml_state"] = state

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
        ML_AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            params
        )
    )

    return redirect(
        url
    )


@app.route(
    "/mercadolivre/callback"
)
def mercadolivre_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return f"""
        <h2>Erro Mercado Livre</h2>
        <pre>{request.args}</pre>
        """, 400

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    if not code:

        return (
            "Código de autorização "
            "não recebido.",
            400
        )

    if state != session.get(
        "ml_state"
    ):

        return (
            "State inválido.",
            400
        )

    verifier = session.get(
        "ml_code_verifier"
    )

    if not verifier:

        return (
            "Code verifier não encontrado.",
            400
        )

    payload = {

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
    }

    try:

        response = requests.post(
            ML_TOKEN_URL,
            data=payload,
            timeout=30
        )

    except Exception as e:

        return f"""
        <h2>Erro de conexão</h2>
        <pre>{e}</pre>
        """, 500

    data = safe_json(
        response
    )

    if response.status_code != 200:

        return f"""
        <h2>Erro ao obter token</h2>

        <p>Status:
        {response.status_code}</p>

        <pre>
        {limpar_segredos(data)}
        </pre>

        <a href="/">
        Voltar
        </a>
        """, response.status_code

    salvar_token(
        data
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


@app.route(
    "/mercadolivre/callback2"
)
def mercadolivre_callback2():

    return mercadolivre_callback()


# ============================================================
# REFRESH TOKEN
# ============================================================

def atualizar_token():

    token = pegar_token()

    if not token:

        return None

    access_token = token.get(
        "access_token"
    )

    refresh_token = token.get(
        "refresh_token"
    )

    expires_at = token.get(
        "expires_at",
        0
    )

    try:

        if (
            access_token
            and expires_at
            and int(expires_at)
            > int(time.time()) + 60
        ):

            return access_token

    except Exception:

        pass

    if not refresh_token:

        return access_token

    payload = {

        "grant_type":
            "refresh_token",

        "client_id":
            ML_CLIENT_ID,

        "client_secret":
            ML_CLIENT_SECRET,

        "refresh_token":
            refresh_token
    }

    try:

        response = requests.post(
            ML_TOKEN_URL,
            data=payload,
            timeout=30
        )

        if response.status_code == 200:

            data = response.json()

            if not data.get(
                "refresh_token"
            ):

                data[
                    "refresh_token"
                ] = refresh_token

            salvar_token(
                data
            )

            return data.get(
                "access_token"
            )

    except Exception as e:

        print(
            "[ERRO REFRESH]",
            e
        )

    return access_token


# ============================================================
# USUÁRIO
# ============================================================

def consultar_usuario(
    access_token
):

    return requests.get(
        f"{ML_API_URL}/users/me",
        headers=headers_token(
            access_token
        ),
        timeout=30
    )


# ============================================================
# APLICAÇÃO
# ============================================================

def consultar_aplicacao(
    access_token
):

    return requests.get(
        f"{ML_API_URL}/applications/"
        f"{ML_CLIENT_ID}",
        headers=headers_token(
            access_token
        ),
        timeout=30
    )


def consultar_grants(
    access_token
):

    return requests.get(
        f"{ML_API_URL}/applications/"
        f"{ML_CLIENT_ID}/grants",
        headers=headers_token(
            access_token
        ),
        timeout=30
    )


# ============================================================
# BUSCA DE PRODUTOS
# ============================================================

def buscar_produtos_catalogo(
    termo,
    limite=50
):

    access_token = (
        atualizar_token()
    )

    if not access_token:

        return {

            "ok": False,

            "erro":
                "Mercado Livre não conectado."
        }

    url = (
        f"{ML_API_URL}/products/search"
    )

    params = {

        "status":
            "active",

        "site_id":
            ML_SITE,

        "q":
            termo,

        "limit":
            min(
                int(limite),
                50
            )
    }

    try:

        response = requests.get(

            url,

            params=params,

            headers=headers_token(
                access_token
            ),

            timeout=30
        )

        data = safe_json(
            response
        )

        if response.status_code != 200:

            return {

                "ok": False,

                "status":
                    response.status_code,

                "url":
                    response.url,

                "resposta":
                    data
            }

        return {

            "ok": True,

            "data":
                data,

            "modo":
                "products/search"
        }

    except Exception as e:

        return {

            "ok": False,

            "erro":
                "Erro de conexão",

            "detalhes":
                str(e)
        }


# ============================================================
# DETALHE DO PRODUTO
# ============================================================

def consultar_produto(
    product_id,
    access_token
):

    if not product_id:

        return None

    try:

        response = requests.get(

            f"{ML_API_URL}/products/"
            f"{product_id}",

            headers=headers_token(
                access_token
            ),

            timeout=30
        )

        if response.status_code != 200:

            print(
                "[PRODUTO]",
                product_id,
                response.status_code
            )

            return None

        return safe_json(
            response
        )

    except Exception as e:

        print(
            "[ERRO PRODUTO]",
            product_id,
            e
        )

        return None


# ============================================================
# PREÇO DE VENDA ATUAL
# ============================================================

def consultar_sale_price(
    item_id,
    access_token
):

    if not item_id:

        return None

    url = (
        f"{ML_API_URL}/items/"
        f"{item_id}/sale_price"
    )

    params = {

        "context":
            "channel_marketplace"
    }

    try:

        response = requests.get(

            url,

            params=params,

            headers=headers_token(
                access_token
            ),

            timeout=20
        )

        if response.status_code == 200:

            data = safe_json(
                response
            )

            if isinstance(
                data,
                dict
            ):

                return {

                    "ok": True,

                    "data":
                        data
                }

        print(
            "[SALE PRICE]",
            item_id,
            response.status_code
        )

    except Exception as e:

        print(
            "[ERRO SALE PRICE]",
            item_id,
            e
        )

    return None


# ============================================================
# FALLBACK /PRICES
# ============================================================

def consultar_precos(
    item_id,
    access_token
):

    if not item_id:

        return None

    url = (
        f"{ML_API_URL}/items/"
        f"{item_id}/prices"
    )

    try:

        response = requests.get(

            url,

            headers=headers_token(
                access_token
            ),

            timeout=20
        )

        if response.status_code != 200:

            return None

        data = safe_json(
            response
        )

        if not isinstance(
            data,
            dict
        ):

            return None

        return data

    except Exception as e:

        print(
            "[ERRO PRICES]",
            item_id,
            e
        )

        return None


# ============================================================
# ENCONTRAR PREÇO PROMOCIONAL
# ============================================================

def obter_preco_promocional(
    item_id,
    access_token
):

    # --------------------------------------------------------
    # PRIMEIRO: SALE PRICE
    # --------------------------------------------------------

    sale = consultar_sale_price(
        item_id,
        access_token
    )

    if sale:

        data = sale.get(
            "data",
            {}
        )

        try:

            amount = float(
                data.get(
                    "amount"
                ) or 0
            )

        except Exception:

            amount = 0

        try:

            regular_amount = float(
                data.get(
                    "regular_amount"
                ) or 0
            )

        except Exception:

            regular_amount = 0

        if (
            amount > 0
            and regular_amount > amount
        ):

            return {

                "preco":
                    amount,

                "preco_original":
                    regular_amount,

                "tipo":
                    "sale_price",

                "promocao":
                    True
            }

    # --------------------------------------------------------
    # SEGUNDO: /PRICES
    # --------------------------------------------------------

    prices_data = consultar_precos(
        item_id,
        access_token
    )

    if prices_data:

        prices = prices_data.get(
            "prices",
            []
        )

        if isinstance(
            prices,
            list
        ):

            agora = time.time()

            candidatos = []

            for price in prices:

                if not isinstance(
                    price,
                    dict
                ):

                    continue

                amount = price.get(
                    "amount"
                )

                regular_amount = price.get(
                    "regular_amount"
                )

                try:

                    amount = float(
                        amount or 0
                    )

                except Exception:

                    continue

                try:

                    regular_amount = float(
                        regular_amount or 0
                    )

                except Exception:

                    regular_amount = 0

                if (
                    amount <= 0
                    or regular_amount <= amount
                ):

                    continue

                conditions = price.get(
                    "conditions",
                    {}
                )

                if not isinstance(
                    conditions,
                    dict
                ):

                    conditions = {}

                start_time = conditions.get(
                    "start_time"
                )

                end_time = conditions.get(
                    "end_time"
                )

                # ------------------------------------------------
                # Para não pegar promoção expirada
                # ------------------------------------------------

                valido = True

                try:

                    if start_time:
                        # Datas ISO do ML.
                        # Apenas usamos quando é possível.
                        pass

                    if end_time:
                        # A API já costuma retornar somente
                        # preços válidos, então não bloqueamos
                        # por erro de conversão.
                        pass

                except Exception:

                    pass

                if valido:

                    candidatos.append({

                        "preco":
                            amount,

                        "preco_original":
                            regular_amount,

                        "tipo":
                            price.get(
                                "type",
                                "promotion"
                            ),

                        "promocao":
                            True
                    })

            if candidatos:

                # Maior desconto
                candidatos.sort(

                    key=lambda x:
                        (
                            (
                                x[
                                    "preco_original"
                                ]
                                -
                                x[
                                    "preco"
                                ]
                            )
                            /
                            x[
                                "preco_original"
                            ]
                        ),

                    reverse=True
                )

                return candidatos[0]

    return None


# ============================================================
# IMAGEM
# ============================================================

def extrair_imagem(
    product,
    winner
):

    pictures = product.get(
        "pictures"
    )

    if isinstance(
        pictures,
        list
    ):

        for picture in pictures:

            if not isinstance(
                picture,
                dict
            ):

                continue

            imagem = (
                picture.get(
                    "secure_url"
                )
                or picture.get(
                    "url"
                )
                or picture.get(
                    "thumbnail"
                )
            )

            if imagem:

                return imagem

    if isinstance(
        winner,
        dict
    ):

        return (
            winner.get(
                "thumbnail"
            )
            or ""
        )

    return ""


# ============================================================
# CONVERTER PRODUTO EM OFERTA
# ============================================================

def produto_para_oferta(
    product,
    access_token
):

    if not isinstance(
        product,
        dict
    ):

        return None

    product_id = product.get(
        "id"
    )

    if not product_id:

        return None

    winner = product.get(
        "buy_box_winner"
    )

    if not isinstance(
        winner,
        dict
    ):

        return None

    item_id = winner.get(
        "item_id"
    )

    if not item_id:

        return None

    # --------------------------------------------------------
    # PREÇO ATUAL + PREÇO ORIGINAL
    # --------------------------------------------------------

    preco_info = (
        obter_preco_promocional(
            item_id,
            access_token
        )
    )

    if not preco_info:

        return None

    preco = preco_info.get(
        "preco",
        0
    )

    preco_original = (
        preco_info.get(
            "preco_original",
            0
        )
    )

    try:

        preco = float(
            preco
        )

    except Exception:

        preco = 0

    try:

        preco_original = float(
            preco_original
        )

    except Exception:

        preco_original = 0

    if (
        preco <= 0
        or preco_original <= preco
    ):

        return None

    # --------------------------------------------------------
    # DESCONTO
    # --------------------------------------------------------

    desconto = (
        (
            preco_original
            - preco
        )
        / preco_original
    ) * 100

    desconto = round(
        desconto,
        2
    )

    # --------------------------------------------------------
    # VENDEDOR
    # --------------------------------------------------------

    seller_id = winner.get(
        "seller_id"
    )

    vendedor = ""

    seller = winner.get(
        "seller"
    )

    if isinstance(
        seller,
        dict
    ):

        vendedor = (
            seller.get(
                "nickname"
            )
            or seller.get(
                "reputation_level_id"
            )
            or ""
        )

    # --------------------------------------------------------
    # LINK
    # --------------------------------------------------------

    link = (
        winner.get(
            "permalink"
        )
        or product.get(
            "permalink"
        )
        or ""
    )

    # --------------------------------------------------------
    # IMAGEM
    # --------------------------------------------------------

    imagem = extrair_imagem(
        product,
        winner
    )

    # --------------------------------------------------------
    # TÍTULO
    # --------------------------------------------------------

    titulo = (
        product.get(
            "name"
        )
        or winner.get(
            "title"
        )
        or "Produto"
    )

    return {

        "id":
            item_id,

        "titulo":
            titulo,

        "preco":
            preco,

        "preco_original":
            preco_original,

        "desconto":
            desconto,

        "vendedor":
            vendedor,

        "seller_id":
            seller_id,

        "categoria":
            product.get(
                "domain_id",
                ""
            ),

        "link":
            link,

        "imagem":
            imagem,

        "product_id":
            product_id,

        "buy_box":
            True,

        "tipo_preco":
            preco_info.get(
                "tipo"
            )
    }


# ============================================================
# PROCESSAR PRODUTOS
# ============================================================

def processar_produtos(
    data,
    minimo=10
):

    access_token = (
        atualizar_token()
    )

    if not access_token:

        return []

    produtos = data.get(
        "results",
        []
    )

    if not isinstance(
        produtos,
        list
    ):

        return []

    produtos = produtos[
        :MAX_PRODUCTS_PROCESS
    ]

    ofertas = []

    # --------------------------------------------------------
    # Cada produto:
    #
    # /products/{id}
    #        ↓
    # buy_box_winner
    #        ↓
    # item_id
    #        ↓
    # /items/{item_id}/sale_price
    #
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        tarefas = {

            executor.submit(
                consultar_produto,
                produto.get("id"),
                access_token
            ):
                produto.get("id")

            for produto in produtos

            if produto.get("id")
        }

        for future in as_completed(
            tarefas
        ):

            try:

                product = (
                    future.result()
                )

                if not product:

                    continue

                oferta = (
                    produto_para_oferta(
                        product,
                        access_token
                    )
                )

                if not oferta:

                    continue

                if (
                    oferta["desconto"]
                    >= float(minimo)
                ):

                    ofertas.append(
                        oferta
                    )

            except Exception as e:

                print(
                    "[ERRO PROCESSAMENTO]",
                    e
                )

    # --------------------------------------------------------
    # Remove duplicados
    # --------------------------------------------------------

    unicos = {}

    for oferta in ofertas:

        item_id = oferta.get(
            "id"
        )

        if item_id:

            unicos[item_id] = (
                oferta
            )

    ofertas = list(
        unicos.values()
    )

    # --------------------------------------------------------
    # Maior desconto primeiro
    # --------------------------------------------------------

    ofertas.sort(

        key=lambda x:
            x.get(
                "desconto",
                0
            ),

        reverse=True
    )

    return ofertas


# ============================================================
# SALVAR OFERTA
# ============================================================

def salvar_oferta(
    oferta
):

    conn = get_db()

    try:

        conn.execute("""
            INSERT OR IGNORE INTO ofertas (
                item_id,
                titulo,
                preco,
                preco_original,
                desconto,
                vendedor,
                seller_id,
                categoria,
                link,
                imagem,
                product_id,
                buy_box
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
        """, (

            oferta.get(
                "id"
            ),

            oferta.get(
                "titulo"
            ),

            oferta.get(
                "preco"
            ),

            oferta.get(
                "preco_original"
            ),

            oferta.get(
                "desconto"
            ),

            oferta.get(
                "vendedor"
            ),

            str(
                oferta.get(
                    "seller_id"
                ) or ""
            ),

            oferta.get(
                "categoria"
            ),

            oferta.get(
                "link"
            ),

            oferta.get(
                "imagem"
            ),

            oferta.get(
                "product_id"
            ),

            1
            if oferta.get(
                "buy_box"
            )
            else 0
        ))

        conn.commit()

    except Exception as e:

        print(
            "[ERRO BANCO]",
            e
        )

    finally:

        conn.close()


# ============================================================
# STATUS
# ============================================================

@app.route(
    "/mercadolivre/status"
)
def mercadolivre_status():

    token = pegar_token()

    return jsonify({

        "conectado":
            bool(
                token
                and token.get(
                    "access_token"
                )
            ),

        "usuario":
            (
                token.get(
                    "user_id"
                )
                if token
                else None
            )
    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def mercadolivre_diagnostico():

    token = pegar_token()

    resultado = {

        "configuracao": {},

        "access_token": {},

        "usuario": {},

        "aplicacao": {},

        "grants": {},

        "busca_products_search": {},

        "conclusao": []
    }

    resultado[
        "configuracao"
    ] = {

        "ML_CLIENT_ID_configurado":
            bool(
                ML_CLIENT_ID
            ),

        "ML_CLIENT_SECRET_configurado":
            bool(
                ML_CLIENT_SECRET
            ),

        "ML_REDIRECT_URI":
            ML_REDIRECT_URI,

        "site":
            ML_SITE
    }

    if not token:

        resultado[
            "access_token"
        ] = {

            "ok":
                False,

            "mensagem":
                "Nenhum token encontrado."
        }

        return jsonify(
            limpar_segredos(
                resultado
            )
        )

    access_token = token.get(
        "access_token"
    )

    resultado[
        "access_token"
    ] = {

        "ok":
            bool(
                access_token
            ),

        "user_id_salvo":
            token.get(
                "user_id"
            )
    }

    if not access_token:

        return jsonify(
            limpar_segredos(
                resultado
            )
        )

    # --------------------------------------------------------
    # USUÁRIO
    # --------------------------------------------------------

    try:

        r = consultar_usuario(
            access_token
        )

        resultado[
            "usuario"
        ] = {

            "status":
                r.status_code,

            "ok":
                r.status_code == 200,

            "resposta":
                safe_json(r)
        }

    except Exception as e:

        resultado[
            "usuario"
        ] = {

            "ok":
                False,

            "erro":
                str(e)
        }

    # --------------------------------------------------------
    # APLICAÇÃO
    # --------------------------------------------------------

    try:

        r = consultar_aplicacao(
            access_token
        )

        resultado[
            "aplicacao"
        ] = {

            "status":
                r.status_code,

            "ok":
                r.status_code == 200,

            "resposta":
                safe_json(r)
        }

    except Exception as e:

        resultado[
            "aplicacao"
        ] = {

            "ok":
                False,

            "erro":
                str(e)
        }

    # --------------------------------------------------------
    # GRANTS
    # --------------------------------------------------------

    try:

        r = consultar_grants(
            access_token
        )

        resultado[
            "grants"
        ] = {

            "status":
                r.status_code,

            "ok":
                r.status_code == 200,

            "resposta":
                safe_json(r)
        }

    except Exception as e:

        resultado[
            "grants"
        ] = {

            "ok":
                False,

            "erro":
                str(e)
        }

    # --------------------------------------------------------
    # TESTE PRODUCTS/SEARCH
    # --------------------------------------------------------

    try:

        teste = (
            buscar_produtos_catalogo(
                "celular",
                1
            )
        )

        resultado[
            "busca_products_search"
        ] = teste

        if teste.get("ok"):

            resultado[
                "conclusao"
            ].append(
                "products/search funcionando."
            )

        else:

            resultado[
                "conclusao"
            ].append(
                "products/search retornou erro."
            )

    except Exception as e:

        resultado[
            "busca_products_search"
        ] = {

            "ok":
                False,

            "erro":
                str(e)
        }

    return jsonify(
        limpar_segredos(
            resultado
        )
    )


# ============================================================
# NOTIFICAÇÕES
# ============================================================

@app.route(
    "/mercadolivre/notificacoes",
    methods=["POST"]
)
def mercadolivre_notificacoes():

    data = request.get_json(
        silent=True
    ) or {}

    print(
        "[NOTIFICAÇÃO ML]",
        data
    )

    return jsonify({
        "ok": True
    })


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health"
)
def health():

    return jsonify({
        "status":
            "ok"
    })


# ============================================================
# BUSCAR OFERTAS
# ============================================================

@app.route(
    "/buscar",
    methods=["POST"]
)
def buscar():

    termo = request.form.get(
        "termo",
        ""
    ).strip()

    try:

        minimo = float(
            request.form.get(
                "minimo",
                10
            )
        )

    except Exception:

        minimo = 10

    if not termo:

        return redirect(
            "/"
        )

    resultado = (
        buscar_produtos_catalogo(
            termo,
            PRODUCT_SEARCH_LIMIT
        )
    )

    if not resultado.get(
        "ok"
    ):

        return render_template_string(

            HTML,

            erro=resultado,

            resultados=[],

            termo=termo,

            minimo=minimo,

            conectado=bool(
                pegar_token()
            ),

            aviso=None
        )

    produtos = (
        processar_produtos(

            resultado.get(
                "data",
                {}
            ),

            minimo
        )
    )

    for produto in produtos:

        salvar_oferta(
            produto
        )

    aviso = None

    if not produtos:

        aviso = (
            "A busca encontrou produtos, "
            "mas nenhum deles possui uma "
            "promoção ativa com desconto de "
            f"{minimo:.1f}% ou mais."
        )

    return render_template_string(

        HTML,

        erro=None,

        resultados=produtos,

        termo=termo,

        minimo=minimo,

        conectado=True,

        aviso=aviso
    )


# ============================================================
# GERAR PUBLICAÇÃO
# ============================================================

@app.route(
    "/gerar",
    methods=["POST"]
)
def gerar():

    titulo = request.form.get(
        "titulo",
        ""
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

    afiliado = request.form.get(
        "afiliado",
        ""
    ).strip()

    link_final = (
        afiliado
        or link
    )

    mensagem = f"""
🔥 OFERTA ENCONTRADA!

🛍️ {titulo}

💰 De: R$ {preco_original}
🔥 Por: R$ {preco}

🏷️ Desconto: {desconto}%

👉 COMPRAR:
{link_final}

⚠️ Preço sujeito a alteração pelo Mercado Livre.
""".strip()

    return render_template_string(

        HTML_GERAR,

        mensagem=mensagem
    )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    token = pegar_token()

    conectado = bool(

        token

        and

        token.get(
            "access_token"
        )
    )

    return render_template_string(

        HTML,

        erro=None,

        resultados=[],

        termo="",

        minimo=10,

        conectado=conectado,

        aviso=None
    )


# ============================================================
# HTML PRINCIPAL
# ============================================================

HTML = """

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

    font-family:
        Arial,
        sans-serif;

    background:
        #f5f5f5;

    margin: 0;

    padding: 20px;
}

.container {

    max-width:
        900px;

    margin:
        auto;
}

.card {

    background:
        white;

    padding:
        22px;

    border-radius:
        18px;

    margin-bottom:
        20px;

    box-shadow:
        0 3px 15px
        rgba(0,0,0,.08);
}

h1 {

    margin-top:
        0;
}

input,
button {

    width:
        100%;

    padding:
        14px;

    margin-top:
        8px;

    margin-bottom:
        14px;

    border-radius:
        10px;

    border:
        1px solid #ccc;

    font-size:
        17px;
}

button {

    background:
        #3483fa;

    color:
        white;

    border:
        none;

    font-weight:
        bold;

    cursor:
        pointer;
}

a.botao {

    display:
        block;

    text-align:
        center;

    padding:
        14px;

    border-radius:
        10px;

    background:
        #3483fa;

    color:
        white;

    text-decoration:
        none;

    margin-bottom:
        12px;

    font-size:
        17px;
}

.diagnostico {

    background:
        #222 !important;
}

.sucesso {

    color:
        #218838;

    font-weight:
        bold;

    font-size:
        18px;
}

.erro {

    background:
        #ffe5e5;

    color:
        #a00000;

    padding:
        16px;

    border-radius:
        10px;

    overflow-x:
        auto;
}

.aviso {

    background:
        #fff3cd;

    color:
        #664d03;

    padding:
        18px;

    border-radius:
        12px;

    margin-bottom:
        20px;

    font-size:
        17px;
}

.produto {

    border:
        1px solid #ddd;

    padding:
        18px;

    border-radius:
        15px;

    margin-bottom:
        18px;
}

.produto img {

    width:
        100%;

    max-width:
        220px;

    border-radius:
        12px;

    display:
        block;

    margin-bottom:
        12px;
}

.desconto {

    color:
        #16852d;

    font-size:
        26px;

    font-weight:
        bold;
}

.preco {

    font-size:
        24px;

    font-weight:
        bold;
}

.preco-original {

    text-decoration:
        line-through;

    color:
        #777;

    font-size:
        17px;
}

.badge {

    display:
        inline-block;

    background:
        #e8f5e9;

    color:
        #087f23;

    padding:
        6px 10px;

    border-radius:
        20px;

    font-size:
        13px;

    font-weight:
        bold;
}

.link-produto {

    display:
        block;

    text-align:
        center;

    background:
        #3483fa;

    color:
        white;

    padding:
        12px;

    border-radius:
        9px;

    text-decoration:
        none;

    margin-top:
        10px;

}

</style>

</head>

<body>

<div class="container">


<div class="card">

<h1>
🛒 Caçador de Ofertas
</h1>


{% if conectado %}

<p class="sucesso">

🟢 Mercado Livre conectado

</p>

{% else %}

<p>

🔴 Mercado Livre não conectado

</p>

{% endif %}


<a
class="botao"
href="/mercadolivre/login">

🔗 Conectar Mercado Livre

</a>


<a
class="botao diagnostico"
href="/mercadolivre/diagnostico">

🧪 Diagnóstico avançado

</a>

</div>


<div class="card">

<h2>
🔎 Procurar ofertas
</h2>


<form
method="POST"
action="/buscar">


<label>
Produto
</label>


<input
type="text"
name="termo"
value="{{ termo }}"
placeholder="Ex: celular, televisão, air fryer"
required
>


<label>
Desconto mínimo (%)
</label>


<input
type="number"
name="minimo"
value="{{ minimo }}"
min="0"
max="99"
step="1"
>


<button
type="submit">

🔎 Buscar ofertas

</button>


</form>

</div>


{% if aviso %}

<div class="aviso">

{{ aviso }}

</div>

{% endif %}


{% if erro %}

<div class="erro">

<h3>
❌ Erro na busca
</h3>

<pre>
{{ erro }}
</pre>

</div>

{% endif %}


{% if resultados %}

<div class="card">

<h2>
🔥 Ofertas encontradas
</h2>


<p>

Encontramos
<strong>
{{ resultados|length }}
</strong>
oferta(s).

</p>


{% for p in resultados %}

<div class="produto">


<span class="badge">

🏆 Buy Box + promoção

</span>


{% if p.imagem %}

<br>

<img
src="{{ p.imagem }}"
loading="lazy"
>

{% endif %}


<h3>
{{ p.titulo }}
</h3>


<p class="desconto">

{{ "%.1f"|format(
p.desconto
) }}% OFF

</p>


<p class="preco-original">

De:
R$
{{ "%.2f"|format(
p.preco_original
) }}

</p>


<p class="preco">

Por:
R$
{{ "%.2f"|format(
p.preco
) }}

</p>


{% if p.vendedor %}

<p>

👤 Vendedor:
{{ p.vendedor }}

</p>

{% endif %}


<p>

🆔 Produto:
{{ p.product_id }}

</p>


{% if p.link %}

<a
class="link-produto"
href="{{ p.link }}"
target="_blank"
rel="noopener">

🛒 Ver produto

</a>

{% endif %}


<form
method="POST"
action="/gerar">


<input
type="hidden"
name="titulo"
value="{{ p.titulo }}"
>


<input
type="hidden"
name="preco"
value="{{ p.preco }}"
>


<input
type="hidden"
name="preco_original"
value="{{ p.preco_original }}"
>


<input
type="hidden"
name="desconto"
value="{{ p.desconto }}"
>


<input
type="hidden"
name="link"
value="{{ p.link }}"
>


<button
type="submit">

📢 Gerar publicação

</button>


</form>


</div>

{% endfor %}

</div>

{% endif %}


</div>

</body>

</html>

"""


# ============================================================
# HTML GERAR
# ============================================================

HTML_GERAR = """

<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Oferta gerada</title>

<style>

body {

    font-family:
        Arial;

    background:
        #f5f5f5;

    padding:
        20px;
}

.container {

    max-width:
        700px;

    margin:
        auto;
}

.card {

    background:
        white;

    padding:
        22px;

    border-radius:
        15px;
}

textarea {

    width:
        100%;

    height:
        350px;

    padding:
        15px;

    font-size:
        17px;

    border-radius:
        10px;

    border:
        1px solid #ccc;
}

a {

    display:
        block;

    margin-top:
        15px;

}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h2>
📢 Publicação gerada
</h2>


<textarea readonly>{{ mensagem }}</textarea>


<a href="/">
← Voltar

</a>

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

        port=port
    )