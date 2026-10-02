import os
import sqlite3
import secrets
import hashlib
import base64
import urllib.parse
import requests

from flask import (
    Flask,
    request,
    redirect,
    render_template_string,
    send_file,
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

ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_API_URL = "https://api.mercadolibre.com"
ML_SITE = "MLB"

DB_NAME = "ofertas.db"


# ============================================================
# BANCO
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            created_at INTEGER DEFAULT (strftime('%s','now'))
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
            categoria TEXT,
            link TEXT,
            imagem TEXT,
            criado_em INTEGER DEFAULT (strftime('%s','now'))
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# TOKEN
# ============================================================

def salvar_token(data):

    conn = get_db()

    conn.execute("DELETE FROM tokens")

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
        data.get("expires_in", 0)
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

    return dict(row) if row else None


# ============================================================
# PKCE
# ============================================================

def gerar_pkce():

    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()

    challenge = base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")

    return verifier, challenge


# ============================================================
# HTTP
# ============================================================

def headers_token(access_token):

    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "CacadorDeOfertas/1.0"
    }


def safe_json(response):

    try:
        return response.json()
    except Exception:
        return {
            "texto": response.text[:2000]
        }


def limpar_segredos(data):

    if isinstance(data, dict):

        resultado = {}

        for chave, valor in data.items():

            chave_lower = str(chave).lower()

            if any(x in chave_lower for x in [
                "access_token",
                "refresh_token",
                "client_secret",
                "authorization",
                "token"
            ]):
                resultado[chave] = "*** OCULTO ***"

            else:
                resultado[chave] = limpar_segredos(valor)

        return resultado

    if isinstance(data, list):
        return [limpar_segredos(x) for x in data]

    return data


# ============================================================
# OAUTH
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:
        return "ML_CLIENT_ID não configurado.", 500

    verifier, challenge = gerar_pkce()

    state = secrets.token_urlsafe(32)

    session["ml_state"] = state
    session["ml_code_verifier"] = verifier

    params = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256"
    }

    url = ML_AUTH_URL + "?" + urllib.parse.urlencode(params)

    return redirect(url)


@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    error = request.args.get("error")

    if error:
        return f"""
        <h2>Erro Mercado Livre</h2>
        <pre>{request.args}</pre>
        """, 400

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return "Código de autorização não recebido.", 400

    if state != session.get("ml_state"):
        return "State inválido.", 400

    verifier = session.get("ml_code_verifier")

    if not verifier:
        return "Code verifier não encontrado.", 400

    payload = {
        "grant_type": "authorization_code",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "code": code,
        "redirect_uri": ML_REDIRECT_URI,
        "code_verifier": verifier
    }

    response = requests.post(
        ML_TOKEN_URL,
        data=payload,
        timeout=30
    )

    data = safe_json(response)

    if response.status_code != 200:

        return f"""
        <h2>Erro ao obter token</h2>

        <p>Status: {response.status_code}</p>

        <pre>{data}</pre>

        <br>

        <a href="/">Voltar</a>
        """, response.status_code

    salvar_token(data)

    session.pop("ml_state", None)
    session.pop("ml_code_verifier", None)

    return redirect("/?conectado=1")


# Compatibilidade
@app.route("/mercadolivre/callback2")
def mercadolivre_callback2():
    return mercadolivre_callback()


# ============================================================
# REFRESH TOKEN
# ============================================================

def atualizar_token():

    token = pegar_token()

    if not token:
        return None

    refresh_token = token.get("refresh_token")

    if not refresh_token:
        return token.get("access_token")

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

        if response.status_code == 200:

            data = response.json()

            if "refresh_token" not in data:
                data["refresh_token"] = refresh_token

            salvar_token(data)

            return data.get("access_token")

    except Exception:
        pass

    return token.get("access_token")


# ============================================================
# USERS/ME
# ============================================================

def consultar_usuario(access_token):

    response = requests.get(
        f"{ML_API_URL}/users/me",
        headers=headers_token(access_token),
        timeout=30
    )

    return response


