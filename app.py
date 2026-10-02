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
    "troque-esta-chave"
)

DB_FILE = os.getenv(
    "DB_FILE",
    "ofertas.db"
)


# ============================================================
# MERCADO LIVRE
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

ML_PRODUCTS_SEARCH_URL = (
    "https://api.mercadolibre.com/products/search"
)


# ============================================================
# BANCO
# ============================================================

def init_db():

    conn = sqlite3.connect(DB_FILE)

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

    conn = sqlite3.connect(DB_FILE)

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

    conn = sqlite3.connect(DB_FILE)

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
        "grant_type": "refresh_token",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "refresh_token": refresh_token
    }

    try:

        response = requests.post(
            ML_TOKEN_URL,
            data=payload,
            timeout=30
        )

        print(
            "[REFRESH]",
            response.status_code
        )

        response.raise_for_status()

        data = response.json()

        salvar_tokens(data)

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

    if tokens["expires_at"] > agora + 300:

        return tokens[
            "access_token"
        ]

    print(
        "[TOKEN] Renovando..."
    )

    return renovar_access_token()


def headers_ml():

    token = obter_access_token()

    if not token:
        return None

    return {
        "Authorization": (
            f"Bearer {token}"
        ),
        "Accept": "application/json"
    }


# ============================================================
# OAUTH
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    state = secrets.token_urlsafe(
        32
    )

    code_verifier = secrets.token_urlsafe(
        64
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

        "response_type": "code",

        "client_id": ML_CLIENT_ID,

        "redirect_uri":
            ML_REDIRECT_URI,

        "state": state,

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
            "Código não recebido.",
            400
        )

    if state != saved_state:

        return (
            "Estado OAuth inválido.",
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
            "[OAUTH]",
            response.status_code
        )

        response.raise_for_status()

        data = response.json()

        if not data.get(
            "refresh_token"
        ):

            return (
                "Mercado Livre não "
                "retornou Refresh Token.",
                400
            )

        salvar_tokens(data)

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

        print(
            "[ERRO OAUTH]",
            e
        )

        return (
            f"Erro OAuth: {e}",
            500
        )


@app.route("/mercadolivre/callback2")
def callback2():

    return redirect(
        "/mercadolivre/callback"
    )


# ============================================================
# STATUS
# ============================================================

@app.route("/mercadolivre/status")
def mercadolivre_status():

    conectado = (
        obter_access_token()
        is not None
    )

    return {
        "conectado": conectado
    }


# ============================================================
# NOTIFICAÇÕES
# ============================================================

@app.route(
    "/mercadolivre/notificacoes",
    methods=["GET", "POST"]
)
def notificacoes():

    print(
        "[NOTIFICACAO]",
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

@app.route("/health")
def health():

    return {
        "status": "ok"
    }


# ============================================================
# BUSCA CATÁLOGO
# ============================================================

def buscar_catalogo(
    query,
    limite=20
):

    headers = headers_ml()

    if not headers:

        raise Exception(
            "Mercado Livre não conectado."
        )

    params = {

        "status": "active",

        "site_id": "MLB",

        "q": query,

        "limit": limite,

        "offset": 0
    }

    response = requests.get(
        ML_PRODUCTS_SEARCH_URL,
        headers=headers,
        params=params,
        timeout=30
    )

    print(
        "[CATALOGO]",
        response.status_code,
        response.url
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# DETALHES DO PRODUTO
# ============================================================

def obter_produto(
    product_id
):

    headers = headers_ml()

    if not headers:
        return None

    url = (
        f"{ML_API_URL}"
        f"/products/{product_id}"
    )

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        print(
            "[PRODUTO]",
            product_id,
            response.status_code
        )

        if response.status_code != 200:

            return None

        return response.json()

    except Exception as e:

        print(
            "[ERRO PRODUTO]",
            product_id,
            e
        )

        return None


# ============================================================
# NOVO:
# ENCONTRAR BUY BOX DENTRO DOS FILHOS
# ============================================================

def encontrar_buy_box(
    product_id,
    max_filhos=8
):

    produto = obter_produto(
        product_id
    )

    if not produto:

        return None, None

    # --------------------------------------------------------
    # PRIMEIRO:
    # produto já possui buy_box_winner
    # --------------------------------------------------------

    winner = produto.get(
        "buy_box_winner"
    )

    if winner:

        return produto, winner

    # --------------------------------------------------------
    # SEGUNDO:
    # produto é pai e possui children_ids
    # --------------------------------------------------------

    children_ids = produto.get(
        "children_ids",
        []
    )

    if not children_ids:

        print(
            "[SEM BUY BOX]",
            product_id
        )

        return produto, None

    print(
        "[PRODUTO PAI]",
        product_id,
        "filhos:",
        len(children_ids)
    )

    # Limita quantidade para
    # evitar muitas chamadas à API
    children_ids = children_ids[
        :max_filhos
    ]

    for child_id in children_ids:

        print(
            "[TESTANDO FILHO]",
            child_id
        )

        child = obter_produto(
            child_id
        )

        if not child:
            continue

        # Só queremos produto ativo
        if child.get(
            "status"
        ) != "active":

            continue

        child_winner = child.get(
            "buy_box_winner"
        )

        if child_winner:

            print(
                "[BUY BOX ENCONTRADO]",
                child_id
            )

            return (
                child,
                child_winner
            )

    print(
        "[NENHUM BUY BOX]",
        product_id
    )

    return produto, None


# ============================================================
# ITEM REAL
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
                response.text[:300]
            )

            return None

        return response.json()

    except Exception as e:

        print(
            "[ERRO ITEM]",
            e
        )

        return None


# ============================================================
# SALE PRICE
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

            return None

        data = response.json()

        amount = data.get(
            "amount"
        )

        regular = data.get(
            "regular_amount"
        )

        if amount is None:

            return None

        return {

            "preco": amount,

            "preco_original":
                regular,

            "moeda":
                data.get(
                    "currency_id",
                    "BRL"
                )
        }

    except Exception as e:

        print(
            "[ERRO SALE PRICE]",
            e
        )

        return None


# ============================================================
# PRICES
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

                if (
                    promocao is None
                    or amount < promocao.get(
                        "amount",
                        999999999
                    )
                ):

                    promocao = price

        # ----------------------------------------------------
        # PROMOÇÃO
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

                "preco": preco,

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
            e
        )

    return None


