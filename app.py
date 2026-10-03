import os
import re
import time
import uuid
import sqlite3
import secrets
import hashlib
import base64
from urllib.parse import urlencode
from threading import Lock, Thread

import requests
from flask import Flask, request, redirect, render_template_string, jsonify


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

PORT = int(os.getenv("PORT", "8080"))

DB_FILE = "ofertas.db"

ML_API = "https://api.mercadolibre.com"

ML_AUTH_URL = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN_URL = (
    "https://api.mercadolibre.com/oauth/token"
)

SITE_ID = "MLB"

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

MIN_PRODUCT_PRICE = 69.90

REQUEST_TIMEOUT = 15

MAX_PRODUCTS = 45

SEARCH_LIMIT = 6

QUERIES = [
    "celular",
    "perfume",
    "academia",
    "ferramentas",
    "eletronicos",
    "casa",
    "automotivo",
    "cozinha",
    "moda"
]


# ============================================================
# ESTADO
# ============================================================

ML_TOKEN = None

ML_REFRESH_TOKEN = None

ML_USER = None

oauth_states = {}

jobs = {}

LOCK = Lock()


# ============================================================
# BANCO
# ============================================================

def db():

    c = sqlite3.connect(DB_FILE)

    c.row_factory = sqlite3.Row

    return c


def init_db():

    c = db()

    c.execute("""
        CREATE TABLE IF NOT EXISTS offers (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            product_id TEXT UNIQUE,

            product_title TEXT,

            item_id TEXT,

            seller_id TEXT,

            price REAL,

            original_price REAL,

            coupon_code TEXT,

            coupon_type TEXT,

            coupon_value REAL,

            coupon_min REAL,

            coupon_max REAL,

            discount REAL,

            final_price REAL,

            coupon_confirmed INTEGER DEFAULT 0,

            coupon_source TEXT,

            shipping_free INTEGER DEFAULT 0,

            link TEXT,

            created_at TEXT
        )
    """)

    cols = {
        r[1]
        for r in c.execute(
            "PRAGMA table_info(offers)"
        ).fetchall()
    }

    if "original_price" not in cols:

        c.execute(
            "ALTER TABLE offers "
            "ADD COLUMN original_price REAL"
        )

    c.commit()

    c.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

def safe_float(
    v,
    default=0
):

    try:

        if v is None:

            return default

        if isinstance(
            v,
            (int, float)
        ):

            return float(v)

        s = str(v).strip()

        s = (
            s
            .replace("R$", "")
            .replace(" ", "")
        )

        if "," in s:

            s = (
                s
                .replace(".", "")
                .replace(",", ".")
            )

        return float(s)

    except Exception:

        return default


def unique(xs):

    out = []

    seen = set()

    for x in xs:

        if x and x not in seen:

            seen.add(x)

            out.append(x)

    return out


def headers(
    auth=True
):

    h = {

        "Accept":
            "application/json",

        "User-Agent":
            "CacadorDeOfertas/4.0"
    }

    if auth and ML_TOKEN:

        h["Authorization"] = (
            f"Bearer {ML_TOKEN}"
        )

    return h


# ============================================================
# PKCE
# ============================================================

def generate_pkce():

    verifier = secrets.token_urlsafe(
        64
    )

    digest = hashlib.sha256(
        verifier.encode()
    ).digest()

    challenge = (
        base64.urlsafe_b64encode(
            digest
        )
        .decode()
        .rstrip("=")
    )

    return verifier, challenge


# ============================================================
# LOGIN MERCADO LIVRE
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def ml_login():

    if not ML_CLIENT_ID:

        return (
            "ML_CLIENT_ID não configurado.",
            500
        )

    state = secrets.token_urlsafe(
        32
    )

    verifier, challenge = (
        generate_pkce()
    )

    oauth_states[state] = {

        "verifier":
            verifier,

        "created":
            time.time()
    }

    return redirect(

        ML_AUTH_URL
        + "?"
        + urlencode({

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
        })
    )


# ============================================================
# CALLBACK
# ============================================================

@app.route(
    "/mercadolivre/callback"
)
def ml_callback():

    global ML_TOKEN

    global ML_REFRESH_TOKEN

    global ML_USER

    error = request.args.get(
        "error"
    )

    if error:

        return (
            f"Erro Mercado Livre: "
            f"{error}",
            400
        )

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    saved = (
        oauth_states.pop(
            state,
            None
        )
        if code and state
        else None
    )

    if not saved:

        return (
            "OAuth inválido ou "
            "state expirado.",
            400
        )

    try:

        r = requests.post(

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
                    saved["verifier"]
            },

            timeout=REQUEST_TIMEOUT
        )

        data = r.json()

    except Exception as e:

        return (
            f"Erro OAuth: {e}",
            500
        )

    if not r.ok:

        return jsonify(
            data
        ), r.status_code

    ML_TOKEN = data.get(
        "access_token"
    )

    ML_REFRESH_TOKEN = data.get(
        "refresh_token"
    )

    if not ML_TOKEN:

        return (
            "Token não recebido.",
            500
        )

    try:

        me = requests.get(

            f"{ML_API}/users/me",

            headers=headers(),

            timeout=REQUEST_TIMEOUT
        )

        if me.ok:

            ML_USER = me.json()

    except Exception:

        pass

    return redirect("/")


