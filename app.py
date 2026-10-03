import os
import re
import sqlite3
import secrets
from datetime import datetime
from urllib.parse import urlencode

import requests

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    jsonify,
    session,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = (
    os.getenv("SECRET_KEY")
    or os.getenv("FLASK_SECRET_KEY")
    or "troque-essa-chave-no-railway"
)

DB_PATH = os.getenv(
    "DB_PATH",
    "promocoes.db"
)

SITE_ID = "MLB"

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "20"
    )
)

DEFAULT_LIMIT = int(
    os.getenv(
        "DEFAULT_LIMIT",
        "20"
    )
)

MAX_LIMIT = int(
    os.getenv(
        "MAX_LIMIT",
        "50"
    )
)


# ============================================================
# CREDENCIAIS
#
# Aceita vários nomes para não quebrar o que você já
# configurou no Railway.
# ============================================================

CLIENT_ID = (
    os.getenv("ML_CLIENT_ID")
    or os.getenv("MERCADOLIVRE_CLIENT_ID")
    or os.getenv("CLIENT_ID")
    or os.getenv("APP_ID")
)

CLIENT_SECRET = (
    os.getenv("ML_CLIENT_SECRET")
    or os.getenv("MERCADOLIVRE_CLIENT_SECRET")
    or os.getenv("CLIENT_SECRET")
    or os.getenv("SECRET_KEY_ML")
)

REDIRECT_URI = (
    os.getenv("ML_REDIRECT_URI")
    or os.getenv("MERCADOLIVRE_REDIRECT_URI")
    or os.getenv("REDIRECT_URI")
)

APP_URL = (
    os.getenv("APP_URL")
    or os.getenv("RAILWAY_PUBLIC_DOMAIN")
    or ""
).strip()


# ============================================================
# PKCE
#
# Se sua aplicação do Mercado Livre estiver com PKCE habilitado,
# coloque:
#
# ML_USE_PKCE=true
#
# ============================================================