# ============================================================
# APLICAÇÃO
# ============================================================

def consultar_aplicacao(access_token):

    response = requests.get(
        f"{ML_API_URL}/applications/{ML_CLIENT_ID}",
        headers=headers_token(access_token),
        timeout=30
    )

    return response


# ============================================================
# GRANTS
# ============================================================

def consultar_grants(access_token):

    response = requests.get(
        f"{ML_API_URL}/applications/{ML_CLIENT_ID}/grants",
        headers=headers_token(access_token),
        timeout=30
    )

    return response


# ============================================================
# APLICAÇÕES DO USUÁRIO
# ============================================================

def consultar_aplicacoes_usuario(access_token, user_id):

    response = requests.get(
        f"{ML_API_URL}/users/{user_id}/applications",
        headers=headers_token(access_token),
        timeout=30
    )

    return response


# ============================================================
# BUSCA MERCADO LIVRE
# ============================================================

def buscar_mercadolivre(q, limite=30):

    access_token = atualizar_token()

    if not access_token:
        return {
            "ok": False,
            "erro": "Mercado Livre não conectado."
        }

    url = f"{ML_API_URL}/sites/{ML_SITE}/search"

    params = {
        "q": q,
        "limit": limite
    }

    # ========================================================
    # PRIMEIRO: COM TOKEN
    # ========================================================

    try:

        response = requests.get(
            url,
            params=params,
            headers=headers_token(access_token),
            timeout=30
        )

        data = safe_json(response)

        if response.status_code == 200:

            return {
                "ok": True,
                "data": data,
                "modo": "autenticado"
            }

        # ====================================================
        # SE DER 403, TESTA SEM TOKEN
        # ====================================================

        if response.status_code == 403:

            try:

                publico = requests.get(
                    url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "CacadorDeOfertas/1.0"
                    },
                    timeout=30
                )

                publico_data = safe_json(publico)

                if publico.status_code == 200:

                    return {
                        "ok": True,
                        "data": publico_data,
                        "modo": "publico",
                        "aviso": (
                            "A busca funcionou sem token, "
                            "mas foi bloqueada quando enviada "
                            "com o token da aplicação."
                        )
                    }

                return {
                    "ok": False,
                    "erro": "Busca bloqueada",
                    "status_token": response.status_code,
                    "resposta_token": data,
                    "status_publico": publico.status_code,
                    "resposta_publico": publico_data
                }

            except Exception as e:

                return {
                    "ok": False,
                    "erro": "Erro no teste sem token",
                    "detalhes": str(e)
                }

        return {
            "ok": False,
            "erro": "Erro na busca",
            "status": response.status_code,
            "resposta": data
        }

    except Exception as e:

        return {
            "ok": False,
            "erro": "Falha de conexão",
            "detalhes": str(e)
        }


# ============================================================
# PROCESSAR PRODUTOS
# ============================================================

def calcular_desconto(preco, original):

    try:

        preco = float(preco or 0)
        original = float(original or 0)

        if original <= 0 or preco <= 0:
            return 0

        desconto = ((original - preco) / original) * 100

        return round(desconto, 2)

    except Exception:
        return 0


def processar_resultados(data, minimo=10):

    resultados = []

    for item in data.get("results", []):

        preco = item.get("price") or 0
        original = item.get("original_price") or 0

        desconto = calcular_desconto(
            preco,
            original
        )

        if desconto < minimo:
            continue

        resultados.append({

            "id": item.get("id"),

            "titulo": item.get(
                "title",
                "Produto"
            ),

            "preco": preco,

            "preco_original": original,

            "desconto": desconto,

            "vendedor": (
                item.get("seller", {})
                .get("nickname", "")
            ),

            "categoria": item.get(
                "category_id",
                ""
            ),

            "link": item.get(
                "permalink",
                ""
            ),

            "imagem": item.get(
                "thumbnail",
                ""
            )
        })

    resultados.sort(
        key=lambda x: x["desconto"],
        reverse=True
    )

    return resultados


# ============================================================
# SALVAR OFERTA
# ============================================================