# ============================================================
# REFRESH TOKEN
# ============================================================

def refresh_token():

    global ML_TOKEN

    global ML_REFRESH_TOKEN

    if not ML_REFRESH_TOKEN:

        return False

    try:

        r = requests.post(

            ML_TOKEN_URL,

            data={

                "grant_type":
                    "refresh_token",

                "client_id":
                    ML_CLIENT_ID,

                "client_secret":
                    ML_CLIENT_SECRET,

                "refresh_token":
                    ML_REFRESH_TOKEN
            },

            timeout=REQUEST_TIMEOUT
        )

        d = r.json()

        if (
            r.ok
            and d.get("access_token")
        ):

            ML_TOKEN = d[
                "access_token"
            ]

            if d.get(
                "refresh_token"
            ):

                ML_REFRESH_TOKEN = d[
                    "refresh_token"
                ]

            return True

    except Exception:

        pass

    return False


# ============================================================
# GET MERCADO LIVRE
# ============================================================

def ml_get(
    path,
    params=None,
    auth=True,
    retry=True
):

    try:

        r = requests.get(

            ML_API + path,

            headers=headers(auth),

            params=params,

            timeout=REQUEST_TIMEOUT
        )

        if (
            r.status_code == 401
            and auth
            and retry
            and refresh_token()
        ):

            return ml_get(

                path,

                params,

                auth=True,

                retry=False
            )

        return r

    except Exception as e:

        print(
            "[ML GET ERROR]",
            path,
            repr(e)
        )

        return None


# ============================================================
# BUSCA DE ANÚNCIOS
# ============================================================

def search_items(
    query,
    limit=SEARCH_LIMIT
):

    # --------------------------------------------------------
    # IMPORTANTE:
    #
    # Não usamos status=active.
    #
    # O endpoint /sites/MLB/search já trabalha
    # com os anúncios disponíveis na busca.
    # --------------------------------------------------------

    params = {

        "q":
            query,

        "limit":
            limit
    }

    r = ml_get(

        f"/sites/{SITE_ID}/search",

        params=params,

        auth=True
    )

    if r is None:

        return [], {

            "status":
                "sem_resposta",

            "detail":
                "Falha de conexão com a API."
        }

    if r.ok:

        try:

            d = r.json()

            return (
                d.get(
                    "results",
                    []
                ) or [],
                {

                    "status":
                        r.status_code,

                    "total":
                        (
                            d.get(
                                "paging"
                            )
                            or {}
                        ).get(
                            "total",
                            0
                        )
                }
            )

        except Exception as e:

            return [], {

                "status":
                    r.status_code,

                "detail":
                    f"JSON inválido: {e}"
            }

    # --------------------------------------------------------
    # Se a chamada autenticada falhar,
    # fazemos uma chamada pública para descobrir
    # se o problema está no token.
    # --------------------------------------------------------

    try:

        detail = r.json()

    except Exception:

        detail = r.text[:500]

    rp = ml_get(

        f"/sites/{SITE_ID}/search",

        params=params,

        auth=False
    )

    if (
        rp is not None
        and rp.ok
    ):

        try:

            d = rp.json()

            return (
                d.get(
                    "results",
                    []
                ) or [],
                {

                    "status":
                        rp.status_code,

                    "total":
                        (
                            d.get(
                                "paging"
                            )
                            or {}
                        ).get(
                            "total",
                            0
                        ),

                    "fallback_public":
                        True,

                    "auth_error":
                        detail
                }
            )

        except Exception:

            pass

    return [], {

        "status":
            r.status_code,

        "detail":
            detail,

        "public_status":
            getattr(
                rp,
                "status_code",
                None
            ),

        "public_detail":
            (
                getattr(
                    rp,
                    "text",
                    ""
                )[:500]
                if rp
                else ""
            )
    }


# ============================================================
# ITEM
# ============================================================

def get_item(
    item_id
):

    r = ml_get(
        f"/items/{item_id}",
        auth=True
    )

    if not r or not r.ok:

        return None

    try:

        return r.json()

    except Exception:

        return None


# ============================================================
# LIMPAR HTML
# ============================================================

def clean_html(
    html
):

    s = re.sub(

        r"<script[^>]*>.*?</script>",

        " ",

        html or "",

        flags=re.I | re.S
    )

    s = re.sub(

        r"<style[^>]*>.*?</style>",

        " ",

        s,

        flags=re.I | re.S
    )

    s = re.sub(

        r"<[^>]+>",

        " ",

        s
    )

    s = (
        s
        .replace("&nbsp;", " ")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&amp;", "&")
    )

    return re.sub(
        r"\s+",
        " ",
        s
    ).strip()


# ============================================================
# PÁGINA PÚBLICA
# ============================================================