USE_PKCE = (
    os.getenv(
        "ML_USE_PKCE",
        os.getenv(
            "MERCADOLIVRE_USE_PKCE",
            "false"
        )
    ).lower()
    == "true"
)


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            titulo TEXT,
            preco REAL,
            preco_antigo REAL,
            desconto REAL,
            imagem TEXT,
            permalink TEXT,
            link_afiliado TEXT,
            texto TEXT,
            criado_em TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ml_auth (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT,
            token_type TEXT,
            expires_at INTEGER,
            user_id TEXT,
            scope TEXT,
            atualizado_em TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# CONFIGURAÇÃO OAUTH
# ============================================================

def get_redirect_uri():

    if REDIRECT_URI:
        return REDIRECT_URI

    if APP_URL:

        base = APP_URL.rstrip("/")

        if base.startswith("http://") or base.startswith("https://"):
            return base + "/oauth/callback"

        return "https://" + base + "/oauth/callback"

    return url_for(
        "oauth_callback",
        _external=True
    )


def oauth_configured():

    return bool(
        CLIENT_ID
        and CLIENT_SECRET
        and get_redirect_uri()
    )


# ============================================================
# TOKEN
# ============================================================

def get_auth():

    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM ml_auth
        WHERE id = 1
    """).fetchone()

    conn.close()

    return row


def save_auth(
    access_token,
    refresh_token,
    token_type,
    expires_in,
    user_id,
    scope
):

    expires_at = (
        int(datetime.now().timestamp())
        + int(expires_in or 21600)
        - 60
    )

    conn = get_db()

    conn.execute("""
        INSERT INTO ml_auth (
            id,
            access_token,
            refresh_token,
            token_type,
            expires_at,
            user_id,
            scope,
            atualizado_em
        )
        VALUES (
            1, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(id)
        DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            token_type = excluded.token_type,
            expires_at = excluded.expires_at,
            user_id = excluded.user_id,
            scope = excluded.scope,
            atualizado_em = excluded.atualizado_em
    """, (
        access_token,
        refresh_token,
        token_type or "Bearer",
        expires_at,
        str(user_id) if user_id is not None else "",
        scope or "",
        datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()


def delete_auth():

    conn = get_db()

    conn.execute("""
        DELETE FROM ml_auth
        WHERE id = 1
    """)

    conn.commit()
    conn.close()


def token_expired(auth):

    if not auth:
        return True

    try:
        expires_at = int(
            auth["expires_at"]
        )

        return (
            int(datetime.now().timestamp())
            >= expires_at
        )

    except Exception:
        return True


# ============================================================
# REFRESH TOKEN
# ============================================================

def refresh_access_token():

    auth = get_auth()

    if not auth:
        return False

    refresh_token = auth["refresh_token"]

    if not refresh_token:
        return False

    if not CLIENT_ID or not CLIENT_SECRET:
        return False

    data = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token
    }

    try:

        response = requests.post(
            ML_TOKEN,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type":
                    "application/x-www-form-urlencoded"
            },
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "[ML REFRESH ERRO]",
                response.status_code,
                response.text
            )

            return False

        result = response.json()

        save_auth(
            access_token=result.get(
                "access_token"
            ),
            refresh_token=result.get(
                "refresh_token"
            ),
            token_type=result.get(
                "token_type",
                "Bearer"
            ),
            expires_in=result.get(
                "expires_in",
                21600
            ),
            user_id=result.get(
                "user_id"
            ),
            scope=result.get(
                "scope",
                ""
            )
        )

        print("[ML] Access Token renovado")

        return True

    except Exception as e:

        print(
            "[ML REFRESH EXCEPTION]",
            e
        )

        return False


# ============================================================
# GARANTIR TOKEN
# ============================================================

def get_valid_access_token():

    auth = get_auth()

    if not auth:
        return None

    if token_expired(auth):

        ok = refresh_access_token()

        if not ok:
            return None

        auth = get_auth()

    return auth["access_token"]


# ============================================================
# REQUEST MERCADO LIVRE
# ============================================================

def ml_get(
    endpoint,
    params=None,
    authenticated=False
):

    headers = {
        "Accept": "application/json",
        "User-Agent":
            "PromocoesML/2.0"
    }

    if authenticated:

        token = get_valid_access_token()

        if not token:

            raise Exception(
                "Mercado Livre não está conectado."
            )

        headers["Authorization"] = (
            f"Bearer {token}"
        )

    response = requests.get(
        ML_API + endpoint,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# UTILIDADES
# ============================================================

def money(value):

    try:

        value = float(value)

        return (
            f"R$ {value:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except Exception:

        return "R$ 0,00"


def calcular_desconto(
    preco,
    preco_antigo
):

    try:

        preco = float(preco)
        preco_antigo = float(
            preco_antigo
        )

        if (
            preco_antigo > preco
            and preco_antigo > 0
        ):

            return round(
                (
                    (
                        preco_antigo
                        - preco
                    )
                    / preco_antigo
                )
                * 100,
                1
            )

    except Exception:
        pass

    return 0


def limpar_titulo(titulo):

    if not titulo:
        return ""

    titulo = re.sub(
        r"\s+",
        " ",
        titulo
    ).strip()

    if len(titulo) > 110:

        titulo = (
            titulo[:107]
            + "..."
        )

    return titulo


def normalizar_item_id(valor):

    if not valor:
        return ""

    valor = valor.strip()

    match = re.search(
        r"(MLB\d+)",
        valor.upper()
    )

    if match:
        return match.group(1)

    return valor.upper()


# ============================================================
# BUSCA PRODUTOS
# ============================================================

def buscar_produtos(
    query,
    limit=20
):

    limit = max(
        1,
        min(
            int(limit),
            MAX_LIMIT
        )
    )

    data = ml_get(
        f"/sites/{SITE_ID}/search",
        params={
            "q": query,
            "limit": limit
        }
    )

    resultados = []

    for item in data.get(
        "results",
        []
    ):

        item_id = item.get("id")

        if not item_id:
            continue

        titulo = (
            item.get("title")
            or "Produto"
        )

        preco = item.get("price")

        preco_antigo = (
            item.get(
                "original_price"
            )
        )

        imagem = (
            item.get("thumbnail")
            or ""
        )

        permalink = (
            item.get("permalink")
            or ""
        )

        desconto = calcular_desconto(
            preco,
            preco_antigo
        )

        resultados.append({

            "id": item_id,

            "titulo":
                limpar_titulo(
                    titulo
                ),

            "preco":
                preco,

            "preco_formatado":
                money(preco),

            "preco_antigo":
                preco_antigo,

            "preco_antigo_formatado":
                (
                    money(
                        preco_antigo
                    )
                    if preco_antigo
                    else ""
                ),

            "desconto":
                desconto,

            "imagem":
                imagem,

            "permalink":
                permalink

        })

    return resultados


# ============================================================
# ITEM
# ============================================================

def buscar_item(item_id):

    item_id = normalizar_item_id(
        item_id
    )

    if not item_id:
        return None

    data = ml_get(
        f"/items/{item_id}"
    )

    preco = data.get(
        "price"
    )

    preco_antigo = data.get(
        "original_price"
    )

    desconto = calcular_desconto(
        preco,
        preco_antigo
    )

    imagem = ""

    pictures = (
        data.get("pictures")
        or []
    )

    if pictures:

        imagem = (
            pictures[0].get(
                "secure_url"
            )
            or pictures[0].get(
                "url"
            )
            or ""
        )

    if not imagem:

        imagem = (
            data.get(
                "thumbnail"
            )
            or ""
        )

    return {

        "id":
            data.get("id"),

        "titulo":
            limpar_titulo(
                data.get("title")
            ),

        "preco":
            preco,

        "preco_formatado":
            money(preco),

        "preco_antigo":
            preco_antigo,

        "preco_antigo_formatado":
            (
                money(
                    preco_antigo
                )
                if preco_antigo
                else ""
            ),

        "desconto":
            desconto,

        "imagem":
            imagem,

        "permalink":
            data.get(
                "permalink"
            )
            or "",

        "status":
            data.get(
                "status"
            ),

        "condition":
            data.get(
                "condition"
            ),

        "available_quantity":
            data.get(
                "available_quantity"
            ),

        "sold_quantity":
            data.get(
                "sold_quantity"
            )

    }


# ============================================================
# TEXTO OFERTA
# ============================================================

def gerar_texto_oferta(
    titulo,
    preco,
    preco_antigo=None,
    desconto=0,
    link=""
):

    linhas = []

    linhas.append(
        "🔥 OFERTA DO DIA 🔥"
    )

    linhas.append("")

    linhas.append(
        f"🛒 {titulo}"
    )

    linhas.append("")

    if (
        preco_antigo
        and desconto > 0
    ):

        linhas.append(
            f"❌ De: {money(preco_antigo)}"
        )

        linhas.append(
            f"🔥 Por: {money(preco)}"
        )

        linhas.append(
            f"🏷️ {desconto}% OFF"
        )

    else:

        linhas.append(
            f"💰 Por apenas: {money(preco)}"
        )

    linhas.append("")

    if link:

        linhas.append(
            "👉 COMPRAR AQUI:"
        )

        linhas.append(
            link
        )

    linhas.append("")

    linhas.append(
        "⚠️ Preço e disponibilidade "
        "podem mudar sem aviso."
    )

    return "\n".join(
        linhas
    )


# ============================================================
# OAUTH — CONECTAR
# ============================================================

@app.route("/conectar")
def conectar():

    if not CLIENT_ID:

        return render_template_string(
            OAUTH_ERROR_HTML,
            titulo="Configuração incompleta",
            mensagem=(
                "Não encontrei o Client ID "
                "do Mercado Livre nas variáveis "
                "do Railway."
            )
        )

    redirect_uri = get_redirect_uri()

    if not redirect_uri:

        return render_template_string(
            OAUTH_ERROR_HTML,
            titulo="Redirect URI ausente",
            mensagem=(
                "Não foi possível determinar "
                "a Redirect URI."
            )
        )

    state = secrets.token_urlsafe(
        32
    )

    session["ml_oauth_state"] = state

    params = {

        "response_type":
            "code",

        "client_id":
            CLIENT_ID,

        "redirect_uri":
            redirect_uri,

        "state":
            state

    }

    # --------------------------------------------------------
    # PKCE
    # --------------------------------------------------------

    if USE_PKCE:

        code_verifier = (
            secrets.token_urlsafe(
                64
            )
        )

        import hashlib
        import base64

        challenge = (
            hashlib.sha256(
                code_verifier.encode(
                    "utf-8"
                )
            ).digest()
        )

        code_challenge = (
            base64.urlsafe_b64encode(
                challenge
            )
            .decode(
                "utf-8"
            )
            .rstrip("=")
        )

        session[
            "ml_code_verifier"
        ] = code_verifier

        params[
            "code_challenge"
        ] = code_challenge

        params[
            "code_challenge_method"
        ] = "S256"

    authorization_url = (
        ML_AUTH
        + "?"
        + urlencode(params)
    )

    return redirect(
        authorization_url
    )


# ============================================================
# OAUTH — CALLBACK
# ============================================================

@app.route("/oauth/callback")
def oauth_callback():

    error = request.args.get(
        "error"
    )

    if error:

        descricao = request.args.get(
            "error_description",
            error
        )

        return render_template_string(
            OAUTH_ERROR_HTML,
            titulo="Mercado Livre",
            mensagem=(
                "A autorização foi cancelada "
                "ou recusada.<br><br>"
                f"{descricao}"
            )
        )

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    saved_state = session.pop(
        "ml_oauth_state",
        None
    )

    if not code:

        return render_template_string(
            OAUTH_ERROR_HTML,
            titulo="Erro de autorização",
            mensagem=(
                "O Mercado Livre não enviou "
                "o código de autorização."
            )
        )

    if not state or state != saved_state:

        return render_template_string(
            OAUTH_ERROR_HTML,
            titulo="Erro de segurança",
            mensagem=(
                "O parâmetro state não "
                "corresponde à solicitação "
                "iniciada pelo sistema."
            )
        )

    if not CLIENT_ID or not CLIENT_SECRET:

        return render_template_string(
            OAUTH_ERROR_HTML,
            titulo="Credenciais ausentes",
            mensagem=(
                "Client ID ou Client Secret "
                "não configurado."
            )
        )

    redirect_uri = get_redirect_uri()

    data = {

        "grant_type":
            "authorization_code",

        "client_id":
            CLIENT_ID,

        "client_secret":
            CLIENT_SECRET,

        "code":
            code,

        "redirect_uri":
            redirect_uri

    }

    # --------------------------------------------------------
    # PKCE
    # --------------------------------------------------------

    if USE_PKCE:

        code_verifier = session.pop(
            "ml_code_verifier",
            None
        )

        if not code_verifier:

            return render_template_string(
                OAUTH_ERROR_HTML,
                titulo="Erro PKCE",
                mensagem=(
                    "O code_verifier não foi "
                    "encontrado na sessão."
                )
            )

        data[
            "code_verifier"
        ] = code_verifier

    try:

        response = requests.post(

            ML_TOKEN,

            data=data,

            headers={
                "Accept":
                    "application/json",

                "Content-Type":
                    "application/x-www-form-urlencoded"
            },

            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            try:
                erro = response.json()

            except Exception:
                erro = {
                    "resposta":
                        response.text
                }

            print(
                "[ML OAUTH ERRO]",
                response.status_code,
                erro
            )

            return render_template_string(
                OAUTH_ERROR_HTML,
                titulo="Erro ao conectar",
                mensagem=(
                    "O Mercado Livre "
                    "recusou a troca do código "
                    "por token.<br><br>"
                    f"<pre>{erro}</pre>"
                )
            )

        token_data = response.json()

        access_token = token_data.get(
            "access_token"
        )

        refresh_token = token_data.get(
            "refresh_token"
        )

        if not access_token:

            return render_template_string(
                OAUTH_ERROR_HTML,
                titulo="Token não recebido",
                mensagem=(
                    "O Mercado Livre não "
                    "retornou um access token."
                )
            )

        save_auth(

            access_token=
                access_token,

            refresh_token=
                refresh_token,

            token_type=
                token_data.get(
                    "token_type",
                    "Bearer"
                ),

            expires_in=
                token_data.get(
                    "expires_in",
                    21600
                ),

            user_id=
                token_data.get(
                    "user_id"
                ),

            scope=
                token_data.get(
                    "scope",
                    ""
                )
        )

        print(
            "[ML] Conta conectada com sucesso"
        )

        return redirect(
            url_for("index")
            + "?conectado=1"
        )

    except Exception as e:

        print(
            "[ML CALLBACK EXCEPTION]",
            e
        )

        return render_template_string(
            OAUTH_ERROR_HTML,
            titulo="Erro de conexão",
            mensagem=str(e)
        )


# ============================================================
# DESCONECTAR
# ============================================================

@app.route("/desconectar")
def desconectar():

    delete_auth()

    return redirect(
        url_for("index")
        + "?desconectado=1"
    )


# ============================================================
# DADOS DA CONTA
# ============================================================

def get_user_me():

    token = get_valid_access_token()

    if not token:
        return None

    response = requests.get(

        ML_API + "/users/me",

        headers={
            "Authorization":
                f"Bearer {token}",

            "Accept":
                "application/json"
        },

        timeout=REQUEST_TIMEOUT
    )

    if response.status_code == 401:

        if refresh_access_token():

            token = get_valid_access_token()

            if not token:
                return None

            response = requests.get(

                ML_API + "/users/me",

                headers={
                    "Authorization":
                        f"Bearer {token}",

                    "Accept":
                        "application/json"
                },

                timeout=REQUEST_TIMEOUT
            )

    response.raise_for_status()

    return response.json()


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

<title>Promoções Mercado Livre</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    background: #f3f4f6;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    color: #222;
}

header {

    background: #ffe600;

    padding: 18px;

    text-align: center;

    box-shadow:
        0 2px 8px
        rgba(0,0,0,.12);
}

header h1 {

    margin: 0;

    font-size: 24px;
}

header p {

    margin:
        7px 0 0;

    font-size: 14px;
}

.container {

    max-width: 1100px;

    margin: auto;

    padding: 18px;
}

.box {

    background: white;

    border-radius: 14px;

    padding: 18px;

    margin-bottom: 18px;

    box-shadow:
        0 2px 10px
        rgba(0,0,0,.07);
}

.connection {

    display: flex;

    align-items: center;

    justify-content:
        space-between;

    gap: 15px;

    flex-wrap: wrap;
}

.connected {

    background: #d1e7dd;

    color: #0a5c36;

    padding: 10px 14px;

    border-radius: 9px;

    font-weight: bold;
}

.disconnected {

    background: #fff3cd;

    color: #664d03;

    padding: 10px 14px;

    border-radius: 9px;

    font-weight: bold;
}

.search {

    display: flex;

    gap: 10px;
}

.search input {

    flex: 1;
}

input,
textarea,
button {

    font-size: 16px;
}

input,
textarea {

    width: 100%;

    border:
        1px solid #ddd;

    border-radius: 9px;

    padding: 12px;
}

button {

    border: 0;

    border-radius: 9px;

    padding:
        12px 18px;

    cursor: pointer;

    font-weight: bold;
}

.btn {

    background: #3483fa;

    color: white;

    text-decoration: none;

    display: inline-block;

    text-align: center;
}

.btn-green {

    background: #00a650;

    color: white;

    text-decoration: none;

    display: inline-block;

    text-align: center;
}

.btn-red {

    background: #d32f2f;

    color: white;

    text-decoration: none;

    display: inline-block;

    text-align: center;
}

.btn-yellow {

    background: #ffe600;

    color: #222;
}

.grid {

    display: grid;

    grid-template-columns:
        repeat(
            auto-fill,
            minmax(280px, 1fr)
        );

    gap: 16px;
}

.card {

    background: white;

    border-radius: 14px;

    overflow: hidden;

    box-shadow:
        0 2px 10px
        rgba(0,0,0,.08);

    display: flex;

    flex-direction: column;
}

.card img {

    width: 100%;

    height: 230px;

    object-fit: contain;

    background: white;
}

.card-body {

    padding: 15px;
}

.title {

    font-weight: bold;

    line-height: 1.35;

    min-height: 58px;
}

.price-old {

    color: #777;

    text-decoration:
        line-through;

    margin-top: 10px;
}

.price {

    font-size: 25px;

    font-weight: bold;

    color: #00a650;

    margin-top: 3px;
}

.discount {

    display: inline-block;

    background: #e6f7ed;

    color: #008c45;

    padding:
        5px 8px;

    border-radius: 6px;

    font-weight: bold;

    margin-top: 7px;
}

.card-actions {

    display: flex;

    gap: 8px;

    margin-top: 12px;
}

.card-actions a,
.card-actions button {

    flex: 1;
}

.form-group {

    margin-top: 12px;
}

label {

    display: block;

    font-weight: bold;

    margin-bottom: 6px;
}

textarea {

    min-height: 150px;

    resize: vertical;
}

.alert {

    background: #fff3cd;

    padding: 12px;

    border-radius: 9px;

    margin-bottom: 15px;
}

.success {

    background: #d1e7dd;

    padding: 12px;

    border-radius: 9px;

    margin-bottom: 15px;

    color: #0a5c36;
}

.error {

    background: #f8d7da;

    padding: 15px;

    border-radius: 9px;

    color: #842029;
}

.small {

    color: #777;

    font-size: 13px;
}

.account {

    font-size: 14px;

    margin-top: 6px;
}

@media(max-width:600px) {

    .search {

        flex-direction:
            column;
    }

    .connection {

        align-items:
            stretch;
    }

    .card img {

        height: 210px;
    }

}

</style>

</head>

<body>


<header>

<h1>
🛒 Promoções Mercado Livre
</h1>

<p>
Seu painel de ofertas
</p>

</header>


<div class="container">


<!-- ===================================================== -->
<!-- CONEXÃO -->
<!-- ===================================================== -->

<div class="box">

<div class="connection">

<div>

{% if conectado %}

<div class="connected">
🟢 Mercado Livre conectado
</div>

{% if usuario %}

<div class="account">

👤
<strong>
{{ usuario.nickname or usuario.first_name or "Conta conectada" }}
</strong>

{% if usuario.id %}
<br>
ID: {{ usuario.id }}
{% endif %}

</div>

{% endif %}

{% else %}

<div class="disconnected">
🟡 Mercado Livre não conectado
</div>

{% endif %}

</div>


<div>

{% if conectado %}

<a
href="/desconectar"
class="btn-red"
style="
padding:12px 18px;
border-radius:9px;
"
>
Desconectar
</a>

{% else %}

<a
href="/conectar"
class="btn-green"
style="
padding:12px 18px;
border-radius:9px;
"
>
🔐 Conectar minha conta
</a>

{% endif %}

</div>

</div>

</div>


{% if mensagem %}

<div class="{{ mensagem_tipo }}">
{{ mensagem|safe }}
</div>

{% endif %}


<!-- ===================================================== -->
<!-- BUSCA -->
<!-- ===================================================== -->

<div class="box">

<h2>
🔎 Buscar produto
</h2>

<form
method="GET"
action="/buscar"
>

<div class="search">

<input
type="text"
name="q"
placeholder="Ex.: fone bluetooth, air fryer, celular..."
value="{{ query }}"
required
>

<button
class="btn"
type="submit"
>
Buscar
</button>

</div>

</form>

</div>


<!-- ===================================================== -->
<!-- RESULTADOS -->
<!-- ===================================================== -->

{% if resultados %}

<div class="box">

<div style="
margin-bottom:15px;
font-weight:bold;
">

{{ resultados|length }}
produto(s) encontrado(s)

</div>


<div class="grid">

{% for p in resultados %}

<div class="card">


{% if p.imagem %}

<img
src="{{ p.imagem }}"
alt="{{ p.titulo }}"
loading="lazy"
>

{% endif %}


<div class="card-body">


<div class="title">
{{ p.titulo }}
</div>


{% if p.preco_antigo
and p.desconto > 0 %}

<div class="price-old">
{{ p.preco_antigo_formatado }}
</div>

{% endif %}


<div class="price">
{{ p.preco_formatado }}
</div>


{% if p.desconto > 0 %}

<div class="discount">
{{ p.desconto }}% OFF
</div>

{% endif %}


<div class="card-actions">

<a
class="btn"
href="{{ p.permalink }}"
target="_blank"
>
Ver produto
</a>


<a
class="btn-green"
href="/gerar?item_id={{ p.id }}"
>
Criar oferta
</a>

</div>


</div>

</div>

{% endfor %}

</div>

</div>

{% endif %}


<!-- ===================================================== -->
<!-- PRODUTO -->
<!-- ===================================================== -->

{% if produto %}

<div class="box">

<h2>
🔥 Criar oferta
</h2>


<div class="grid">


<div>

{% if produto.imagem %}

<img
src="{{ produto.imagem }}"
style="
width:100%;
max-height:320px;
object-fit:contain;
border-radius:10px;
"
>

{% endif %}

</div>


<div>

<h3>
{{ produto.titulo }}
</h3>


{% if produto.preco_antigo
and produto.desconto > 0 %}

<p>

De:

<strong
style="
text-decoration:line-through;
"
>

{{ produto.preco_antigo_formatado }}

</strong>

</p>

{% endif %}


<p>

Preço:

<strong
style="
font-size:25px;
color:#00a650;
"
>

{{ produto.preco_formatado }}

</strong>

</p>


{% if produto.desconto > 0 %}

<p>

🏷️

<strong>
{{ produto.desconto }}% OFF
</strong>

</p>

{% endif %}

</div>

</div>


<form
method="POST"
action="/gerar"
>


<input
type="hidden"
name="item_id"
value="{{ produto.id }}"
>


<div class="form-group">

<label>
🔗 Seu link de afiliado
</label>

<input
type="url"
name="link_afiliado"
placeholder="Cole seu link de afiliado aqui"
>

<div class="small">
O link de afiliado continua sendo o link fornecido pelo seu programa de afiliados.
</div>

</div>


<div class="form-group">

<label>
📝 Texto da oferta
</label>

<textarea
name="texto"
>{{ texto }}</textarea>

</div>


<button
class="btn-green"
type="submit"
>
💾 Salvar oferta
</button>


</form>

</div>

{% endif %}


<!-- ===================================================== -->
<!-- OFERTAS SALVAS -->
<!-- ===================================================== -->

<div class="box">

<h2>
📦 Ofertas salvas
</h2>


{% if ofertas %}

<div class="grid">

{% for oferta in ofertas %}

<div class="card">


{% if oferta.imagem %}

<img
src="{{ oferta.imagem }}"
loading="lazy"
>

{% endif %}


<div class="card-body">


<div class="title">
{{ oferta.titulo }}
</div>


<div class="price">
{{ money(oferta.preco) }}
</div>


{% if oferta.desconto > 0 %}

<div class="discount">
{{ oferta.desconto }}% OFF
</div>

{% endif %}


<div class="form-group">

<textarea
id="texto{{ oferta.id }}"
readonly
>{{ oferta.texto }}</textarea>

</div>


<div class="card-actions">

<button
class="btn-yellow"
onclick="copiarTexto('texto{{ oferta.id }}')"
>
📋 Copiar
</button>


{% if oferta.link_afiliado %}

<a
class="btn-green"
href="{{ oferta.link_afiliado }}"
target="_blank"
>
Abrir link
</a>

{% endif %}

</div>


</div>

</div>

{% endfor %}

</div>

{% else %}

<p>
Nenhuma oferta salva ainda.
</p>

{% endif %}

</div>


<div
style="
text-align:center;
margin:25px 0;
color:#777;
font-size:13px;
"
>

<a href="/health">
Status
</a>

&nbsp; | &nbsp;

<a href="/mercadolivre/status">
API Mercado Livre
</a>

&nbsp; | &nbsp;

<a href="/mercadolivre/diagnostico">
Diagnóstico
</a>

</div>


</div>


<script>

function copiarTexto(id) {

    const elemento =
        document.getElementById(id);

    navigator.clipboard
        .writeText(elemento.value)
        .then(function() {

            alert(
                "Oferta copiada!"
            );

        })
        .catch(function() {

            elemento.select();

            document.execCommand(
                "copy"
            );

            alert(
                "Oferta copiada!"
            );

        });

}

</script>


</body>

</html>
"""


# ============================================================
# OAUTH ERROR HTML
# ============================================================

OAUTH_ERROR_HTML = """

<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1.0">

<title>Mercado Livre</title>

<style>

body {

    margin:0;

    background:#f3f4f6;

    font-family:Arial;

    display:flex;

    align-items:center;

    justify-content:center;

    min-height:100vh;

}

.box {

    width:90%;

    max-width:600px;

    background:white;

    padding:25px;

    border-radius:15px;

    box-shadow:
        0 4px 20px
        rgba(0,0,0,.12);

}

h1 {

    margin-top:0;
}

a {

    display:inline-block;

    margin-top:15px;

    padding:12px 18px;

    background:#ffe600;

    color:#222;

    text-decoration:none;

    border-radius:9px;

    font-weight:bold;
}

</style>

</head>

<body>

<div class="box">

<h1>
{{ titulo }}
</h1>

<p>
{{ mensagem|safe }}
</p>

<a href="/">
Voltar
</a>

</div>

</body>

</html>

"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    conectado = False

    usuario = None

    auth = get_auth()

    if auth:

        token = get_valid_access_token()

        if token:

            conectado = True

            try:

                usuario = get_user_me()

            except Exception as e:

                print(
                    "[ML USER ERRO]",
                    e
                )

                usuario = {
                    "id":
                        auth["user_id"]
                }

    conn = get_db()

    ofertas = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    mensagem = ""

    mensagem_tipo = ""

    if request.args.get(
        "conectado"
    ) == "1":

        mensagem = (
            "✅ <strong>Conta do Mercado Livre "
            "conectada com sucesso!</strong>"
        )

        mensagem_tipo = "success"

    elif request.args.get(
        "desconectado"
    ) == "1":

        mensagem = (
            "Conta desconectada."
        )

        mensagem_tipo = "alert"

    return render_template_string(

        HTML,

        resultados=[],

        query="",

        produto=None,

        texto="",

        ofertas=ofertas,

        money=money,

        conectado=conectado,

        usuario=usuario,

        mensagem=mensagem,

        mensagem_tipo=mensagem_tipo
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

        return redirect(
            url_for("index")
        )

    try:

        resultados = buscar_produtos(
            query,
            DEFAULT_LIMIT
        )

        message = ""

        message_type = ""

        if not resultados:

            message = (
                "Nenhum produto encontrado."
            )

            message_type = "alert"

    except Exception as e:

        resultados = []

        message = (
            "Erro ao buscar produtos: "
            + str(e)
        )

        message_type = "alert"

    auth = get_auth()

    conectado = bool(
        auth
        and get_valid_access_token()
    )

    usuario = None

    if conectado:

        try:
            usuario = get_user_me()

        except Exception:
            pass

    conn = get_db()

    ofertas = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    return render_template_string(

        HTML,

        resultados=resultados,

        query=query,

        produto=None,

        texto="",

        ofertas=ofertas,

        money=money,

        conectado=conectado,

        usuario=usuario,

        mensagem=message,

        mensagem_tipo=message_type
    )


# ============================================================
# ABRIR PRODUTO
# ============================================================

@app.route(
    "/gerar",
    methods=["GET"]
)
def abrir_gerar():

    item_id = request.args.get(
        "item_id",
        ""
    ).strip()

    if not item_id:

        return redirect(
            url_for("index")
        )

    try:

        produto = buscar_item(
            item_id
        )

        if not produto:

            raise Exception(
                "Produto não encontrado."
            )

        texto = gerar_texto_oferta(

            produto["titulo"],

            produto["preco"],

            produto["preco_antigo"],

            produto["desconto"],

            ""
        )

    except Exception as e:

        produto = None

        texto = ""

        error = str(e)

    else:

        error = ""

    auth = get_auth()

    conectado = bool(
        auth
        and get_valid_access_token()
    )

    usuario = None

    if conectado:

        try:

            usuario = get_user_me()

        except Exception:
            pass

    conn = get_db()

    ofertas = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    return render_template_string(

        HTML,

        resultados=[],

        query="",

        produto=produto,

        texto=texto,

        ofertas=ofertas,

        money=money,

        conectado=conectado,

        usuario=usuario,

        mensagem=error,

        mensagem_tipo=(
            "alert"
            if error
            else ""
        )
    )


# ============================================================
# SALVAR OFERTA
# ============================================================

@app.route(
    "/gerar",
    methods=["POST"]
)
def salvar_oferta():

    item_id = normalizar_item_id(
        request.form.get(
            "item_id",
            ""
        )
    )

    link_afiliado = (
        request.form.get(
            "link_afiliado",
            ""
        ).strip()
    )

    texto = (
        request.form.get(
            "texto",
            ""
        ).strip()
    )

    if not item_id:

        return redirect(
            url_for("index")
        )

    try:

        produto = buscar_item(
            item_id
        )

        if not produto:

            raise Exception(
                "Produto não encontrado."
            )

        if not texto:

            texto = gerar_texto_oferta(

                produto["titulo"],

                produto["preco"],

                produto["preco_antigo"],

                produto["desconto"],

                link_afiliado
            )

        elif (
            link_afiliado
            and link_afiliado not in texto
        ):

            texto += (
                "\n\n👉 COMPRAR AQUI:\n"
                + link_afiliado
            )

        conn = get_db()

        conn.execute("""
            INSERT INTO ofertas (
                item_id,
                titulo,
                preco,
                preco_antigo,
                desconto,
                imagem,
                permalink,
                link_afiliado,
                texto,
                criado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (

            produto["id"],

            produto["titulo"],

            produto["preco"],

            produto["preco_antigo"],

            produto["desconto"],

            produto["imagem"],

            produto["permalink"],

            link_afiliado,

            texto,

            datetime.now().isoformat()

        ))

        conn.commit()

        conn.close()

        return redirect(
            url_for("index")
        )

    except Exception as e:

        return redirect(
            url_for("index")
            + "?erro="
            + str(e)
        )


# ============================================================
# API — BUSCAR
# ============================================================

@app.route("/api/buscar")
def api_buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:

        return jsonify({
            "erro":
                "Informe q"
        }), 400

    try:

        produtos = buscar_produtos(
            query,
            DEFAULT_LIMIT
        )

        return jsonify({

            "erro":
                None,

            "query":
                query,

            "total":
                len(produtos),

            "produtos":
                produtos

        })

    except Exception as e:

        return jsonify({
            "erro":
                str(e)
        }), 500


# ============================================================
# API — ITEM
# ============================================================

@app.route(
    "/api/item/<item_id>"
)
def api_item(item_id):

    try:

        produto = buscar_item(
            item_id
        )

        if not produto:

            return jsonify({
                "erro":
                    "Produto não encontrado"
            }), 404

        return jsonify({

            "erro":
                None,

            "produto":
                produto

        })

    except Exception as e:

        return jsonify({
            "erro":
                str(e)
        }), 500


# ============================================================
# STATUS MERCADO LIVRE
# ============================================================

@app.route(
    "/mercadolivre/status"
)
def mercadolivre_status():

    auth = get_auth()

    conectado = False

    usuario = None

    erro = None

    if auth:

        token = get_valid_access_token()

        if token:

            conectado = True

            try:

                usuario = get_user_me()

            except Exception as e:

                erro = str(e)

    return jsonify({

        "ok":
            True,

        "oauth_configurado":
            oauth_configured(),

        "conectado":
            conectado,

        "user_id":
            (
                auth["user_id"]
                if auth
                else None
            ),

        "usuario":
            usuario,

        "erro":
            erro

    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    resultado = {

        "site":
            SITE_ID,

        "api":
            ML_API,

        "client_id_configurado":
            bool(CLIENT_ID),

        "client_secret_configurado":
            bool(CLIENT_SECRET),

        "redirect_uri":
            get_redirect_uri(),

        "pkce":
            USE_PKCE,

        "oauth_configurado":
            oauth_configured(),

        "conta_conectada":
            bool(get_auth()),

    }

    return jsonify(
        resultado
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({

        "ok":
            True,

        "app":
            "Promocoes Mercado Livre",

        "status":
            "online",

        "oauth":
            oauth_configured(),

        "conectado":
            bool(get_auth())

    })


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