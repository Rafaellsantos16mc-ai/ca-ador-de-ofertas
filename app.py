import os
import sqlite3
import secrets
import hashlib
import base64
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from flask import (
    Flask,
    request,
    render_template_string,
    redirect,
    session
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "troque-esta-chave-no-railway"
)

DB_FILE = os.getenv(
    "DB_FILE",
    "ofertas.db"
)


# ============================================================
# MERCADO LIVRE
# ============================================================

ML_CLIENT_ID = os.getenv(
    "ML_CLIENT_ID"
)

ML_CLIENT_SECRET = os.getenv(
    "ML_CLIENT_SECRET"
)

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

# Busca de anúncios REAIS
ML_SEARCH_URL = (
    "https://api.mercadolibre.com/sites/MLB/search"
)


# ============================================================
# BANCO DE DADOS
# ============================================================

def init_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY,
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produto_id TEXT,
            item_id TEXT,
            titulo TEXT,
            preco REAL,
            preco_original REAL,
            desconto REAL,
            link TEXT,
            affiliate_link TEXT,
            criado_em TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# TOKEN
# ============================================================

def salvar_tokens(data):

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
        datetime.now(
            timezone.utc
        ).timestamp()
    ) + expires_in

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.execute(
        "DELETE FROM tokens"
    )

    conn.execute("""
        INSERT INTO tokens (
            id,
            access_token,
            refresh_token,
            expires_at
        )
        VALUES (1, ?, ?, ?)
    """, (
        access_token,
        refresh_token,
        expires_at
    ))

    conn.commit()
    conn.close()


def obter_tokens():

    conn = sqlite3.connect(
        DB_FILE
    )

    row = conn.execute("""
        SELECT
            access_token,
            refresh_token,
            expires_at
        FROM tokens
        WHERE id = 1
    """).fetchone()

    conn.close()

    if not row:
        return None

    return {
        "access_token": row[0],
        "refresh_token": row[1],
        "expires_at": row[2]
    }


def renovar_access_token():

    tokens = obter_tokens()

    if not tokens:
        return None

    refresh_token = tokens.get(
        "refresh_token"
    )

    if not refresh_token:
        return None

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

        print(
            "[REFRESH TOKEN]",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "[REFRESH ERRO]",
                response.text[:1000]
            )

            return None

        data = response.json()

        salvar_tokens(
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

        return None


def obter_access_token():

    tokens = obter_tokens()

    if not tokens:
        return None

    agora = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    # Renova 5 minutos antes
    # do vencimento
    if (
        tokens["expires_at"]
        > agora + 300
    ):

        return tokens[
            "access_token"
        ]

    print(
        "[TOKEN] Renovando token..."
    )

    return renovar_access_token()


def headers_ml():

    token = obter_access_token()

    if not token:
        return None

    return {

        "Authorization":
            f"Bearer {token}",

        "Accept":
            "application/json"
    }


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

    state = secrets.token_urlsafe(
        32
    )

    code_verifier = (
        secrets.token_urlsafe(
            64
        )
    )

    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(
                code_verifier.encode()
            ).digest()
        )
        .rstrip(b"=")
        .decode()
    )

    session[
        "oauth_state"
    ] = state

    session[
        "code_verifier"
    ] = code_verifier

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
            code_challenge,

        "code_challenge_method":
            "S256"
    }

    url = (
        ML_AUTH_URL
        + "?"
        + urlencode(params)
    )

    return redirect(
        url
    )


# ============================================================
# CALLBACK OAUTH
# ============================================================

@app.route(
    "/mercadolivre/callback"
)
def mercadolivre_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return f"""
        <h2>Erro no Mercado Livre</h2>
        <p>{error}</p>
        """

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    saved_state = session.get(
        "oauth_state"
    )

    code_verifier = session.get(
        "code_verifier"
    )

    if not code:

        return (
            "Código de autorização não recebido.",
            400
        )

    if (
        not state
        or state != saved_state
    ):

        return (
            "Estado OAuth inválido.",
            400
        )

    if not code_verifier:

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
            code_verifier
    }

    try:

        response = requests.post(
            ML_TOKEN_URL,
            data=payload,
            timeout=30
        )

        print(
            "[OAUTH TOKEN]",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "[OAUTH ERRO]",
                response.text[:1000]
            )

            return (
                "Erro ao obter token: "
                + response.text[:1000],
                400
            )

        data = response.json()

        if not data.get(
            "refresh_token"
        ):

            return (
                "Mercado Livre não retornou "
                "Refresh Token.",
                400
            )

        salvar_tokens(
            data
        )

        session.pop(
            "oauth_state",
            None
        )

        session.pop(
            "code_verifier",
            None
        )

        return redirect(
            "/"
        )

    except Exception as e:

        print(
            "[ERRO OAUTH]",
            e
        )

        return (
            f"Erro OAuth: {e}",
            500
        )