# ============================================================
# DESCONTO
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

        preco = float(preco)
        original = float(original)

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

    catalogo = buscar_catalogo(
        query,
        limite
    )

    produtos = catalogo.get(
        "results",
        []
    )

    resultados = []

    print(
        "[RESULTADOS CATALOGO]",
        len(produtos)
    )

    for produto in produtos:

        product_id = produto.get(
            "id"
        )

        if not product_id:
            continue

        # ----------------------------------------------------
        # AQUI ESTÁ A PRINCIPAL CORREÇÃO
        # ----------------------------------------------------

        detalhes, winner = (
            encontrar_buy_box(
                product_id
            )
        )

        if not detalhes:
            continue

        if not winner:

            continue

        item_id = winner.get(
            "item_id"
        )

        if not item_id:

            continue

        # ----------------------------------------------------
        # ITEM REAL
        # ----------------------------------------------------

        item = obter_item(
            item_id
        )

        if not item:

            continue

        titulo = (
            item.get("title")
            or detalhes.get("name")
            or produto.get("name")
            or "Produto"
        )

        link = (
            item.get("permalink")
            or detalhes.get("permalink")
            or ""
        )

        # ----------------------------------------------------
        # PREÇO
        # ----------------------------------------------------

        preco_info = (
            obter_sale_price(
                item_id
            )
        )

        if not preco_info:

            preco_info = (
                obter_precos(
                    item_id
                )
            )

        # ----------------------------------------------------
        # FALLBACK BUY BOX
        # ----------------------------------------------------

        if not preco_info:

            preco = winner.get(
                "price"
            )

            if preco is not None:

                preco_info = {

                    "preco":
                        preco,

                    "preco_original":
                        winner.get(
                            "original_price"
                        ),

                    "moeda":
                        winner.get(
                            "currency_id",
                            "BRL"
                        )
                }

        if not preco_info:

            continue

        preco = preco_info.get(
            "preco"
        )

        preco_original = (
            preco_info.get(
                "preco_original"
            )
        )

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
            item.get(
                "thumbnail"
            )
            or ""
        )

        # ----------------------------------------------------
        # RESULTADO
        # ----------------------------------------------------

        resultados.append({

            "produto_id":
                detalhes.get(
                    "id",
                    product_id
                ),

            "item_id":
                item_id,

            "titulo":
                titulo,

            "categoria":
                item.get(
                    "category_id"
                )
                or detalhes.get(
                    "domain_id",
                    ""
                ),

            "preco":
                preco,

            "preco_original":
                preco_original,

            "desconto":
                desconto,

            "moeda":
                preco_info.get(
                    "moeda",
                    "BRL"
                ),

            "link":
                link,

            "imagem":
                imagem
        })

    # --------------------------------------------------------
    # MAIOR DESCONTO PRIMEIRO
    # --------------------------------------------------------

    resultados.sort(
        key=lambda x:
            x.get(
                "desconto",
                0
            ),
        reverse=True
    )

    print(
        "[OFERTAS REAIS]",
        len(resultados)
    )

    return resultados


# ============================================================
# SALVAR
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
# FORMATAÇÃO
# ============================================================

def formatar_preco(
    valor
):

    if valor is None:

        return "Consultar"

    try:

        return (
            "R$ "
            + f"{float(valor):,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except:

        return str(valor)


# ============================================================
# MENSAGEM
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
            f"❌ De: "
            f"{formatar_preco(preco_original)}\n"
        )

        mensagem += (
            f"🔥 Por: "
            f"{formatar_preco(preco)}\n"
        )

        mensagem += (
            f"💰 Desconto: "
            f"{desconto:.0f}%\n\n"
        )

    else:

        mensagem += (
            f"💰 Preço: "
            f"{formatar_preco(preco)}\n\n"
        )

    mensagem += (
        "👉 COMPRAR AQUI:\n"
    )

    mensagem += affiliate_link

    mensagem += (
        "\n\n"
        "⚠️ Preço e disponibilidade "
        "podem mudar a qualquer momento."
    )

    return mensagem


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

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    padding: 20px;

    background: #f5f5f5;

    font-family: Arial, sans-serif;

    color: #222;
}