def public_html(
    item
):

    urls = unique([

        item.get(
            "permalink"
        ),

        (
            f"https://www.mercadolivre.com.br/"
            f"p/{item.get('id')}"
            if item.get("id")
            else None
        )
    ])

    h = {

        "User-Agent":
            (
                "Mozilla/5.0 "
                "(iPhone; CPU iPhone OS 18_0 "
                "like Mac OS X) "
                "AppleWebKit/605.1.15 "
                "Version/18.0 "
                "Mobile/15E148 "
                "Safari/604.1"
            ),

        "Accept-Language":
            "pt-BR,pt;q=0.9"
    }

    for u in urls:

        try:

            r = requests.get(

                u,

                headers=h,

                timeout=REQUEST_TIMEOUT
            )

            if r.ok:

                return r.text

        except Exception:

            pass

    return ""


# ============================================================
# ENCONTRAR CÓDIGO
# ============================================================

def extract_code(
    text,
    start=0,
    end=None
):

    if not text:

        return None

    if end is None:

        end = len(text)

    w = text[
        max(
            0,
            start - 600
        ):
        min(
            len(text),
            end + 900
        )
    ]

    pats = [

        r"(?:cupom|código)"
        r"\s*[:\-]?\s*"
        r"([A-Z0-9]"
        r"[A-Z0-9_-]{3,24})",

        r"(?:use|usar)"
        r"\s+(?:o\s+)?"
        r"(?:cupom|código)"
        r"\s*[:\-]?\s*"
        r"([A-Z0-9]"
        r"[A-Z0-9_-]{3,24})"
    ]

    bad = {

        "DESCONTO",
        "CUPOM",
        "OFF",
        "MERCADO",
        "LIVRE",
        "PROMOCAO",
        "PROMOÇÃO",
        "PARA",
        "TODOS"
    }

    for p in pats:

        m = re.search(
            p,
            w,
            re.I
        )

        if (
            m
            and m.group(1).upper()
            not in bad
        ):

            return (
                m.group(1)
                .upper()
            )

    return None


# ============================================================
# DETECTAR CUPOM
# ============================================================

def detect_coupon(
    html
):

    if not html:

        return None

    text = clean_html(
        html
    )

    lower = text.lower()

    patterns = [

        (
            "percent",

            r"(\d{1,2}"
            r"(?:[,.]\d+)?)"
            r"\s*%"
            r"\s*(?:off|de\s+desconto)"
            r".{0,220}"
            r"cupom"
        ),

        (
            "percent",

            r"cupom"
            r".{0,220}"
            r"(\d{1,2}"
            r"(?:[,.]\d+)?)"
            r"\s*%"
            r"\s*(?:off|de\s+desconto)"
        ),

        (
            "fixed",

            r"R\$\s*"
            r"([\d\.,]+)"
            r"\s*(?:off|de\s+desconto)"
            r".{0,220}"
            r"cupom"
        ),

        (
            "fixed",

            r"cupom"
            r".{0,220}"
            r"R\$\s*"
            r"([\d\.,]+)"
            r"\s*(?:off|de\s+desconto)"
        )
    ]

    for typ, pat in patterns:

        m = re.search(
            pat,
            lower,
            re.I
        )

        if not m:

            continue

        val = safe_float(
            m.group(1)
        )

        if (
            val > 0
            and (
                typ != "percent"
                or val <= 100
            )
        ):

            return {

                "type":
                    typ,

                "value":
                    val,

                "code":
                    (
                        extract_code(
                            text,
                            m.start(),
                            m.end()
                        )
                        or "CUPOM"
                    ),

                "confirmed":
                    True,

                "source":
                    "pagina_produto"
            }

    # --------------------------------------------------------
    # Janela ampla ao redor de "cupom".
    # --------------------------------------------------------

    for m in re.finditer(
        r"cupom|coupon",
        lower,
        re.I
    ):

        w = text[

            max(
                0,
                m.start() - 300
            ):

            min(
                len(text),
                m.end() + 900
            )
        ]

        p = re.search(

            r"(\d{1,2}"
            r"(?:[,.]\d+)?)"
            r"\s*%",

            w
        )

        if p:

            value = safe_float(
                p.group(1)
            )

            if (
                0 < value <= 100
            ):

                return {

                    "type":
                        "percent",

                    "value":
                        value,

                    "code":
                        (
                            extract_code(w)
                            or "CUPOM"
                        ),

                    "confirmed":
                        True,

                    "source":
                        "pagina_produto"
                }

        f = re.search(

            r"R\$\s*"
            r"([\d\.,]+)",

            w,

            re.I
        )

        if f:

            value = safe_float(
                f.group(1)
            )

            if value > 0:

                return {

                    "type":
                        "fixed",

                    "value":
                        value,

                    "code":
                        (
                            extract_code(w)
                            or "CUPOM"
                        ),

                    "confirmed":
                        True,

                    "source":
                        "pagina_produto"
                }

    return None


# ============================================================
# CALCULAR DESCONTO
# ============================================================