# ============================================================
# CALLBACK 2
# ============================================================

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

    conectado = (
        obter_access_token()
        is not None
    )

    return {
        "conectado":
            conectado
    }


# ============================================================
# NOTIFICAÇÕES
# ============================================================

@app.route(
    "/mercadolivre/notificacoes",
    methods=[
        "GET",
        "POST"
    ]
)
def notificacoes():

    print(
        "[NOTIFICACAO ML]",
        request.method,
        request.get_json(
            silent=True
        )
    )

    return {
        "ok": True
    }


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health"
)
def health():

    return {
        "status":
            "ok"
    }


# ============================================================
# BUSCAR ANÚNCIOS REAIS
# ============================================================

def buscar_anuncios(
    query,
    limite=20
):

    headers = headers_ml()

    if not headers:

        raise Exception(
            "Mercado Livre não conectado."
        )

    params = {

        "q":
            query,

        "limit":
            limite,

        "offset":
            0
    }

    try:

        response = requests.get(
            ML_SEARCH_URL,
            headers=headers,
            params=params,
            timeout=30
        )

        print(
            "[BUSCA ANÚNCIOS]",
            response.status_code
        )

        print(
            "[URL]",
            response.url
        )

        if response.status_code != 200:

            print(
                "[ERRO API]",
                response.text[:1500]
            )

            raise Exception(
                "Mercado Livre retornou "
                f"HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        data = response.json()

        resultados = data.get(
            "results",
            []
        )

        print(
            "[ANÚNCIOS ENCONTRADOS]",
            len(resultados)
        )

        return resultados

    except requests.RequestException as e:

        print(
            "[ERRO REQUEST]",
            e
        )

        raise Exception(
            f"Erro de comunicação com "
            f"Mercado Livre: {e}"
        )


# ============================================================
# OBTER ITEM REAL
# ============================================================

def obter_item(
    item_id
):

    headers = headers_ml()

    if not headers:
        return None

    url = (
        f"{ML_API_URL}"
        f"/items/{item_id}"
    )

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        print(
            "[ITEM]",
            item_id,
            response.status_code
        )

        if response.status_code != 200:

            print(
                "[ITEM ERRO]",
                response.text[:500]
            )

            return None

        return response.json()

    except Exception as e:

        print(
            "[ERRO ITEM]",
            item_id,
            e
        )

        return None


# ============================================================
# PREÇO DE VENDA ATUAL
# ============================================================

def obter_sale_price(
    item_id
):

    headers = headers_ml()

    if not headers:
        return None

    url = (
        f"{ML_API_URL}"
        f"/items/{item_id}/sale_price"
    )

    params = {

        "context":
            "channel_marketplace"
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=30
        )

        print(
            "[SALE PRICE]",
            item_id,
            response.status_code
        )

        if response.status_code != 200:

            print(
                "[SALE PRICE ERRO]",
                response.text[:500]
            )

            return None

        data = response.json()

        amount = data.get(
            "amount"
        )

        regular_amount = data.get(
            "regular_amount"
        )

        if amount is None:

            return None

        return {

            "preco":
                amount,

            "preco_original":
                regular_amount,

            "moeda":
                data.get(
                    "currency_id",
                    "BRL"
                )
        }

    except Exception as e:

        print(
            "[ERRO SALE PRICE]",
            item_id,
            e
        )

        return None


# ============================================================
# PREÇOS
# ============================================================

def obter_precos(
    item_id
):

    headers = headers_ml()

    if not headers:
        return None

    url = (
        f"{ML_API_URL}"
        f"/items/{item_id}/prices"
    )

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        print(
            "[PRICES]",
            item_id,
            response.status_code
        )

        if response.status_code != 200:

            return None

        data = response.json()

        prices = data.get(
            "prices",
            []
        )

        if not prices:

            return None

        preco_standard = None

        promocao = None

        agora = datetime.now(
            timezone.utc
        )

        for price in prices:

            tipo = price.get(
                "type"
            )

            amount = price.get(
                "amount"
            )

            if amount is None:
                continue

            if tipo == "standard":

                preco_standard = amount

            elif tipo == "promotion":

                conditions = (
                    price.get(
                        "conditions",
                        {}
                    )
                )

                inicio = (
                    conditions.get(
                        "start_time"
                    )
                )

                fim = (
                    conditions.get(
                        "end_time"
                    )
                )

                ativa = True

                try:

                    if inicio:

                        inicio_dt = datetime.fromisoformat(
                            inicio.replace("Z", "+00:00")
                        )

                        if agora < inicio_dt:

                            ativa = False

                    if fim:

                        fim_dt = datetime.fromisoformat(
                            fim.replace("Z", "+00:00")
                        )

                        if agora > fim_dt:

                            ativa = False

                except Exception:

                    # Se não conseguirmos
                    # interpretar as datas,
                    # mantemos o preço
                    ativa = True

                if ativa:

                    if (
                        promocao is None
                        or amount < promocao.get("amount", 999999999)
                    ):

                        promocao = price

        # ----------------------------------------------------
        # PROMOÇÃO ATIVA
        # ----------------------------------------------------

        if promocao:

            preco = promocao.get(
                "amount"
            )

            original = (
                promocao.get(
                    "regular_amount"
                )
                or preco_standard
            )

            return {

                "preco":
                    preco,

                "preco_original":
                    original,

                "moeda":
                    promocao.get(
                        "currency_id",
                        "BRL"
                    )
            }

        # ----------------------------------------------------
        # PREÇO NORMAL
        # ----------------------------------------------------

        if preco_standard is not None:

            return {

                "preco":
                    preco_standard,

                "preco_original":
                    None,

                "moeda":
                    "BRL"
            }

    except Exception as e:

        print(
            "[ERRO PRICES]",
            item_id,
            e
        )

    return None


# ============================================================
# CALCULAR DESCONTO
# ============================================================

def calcular_desconto(
    preco,
    original
):

    try:

        if (
            preco is None
            or original is None
        ):

            return 0

        preco = float(
            preco
        )

        original = float(
            original
        )

        if original <= 0:

            return 0

        if preco >= original:

            return 0

        desconto = (
            (
                original - preco
            )
            / original
        ) * 100

        return round(
            desconto,
            1
        )

    except Exception:

        return 0


# ============================================================
# BUSCAR OFERTAS
# ============================================================

def buscar_ofertas(
    query,
    limite=20
):

    anuncios = buscar_anuncios(
        query,
        limite
    )

    resultados = []

    for anuncio in anuncios:

        item_id = anuncio.get(
            "id"
        )

        if not item_id:
            continue

        titulo = anuncio.get(
            "title"
        ) or "Produto"

        link = anuncio.get(
            "permalink"
        ) or ""

        # ----------------------------------------------------
        # PREÇO QUE VEIO NA BUSCA
        # ----------------------------------------------------

        preco = anuncio.get(
            "price"
        )

        preco_original = anuncio.get(
            "original_price"
        )

        moeda = anuncio.get(
            "currency_id",
            "BRL"
        )

        # ----------------------------------------------------
        # TENTA PREÇO ATUAL
        # ----------------------------------------------------

        preco_info = (
            obter_sale_price(
                item_id
            )
        )

        # ----------------------------------------------------
        # FALLBACK PARA /prices
        # ----------------------------------------------------

        if not preco_info:

            preco_info = (
                obter_precos(
                    item_id
                )
            )

        # ----------------------------------------------------
        # ATUALIZA PREÇO
        # ----------------------------------------------------

        if preco_info:

            if (
                preco_info.get(
                    "preco"
                ) is not None
            ):

                preco = (
                    preco_info.get(
                        "preco"
                    )
                )

            if (
                preco_info.get(
                    "preco_original"
                ) is not None
            ):

                preco_original = (
                    preco_info.get(
                        "preco_original"
                    )
                )

            moeda = (
                preco_info.get(
                    "moeda",
                    moeda
                )
            )

        # ----------------------------------------------------
        # CALCULA DESCONTO
        # ----------------------------------------------------

        desconto = (
            calcular_desconto(
                preco,
                preco_original
            )
        )

        # ----------------------------------------------------
        # IMAGEM
        # ----------------------------------------------------

        imagem = (
            anuncio.get(
                "thumbnail"
            )
            or ""
        )

        # ----------------------------------------------------
        # PRODUTO DE CATÁLOGO
        # ----------------------------------------------------

        catalog_product_id = (
            anuncio.get(
                "catalog_product_id"
            )
            or ""
        )

        # ----------------------------------------------------
        # CATEGORIA
        # ----------------------------------------------------

        categoria = (
            anuncio.get(
                "category_id"
            )
            or ""
        )

        # ----------------------------------------------------
        # VENDEDOR
        # ----------------------------------------------------

        seller = anuncio.get(
            "seller",
            {}
        )

        seller_id = seller.get(
            "id"
        )

        seller_nickname = seller.get(
            "nickname"
        )

        # ----------------------------------------------------
        # RESULTADO
        # ----------------------------------------------------

        resultados.append({

            "produto_id":
                catalog_product_id,

            "item_id":
                item_id,

            "titulo":
                titulo,

            "categoria":
                categoria,

            "preco":
                preco,

            "preco_original":
                preco_original,

            "desconto":
                desconto,

            "moeda":
                moeda,

            "link":
                link,

            "imagem":
                imagem,

            "seller_id":
                seller_id,

            "seller_nickname":
                seller_nickname
        })

    # ========================================================
    # PRIMEIRO OS QUE POSSUEM DESCONTO
    # ========================================================

    resultados.sort(
        key=lambda x: (
            x.get(
                "desconto",
                0
            ),
            -float(
                x.get(
                    "preco",
                    999999999
                ) or 999999999
            )
        ),
        reverse=True
    )

    print(
        "[OFERTAS REAIS]",
        len(resultados)
    )

    return resultados


# ============================================================
# SALVAR OFERTA
# ============================================================

def salvar_oferta(
    produto,
    affiliate_link
):

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.execute("""
        INSERT INTO ofertas (
            produto_id,
            item_id,
            titulo,
            preco,
            preco_original,
            desconto,
            link,
            affiliate_link,
            criado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (

        produto.get(
            "produto_id"
        ),

        produto.get(
            "item_id"
        ),

        produto.get(
            "titulo"
        ),

        produto.get(
            "preco"
        ),

        produto.get(
            "preco_original"
        ),

        produto.get(
            "desconto"
        ),

        produto.get(
            "link"
        ),

        affiliate_link,

        datetime.now(
            timezone.utc
        ).isoformat()
    ))

    conn.commit()
    conn.close()


# ============================================================
# FORMATAR PREÇO
# ============================================================

def formatar_preco(
    valor
):

    if valor is None:

        return "Consultar"

    try:

        numero = float(
            valor
        )

        return (
            "R$ "
            + f"{numero:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except Exception:

        return str(
            valor
        )


# ============================================================
# GERAR MENSAGEM
# ============================================================

def gerar_mensagem(
    titulo,
    preco,
    preco_original,
    desconto,
    affiliate_link
):

    mensagem = (
        "🔥 OFERTA ENCONTRADA!\n\n"
    )

    mensagem += (
        f"🛒 {titulo}\n\n"
    )

    if (
        preco_original
        and desconto > 0
    ):

        mensagem += (
            "❌ De: "
            + formatar_preco(
                preco_original
            )
            + "\n"
        )

        mensagem += (
            "🔥 Por: "
            + formatar_preco(
                preco
            )
            + "\n"
        )

        mensagem += (
            f"💰 Desconto: "
            f"{desconto:.0f}%\n\n"
        )

    else:

        mensagem += (
            "💰 Preço: "
            + formatar_preco(
                preco
            )
            + "\n\n"
        )

    mensagem += (
        "👉 COMPRAR AQUI:\n"
    )

    mensagem += (
        affiliate_link
    )

    mensagem += (
        "\n\n"
        "⚠️️ Preço e disponibilidade "
        "podem mudar a qualquer momento."
    )

    return mensagem


# ============================================================
# FILTRO BRL
# ============================================================

@app.template_filter(
    "brl"
)
def brl_filter(
    value
):

    return formatar_preco(
        value
    )


# ============================================================
# HTML
# ============================================================

HTML = """

<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width, initial-scale=1.0"
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

    padding: 20px;

    background: #f5f5f5;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    color: #222;
}

.container {

    max-width: 1100px;

    margin: auto;
}

h1 {

    margin-bottom: 5px;

    font-size: 34px;
}

.sub {

    color: #666;

    font-size: 18px;

    margin-bottom: 25px;
}

.card {

    background: white;

    padding: 20px;

    border-radius: 16px;

    margin-bottom: 20px;

    box-shadow:
        0 3px 15px
        rgba(0,0,0,.06);
}

.status {

    padding: 15px;

    border-radius: 12px;

    background: #e8f5e9;

    color: #237a36;

    font-size: 18px;

    margin-bottom: 20px;
}

input {

    width: 100%;

    padding: 15px;

    border: 1px solid #ddd;

    border-radius: 11px;

    font-size: 17px;

    margin-bottom: 12px;
}

button {

    width: 100%;

    padding: 15px;

    border: none;

    border-radius: 11px;

    background: #3483fa;

    color: white;

    font-size: 17px;

    font-weight: bold;

    cursor: pointer;
}

button:hover {

    opacity: .92;
}

.produto {

    border: 1px solid #e5e5e5;

    border-radius: 15px;

    padding: 18px;

    margin-top: 16px;

    background: white;
}

.produto h3 {

    margin-top: 12px;

    line-height: 1.4;

    font-size: 19px;
}

.badge {

    display: inline-block;

    padding: 6px 9px;

    background: #e8f0fe;

    color: #174ea6;

    border-radius: 7px;

    font-size: 12px;

    margin: 2px;
}

.badge-oferta {

    display: inline-block;

    padding: 7px 10px;

    background: #e6f4ea;

    color: #137333;

    border-radius: 8px;

    font-weight: bold;

    font-size: 13px;
}

.preco-antigo {

    color: #777;

    text-decoration: line-through;

    font-size: 15px;

    margin-top: 8px;
}

.preco {

    font-size: 28px;

    font-weight: bold;

    margin-top: 3px;
}

.desconto {

    display: inline-block;

    padding: 8px 11px;

    background: #e6f4ea;

    color: #137333;

    border-radius: 9px;

    font-weight: bold;

    margin-top: 9px;
}

.link {

    display: block;

    margin-top: 12px;

    color: #3483fa;

    word-break: break-all;

    text-decoration: none;
}

.link:hover {

    text-decoration: underline;
}

textarea {

    width: 100%;

    min-height: 190px;

    margin-top: 12px;

    padding: 14px;

    border-radius: 11px;

    border: 1px solid #ddd;

    font-size: 15px;
}

.small {

    font-size: 13px;

    color: #777;
}

.erro {

    background: white;

    padding: 20px;

    border-radius: 15px;

    border-left:
        6px solid #e53935;

    margin-bottom: 20px;
}

.info {

    background: #f1f5ff;

    border-radius: 11px;

    padding: 13px;

    color: #345;
}

hr {

    border: none;

    border-top: 1px solid #eee;

    margin: 20px 0;
}

</style>

</head>

<body>

<div class="container">


<h1>
🛒 Caçador de Ofertas
</h1>

<div class="sub">
Mercado Livre → anúncios reais → preços → descontos → publicação
</div>


{% if conectado %}

<div class="status">
✅ Mercado Livre conectado
</div>

{% else %}

<div class="card">

<h3>
🔐 Mercado Livre
</h3>

<p>
Conecte sua conta do Mercado Livre para começar.
</p>

<a href="/mercadolivre/login">

<button>
🔐 Conectar Mercado Livre
</button>

</a>

</div>

{% endif %}


<div class="card">

<h2>
🔎 Procurar ofertas
</h2>

<div class="info">

Agora a busca procura diretamente
por <strong>anúncios reais</strong>
do Mercado Livre.

</div>

<br>

<form
method="GET"
action="/buscar"
>

<input
type="text"
name="q"
placeholder="Ex: Air Fryer, celular, TV, tênis..."
value="{{ query or '' }}"
required
>

<button type="submit">
🔍 Buscar ofertas
</button>

</form>

</div>


{% if erro %}

<div class="erro">

<h2>
❌ Erro
</h2>

<p>
{{ erro }}
</p>

</div>

{% endif %}


{% if produtos %}

<div class="card">

<h2>
🔥 Ofertas encontradas
</h2>

<p class="small">

Encontramos
<strong>
{{ produtos|length }}
</strong>
anúncio(s) real(is).

</p>


{% for p in produtos %}

<div class="produto">


<span class="badge-oferta">
🔥 ANÚNCIO REAL
</span>


<span class="badge">
ID: {{ p.item_id }}
</span>


{% if p.desconto > 0 %}

<span class="badge-oferta">
{{ "%.0f"|format(p.desconto) }}% OFF
</span>

{% endif %}


<h3>
{{ p.titulo }}
</h3>


{% if p.preco_original and p.desconto > 0 %}

<div class="preco-antigo">

De:
{{ p.preco_original|brl }}

</div>

<div class="preco">

Por:
{{ p.preco|brl }}

</div>

<div class="desconto">

🔥
{{ "%.0f"|format(p.desconto) }}%
OFF

</div>

{% else %}

<div class="preco">

{{ p.preco|brl }}

</div>

{% endif %}


<hr>


<div class="small">

Categoria:
{{ p.categoria }}

</div>


{% if p.seller_nickname %}

<div class="small">

Vendedor:
{{ p.seller_nickname }}

</div>

{% endif %}


<a
class="link"
href="{{ p.link }}"
target="_blank"
>

🔗 Ver anúncio no Mercado Livre

</a>


<form
method="POST"
action="/gerar"
>

<input
type="hidden"
name="produto_id"
value="{{ p.produto_id }}"
>

<input
type="hidden"
name="item_id"
value="{{ p.item_id }}"
>

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
value="{{ p.preco_original or '' }}"
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

<input
type="text"
name="affiliate_link"
placeholder="Cole aqui seu link de afiliado"
required
>

<button type="submit">

📢 Gerar publicação

</button>

</form>


</div>

{% endfor %}

</div>

{% endif %}


{% if mensagem %}

<div class="card">

<h2>
📢 Publicação pronta
</h2>

<p class="small">
Sua mensagem está pronta para copiar.
</p>

<textarea
readonly
>{{ mensagem }}</textarea>

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

    conectado = (
        obter_access_token()
        is not None
    )

    return render_template_string(

        HTML,

        conectado=conectado,

        produtos=None,

        query=None,

        erro=None,

        mensagem=None
    )


# ============================================================
# BUSCAR
# ============================================================

@app.route(
    "/buscar"
)
def buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:

        return redirect(
            "/"
        )

    conectado = (
        obter_access_token()
        is not None
    )

    if not conectado:

        return render_template_string(

            HTML,

            conectado=False,

            produtos=None,

            query=query,

            erro=(
                "Conecte primeiro "
                "sua conta do Mercado Livre."
            ),

            mensagem=None
        )

    try:

        produtos = buscar_ofertas(
            query,
            limite=20
        )

        if not produtos:

            erro = (
                "Nenhum anúncio foi "
                "encontrado para essa busca."
            )

        else:

            erro = None

        return render_template_string(

            HTML,

            conectado=True,

            produtos=produtos,

            query=query,

            erro=erro,

            mensagem=None
        )

    except Exception as e:

        print(
            "[ERRO BUSCA]",
            e
        )

        return render_template_string(

            HTML,

            conectado=True,

            produtos=None,

            query=query,

            erro=str(e),

            mensagem=None
        )


# ============================================================
# GERAR PUBLICAÇÃO
# ============================================================

@app.route(
    "/gerar",
    methods=["POST"]
)
def gerar():

    produto_id = request.form.get(
        "produto_id"
    )

    item_id = request.form.get(
        "item_id"
    )

    titulo = request.form.get(
        "titulo"
    )

    preco = request.form.get(
        "preco"
    )

    preco_original = request.form.get(
        "preco_original"
    )

    desconto = request.form.get(
        "desconto",
        "0"
    )

    link = request.form.get(
        "link"
    )

    affiliate_link = request.form.get(
        "affiliate_link"
    )

    try:

        preco_float = float(
            preco
        )

    except Exception:

        preco_float = 0

    try:

        original_float = (

            float(
                preco_original
            )

            if preco_original

            else None
        )

    except Exception:

        original_float = None

    try:

        desconto_float = float(
            desconto
        )

    except Exception:

        desconto_float = 0

    mensagem = gerar_mensagem(

        titulo,

        preco_float,

        original_float,

        desconto_float,

        affiliate_link
    )

    salvar_oferta(

        {

            "produto_id":
                produto_id,

            "item_id":
                item_id,

            "titulo":
                titulo,

            "preco":
                preco_float,

            "preco_original":
                original_float,

            "desconto":
                desconto_float,

            "link":
                link
        },

        affiliate_link
    )

    return render_template_string(

        HTML,

        conectado=True,

        produtos=None,

        query=None,

        erro=None,

        mensagem=mensagem
    )


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