.container {

    max-width: 1100px;

    margin: auto;
}

h1 {

    margin-bottom: 5px;
}

.sub {

    color: #666;

    margin-bottom: 25px;
}

.card {

    background: white;

    padding: 20px;

    border-radius: 14px;

    margin-bottom: 20px;

    box-shadow:
        0 2px 10px
        rgba(0,0,0,.06);
}

input {

    width: 100%;

    padding: 14px;

    border: 1px solid #ddd;

    border-radius: 10px;

    font-size: 16px;

    margin-bottom: 12px;
}

button {

    width: 100%;

    padding: 14px;

    border: none;

    border-radius: 10px;

    background: #3483fa;

    color: white;

    font-size: 16px;

    font-weight: bold;

    cursor: pointer;
}

.status {

    padding: 14px;

    border-radius: 10px;

    background: #e8f5e9;

    color: #137333;

    margin-bottom: 20px;
}

.produto {

    border: 1px solid #e5e5e5;

    border-radius: 14px;

    padding: 18px;

    margin-top: 15px;

    background: white;
}

.produto h3 {

    margin-top: 0;

    line-height: 1.4;
}

.badge {

    display: inline-block;

    padding: 5px 8px;

    background: #e8f0fe;

    color: #174ea6;

    border-radius: 7px;

    font-size: 12px;

    margin: 2px;
}

.desconto {

    display: inline-block;

    padding: 7px 10px;

    background: #e6f4ea;

    color: #137333;

    border-radius: 8px;

    font-weight: bold;

    margin: 8px 0;
}

.preco-antigo {

    color: #777;

    text-decoration: line-through;

    font-size: 14px;
}

.preco {

    font-size: 26px;

    font-weight: bold;

    margin-top: 4px;
}

.link {

    display: block;

    margin-top: 10px;

    color: #3483fa;

    word-break: break-all;
}

textarea {

    width: 100%;

    min-height: 180px;

    margin-top: 12px;

    padding: 12px;

    border-radius: 10px;

    border: 1px solid #ddd;

    font-size: 14px;
}

.small {

    font-size: 13px;

    color: #777;
}

hr {

    border: none;

    border-top: 1px solid #eee;

    margin: 20px 0;
}

.erro {

    background: #fff;

    padding: 20px;

    border-radius: 14px;

    border-left: 5px solid #e53935;

    margin-bottom: 20px;
}

</style>

</head>

<body>

<div class="container">

<h1>🛒 Caçador de Ofertas</h1>

<div class="sub">
Mercado Livre → produtos → preços → descontos → publicação
</div>

{% if conectado %}

<div class="status">
✅ Mercado Livre conectado
</div>

{% else %}

<div class="card">

<h3>🔐 Mercado Livre</h3>

<p>
Conecte sua conta do Mercado Livre.
</p>

<a href="/mercadolivre/login">

<button>
Conectar Mercado Livre
</button>

</a>

</div>

{% endif %}


<div class="card">

<h2>
🔎 Procurar ofertas
</h2>

<form
method="GET"
action="/buscar"
>

<input
type="text"
name="q"
placeholder="Ex: celular, air fryer, televisão..."
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

{{ produtos|length }}
oferta(s) encontrada(s).

</p>


{% for p in produtos %}

<div class="produto">

<span class="badge">
Produto: {{ p.produto_id }}
</span>

<span class="badge">
Anúncio: {{ p.item_id }}
</span>

<h3>
{{ p.titulo }}
</h3>


{% if p.preco_original and p.desconto > 0 %}

<div class="preco-antigo">
De: {{ p.preco_original|brl }}
</div>

<div class="preco">
Por: {{ p.preco|brl }}
</div>

<div class="desconto">
🔥 {{ "%.0f"|format(p.desconto) }}% OFF
</div>

{% else %}

<div class="preco">
{{ p.preco|brl }}
</div>

{% endif %}


<hr>

<div class="small">
Categoria: {{ p.categoria }}
</div>


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
Mensagem pronta para copiar para o WhatsApp.
</p>

<textarea readonly>{{ mensagem }}</textarea>

</div>

{% endif %}


</div>

</body>

</html>

"""


# ============================================================
# FILTRO BRL
# ============================================================

@app.template_filter("brl")
def brl_filter(
    value
):

    return formatar_preco(
        value
    )


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

@app.route("/buscar")
def buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:

        return redirect("/")

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
                "O Mercado Livre encontrou "
                "produtos de catálogo, mas não "
                "encontramos anúncios reais "
                "com preço para esses produtos."
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

    except:

        preco_float = 0

    try:

        original_float = (
            float(preco_original)
            if preco_original
            else None
        )

    except:

        original_float = None

    try:

        desconto_float = float(
            desconto
        )

    except:

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