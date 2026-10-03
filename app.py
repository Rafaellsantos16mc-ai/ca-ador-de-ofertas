import os
import re
import json
import time
import base64
import hashlib
import secrets
import sqlite3
from urllib.parse import urlencode

import requests
from flask import (
    Flask,
    request,
    redirect,
    session,
    jsonify,
    render_template_string,
    send_file,
)

# ============================================================
# CONFIGURAÇÃO
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "troque-esta-chave-no-railway"
)

DATABASE = "ofertas.db"

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

SITE_ID = "MLB"

MIN_PRODUCT_PRICE = 69.90
MAX_PRODUCTS = 45
SEARCH_LIMIT = 6

CATEGORIES = [
    "celular",
    "perfume",
    "academia",
    "ferramentas",
    "eletronicos",
    "casa",
    "automotivo",
    "cozinha",
    "moda",
]

# ============================================================
# BANCO
# ============================================================

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ml_auth (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT,
            updated_at INTEGER
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# TOKEN
# ============================================================

def save_token(
    access_token,
    refresh_token=None,
    expires_in=None,
    user_id=None,
    nickname=None
):
    expires_at = int(time.time()) + int(expires_in or 21600)

    conn = db()

    old = conn.execute(
        "SELECT refresh_token, user_id, nickname FROM ml_auth WHERE id=1"
    ).fetchone()

    if refresh_token is None and old:
        refresh_token = old["refresh_token"]

    if user_id is None and old:
        user_id = old["user_id"]

    if nickname is None and old:
        nickname = old["nickname"]

    conn.execute("""
        INSERT INTO ml_auth
        (id, access_token, refresh_token, expires_at, user_id, nickname, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            access_token=excluded.access_token,
            refresh_token=excluded.refresh_token,
            expires_at=excluded.expires_at,
            user_id=excluded.user_id,
            nickname=excluded.nickname,
            updated_at=excluded.updated_at
    """, (
        access_token,
        refresh_token,
        expires_at,
        user_id,
        nickname,
        int(time.time()),
    ))

    conn.commit()
    conn.close()


def get_auth():
    conn = db()

    row = conn.execute(
        "SELECT * FROM ml_auth WHERE id=1"
    ).fetchone()

    conn.close()

    if not row:
        return None

    return dict(row)


def clear_auth():
    conn = db()

    conn.execute("DELETE FROM ml_auth WHERE id=1")

    conn.commit()
    conn.close()


# ============================================================
# REFRESH TOKEN
# ============================================================

def refresh_access_token():
    auth = get_auth()

    if not auth:
        return None

    refresh_token = auth.get("refresh_token")

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
                "refresh_token": refresh_token,
            },
            timeout=20,
        )

        data = response.json()

        if response.status_code != 200:
            print("[REFRESH ERRO]", response.status_code, data)
            return None

        new_access = data.get("access_token")

        if not new_access:
            return None

        new_refresh = data.get(
            "refresh_token",
            refresh_token
        )

        save_token(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_in=data.get("expires_in", 21600),
            user_id=auth.get("user_id"),
            nickname=auth.get("nickname"),
        )

        print("[TOKEN] Renovado com sucesso")

        return new_access

    except Exception as e:
        print("[REFRESH EXCEPTION]", repr(e))
        return None


def get_access_token(force_refresh=False):
    auth = get_auth()

    if not auth:
        return None

    token = auth.get("access_token")
    expires_at = int(auth.get("expires_at") or 0)

    if not force_refresh and token:
        # Renova 2 minutos antes de expirar
        if expires_at > int(time.time()) + 120:
            return token

    return refresh_access_token()


# ============================================================
# HEADERS
# ============================================================