def salvar_oferta(oferta):

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
                categoria,
                link,
                imagem
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            oferta.get("id"),
            oferta.get("titulo"),
            oferta.get("preco"),
            oferta.get("preco_original"),
            oferta.get("desconto"),
            oferta.get("vendedor"),
            oferta.get("categoria"),
            oferta.get("link"),
            oferta.get("imagem")
        ))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# STATUS
# ============================================================

@app.route("/mercadolivre/status")
def mercadolivre_status():

    token = pegar_token()

    return jsonify({
        "conectado": bool(
            token and token.get("access_token")
        ),
        "usuario": (
            token.get("user_id")
            if token else None
        )
    })


# ============================================================
# DIAGNÓSTICO AVANÇADO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def mercadolivre_diagnostico():

    token = pegar_token()

    resultado = {
        "configuracao": {},
        "access_token": {},
        "usuario": {},
        "aplicacao": {},
        "grants": {},
        "aplicacoes_usuario": {},
        "busca_token": {},
        "busca_sem_token": {},
        "conclusao": []
    }

    # ========================================================
    # CONFIGURAÇÃO
    # ========================================================

    resultado["configuracao"] = {
        "ML_CLIENT_ID_configurado": bool(ML_CLIENT_ID),
        "ML_CLIENT_SECRET_configurado": bool(ML_CLIENT_SECRET),
        "ML_REDIRECT_URI": ML_REDIRECT_URI,
        "site": ML_SITE
    }

    if not token:

        resultado["access_token"] = {
            "ok": False,
            "mensagem": "Nenhum token encontrado no banco."
        }

        return jsonify(
            limpar_segredos(resultado)
        )

    access_token = token.get("access_token")

    resultado["access_token"] = {
        "ok": bool(access_token),
        "user_id_salvo": token.get("user_id")
    }

    if not access_token:

        return jsonify(
            limpar_segredos(resultado)
        )

    # ========================================================
    # USERS/ME
    # ========================================================

    try:

        r = consultar_usuario(access_token)

        resultado["usuario"] = {
            "status": r.status_code,
            "ok": r.status_code == 200,
            "resposta": safe_json(r)
        }

    except Exception as e:

        resultado["usuario"] = {
            "ok": False,
            "erro": str(e)
        }

    # ========================================================
    # APPLICATION
    # ========================================================

    try:

        r = consultar_aplicacao(access_token)

        app_data = safe_json(r)

        resultado["aplicacao"] = {
            "status": r.status_code,
            "ok": r.status_code == 200,
            "resposta": app_data
        }

        # Verificações automáticas

        if isinstance(app_data, dict):

            if app_data.get("active") is False:

                resultado["conclusao"].append(
                    "ATENÇÃO: a aplicação aparece como INATIVA."
                )

            scopes = app_data.get("scopes", [])

            if isinstance(scopes, list):

                mp_scopes = [
                    s for s in scopes
                    if str(s).startswith("urn:mp:")
                ]

                if mp_scopes:

                    resultado["conclusao"].append(
                        "ATENÇÃO: foram encontrados scopes "
                        "do Mercado Pago (urn:mp:...)."
                    )

                    resultado["aplicacao"]["scopes_mp"] = mp_scopes

    except Exception as e:

        resultado["aplicacao"] = {
            "ok": False,
            "erro": str(e)
        }

    # ========================================================
    # GRANTS
    # ========================================================

    try:

        r = consultar_grants(access_token)

        grants_data = safe_json(r)

        resultado["grants"] = {
            "status": r.status_code,
            "ok": r.status_code == 200,
            "resposta": grants_data
        }

    except Exception as e:

        resultado["grants"] = {
            "ok": False,
            "erro": str(e)
        }

    # ========================================================
    # APLICAÇÕES DO USUÁRIO
    # ========================================================

    user_id = token.get("user_id")

    if not user_id:

        try:

            if resultado["usuario"].get("ok"):

                user_data = resultado["usuario"].get(
                    "resposta",
                    {}
                )

                user_id = user_data.get("id")

        except Exception:
            pass

    if user_id:

        try:

            r = consultar_aplicacoes_usuario(
                access_token,
                user_id
            )

            resultado["aplicacoes_usuario"] = {
                "status": r.status_code,
                "ok": r.status_code == 200,
                "resposta": safe_json(r)
            }

        except Exception as e:

            resultado["aplicacoes_usuario"] = {
                "ok": False,
                "erro": str(e)
            }

    # ========================================================
    # BUSCA COM TOKEN
    # ========================================================

    try:

        url = f"{ML_API_URL}/sites/{ML_SITE}/search"

        r = requests.get(
            url,
            params={
                "q": "celular",
                "limit": 1
            },
            headers=headers_token(access_token),
            timeout=30
        )

        resultado["busca_token"] = {
            "status": r.status_code,
            "ok": r.status_code == 200,
            "url": r.url,
            "resposta": safe_json(r)
        }

    except Exception as e:

        resultado["busca_token"] = {
            "ok": False,
            "erro": str(e)
        }

    # ========================================================
    # BUSCA SEM TOKEN
    # ========================================================

    try:

        url = f"{ML_API_URL}/sites/{ML_SITE}/search"

        r = requests.get(
            url,
            params={
                "q": "celular",
                "limit": 1
            },
            headers={
                "Accept": "application/json",
                "User-Agent": "CacadorDeOfertas/1.0"
            },
            timeout=30
        )

        resultado["busca_sem_token"] = {
            "status": r.status_code,
            "ok": r.status_code == 200,
            "url": r.url,
            "resposta": safe_json(r)
        }

    except Exception as e:

        resultado["busca_sem_token"] = {
            "ok": False,
            "erro": str(e)
        }

    # ========================================================
    # CONCLUSÃO AUTOMÁTICA
    # ========================================================

    token_ok = resultado["usuario"].get("ok")
    busca_token_ok = resultado["busca_token"].get("ok")
    busca_publica_ok = resultado["busca_sem_token"].get("ok")

    if token_ok and busca_token_ok:

        resultado["conclusao"].append(
            "OAuth/token e busca autenticada estão funcionando."
        )

    elif token_ok and not busca_token_ok:

        resultado["conclusao"].append(
            "O token consegue acessar users/me, "
            "mas a busca autenticada está sendo bloqueada."
        )

    if busca_publica_ok and not busca_token_ok:

        resultado["conclusao"].append(
            "IMPORTANTE: a busca funciona sem token, "
            "mas é bloqueada com token. Isso aponta para "
            "permissão, grant, aplicação ou política de acesso."
        )

    if not busca_publica_ok and not busca_token_ok:

        resultado["conclusao"].append(
            "A busca está sendo bloqueada tanto com token "
            "quanto sem token."
        )

    return jsonify(
        limpar_segredos(resultado)
    )


