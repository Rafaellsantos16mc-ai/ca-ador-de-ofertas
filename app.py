import os
import sqlite3
import secrets
import hashlib
import base64
from datetime import datetime, timezone
from urllib.parse import urlencode, quote

import requests

from flask import (
    Flask,
    request,
    render_template_string,
    redirect,
    url_for,
    session,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

# Permite usar HTTPS corretamente no Railway
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ============================================================
# CONFIGURAÇÕES
# ============================================================

DB_FILE = os.getenv("DB_FILE", "ofertas.db")

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback",
).strip()

ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

ML_SEARCH_URL = "https://api.mercadolibre.com/sites/MLB/search"

SITE_ID = "MLB"


# ============================================================
# BANCO DE DADOS
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produto_id TEXT,
            titulo TEXT,
            preco REAL,
            preco_original REAL,
            desconto INTEGER,
            link TEXT,
            imagem TEXT,
            criado_em TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mercadolivre_token (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            scope TEXT,
            atualizado_em TEXT
        )
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def agora_timestamp():
    return int(datetime.now(timezone.utc).timestamp())


def moeda(valor):
    if valor is None:
        return "R$ 0,00"

    try:
        valor = float(valor)
    except Exception:
        valor = 0

    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")

    return f"R$ {texto}"


def limpar_titulo(titulo):
    if not titulo:
        return ""

    titulo = " ".join(str(titulo).split())

    return titulo[:150]


def calcular_desconto(preco, preco_original):
    try:
        preco = float(preco or 0)
        preco_original = float(preco_original or 0)

        if preco_original <= 0 or preco >= preco_original:
            return 0

        desconto = ((preco_original - preco) / preco_original) * 100

        return int(round(desconto))

    except Exception:
        return 0


# ============================================================
# PKCE
# ============================================================

def gerar_code_verifier():
    """
    Gera um code_verifier seguro para PKCE.
    """

    return secrets.token_urlsafe(64)