def ml_headers(token=None):
    if token is None:
        token = get_access_token()

    headers = {
        "Accept": "application/json",
        "User-Agent": "CacadorDeOfertas/1.0",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


# ============================================================
# REQUEST ML
# ============================================================

def ml_get(path, params=None, retry_refresh=True):
    token = get_access_token()

    if not token:
        return {
            "ok": False,
            "status": 401,
            "data": {
                "error": "not_authenticated",
                "message": "Mercado Livre não conectado."
            }
        }

    url = ML_API + path

    try:
        response = requests.get(
            url,
            params=params,
            headers=ml_headers(token),
            timeout=25,
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "raw": response.text[:3000]
            }

        if response.status_code == 401 and retry_refresh:
            print("[ML] Token expirado. Tentando renovar...")

            new_token = refresh_access_token()

            if new_token:
                return ml_get(
                    path,
                    params=params,
                    retry_refresh=False
                )

        return {
            "ok": response.ok,
            "status": response.status_code,
            "data": data,
            "url": response.url,
        }

    except requests.RequestException as e:
        return {
            "ok": False,
            "status": 0,
            "data": {
                "error": "request_exception",
                "message": str(e),
            },
            "url": url,
        }


# ============================================================
# OAUTH PKCE
# ============================================================

def make_code_verifier():
    return secrets.token_urlsafe(64)[:128]


def make_code_challenge(verifier):
    digest = hashlib.sha256(
        verifier.encode("utf-8")
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).decode("utf-8").rstrip("=")


@app.route("/mercadolivre/login")
def ml_login():

    if not ML_CLIENT_ID:
        return """
        <h2>ML_CLIENT_ID não configurado</h2>
        <p>Configure as variáveis do Mercado Livre no Railway.</p>
        """, 500

    verifier = make_code_verifier()
    challenge = make_code_challenge(verifier)

    state = secrets.token_urlsafe(32)

    session["ml_code_verifier"] = verifier
    session["ml_state"] = state

    params = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,

        # IMPORTANTE:
        # A aplicação precisa solicitar leitura e refresh.
        "scope": "read offline_access",

        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    url = ML_AUTH + "?" + urlencode(params)

    return redirect(url)


# ============================================================
# CALLBACK
# ============================================================

@app.route("/mercadolivre/callback")
def ml_callback():

    error = request.args.get("error")

    if error:
        return f"""
        <html>
        <body style="font-family:Arial;padding:30px">
            <h2>❌ Mercado Livre recusou a autorização</h2>
            <p><b>Erro:</b> {error}</p>
            <p>{request.args.get("error_description", "")}</p>
            <a href="/">Voltar</a>
        </body>
        </html>
        """

    code = request.args.get("code")
    state = request.args.get("state")

    saved_state = session.get("ml_state")
    verifier = session.get("ml_code_verifier")

    if not code:
        return "Código OAuth não recebido.", 400

    if not state or state != saved_state:
        return "State OAuth inválido.", 400

    if not verifier:
        return "Code verifier não encontrado. Faça o login novamente.", 400

    try:
        response = requests.post(
            ML_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "code": code,
                "redirect_uri": ML_REDIRECT_URI,
                "code_verifier": verifier,
            },
            timeout=25,
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "raw": response.text
            }

        if response.status_code != 200:
            return f"""
            <html>
            <body style="font-family:Arial;padding:30px">
                <h2>❌ Erro ao obter token</h2>
                <pre>{json.dumps(data, indent=2, ensure_ascii=False)}</pre>
                <a href="/">Voltar</a>
            </body>
            </html>
            """, response.status_code

        access_token = data.get("access_token")

        if not access_token:
            return "Mercado Livre não retornou access_token.", 500

        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 21600)

        # Busca usuário
        user_response = requests.get(
            ML_API + "/users/me",
            headers=ml_headers(access_token),
            timeout=20,
        )

        user_data = {}

        try:
            user_data = user_response.json()
        except Exception:
            pass

        user_id = user_data.get("id")
        nickname = user_data.get("nickname")

        save_token(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            user_id=user_id,
            nickname=nickname,
        )

        session.pop("ml_state", None)
        session.pop("ml_code_verifier", None)

        return redirect("/?connected=1")

    except Exception as e:
        return f"""
        <html>
        <body style="font-family:Arial;padding:30px">
            <h2>❌ Erro na conexão</h2>
            <pre>{e}</pre>
            <a href="/">Voltar</a>
        </body>
        </html>
        """, 500


# ============================================================
# RECONNECT
# ============================================================

@app.route("/mercadolivre/reconnect")
def ml_reconnect():

    clear_auth()

    session.pop("ml_state", None)
    session.pop("ml_code_verifier", None)

    return redirect("/mercadolivre/login")


# ============================================================
# DIAGNÓSTICO
# ============================================================

def clean_diagnostic_data(data):
    """
    Remove informações que não precisamos mostrar no painel.
    """
    if not isinstance(data, dict):
        return data

    result = dict(data)

    sensitive_keys = {
        "access_token",
        "refresh_token",
        "client_secret",
        "code",
        "code_verifier",
    }

    for key in list(result.keys()):
        if key.lower() in sensitive_keys:
            result[key] = "***"

    return result


@app.route("/api/ml-diagnostic")
def ml_diagnostic():

    auth = get_auth()

    result = {
        "generated_at": int(time.time()),
        "configuration": {
            "client_id_configured": bool(ML_CLIENT_ID),
            "client_secret_configured": bool(ML_CLIENT_SECRET),
            "redirect_uri": ML_REDIRECT_URI,
            "site_id": SITE_ID,
            "oauth_scope_requested": "read offline_access",
        },
        "token": {
            "connected": bool(auth and auth.get("access_token")),
            "user_id": auth.get("user_id") if auth else None,
            "nickname": auth.get("nickname") if auth else None,
            "expires_at": auth.get("expires_at") if auth else None,
            "has_refresh_token": bool(
                auth and auth.get("refresh_token")
            ),
        },
        "tests": {},
        "conclusion": [],
    }

    if not auth:
        result["conclusion"].append(
            "Nenhum token do Mercado Livre está salvo. Conecte a conta primeiro."
        )
        return jsonify(result)

    # --------------------------------------------------------
    # TESTE 1 - USERS ME
    # --------------------------------------------------------

    me = ml_get("/users/me")

    result["tests"]["users_me"] = {
        "status": me["status"],
        "ok": me["ok"],
        "data": clean_diagnostic_data(me.get("data")),
    }

    user_id = None

    if me["ok"] and isinstance(me.get("data"), dict):
        user_id = me["data"].get("id")

        if user_id:
            result["token"]["user_id"] = user_id

    # --------------------------------------------------------
    # TESTE 2 - APPLICATION
    # --------------------------------------------------------

    if ML_CLIENT_ID:

        app_test = ml_get(
            f"/applications/{ML_CLIENT_ID}"
        )

        result["tests"]["application"] = {
            "status": app_test["status"],
            "ok": app_test["ok"],
            "data": clean_diagnostic_data(
                app_test.get("data")
            ),
        }

    # --------------------------------------------------------
    # TESTE 3 - APLICAÇÕES DO USUÁRIO
    # --------------------------------------------------------

    if user_id:

        user_apps = ml_get(
            f"/users/{user_id}/applications"
        )

        result["tests"]["user_applications"] = {
            "status": user_apps["status"],
            "ok": user_apps["ok"],
            "data": clean_diagnostic_data(
                user_apps.get("data")
            ),
        }

        # Procura nossa aplicação
        if user_apps["ok"] and isinstance(
            user_apps.get("data"),
            (dict, list)
        ):

            raw_apps = user_apps.get("data")

            if isinstance(raw_apps, dict):
                apps_list = (
                    raw_apps.get("applications")
                    or raw_apps.get("results")
                    or []
                )
            else:
                apps_list = raw_apps

            matching_app = None

            for item in apps_list:
                if not isinstance(item, dict):
                    continue

                app_id = str(
                    item.get("app_id")
                    or item.get("id")
                    or ""
                )

                if app_id == str(ML_CLIENT_ID):
                    matching_app = item
                    break

            if matching_app:

                scopes = (
                    matching_app.get("scopes")
                    or matching_app.get("scope")
                )

                result["tests"]["our_grant"] = {
                    "found": True,
                    "app_id": matching_app.get(
                        "app_id",
                        matching_app.get("id")
                    ),
                    "scopes": scopes,
                    "data": clean_diagnostic_data(
                        matching_app
                    ),
                }

                if scopes:

                    scope_text = (
                        " ".join(scopes)
                        if isinstance(scopes, list)
                        else str(scopes)
                    )

                    if "read" not in scope_text:
                        result["conclusion"].append(
                            "A autorização encontrada não possui o escopo read."
                        )

                    if "urn:mp:" in scope_text:
                        result["conclusion"].append(
                            "Foram encontrados escopos relacionados ao Mercado Pago nesta autorização. Verifique a separação entre aplicativos Mercado Livre e Mercado Pago."
                        )

            else:

                result["tests"]["our_grant"] = {
                    "found": False,
                    "message": (
                        "A aplicação não apareceu entre as autorizações "
                        "deste usuário."
                    ),
                }

    # --------------------------------------------------------
    # TESTE 4 - BUSCA
    # --------------------------------------------------------

    search_test = ml_get(
        "/sites/MLB/search",
        params={
            "q": "celular",
            "limit": 1,
        }
    )

    result["tests"]["search"] = {
        "status": search_test["status"],
        "ok": search_test["ok"],
        "data": clean_diagnostic_data(
            search_test.get("data")
        ),
    }

    # --------------------------------------------------------
    # CONCLUSÃO AUTOMÁTICA
    # --------------------------------------------------------

    if me["status"] == 403:
        result["conclusion"].append(
            "O token atual recebe HTTP 403 em /users/me. Isso indica problema de autorização/permissão da aplicação ou do acesso concedido."
        )

    if search_test["status"] == 403:
        result["conclusion"].append(
            "A busca /sites/MLB/search está bloqueada com HTTP 403. O código de busca não consegue contornar esse bloqueio."
        )

    app_data = (
        result["tests"]
        .get("application", {})
        .get("data")
    )

    if isinstance(app_data, dict):

        if app_data.get("active") is False:
            result["conclusion"].append(
                "A aplicação do Mercado Livre aparece como inativa."
            )

        certification = app_data.get(
            "certification_status"
        )

        if certification:
            result["conclusion"].append(
                f"Status de certificação informado pelo Mercado Livre: {certification}"
            )

    if not result["conclusion"]:
        result["conclusion"].append(
            "Os testes básicos não encontraram um bloqueio evidente. A busca pode ser executada novamente."
        )

    return jsonify(result)


# ============================================================
# BUSCA DE PRODUTOS
# ============================================================

def search_items(query, diagnostics=None):

    if diagnostics is None:
        diagnostics = []

    response = ml_get(
        "/sites/MLB/search",
        params={
            "q": query,
            "limit": SEARCH_LIMIT,
        }
    )

    status = response["status"]
    data = response.get("data")

    if status != 200:

        diagnostics.append({
            "query": query,
            "status": status,
            "count": 0,
            "error": data,
        })

        return []

    results = []

    if isinstance(data, dict):
        results = data.get("results") or []

    diagnostics.append({
        "query": query,
        "status": 200,
        "count": len(results),
    })

    return results


# ============================================================
# PREÇO
# ============================================================

def safe_float(value):

    try:
        return float(value)
    except Exception:
        return 0.0


# ============================================================
# DESCONTO
# ============================================================

def get_discount(item):

    original = safe_float(
        item.get("original_price")
    )

    current = safe_float(
        item.get("price")
    )

    if original <= 0 or current <= 0:
        return 0.0

    if original <= current:
        return 0.0

    return round(
        ((original - current) / original) * 100,
        2
    )


# ============================================================
# CUPOM REAL
# ============================================================

COUPON_PATTERNS = [
    r"cupom.{0,80}",
    r"coupon.{0,80}",
    r"R\$\s*\d+\s*OFF",
    r"\d+\s*%\s*OFF",
    r"\d+\s*OFF",
]


def detect_coupon_from_text(text):

    if not text:
        return None

    text = re.sub(
        r"\s+",
        " ",
        str(text)
    ).strip()

    for pattern in COUPON_PATTERNS:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:

            found = match.group(0).strip()

            if len(found) > 160:
                found = found[:160]

            return found

    return None


def get_public_page_coupon(url):

    if not url:
        return None

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                )
            },
            timeout=15,
            allow_redirects=True,
        )

        if response.status_code != 200:
            return None

        html = response.text

        # Procura somente evidências existentes na página.
        coupon = detect_coupon_from_text(html)

        return coupon

    except Exception:
        return None


# ============================================================
# ANALISAR PRODUTO
# ============================================================

def analyze_product(item):

    item_id = item.get("id")

    title = (
        item.get("title")
        or "Produto sem título"
    )

    price = safe_float(
        item.get("price")
    )

    original_price = safe_float(
        item.get("original_price")
    )

    if price < MIN_PRODUCT_PRICE:
        return None

    discount = get_discount(item)

    permalink = (
        item.get("permalink")
        or ""
    )

    thumbnail = (
        item.get("thumbnail")
        or ""
    )

    shipping = item.get("shipping") or {}

    free_shipping = bool(
        shipping.get("free_shipping")
    )

    condition = (
        item.get("condition")
        or ""
    )

    if condition and condition != "new":
        return None

    coupon = get_public_page_coupon(
        permalink
    )

    estimated_savings = 0.0

    if original_price > price:
        estimated_savings = round(
            original_price - price,
            2
        )

    return {
        "id": item_id,
        "title": title,
        "price": price,
        "original_price": original_price,
        "discount": discount,
        "coupon": coupon,
        "estimated_savings": estimated_savings,
        "permalink": permalink,
        "thumbnail": thumbnail,
        "free_shipping": free_shipping,
    }


# ============================================================
# CAÇAR OFERTAS
# ============================================================

@app.route("/api/hunt")
def api_hunt():

    auth = get_auth()

    if not auth or not auth.get("access_token"):
        return jsonify({
            "ok": False,
            "error": "Mercado Livre não conectado.",
            "login_url": "/mercadolivre/login",
        }), 401

    diagnostics = []

    all_items = []
    seen_ids = set()

    # --------------------------------------------------------
    # BUSCAR
    # --------------------------------------------------------

    for query in CATEGORIES:

        items = search_items(
            query,
            diagnostics
        )

        for item in items:

            item_id = item.get("id")

            if not item_id:
                continue

            if item_id in seen_ids:
                continue

            seen_ids.add(item_id)
            all_items.append(item)

            if len(all_items) >= MAX_PRODUCTS:
                break

        if len(all_items) >= MAX_PRODUCTS:
            break

    # --------------------------------------------------------
    # SE NÃO VEIO NADA
    # --------------------------------------------------------

    if not all_items:

        has_403 = any(
            d.get("status") == 403
            for d in diagnostics
        )

        message = (
            "O Mercado Livre está retornando HTTP 403 para as buscas."
            if has_403
            else
            "Nenhum anúncio foi retornado pela busca."
        )

        return jsonify({
            "ok": False,
            "message": message,
            "stats": {
                "queries": len(CATEGORIES),
                "returned": 0,
                "unique": 0,
                "analyzed": 0,
                "opportunities": 0,
                "coupons": 0,
                "value": 0,
            },
            "diagnostics": diagnostics,
            "diagnostic_url": "/api/ml-diagnostic",
        })

    # --------------------------------------------------------
    # ANALISAR
    # --------------------------------------------------------

    offers = []

    analyzed = 0
    coupons = 0

    for item in all_items:

        analyzed += 1

        try:

            offer = analyze_product(item)

            if not offer:
                continue

            # Consideramos oportunidade quando existe
            # desconto real ou frete grátis.
            if (
                offer["discount"] > 0
                or offer["free_shipping"]
                or offer["coupon"]
            ):

                offers.append(offer)

                if offer["coupon"]:
                    coupons += 1

        except Exception as e:

            print(
                "[ERRO ANALISANDO]",
                item.get("id"),
                repr(e)
            )

    # Ordenar pelo maior desconto
    offers.sort(
        key=lambda x: (
            x.get("discount", 0),
            x.get("estimated_savings", 0)
        ),
        reverse=True
    )

    total_value = sum(
        safe_float(x.get("estimated_savings"))
        for x in offers
    )

    return jsonify({
        "ok": True,
        "message": (
            f"Caça finalizada — "
            f"{len(offers)} oportunidades encontradas."
        ),
        "stats": {
            "queries": len(CATEGORIES),
            "returned": len(all_items),
            "unique": len(seen_ids),
            "analyzed": analyzed,
            "opportunities": len(offers),
            "coupons": coupons,
            "value": round(total_value, 2),
        },
        "diagnostics": diagnostics,
        "offers": offers,
    })


# ============================================================
# API STATUS
# ============================================================

@app.route("/api/status")
def api_status():

    auth = get_auth()

    return jsonify({
        "connected": bool(
            auth and auth.get("access_token")
        ),
        "user_id": (
            auth.get("user_id")
            if auth else None
        ),
        "nickname": (
            auth.get("nickname")
            if auth else None
        ),
        "expires_at": (
            auth.get("expires_at")
            if auth else None
        ),
    })


# ============================================================
# HOME
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
    background: #f3f4f6;
    color: #111827;
    font-family: Arial, Helvetica, sans-serif;
}

.container {
    width: 94%;
    max-width: 1100px;
    margin: 0 auto;
    padding: 25px 0 60px;
}

.header {
    background: #ffe600;
    padding: 22px;
    border-radius: 18px;
    margin-bottom: 18px;
}

.header h1 {
    margin: 0 0 6px;
    font-size: 28px;
}

.header p {
    margin: 0;
    color: #333;
}

.card {
    background: white;
    border-radius: 18px;
    padding: 20px;
    margin-bottom: 18px;
    box-shadow: 0 4px 18px rgba(0,0,0,.06);
}

.status {
    padding: 13px;
    border-radius: 12px;
    background: #f3f4f6;
    margin-bottom: 15px;
}

.connected {
    background: #dcfce7;
    color: #166534;
}

.disconnected {
    background: #fee2e2;
    color: #991b1b;
}

button,
.button {
    display: inline-block;
    border: 0;
    border-radius: 12px;
    padding: 14px 18px;
    font-size: 15px;
    font-weight: bold;
    cursor: pointer;
    text-decoration: none;
    color: white;
    background: #111827;
}

button.primary {
    background: #3483fa;
}

button.danger {
    background: #dc2626;
}

button:disabled {
    opacity: .5;
    cursor: not-allowed;
}

.progress {
    width: 100%;
    height: 10px;
    background: #e5e7eb;
    border-radius: 20px;
    overflow: hidden;
    margin: 15px 0;
}

.progress-bar {
    height: 100%;
    width: 0%;
    background: #3483fa;
    transition: width .3s;
}

.stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
}

.stat {
    background: #f9fafb;
    border-radius: 14px;
    padding: 15px;
}

.stat strong {
    display: block;
    font-size: 23px;
    margin-top: 4px;
}

.offers {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 15px;
}

.offer {
    border: 1px solid #e5e7eb;
    border-radius: 15px;
    padding: 14px;
    background: white;
}

.offer img {
    width: 100%;
    height: 180px;
    object-fit: contain;
    border-radius: 10px;
    background: #f8fafc;
}

.offer h3 {
    font-size: 15px;
    line-height: 1.35;
    min-height: 42px;
}

.price {
    font-size: 23px;
    font-weight: bold;
}

.old {
    color: #9ca3af;
    text-decoration: line-through;
}

.discount {
    display: inline-block;
    background: #dcfce7;
    color: #166534;
    padding: 5px 8px;
    border-radius: 8px;
    font-weight: bold;
    margin: 7px 0;
}

.coupon {
    display: block;
    background: #fef3c7;
    color: #92400e;
    padding: 8px;
    border-radius: 8px;
    margin: 8px 0;
    font-size: 13px;
}

.diagnostic {
    background: #111827;
    color: #e5e7eb;
    border-radius: 14px;
    padding: 15px;
    overflow: auto;
    font-size: 12px;
    white-space: pre-wrap;
}

.notice {
    padding: 14px;
    border-radius: 12px;
    background: #fff7ed;
    color: #9a3412;
    margin-top: 15px;
}

@media(max-width: 800px) {

    .offers {
        grid-template-columns: 1fr 1fr;
    }

    .stats {
        grid-template-columns: 1fr 1fr;
    }
}

@media(max-width: 520px) {

    .offers {
        grid-template-columns: 1fr;
    }

    .stats {
        grid-template-columns: 1fr 1fr;
    }

    .container {
        width: 92%;
    }
}

</style>

</head>

<body>

<div class="container">

    <div class="header">

        <h1>🤑 Caçador de Ofertas</h1>

        <p>
            Busca produtos reais do Mercado Livre e identifica
            descontos e cupons encontrados na página.
        </p>

    </div>


    <div class="card">

        <div id="status" class="status">
            Verificando conexão...
        </div>

        <div style="display:flex;gap:10px;flex-wrap:wrap">

            <a
                class="button primary"
                href="/mercadolivre/login"
            >
                🔗 Conectar Mercado Livre
            </a>

            <a
                class="button"
                href="/mercadolivre/reconnect"
            >
                🔄 Reconectar
            </a>

            <button
                class="primary"
                id="huntBtn"
                onclick="hunt()"
            >
                🔎 CAÇAR OFERTAS
            </button>

        </div>

    </div>


    <div class="card">

        <h2>Caça de ofertas</h2>

        <div id="message">
            Pronto para começar.
        </div>

        <div class="progress">
            <div
                id="progressBar"
                class="progress-bar"
            ></div>
        </div>

        <div class="stats">

            <div class="stat">
                Consultas
                <strong id="queries">0</strong>
            </div>

            <div class="stat">
                Anúncios
                <strong id="returned">0</strong>
            </div>

            <div class="stat">
                Oportunidades
                <strong id="opportunities">0</strong>
            </div>

            <div class="stat">
                Cupons
                <strong id="coupons">0</strong>
            </div>

        </div>

    </div>


    <div class="card">

        <h2>Diagnóstico</h2>

        <div
            id="diagnostic"
            class="diagnostic"
        >
Aguardando...
        </div>

    </div>


    <div class="card">

        <h2>Ofertas encontradas</h2>

        <div
            id="offers"
            class="offers"
        >
            Nenhuma oferta ainda.
        </div>

    </div>

</div>


<script>

async function loadStatus() {

    try {

        const response =
            await fetch("/api/status");

        const data =
            await response.json();

        const status =
            document.getElementById("status");

        if (data.connected) {

            status.className =
                "status connected";

            status.innerHTML =
                "🟢 Mercado Livre conectado" +
                (data.nickname
                    ? " — " + data.nickname
                    : "");

        } else {

            status.className =
                "status disconnected";

            status.innerHTML =
                "🔴 Mercado Livre não conectado";

        }

    } catch (error) {

        document.getElementById("status").innerHTML =
            "Erro verificando conexão.";

    }
}


async function loadDiagnostic() {

    try {

        const response =
            await fetch("/api/ml-diagnostic");

        const data =
            await response.json();

        document.getElementById("diagnostic").textContent =
            JSON.stringify(data, null, 2);

    } catch (error) {

        document.getElementById("diagnostic").textContent =
            "Erro ao executar diagnóstico: " +
            error;

    }
}


async function hunt() {

    const btn =
        document.getElementById("huntBtn");

    const message =
        document.getElementById("message");

    const progress =
        document.getElementById("progressBar");

    btn.disabled = true;

    progress.style.width = "10%";

    message.innerText =
        "🔎 Consultando Mercado Livre...";

    try {

        progress.style.width = "35%";

        const response =
            await fetch("/api/hunt");

        progress.style.width = "70%";

        const data =
            await response.json();

        progress.style.width = "100%";

        if (!data.ok) {

            message.innerText =
                "❌ " +
                (data.message || data.error);

        } else {

            message.innerText =
                "✅ " +
                data.message;

        }

        const stats =
            data.stats || {};

        document.getElementById("queries").innerText =
            stats.queries || 0;

        document.getElementById("returned").innerText =
            stats.returned || 0;

        document.getElementById("opportunities").innerText =
            stats.opportunities || 0;

        document.getElementById("coupons").innerText =
            stats.coupons || 0;

        renderOffers(
            data.offers || []
        );

        document.getElementById(
            "diagnostic"
        ).textContent =
            JSON.stringify(
                data.diagnostics || data,
                null,
                2
            );

    } catch (error) {

        message.innerText =
            "❌ Erro: " + error;

        document.getElementById(
            "diagnostic"
        ).textContent =
            String(error);

    } finally {

        btn.disabled = false;

        setTimeout(() => {
            progress.style.width = "0%";
        }, 1000);
    }
}


function renderOffers(offers) {

    const container =
        document.getElementById("offers");

    if (!offers.length) {

        container.innerHTML =
            "<p>Nenhuma oportunidade encontrada.</p>";

        return;
    }

    container.innerHTML =
        offers.map(offer => {

            const image =
                offer.thumbnail
                    ? `<img src="${offer.thumbnail}">`
                    : "";

            const oldPrice =
                offer.original_price > offer.price
                    ? `<div class="old">
                        R$ ${Number(
                            offer.original_price
                        ).toFixed(2).replace(".", ",")}
                       </div>`
                    : "";

            const discount =
                offer.discount > 0
                    ? `<span class="discount">
                        -${offer.discount}%
                       </span>`
                    : "";

            const coupon =
                offer.coupon
                    ? `<div class="coupon">
                        🎟️ ${escapeHtml(
                            offer.coupon
                        )}
                       </div>`
                    : "";

            const shipping =
                offer.free_shipping
                    ? `<div>
                        🚚 Frete grátis
                       </div>`
                    : "";

            return `
                <div class="offer">

                    ${image}

                    <h3>
                        ${escapeHtml(
                            offer.title
                        )}
                    </h3>

                    ${oldPrice}

                    <div class="price">
                        R$ ${Number(
                            offer.price
                        ).toFixed(2).replace(".", ",")}
                    </div>

                    ${discount}

                    ${coupon}

                    ${shipping}

                    <br>

                    <a
                        class="button primary"
                        target="_blank"
                        href="${offer.permalink}"
                    >
                        Ver produto
                    </a>

                </div>
            `;

        }).join("");
}


function escapeHtml(text) {

    return String(text)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


loadStatus();
loadDiagnostic();

</script>

</body>

</html>
"""


@app.route("/")
def home():
    return render_template_string(HTML)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok"
    })


# ============================================================
# MAIN
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