def calc(
    price,
    typ,
    value
):

    price = safe_float(
        price
    )

    value = safe_float(
        value
    )

    if (
        price <= 0
        or value <= 0
    ):

        return None

    if typ == "percent":

        discount = (
            price
            * value
            / 100
        )

    else:

        discount = value

    discount = min(
        discount,
        price
    )

    if discount <= 0:

        return None

    return {

        "discount":
            round(
                discount,
                2
            ),

        "final_price":
            round(
                price - discount,
                2
            )
    }


# ============================================================
# ANALISAR ANÚNCIO
# ============================================================

def analyze(
    item
):

    item_id = str(
        item.get("id")
        or ""
    )

    price = safe_float(
        item.get("price")
    )

    if not item_id:

        return (
            None,
            "item sem ID"
        )

    if price < MIN_PRODUCT_PRICE:

        return (
            None,
            "abaixo do preço mínimo"
        )

    html = public_html(
        item
    )

    coupon = detect_coupon(
        html
    )

    if not coupon:

        return (
            None,
            "nenhum cupom identificado"
        )

    c = calc(

        price,

        coupon["type"],

        coupon["value"]
    )

    if not c:

        return (
            None,
            "cupom sem desconto válido"
        )

    seller = (
        item.get("seller")
        or {}
    )

    ship = (
        item.get("shipping")
        or {}
    )

    offer = {

        "product_id":
            item_id,

        "product_title":
            (
                item.get("title")
                or "Produto Mercado Livre"
            ),

        "item_id":
            item_id,

        "seller_id":
            (
                seller.get("id")
                or item.get("seller_id")
            ),

        "price":
            round(
                price,
                2
            ),

        "original_price":
            (
                safe_float(
                    item.get(
                        "original_price"
                    )
                )
                or None
            ),

        "coupon_code":
            (
                coupon.get("code")
                or "CUPOM"
            ),

        "coupon_type":
            coupon["type"],

        "coupon_value":
            safe_float(
                coupon["value"]
            ),

        "coupon_min":
            0,

        "coupon_max":
            None,

        "discount":
            c["discount"],

        "final_price":
            c["final_price"],

        "coupon_confirmed":
            1,

        "coupon_source":
            coupon.get(
                "source",
                "pagina_produto"
            ),

        "shipping_free":
            (
                1
                if ship.get(
                    "free_shipping"
                )
                else 0
            ),

        "link":
            (
                item.get(
                    "permalink"
                )
                or
                f"https://produto."
                f"mercadolivre.com.br/"
                f"{item_id}"
            )
    }

    return (
        offer,
        "cupom identificado"
    )


# ============================================================
# SALVAR
# ============================================================

def save_offer(
    o
):

    c = db()

    c.execute(

        """
        INSERT INTO offers (

            product_id,
            product_title,
            item_id,
            seller_id,
            price,
            original_price,
            coupon_code,
            coupon_type,
            coupon_value,
            coupon_min,
            coupon_max,
            discount,
            final_price,
            coupon_confirmed,
            coupon_source,
            shipping_free,
            link,
            created_at

        )

        VALUES (
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?
        )

        ON CONFLICT(product_id)
        DO UPDATE SET

            product_title =
                excluded.product_title,

            item_id =
                excluded.item_id,

            seller_id =
                excluded.seller_id,

            price =
                excluded.price,

            original_price =
                excluded.original_price,

            coupon_code =
                excluded.coupon_code,

            coupon_type =
                excluded.coupon_type,

            coupon_value =
                excluded.coupon_value,

            coupon_min =
                excluded.coupon_min,

            coupon_max =
                excluded.coupon_max,

            discount =
                excluded.discount,

            final_price =
                excluded.final_price,

            coupon_confirmed =
                excluded.coupon_confirmed,

            coupon_source =
                excluded.coupon_source,

            shipping_free =
                excluded.shipping_free,

            link =
                excluded.link,

            created_at =
                excluded.created_at
        """,

        (

            o["product_id"],

            o["product_title"],

            o["item_id"],

            o["seller_id"],

            o["price"],

            o["original_price"],

            o["coupon_code"],

            o["coupon_type"],

            o["coupon_value"],

            o["coupon_min"],

            o["coupon_max"],

            o["discount"],

            o["final_price"],

            o["coupon_confirmed"],

            o["coupon_source"],

            o["shipping_free"],

            o["link"],

            time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
    )

    c.commit()

    c.close()


# ============================================================
# LIMPAR OFERTAS
# ============================================================

def clear_offers():

    c = db()

    c.execute(
        "DELETE FROM offers"
    )

    c.commit()

    c.close()


# ============================================================
# JOB
# ============================================================

def set_job(
    jid,
    **kw
):

    with LOCK:

        if jid in jobs:

            jobs[jid].update(
                kw
            )


# ============================================================
# CAÇA
# ============================================================

def run_hunt(
    jid
):

    try:

        set_job(

            jid,

            status="running",

            progress=2,

            message=(
                "Testando a busca "
                "do Mercado Livre..."
            ),

            found=0,

            analyzed=0
        )

        all_items = []

        seen = set()

        search_errors = []

        totals = []

        # ----------------------------------------------------
        # Faz todas as buscas.
        # ----------------------------------------------------

        for q in QUERIES:

            items, info = search_items(
                q
            )

            if (
                info.get("status")
                != 200
                or info.get(
                    "fallback_public"
                )
                or info.get(
                    "detail"
                )
            ):

                search_errors.append({

                    "query":
                        q,

                    **info
                })

            totals.append({

                "query":
                    q,

                "returned":
                    len(items),

                **info
            })

            for item in items:

                iid = str(
                    item.get("id")
                    or ""
                )

                if (
                    iid
                    and iid not in seen
                ):

                    seen.add(iid)

                    all_items.append(
                        item
                    )

                    if (
                        len(all_items)
                        >= MAX_PRODUCTS
                    ):

                        break

            if (
                len(all_items)
                >= MAX_PRODUCTS
            ):

                break

        clear_offers()

        total = len(
            all_items
        )

        # ----------------------------------------------------
        # Nenhum anúncio.
        # ----------------------------------------------------

        if total == 0:

            set_job(

                jid,

                status="done",

                progress=100,

                found=0,

                analyzed=0,

                message=(
                    "Nenhum anúncio "
                    "foi retornado "
                    "pela busca."
                ),

                diagnostic={

                    "consultas":
                        len(QUERIES),

                    "anuncios_retornados":
                        sum(
                            x[
                                "returned"
                            ]
                            for x in totals
                        ),

                    "anuncios_unicos":
                        0,

                    "detalhes_busca":
                        totals,

                    "erros":
                        search_errors
                }
            )

            return

        # ----------------------------------------------------
        # Analisar anúncios.
        # ----------------------------------------------------

        found = 0

        reasons = {}

        for i, item in enumerate(
            all_items,
            1
        ):

            try:

                offer, reason = analyze(
                    item
                )

            except Exception as e:

                offer = None

                reason = (
                    f"erro: {e}"
                )

            reasons[reason] = (
                reasons.get(
                    reason,
                    0
                )
                + 1
            )

            if offer:

                save_offer(
                    offer
                )

                found += 1

            set_job(

                jid,

                progress=int(
                    i
                    /
                    total
                    * 100
                ),

                found=found,

                analyzed=i,

                message=(
                    f"Analisando "
                    f"{i}/{total} "
                    f"• "
                    f"{found} "
                    f"oportunidades"
                )
            )

        # ----------------------------------------------------
        # Final.
        # ----------------------------------------------------

        set_job(

            jid,

            status="done",

            progress=100,

            found=found,

            analyzed=total,

            message=(
                f"Caça finalizada — "
                f"{found} oportunidades "
                f"encontradas."
            ),

            diagnostic={

                "consultas":
                    len(QUERIES),

                "anuncios_retornados":
                    sum(
                        x[
                            "returned"
                        ]
                        for x in totals
                    ),

                "anuncios_unicos":
                    total,

                "analisados":
                    total,

                "oportunidades":
                    found,

                "motivos":
                    reasons,

                "detalhes_busca":
                    totals,

                "erros":
                    search_errors
            }
        )

    except Exception as e:

        set_job(

            jid,

            status="error",

            message=str(e),

            error=str(e)
        )


# ============================================================
# INICIAR CAÇA
# ============================================================

@app.route(
    "/api/hunt",
    methods=["POST"]
)
def start_hunt():

    if not ML_TOKEN:

        return jsonify({

            "ok":
                False,

            "error":
                "Mercado Livre "
                "não conectado."
        }), 401

    jid = uuid.uuid4().hex

    with LOCK:

        jobs[jid] = {

            "status":
                "queued",

            "progress":
                0,

            "message":
                "Iniciando...",

            "found":
                0
        }

    Thread(

        target=run_hunt,

        args=(jid,),

        daemon=True

    ).start()

    return jsonify({

        "ok":
            True,

        "job_id":
            jid
    })


# ============================================================
# STATUS
# ============================================================

@app.route(
    "/api/job/<jid>"
)
def job_status(
    jid
):

    with LOCK:

        j = jobs.get(
            jid
        )

    if not j:

        return jsonify({

            "error":
                "job não encontrado"

        }), 404

    return jsonify(
        j
    )


# ============================================================
# OFERTAS
# ============================================================

@app.route(
    "/api/offers"
)
def offers():

    c = db()

    rows = c.execute("""

        SELECT *

        FROM offers

        ORDER BY
            discount DESC,
            final_price ASC

    """).fetchall()

    c.close()

    return jsonify([

        dict(r)

        for r in rows
    ])


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health"
)
def health():

    return jsonify({

        "status":
            "ok",

        "app":
            "Cacador de Ofertas",

        "version":
            "4.0",

        "mercadolivre":
            bool(ML_TOKEN)
    })


# ============================================================
# GERAR ANÚNCIO
# ============================================================

@app.route(
    "/api/anuncio/<int:oid>",
    methods=["POST"]
)
def anuncio(
    oid
):

    c = db()

    r = c.execute(

        "SELECT * FROM offers "
        "WHERE id=?",

        (oid,)

    ).fetchone()

    c.close()

    if not r:

        return jsonify({

            "error":
                "oferta não encontrada"

        }), 404

    o = dict(r)

    if (
        o["coupon_type"]
        == "percent"
    ):

        label = (
            f"{o['coupon_value']:.0f}% OFF"
        )

    else:

        label = (
            f"R$ "
            f"{o['coupon_value']:.2f}"
            f" OFF"
        )

    text = (

        "🔥 OFERTA ENCONTRADA!\n\n"

        f"{o['product_title']}\n\n"

        f"💰 Valor: "
        f"R$ {o['price']:.2f}\n"

        f"🎟️ Cupom: "
        f"{o['coupon_code']}\n"

        f"🏷️ {label}\n"

        f"💸 Por apenas "
        f"R$ {o['final_price']:.2f}\n\n"

        f"💰 Economia de "
        f"R$ {o['discount']:.2f}\n\n"

        "⚠️ Confira a aplicação "
        "do cupom no checkout.\n\n"

        "🛒 Comprar:\n"

        f"{o['link']}"
    )

    return jsonify({

        "ok":
            True,

        "text":
            text
    })


# ============================================================
# HTML
# ============================================================

HTML = '''
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
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

    background: #0b0f14;

    color: #fff;

    font-family:
        Arial,
        sans-serif;
}

.container {

    width:
        min(1150px,94%);

    margin:
        auto;

    padding:
        25px 0 50px;
}

.header {

    display:
        flex;

    justify-content:
        space-between;

    align-items:
        center;

    gap:
        15px;

    flex-wrap:
        wrap;

    margin-bottom:
        20px;
}

h1 {

    margin:
        0;

    font-size:
        29px;
}

.subtitle {

    color:
        #9ba5b1;

    margin-top:
        8px;

    font-size:
        17px;
}

button {

    border:
        0;

    cursor:
        pointer;

    border-radius:
        12px;

    font-weight:
        bold;

    color:
        #fff;
}

.hunt {

    background:
        #3483fa;

    padding:
        15px 22px;

    font-size:
        16px;
}

.hunt:disabled {

    opacity:
        .55;
}

.box {

    background:
        #121821;

    border:
        1px solid #202936;

    border-radius:
        14px;

    padding:
        16px;

    margin-bottom:
        14px;

    color:
        #c7d0da;
}

.diag {

    display:
        none;

    line-height:
        1.55;

    font-size:
        14px;
}

.progressbox {

    display:
        none;
}

.barbg {

    height:
        9px;

    background:
        #252e39;

    border-radius:
        20px;

    margin-top:
        12px;

    overflow:
        hidden;
}

.bar {

    width:
        0;

    height:
        100%;

    background:
        #3483fa;

    transition:
        width .25s;
}

.stats {

    display:
        grid;

    grid-template-columns:
        repeat(4,1fr);

    gap:
        12px;

    margin-bottom:
        18px;
}

.stat {

    background:
        #121821;

    border:
        1px solid #202936;

    border-radius:
        14px;

    padding:
        18px;
}

.stat-title {

    color:
        #9ba5b1;

    font-size:
        14px;
}

.stat-value {

    font-size:
        27px;

    font-weight:
        bold;

    margin-top:
        7px;
}

.grid {

    display:
        grid;

    grid-template-columns:
        repeat(2,1fr);

    gap:
        15px;
}

.card {

    background:
        #121821;

    border:
        1px solid #202936;

    border-radius:
        15px;

    padding:
        18px;
}

.card h3 {

    margin:
        0 0 15px;

    line-height:
        1.4;

    font-size:
        17px;
}

.coupon {

    margin-top:
        15px;

    padding:
        13px;

    border-radius:
        11px;

    background:
        #10251b;

    border:
        1px solid #174f32;
}

.couponcode {

    font-size:
        18px;

    font-weight:
        bold;
}

.saving {

    color:
        #43d17a;

    margin-top:
        6px;
}

.final {

    font-size:
        26px;

    font-weight:
        bold;

    margin-top:
        14px;
}

.actions {

    display:
        flex;

    gap:
        8px;

    margin-top:
        15px;

    flex-wrap:
        wrap;
}

.actions a,
.actions button {

    padding:
        11px 14px;

    border-radius:
        9px;

    font-size:
        14px;

    text-decoration:
        none;
}

.link {

    background:
        #3483fa;
}

.ad {

    background:
        #242d38;
}

.empty {

    grid-column:
        1 / -1;

    text-align:
        center;

    padding:
        55px 20px;

    background:
        #121821;

    border:
        1px solid #202936;

    border-radius:
        15px;

    color:
        #9ba5b1;

    font-size:
        18px;
}

@media(max-width:800px) {

    .stats {

        grid-template-columns:
            repeat(2,1fr);
    }

    .grid {

        grid-template-columns:
            1fr;
    }

}

</style>

</head>

<body>

<div class="container">

<div class="header">

<div>

<h1>
🛒 Caçador de Ofertas
</h1>

<div class="subtitle">
Produtos com cupom
</div>

</div>

{% if connected %}

<button
    class="hunt"
    id="hunt"
    onclick="hunt()"
>
🔎 CAÇAR OFERTAS
</button>

{% else %}

<a
    href="/mercadolivre/login"
    style="
        background:#00a650;
        color:#fff;
        padding:15px 20px;
        border-radius:12px;
        text-decoration:none;
        font-weight:bold
    "
>
🔗 Conectar Mercado Livre
</a>

{% endif %}

</div>


<div
    class="box"
    id="status"
>

{% if connected %}

🟢 Mercado Livre conectado

{% if user %}

— {{user.nickname or user.id}}

{% endif %}

{% else %}

🔴 Mercado Livre não conectado

{% endif %}

</div>


<div
    class="box progressbox"
    id="pb"
>

<div id="pt">
Preparando...
</div>

<div class="barbg">

<div
    class="bar"
    id="bar"
></div>

</div>

</div>


<div
    class="box diag"
    id="diag"
>
</div>


<div class="stats">

<div class="stat">

<div class="stat-title">
Ofertas
</div>

<div
    class="stat-value"
    id="oc"
>
0
</div>

</div>


<div class="stat">

<div class="stat-title">
Cupom identificado
</div>

<div
    class="stat-value"
    id="cc"
>
0
</div>

</div>


<div class="stat">

<div class="stat-title">
Valor
</div>

<div
    class="stat-value"
    id="tv"
>
R$ 0,00
</div>

</div>


<div class="stat">

<div class="stat-title">
Valor com desconto
</div>

<div
    class="stat-value"
    id="tf"
>
R$ 0,00
</div>

</div>

</div>


<div
    class="grid"
    id="offers"
>

<div class="empty">

Clique em
<b>CAÇAR OFERTAS</b>
para começar.

</div>

</div>

</div>


<script>

const money = v =>

    Number(
        v || 0
    ).toLocaleString(
        'pt-BR',
        {
            style:
                'currency',

            currency:
                'BRL'
        }
    );


const esc = v =>

    String(
        v || ''
    )

    .replace(
        /&/g,
        '&amp;'
    )

    .replace(
        /</g,
        '&lt;'
    )

    .replace(
        />/g,
        '&gt;'
    )

    .replace(
        /"/g,
        '&quot;'
    )

    .replace(
        /'/g,
        '&#039;'
    );


async function load() {

    try {

        let r =
            await fetch(
                '/api/offers'
            );

        let o =
            await r.json();

        render(o);

    } catch(e) {

        console.error(e);

    }

}


function render(o) {

    let c =
        document.getElementById(
            'offers'
        );

    c.innerHTML = '';

    let tv = 0;

    let tf = 0;


    o.forEach(
        x => {

            tv +=
                Number(
                    x.price
                ) || 0;

            tf +=
                Number(
                    x.final_price
                ) || 0;


            let label =
                x.coupon_type
                === 'percent'

                ?

                Number(
                    x.coupon_value
                ).toFixed(0)
                + '% OFF'

                :

                money(
                    x.coupon_value
                )
                + ' OFF';


            let d =
                document.createElement(
                    'div'
                );

            d.className =
                'card';


            d.innerHTML = `

                <h3>
                    ${esc(
                        x.product_title
                    )}
                </h3>

                <div>
                    Valor:
                    <b>
                        ${money(
                            x.price
                        )}
                    </b>
                </div>

                <div class="coupon">

                    <div class="couponcode">

                        🎟️
                        ${esc(
                            x.coupon_code
                        )}

                    </div>

                    <div class="saving">

                        ${label}

                        • economia de

                        ${money(
                            x.discount
                        )}

                    </div>

                    <div
                        style="
                            color:#43d17a;
                            font-size:12px;
                            margin-top:7px
                        "
                    >

                        ✓ Cupom identificado
                        nesta oferta

                    </div>

                </div>

                <div class="final">

                    ${money(
                        x.final_price
                    )}

                </div>

                <div class="actions">

                    <a
                        class="link"
                        href="${x.link}"
                        target="_blank"
                        rel="noopener"
                    >
                        🛒 Ver produto
                    </a>

                    <button
                        class="ad"
                        onclick="ad(${x.id})"
                    >
                        ✍️ Gerar anúncio
                    </button>

                </div>

            `;


            c.appendChild(d);

        }
    );


    document.getElementById(
        'oc'
    ).innerText =
        o.length;


    document.getElementById(
        'cc'
    ).innerText =
        o.length;


    document.getElementById(
        'tv'
    ).innerText =
        money(tv);


    document.getElementById(
        'tf'
    ).innerText =
        money(tf);


    if (
        !o.length
    ) {

        c.innerHTML = `

            <div class="empty">

                Nenhuma oportunidade
                encontrada nesta rodada.

            </div>

        `;

    }

}


function diag(d) {

    let box =
        document.getElementById(
            'diag'
        );


    if (!d) {

        box.style.display =
            'none';

        return;

    }


    let details = (

        d.detalhes_busca
        || []

    )

    .map(

        x => `

            <div>

                • ${esc(x.query)}:

                ${x.returned || 0}

                anúncio(s)

                — HTTP

                ${esc(x.status)}

            </div>

        `

    )

    .join('');


    let reasons = Object.entries(

        d.motivos
        || {}

    )

    .map(

        ([k,v]) => `

            <div>

                • ${esc(k)}:

                <b>${v}</b>

            </div>

        `

    )

    .join('');


    box.innerHTML = `

        <b>
            🔎 Diagnóstico da caça
        </b>

        <div>
            Consultas:
            ${d.consultas || 0}
        </div>

        <div>
            Anúncios retornados:
            ${d.anuncios_retornados || 0}
        </div>

        <div>
            Anúncios únicos:
            ${d.anuncios_unicos || 0}
        </div>

        <div>
            Analisados:
            ${d.analisados || 0}
        </div>

        <div>
            Oportunidades:
            ${d.oportunidades || 0}
        </div>

        <br>

        <b>
            Motivos:
        </b>

        ${
            reasons
            ||
            '<div>—</div>'
        }

        <br>

        <b>
            Retorno das buscas:
        </b>

        ${
            details
            ||
            '<div>—</div>'
        }

    `;


    if (
        d.erros
        &&
        d.erros.length
    ) {

        box.innerHTML += `

            <br>

            <b>
                Erros da API:
            </b>

            <pre
                style="
                    white-space:pre-wrap
                "
            >${
                esc(
                    JSON.stringify(
                        d.erros,
                        null,
                        2
                    )
                )
            }</pre>

        `;

    }


    box.style.display =
        'block';

}


async function hunt() {

    let b =
        document.getElementById(
            'hunt'
        );

    let pb =
        document.getElementById(
            'pb'
        );

    let bar =
        document.getElementById(
            'bar'
        );

    let pt =
        document.getElementById(
            'pt'
        );

    let st =
        document.getElementById(
            'status'
        );


    b.disabled =
        true;

    b.innerText =
        '⏳ CAÇANDO...';


    pb.style.display =
        'block';


    bar.style.width =
        '0%';


    pt.innerText =
        'Testando a busca do Mercado Livre...';


    st.innerText =
        '🔎 Caçando ofertas...';


    document.getElementById(
        'diag'
    ).style.display =
        'none';


    try {

        let r =
            await fetch(
                '/api/hunt',
                {
                    method:
                        'POST'
                }
            );


        let d =
            await r.json();


        if (
            !d.job_id
        ) {

            throw Error(
                d.error
                ||
                'Não foi possível iniciar.'
            );

        }


        while(true) {

            let jr =
                await fetch(
                    '/api/job/'
                    + d.job_id
                );


            let j =
                await jr.json();


            bar.style.width =
                (
                    j.progress
                    || 0
                )
                + '%';


            pt.innerText =
                j.message
                || '';


            if (
                j.status
                === 'done'
            ) {

                st.innerText =
                    '🟢 '
                    + j.message;


                diag(
                    j.diagnostic
                );


                await load();

                break;

            }


            if (
                j.status
                === 'error'
            ) {

                st.innerText =
                    '❌ '
                    + j.message;

                break;

            }


            await new Promise(
                x =>
                    setTimeout(
                        x,
                        700
                    )
            );

        }

    } catch(e) {

        st.innerText =
            '❌ '
            + e.message;

    } finally {

        b.disabled =
            false;

        b.innerText =
            '🔎 CAÇAR OFERTAS';

    }

}


async function ad(
    id
) {

    try {

        let r =
            await fetch(

                '/api/anuncio/'
                + id,

                {
                    method:
                        'POST'
                }

            );


        let d =
            await r.json();


        if (
            !d.text
        ) {

            return alert(
                'Não foi possível gerar.'
            );

        }


        let w =
            window.open(
                '',
                '_blank'
            );


        w.document.write(`

            <html>

            <body
                style="
                    background:#0b0f14;
                    color:white;
                    font-family:Arial;
                    padding:20px
                "
            >

                <h2>
                    📢 Anúncio
                </h2>

                <textarea
                    style="
                        width:100%;
                        height:400px;
                        background:#121821;
                        color:white;
                        border:1px solid #303b48;
                        border-radius:10px;
                        padding:12px;
                        font-size:15px
                    "
                >${esc(
                    d.text
                )}</textarea>

            </body>

            </html>

        `);


        w.document.close();


    } catch(e) {

        alert(
            'Erro ao gerar anúncio.'
        );

    }

}


load();

</script>

</body>

</html>
'''


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return render_template_string(

        HTML,

        connected=
            bool(ML_TOKEN),

        user=
            ML_USER
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print(
        "CAÇADOR DE OFERTAS v4"
    )

    print(
        "PORT:",
        PORT
    )

    print(
        "ML CLIENT:",
        bool(
            ML_CLIENT_ID
        )
    )

    print(
        "TOKEN:",
        bool(
            ML_TOKEN
        )
    )

    print(
        "REDIRECT:",
        ML_REDIRECT_URI
    )

    app.run(

        host="0.0.0.0",

        port=PORT,

        debug=False
    )