# ============================================================
# NOTIFICAÇÕES
# ============================================================

@app.route("/mercadolivre/notificacoes", methods=["POST"])
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

@app.route("/health")
def health():

    return jsonify({
        "status": "ok"
    })


# ============================================================
# BUSCAR
# ============================================================

@app.route("/buscar", methods=["POST"])
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

        return redirect("/")

    resultado = buscar_mercadolivre(
        termo,
        30
    )

    if not resultado.get("ok"):

        return render_template_string(
            HTML,
            erro=resultado,
            resultados=[],
            termo=termo,
            minimo=minimo
        )

    produtos = processar_resultados(
        resultado.get("data", {}),
        minimo
    )

    for produto in produtos:
        salvar_oferta(produto)

    return render_template_string(
        HTML,
        erro=None,
        resultados=produtos,
        termo=termo,
        minimo=minimo,
        modo=resultado.get("modo"),
        aviso=resultado.get("aviso")
    )


# ============================================================
# GERAR PUBLICAÇÃO
# ============================================================

@app.route("/gerar", methods=["POST"])
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

    link_final = afiliado or link

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
        token and token.get("access_token")
    )

    return render_template_string(
        HTML,
        erro=None,
        resultados=[],
        termo="",
        minimo=10,
        conectado=conectado,
        modo=None,
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

body {
    font-family: Arial, sans-serif;
    background: #f5f5f5;
    margin: 0;
    padding: 20px;
}

.container {
    max-width: 900px;
    margin: auto;
}

.card {
    background: white;
    padding: 20px;
    border-radius: 15px;
    margin-bottom: 20px;
    box-shadow: 0 2px 10px rgba(0,0,0,.08);
}

h1 {
    margin-top: 0;
}

input,
button {
    width: 100%;
    box-sizing: border-box;
    padding: 13px;
    margin-top: 8px;
    margin-bottom: 12px;
    border-radius: 8px;
    border: 1px solid #ccc;
    font-size: 16px;
}

button {
    background: #3483fa;
    color: white;
    border: none;
    font-weight: bold;
}

a.botao {
    display: block;
    text-align: center;
    padding: 13px;
    border-radius: 8px;
    background: #3483fa;
    color: white;
    text-decoration: none;
    margin-bottom: 10px;
}

.diagnostico {
    background: #222;
}

.sucesso {
    color: green;
    font-weight: bold;
}

.erro {
    background: #ffe5e5;
    color: #a00000;
    padding: 15px;
    border-radius: 8px;
    overflow-x: auto;
}

.aviso {
    background: #fff3cd;
    padding: 15px;
    border-radius: 8px;
}

.produto {
    border: 1px solid #ddd;
    padding: 15px;
    border-radius: 12px;
    margin-bottom: 15px;
}

.produto img {
    width: 100%;
    max-width: 180px;
    border-radius: 10px;
}

.desconto {
    color: green;
    font-size: 22px;
    font-weight: bold;
}

.preco {
    font-size: 22px;
    font-weight: bold;
}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>🛒 Caçador de Ofertas</h1>

{% if conectado %}

<p class="sucesso">
🟢 Mercado Livre conectado
</p>

{% else %}

<p>
🔴 Mercado Livre não conectado
</p>

{% endif %}

<a class="botao"
   href="/mercadolivre/login">

🔗 Conectar Mercado Livre

</a>

<a class="botao diagnostico"
   href="/mercadolivre/diagnostico">

🧪 Diagnóstico avançado

</a>

</div>


<div class="card">

<h2>🔎 Procurar ofertas</h2>

<form method="POST"
      action="/buscar">

<label>Produto</label>

<input
    type="text"
    name="termo"
    value="{{ termo }}"
    placeholder="Ex: celular"
    required
>

<label>Desconto mínimo (%)</label>

<input
    type="number"
    name="minimo"
    value="{{ minimo }}"
    min="0"
    max="99"
>

<button type="submit">

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

<h3>❌ Erro</h3>

<pre>{{ erro }}</pre>

</div>

{% endif %}


{% if resultados %}

<div class="card">

<h2>
🔥 Ofertas encontradas
</h2>

{% for p in resultados %}

<div class="produto">

{% if p.imagem %}

<img src="{{ p.imagem }}">

{% endif %}

<h3>
{{ p.titulo }}
</h3>

<p class="desconto">
{{ p.desconto }}% OFF
</p>

{% if p.preco_original %}

<p>
De:
R$ {{ "%.2f"|format(p.preco_original) }}
</p>

{% endif %}

<p class="preco">
Por:
R$ {{ "%.2f"|format(p.preco) }}
</p>

<p>
Vendedor:
{{ p.vendedor }}
</p>

<a href="{{ p.link }}"
   target="_blank">

Ver produto

</a>

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

<title>Gerar publicação</title>

<style>

body {
    font-family: Arial;
    background: #f5f5f5;
    padding: 20px;
}

.container {
    max-width: 700px;
    margin: auto;
}

.card {
    background: white;
    padding: 20px;
    border-radius: 15px;
}

textarea {
    width: 100%;
    height: 350px;
    box-sizing: border-box;
    padding: 15px;
    font-size: 16px;
}

a {
    display: block;
    margin-top: 15px;
}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h2>📢 Publicação gerada</h2>

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