def gerar_code_challenge(code_verifier):
    """
    SHA256 + Base64 URL-safe sem "=".
    """

    digest = hashlib.sha256(
        code_verifier.encode("utf-8")
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")


# ============================================================
# TOKEN
# ============================================================

def salvar_token(
    access_token,
    refresh_token,
    expires_in,
    user_id=None,
    scope=None,
):
    conn = get_db()

    expires_at = agora_timestamp() + int(expires_in or 21600)

    conn.execute(
        """
        INSERT INTO mercadolivre_token
        (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            scope,
            atualizado_em
        )
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            expires_at = excluded.expires_at,
            user_id = excluded.user_id,
            scope = excluded.scope,
            atualizado_em = excluded.atualizado_em
        """,
        (
            access_token,
            refresh_token,
            expires_at,
            str(user_id) if user_id else None,
            scope,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    conn.commit()
    conn.close()


def obter_token_db():
    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM mercadolivre_token
        WHERE id = 1
        """
    ).fetchone()

    conn.close()

    if not row:
        return None

    return dict(row)


def apagar_token():
    conn = get_db()

    conn.execute(
        """
        DELETE FROM mercadolivre_token
        WHERE id = 1
        """
    )

    conn.commit()
    conn.close()


def renovar_access_token(refresh_token):
    """
    Usa o refresh token atual para obter
    um novo access token e um NOVO refresh token.
    """

    if not ML_CLIENT_ID or not ML_CLIENT_SECRET:
        return False, "Credenciais do Mercado Livre não configuradas."

    if not refresh_token:
        return False, "Refresh token não encontrado."

    try:

        resposta = requests.post(
            ML_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )

        if resposta.status_code != 200:

            try:
                erro = resposta.json()
            except Exception:
                erro = resposta.text

            return False, f"Erro ao renovar token: {erro}"

        dados = resposta.json()

        novo_access_token = dados.get("access_token")
        novo_refresh_token = dados.get("refresh_token")
        expires_in = dados.get("expires_in", 21600)

        if not novo_access_token or not novo_refresh_token:
            return False, "Mercado Livre não retornou os novos tokens."

        salvar_token(
            access_token=novo_access_token,
            refresh_token=novo_refresh_token,
            expires_in=expires_in,
            user_id=dados.get("user_id"),
            scope=dados.get("scope"),
        )

        return True, novo_access_token

    except Exception as e:
        return False, f"Erro de conexão ao renovar token: {e}"


def obter_access_token():
    """
    Retorna um access token válido.

    Se estiver próximo de expirar, renova automaticamente.
    """

    token = obter_token_db()

    if not token:
        return None

    access_token = token.get("access_token")
    refresh_token = token.get("refresh_token")
    expires_at = int(token.get("expires_at") or 0)

    agora = agora_timestamp()

    # Renova 2 minutos antes de expirar
    if access_token and expires_at > agora + 120:
        return access_token

    if not refresh_token:
        apagar_token()
        return None

    sucesso, resultado = renovar_access_token(refresh_token)

    if sucesso:
        return resultado

    print("[ERRO REFRESH]", resultado)

    apagar_token()

    return None


# ============================================================
# MERCADO LIVRE - OAUTH
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:
        return """
        <h2>ML_CLIENT_ID não configurado</h2>
        <p>Adicione a variável no Railway.</p>
        """, 500

    if not ML_REDIRECT_URI:
        return """
        <h2>ML_REDIRECT_URI não configurado</h2>
        """, 500

    # State para proteger o fluxo OAuth
    state = secrets.token_urlsafe(32)

    # PKCE
    code_verifier = gerar_code_verifier()
    code_challenge = gerar_code_challenge(code_verifier)

    session["ml_oauth_state"] = state
    session["ml_code_verifier"] = code_verifier

    parametros = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": "offline_access read",
    }

    url = ML_AUTH_URL + "?" + urlencode(parametros)

    return redirect(url)


@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    # Verifica se o Mercado Livre retornou erro
    erro = request.args.get("error")

    if erro:

        descricao = request.args.get(
            "error_description",
            "Autorização recusada.",
        )

        return render_template_string(
            """
            <!doctype html>
            <html lang="pt-br">
            <head>
                <meta charset="utf-8">
                <meta name="viewport"
                      content="width=device-width, initial-scale=1">
                <title>Erro Mercado Livre</title>
                <style>
                    body {
                        font-family: Arial;
                        padding: 30px;
                        background: #f5f5f5;
                    }

                    .box {
                        max-width: 700px;
                        margin: auto;
                        background: white;
                        padding: 25px;
                        border-radius: 15px;
                    }

                    a {
                        display: inline-block;
                        margin-top: 20px;
                        background: #3483fa;
                        color: white;
                        padding: 12px 18px;
                        border-radius: 8px;
                        text-decoration: none;
                    }
                </style>
            </head>

            <body>
                <div class="box">

                    <h2>Autorização não concluída</h2>

                    <p><strong>Erro:</strong> {{ erro }}</p>

                    <p>{{ descricao }}</p>

                    <a href="/">
                        Voltar
                    </a>

                </div>
            </body>
            </html>
            """,
            erro=erro,
            descricao=descricao,
        ), 400

    code = request.args.get("code")
    state = request.args.get("state")

    state_salvo = session.get("ml_oauth_state")
    code_verifier = session.get("ml_code_verifier")

    # Validação do state
    if not state or not state_salvo or state != state_salvo:

        return """
        <h2>Erro de segurança</h2>
        <p>O state do OAuth não corresponde à solicitação iniciada.</p>
        """, 400

    if not code:

        return """
        <h2>Código de autorização não recebido.</h2>
        """, 400

    if not code_verifier:

        return """
        <h2>Code verifier não encontrado.</h2>
        <p>Inicie a conexão novamente.</p>
        """, 400

    # Troca authorization code por tokens
    try:

        resposta = requests.post(
            ML_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "code": code,
                "redirect_uri": ML_REDIRECT_URI,
                "code_verifier": code_verifier,
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )

    except Exception as e:

        return f"""
        <h2>Erro de conexão</h2>
        <p>{e}</p>
        """, 500

    if resposta.status_code != 200:

        try:
            erro_json = resposta.json()
        except Exception:
            erro_json = resposta.text

        print("[ERRO OAUTH]", erro_json)

        return render_template_string(
            """
            <!doctype html>
            <html lang="pt-br">
            <head>
                <meta charset="utf-8">
                <meta name="viewport"
                      content="width=device-width, initial-scale=1">

                <title>Erro OAuth</title>

                <style>
                    body {
                        font-family: Arial;
                        background: #f5f5f5;
                        padding: 30px;
                    }

                    .box {
                        max-width: 750px;
                        margin: auto;
                        background: white;
                        padding: 25px;
                        border-radius: 15px;
                    }

                    pre {
                        white-space: pre-wrap;
                        word-break: break-word;
                        background: #eee;
                        padding: 15px;
                        border-radius: 8px;
                    }

                    a {
                        display: inline-block;
                        margin-top: 20px;
                        background: #3483fa;
                        color: white;
                        padding: 12px 18px;
                        border-radius: 8px;
                        text-decoration: none;
                    }
                </style>
            </head>

            <body>

                <div class="box">

                    <h2>❌ Erro ao conectar ao Mercado Livre</h2>

                    <pre>{{ erro }}</pre>

                    <a href="/">
                        Voltar
                    </a>

                </div>

            </body>
            </html>
            """,
            erro=erro_json,
        ), 400

    dados = resposta.json()

    access_token = dados.get("access_token")
    refresh_token = dados.get("refresh_token")

    if not access_token:

        return """
        <h2>Mercado Livre não retornou o Access Token.</h2>
        """, 500

    if not refresh_token:

        return """
        <h2>Mercado Livre não retornou o Refresh Token.</h2>
        <p>Verifique se o fluxo de Refresh Token está habilitado na aplicação.</p>
        """, 500

    salvar_token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=dados.get("expires_in", 21600),
        user_id=dados.get("user_id"),
        scope=dados.get("scope"),
    )

    # Limpa dados temporários do OAuth
    session.pop("ml_oauth_state", None)
    session.pop("ml_code_verifier", None)

    return redirect(url_for("index"))


# ============================================================
# CALLBACK 2
# ============================================================

@app.route("/mercadolivre/callback2")
def mercadolivre_callback2():

    """
    Segunda URI cadastrada na aplicação.

    Redireciona para o callback principal.
    """

    parametros = request.query_string.decode("utf-8")

    if parametros:
        return redirect(
            url_for("mercadolivre_callback")
            + "?"
            + parametros
        )

    return redirect(url_for("mercadolivre_login"))


# ============================================================
# NOTIFICAÇÕES
# ============================================================

@app.route("/mercadolivre/notificacoes", methods=["GET", "POST"])
def mercadolivre_notificacoes():

    print("========================================")
    print("[MERCADO LIVRE] NOTIFICAÇÃO RECEBIDA")
    print("Método:", request.method)
    print("Dados:", request.get_json(silent=True))
    print("========================================")

    return "OK", 200


# ============================================================
# TESTAR CONEXÃO COM MERCADO LIVRE
# ============================================================

@app.route("/mercadolivre/status")
def mercadolivre_status():

    token = obter_access_token()

    if not token:

        return render_template_string(
            """
            <!doctype html>
            <html lang="pt-br">

            <head>
                <meta charset="utf-8">

                <meta name="viewport"
                      content="width=device-width, initial-scale=1">

                <title>Mercado Livre</title>

                <style>

                    body {
                        font-family: Arial;
                        background: #f5f5f5;
                        padding: 25px;
                    }

                    .box {
                        max-width: 650px;
                        margin: auto;
                        background: white;
                        padding: 25px;
                        border-radius: 15px;
                    }

                    a {
                        display: inline-block;
                        background: #3483fa;
                        color: white;
                        padding: 12px 18px;
                        border-radius: 8px;
                        text-decoration: none;
                        margin-top: 15px;
                    }

                </style>
            </head>

            <body>

                <div class="box">

                    <h2>🔴 Mercado Livre desconectado</h2>

                    <p>
                        O aplicativo ainda não possui um Access Token válido.
                    </p>

                    <a href="/mercadolivre/login">
                        Conectar Mercado Livre
                    </a>

                    <br>

                    <a href="/">
                        Voltar
                    </a>

                </div>

            </body>
            </html>
            """
        )

    # Testa o token
    try:

        resposta = requests.get(
            "https://api.mercadolibre.com/users/me",
            headers={
                "Authorization": f"Bearer {token}"
            },
            timeout=30,
        )

    except Exception as e:

        return f"""
        <h2>Erro de conexão</h2>
        <p>{e}</p>
        """, 500

    if resposta.status_code != 200:

        return render_template_string(
            """
            <h2>❌ Token não funcionou</h2>

            <pre>{{ erro }}</pre>

            <a href="/mercadolivre/login">
                Conectar novamente
            </a>
            """,
            erro=resposta.text,
        ), 400

    dados = resposta.json()

    return render_template_string(
        """
        <!doctype html>
        <html lang="pt-br">

        <head>
            <meta charset="utf-8">

            <meta name="viewport"
                  content="width=device-width, initial-scale=1">

            <title>Status Mercado Livre</title>

            <style>

                body {
                    font-family: Arial;
                    background: #f5f5f5;
                    padding: 25px;
                }

                .box {
                    max-width: 650px;
                    margin: auto;
                    background: white;
                    padding: 25px;
                    border-radius: 15px;
                }

                .ok {
                    color: #16883d;
                    font-weight: bold;
                    font-size: 20px;
                }

                a {
                    display: inline-block;
                    background: #3483fa;
                    color: white;
                    padding: 12px 18px;
                    border-radius: 8px;
                    text-decoration: none;
                    margin-top: 15px;
                }

            </style>
        </head>

        <body>

            <div class="box">

                <div class="ok">
                    🟢 Mercado Livre conectado
                </div>

                <hr>

                <p>
                    <strong>ID do usuário:</strong>
                    {{ dados.get("id") }}
                </p>

                <p>
                    <strong>Nickname:</strong>
                    {{ dados.get("nickname") }}
                </p>

                <a href="/">
                    Voltar para ofertas
                </a>

            </div>

        </body>
        </html>
        """,
        dados=dados,
    )


# ============================================================
# BUSCAR PRODUTOS
# ============================================================

def buscar_produtos(query, limit=30):

    access_token = obter_access_token()

    if not access_token:

        return {
            "erro": "O Mercado Livre ainda não está conectado.",
            "produtos": [],
        }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "CacadorDeOfertas/1.0",
    }

    try:

        resposta = requests.get(
            ML_SEARCH_URL,
            params={
                "q": query,
                "limit": limit,
                "offset": 0,
            },
            headers=headers,
            timeout=30,
        )

    except Exception as e:

        return {
            "erro": f"Erro de conexão: {e}",
            "produtos": [],
        }

    if resposta.status_code == 401:

        apagar_token()

        return {
            "erro": "O token do Mercado Livre expirou. Conecte novamente.",
            "produtos": [],
        }

    if resposta.status_code == 403:

        return {
            "erro": (
                "Mercado Livre recusou a consulta (403). "
                "Verifique a autorização da aplicação, permissões "
                "e se a conta autorizada é a principal."
            ),
            "produtos": [],
        }

    if resposta.status_code != 200:

        try:
            erro = resposta.json()
        except Exception:
            erro = resposta.text

        return {
            "erro": f"Erro Mercado Livre ({resposta.status_code}): {erro}",
            "produtos": [],
        }

    try:
        dados = resposta.json()
    except Exception:

        return {
            "erro": "Resposta inválida do Mercado Livre.",
            "produtos": [],
        }

    produtos = []

    for item in dados.get("results", []):

        preco = item.get("price")
        preco_original = (
            item.get("original_price")
            or item.get("price")
        )

        desconto = calcular_desconto(
            preco,
            preco_original,
        )

        produtos.append(
            {
                "id": item.get("id"),
                "titulo": limpar_titulo(
                    item.get("title", "")
                ),
                "preco": preco,
                "preco_original": preco_original,
                "desconto": desconto,
                "link": item.get("permalink"),
                "imagem": item.get("thumbnail"),
            }
        )

    return {
        "erro": None,
        "produtos": produtos,
    }


# ============================================================
# SALVAR OFERTA
# ============================================================

def salvar_oferta(produto):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO ofertas
        (
            produto_id,
            titulo,
            preco,
            preco_original,
            desconto,
            link,
            imagem,
            criado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            produto.get("id"),
            produto.get("titulo"),
            produto.get("preco"),
            produto.get("preco_original"),
            produto.get("desconto"),
            produto.get("link"),
            produto.get("imagem"),
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# GERAR MENSAGEM
# ============================================================

def gerar_mensagem(produto, link_afiliado=""):

    titulo = produto.get("titulo", "")
    preco = produto.get("preco")
    preco_original = produto.get("preco_original")
    desconto = produto.get("desconto", 0)

    link = link_afiliado.strip()

    if not link:
        link = produto.get("link", "")

    texto = f"""🔥 OFERTA ENCONTRADA!

🛒 {titulo}

💰 Agora: {moeda(preco)}
"""

    if desconto > 0:

        texto += f"""
🏷️ Antes: {moeda(preco_original)}
📉 Desconto: {desconto}% OFF
"""

    texto += f"""
🚚 Confira preço e disponibilidade:

👉 {link}

⚡ Pode mudar a qualquer momento!
"""

    return texto


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

HTML = """
<!doctype html>

<html lang="pt-br">

<head>

    <meta charset="utf-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <title>Caçador de Ofertas</title>

    <style>

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            padding: 20px;
            font-family: Arial, sans-serif;
            background: #f5f5f5;
            color: #222;
        }

        .container {
            max-width: 950px;
            margin: auto;
        }

        .header {
            background: #ffe600;
            padding: 22px;
            border-radius: 16px;
            margin-bottom: 20px;
        }

        .header h1 {
            margin: 0 0 6px 0;
        }

        .header p {
            margin: 0;
        }

        .card {
            background: white;
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 3px 12px rgba(0,0,0,0.06);
        }

        input {
            width: 100%;
            padding: 14px;
            border: 1px solid #ccc;
            border-radius: 10px;
            font-size: 16px;
            margin-bottom: 12px;
        }

        button,
        .button {
            display: inline-block;
            border: none;
            background: #3483fa;
            color: white;
            padding: 13px 18px;
            border-radius: 10px;
            font-size: 16px;
            text-decoration: none;
            cursor: pointer;
        }

        .button-green {
            background: #16883d;
        }

        .button-yellow {
            background: #ffe600;
            color: #222;
        }

        .produto {
            border: 1px solid #ddd;
            border-radius: 14px;
            padding: 15px;
            margin-top: 15px;
        }

        .produto img {
            width: 120px;
            height: 120px;
            object-fit: contain;
            display: block;
            margin-bottom: 10px;
        }

        .titulo {
            font-size: 18px;
            font-weight: bold;
        }

        .preco {
            font-size: 22px;
            font-weight: bold;
            margin-top: 8px;
        }

        .antigo {
            text-decoration: line-through;
            color: #777;
        }

        .desconto {
            color: #16883d;
            font-weight: bold;
        }

        textarea {
            width: 100%;
            min-height: 220px;
            padding: 15px;
            border-radius: 10px;
            border: 1px solid #ccc;
            font-size: 15px;
        }

        .erro {
            background: #ffe1e1;
            color: #a40000;
            padding: 14px;
            border-radius: 10px;
            margin-top: 15px;
        }

        .sucesso {
            background: #e1f7e8;
            color: #12652d;
            padding: 14px;
            border-radius: 10px;
            margin-top: 15px;
        }

        .top-buttons {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-top: 15px;
        }

        @media (max-width: 600px) {

            body {
                padding: 12px;
            }

            .card {
                padding: 15px;
            }

        }

    </style>

</head>

<body>

<div class="container">

    <div class="header">

        <h1>🛒 Caçador de Ofertas</h1>

        <p>
            Busque produtos do Mercado Livre e prepare
            publicações para seu canal.
        </p>

        <div class="top-buttons">

            {% if conectado %}

                <a
                    class="button button-green"
                    href="/mercadolivre/status"
                >
                    🟢 Mercado Livre conectado
                </a>

            {% else %}

                <a
                    class="button"
                    href="/mercadolivre/login"
                >
                    🔐 Conectar Mercado Livre
                </a>

            {% endif %}

        </div>

    </div>


    {% if mensagem %}

        <div class="card">

            <div class="sucesso">
                {{ mensagem }}
            </div>

        </div>

    {% endif %}


    <div class="card">

        <h2>🔎 Buscar produtos</h2>

        <form action="/buscar" method="get">

            <input
                type="text"
                name="q"
                placeholder="Ex.: air fryer, celular, TV 50..."
                value="{{ query }}"
                required
            >

            <button type="submit">
                Buscar ofertas
            </button>

        </form>

    </div>


    {% if erro %}

        <div class="card">

            <div class="erro">
                {{ erro }}
            </div>

            {% if "conect" in erro.lower() or "token" in erro.lower() %}

                <a
                    class="button"
                    href="/mercadolivre/login"
                    style="margin-top: 15px;"
                >
                    Conectar Mercado Livre
                </a>

            {% endif %}

        </div>

    {% endif %}


    {% if produtos %}

        <div class="card">

            <h2>
                🏷️ Produtos encontrados
            </h2>

            {% for produto in produtos %}

                <div class="produto">

                    {% if produto.imagem %}

                        <img
                            src="{{ produto.imagem }}"
                            alt="Produto"
                        >

                    {% endif %}

                    <div class="titulo">
                        {{ produto.titulo }}
                    </div>

                    <div class="preco">
                        {{ moeda(produto.preco) }}
                    </div>

                    {% if produto.preco_original and produto.preco_original > produto.preco %}

                        <div class="antigo">
                            Antes:
                            {{ moeda(produto.preco_original) }}
                        </div>

                    {% endif %}

                    {% if produto.desconto > 0 %}

                        <div class="desconto">
                            {{ produto.desconto }}% OFF
                        </div>

                    {% endif %}

                    <div style="margin-top: 12px;">

                        <a
                            class="button"
                            href="{{ produto.link }}"
                            target="_blank"
                        >
                            Ver produto
                        </a>

                    </div>


                    <form
                        action="/gerar"
                        method="post"
                        style="margin-top: 15px;"
                    >

                        <input
                            type="hidden"
                            name="produto_id"
                            value="{{ produto.id }}"
                        >

                        <input
                            type="hidden"
                            name="titulo"
                            value="{{ produto.titulo }}"
                        >

                        <input
                            type="hidden"
                            name="preco"
                            value="{{ produto.preco }}"
                        >

                        <input
                            type="hidden"
                            name="preco_original"
                            value="{{ produto.preco_original }}"
                        >

                        <input
                            type="hidden"
                            name="desconto"
                            value="{{ produto.desconto }}"
                        >

                        <input
                            type="hidden"
                            name="link"
                            value="{{ produto.link }}"
                        >

                        <input
                            type="hidden"
                            name="imagem"
                            value="{{ produto.imagem }}"
                        >

                        <input
                            type="text"
                            name="link_afiliado"
                            placeholder="Cole aqui seu link de afiliado"
                        >

                        <button
                            type="submit"
                            class="button button-green"
                        >
                            Gerar publicação
                        </button>

                    </form>

                </div>

            {% endfor %}

        </div>

    {% endif %}


    {% if publicacao %}

        <div class="card">

            <h2>
                📱 Publicação pronta
            </h2>

            <textarea id="texto">{{ publicacao }}</textarea>

            <button
                class="button"
                style="margin-top: 12px;"
                onclick="copiarTexto()"
            >
                📋 Copiar publicação
            </button>

        </div>

    {% endif %}


</div>


<script>

function copiarTexto() {

    const texto = document.getElementById("texto");

    texto.select();
    texto.setSelectionRange(0, 99999);

    navigator.clipboard.writeText(texto.value);

    alert("Publicação copiada!");

}

</script>


</body>

</html>
"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    token = obter_token_db()

    conectado = False

    if token:

        access_token = obter_access_token()

        if access_token:
            conectado = True

    return render_template_string(
        HTML,
        conectado=conectado,
        produtos=[],
        erro=None,
        query="",
        publicacao=None,
        mensagem=None,
        moeda=moeda,
    )


# ============================================================
# BUSCAR
# ============================================================

@app.route("/buscar")
def buscar():

    query = request.args.get("q", "").strip()

    if not query:

        return redirect(url_for("index"))

    resultado = buscar_produtos(
        query=query,
        limit=30,
    )

    return render_template_string(
        HTML,
        conectado=bool(obter_access_token()),
        produtos=resultado.get("produtos", []),
        erro=resultado.get("erro"),
        query=query,
        publicacao=None,
        mensagem=None,
        moeda=moeda,
    )


# ============================================================
# GERAR PUBLICAÇÃO
# ============================================================

@app.route("/gerar", methods=["POST"])
def gerar():

    produto = {
        "id": request.form.get("produto_id"),
        "titulo": request.form.get("titulo"),
        "preco": request.form.get("preco"),
        "preco_original": request.form.get("preco_original"),
        "desconto": int(
            request.form.get("desconto") or 0
        ),
        "link": request.form.get("link"),
        "imagem": request.form.get("imagem"),
    }

    link_afiliado = request.form.get(
        "link_afiliado",
        "",
    ).strip()

    salvar_oferta(produto)

    publicacao = gerar_mensagem(
        produto,
        link_afiliado,
    )

    return render_template_string(
        HTML,
        conectado=bool(obter_access_token()),
        produtos=[],
        erro=None,
        query="",
        publicacao=publicacao,
        mensagem="Oferta preparada com sucesso!",
        moeda=moeda,
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return {
        "status": "ok",
        "app": "Cacador de Ofertas",
        "mercadolivre_configurado": bool(
            ML_CLIENT_ID and ML_CLIENT_SECRET
        ),
    }


# ============================================================
# INICIALIZAÇÃO
# ============================================================

init_db()


if __name__ == "__main__":

    port = int(
        os.getenv("PORT", "8080")
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )