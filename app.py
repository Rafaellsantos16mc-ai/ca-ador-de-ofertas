import os
import re
import json
import time
import base64
import hashlib
import secrets
import sqlite3
import urllib.parse
from datetime import datetime

import requests
from flask import (
    Flask,
    request,
    redirect,
    render_template_string,
    jsonify,
    session,
)

# ============================================================
# APP
# ============================================================

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))

PORT = int(os.getenv("PORT", "8080"))

DB_FILE = "ofertas.db"

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_API = "https://api.mercadolibre.com"

PUBLIC_COUPON_URL = "https://www.mercadolivre.com.br/l/descontaco-cupons"

MIN_PRICE = 69.90


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT,
            updated_at TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def money_br(value):
    if value is None:
        return "R$ 0,00"

    try:
        value = float(value)
    except Exception:
        return "R$ 0,00"

    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_money(text):
    if not text:
        return None

    text = str(text)

    # R$ 1.299,99
    m = re.search(
        r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)",
        text,
        re.I
    )

    if not m:
        return None

    value = m.group(1)

    try:
        if "," in value:
            value = value.replace(".", "").replace(",", ".")
        return float(value)
    except Exception:
        return None


def normalize_public_text(text):
    """
    A página pública do Mercado Livre às vezes chega com espaços
    ou quebras entre os números.

    Exemplos:
    R$ 1 . 299 , 99
    R$ 69 , 34
    """

    if not text:
        return ""

    text = str(text)

    # espaços em volta de pontuação monetária
    text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", text)
    text = re.sub(r"(?<=\d)\s*,\s*(?=\d)", ",", text)

    # espaços duplicados
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def clean_spaces(text):
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text)
    return text.strip(" -|•\t\r\n")


# ============================================================
# TITLE CLEANER
# ============================================================

def clean_product_title(title):
    """
    Limpa títulos contaminados por textos da página.

    A correção principal está aqui.
    """

    if not title:
        return ""

    title = normalize_public_text(title)

    # Remove lixo conhecido que pode aparecer antes/depois do produto
    garbage_patterns = [
        r"\bChegará\s+até\b.*?$",
        r"\bChega\s+até\b.*?$",
        r"\bEntrega\s+grátis\b.*?$",
        r"\bFrete\s+grátis\b.*?$",
        r"\bEnviado\s+por\b.*?$",
        r"\bVendido\s+por\b.*?$",
        r"\bParcelado\b.*?$",
        r"\bem\s+\d+x\b.*?$",
        r"\b\d+x\s+R\$\s*[\d\.,]+.*?$",
        r"\bPix\b.*?$",
        r"\bno\s+Pix\b.*?$",
        r"\bem\s+outros\s+meios\b.*?$",
        r"\bCupom\b.*?$",
        r"\b\d+%\s+OFF\b.*?$",
        r"\bR\$\s*[\d\.,]+\s+OFF\b.*?$",
        r"\bAmanhã\b.*?$",
        r"\bHoje\b.*?$",
        r"\bdomingo\b.*?$",
        r"\bsegunda-feira\b.*?$",
        r"\bterça-feira\b.*?$",
        r"\bquarta-feira\b.*?$",
        r"\bquinta-feira\b.*?$",
        r"\bsexta-feira\b.*?$",
        r"\bsábado\b.*?$",
    ]

    for pattern in garbage_patterns:
        title = re.sub(pattern, "", title, flags=re.I)

    # Remove lixo no começo
    title = re.sub(
        r"^(?:DIA|OFERTA|OFERTAS|PROMOÇÃO|PROMOCAO|GRÁTIS|GRATIS)\s+",
        "",
        title,
        flags=re.I
    )

    # Remove chamadas promocionais isoladas
    title = re.sub(
        r"\b(?:corra|aproveite|imperdível|imperdivel|últimas unidades|ultimas unidades)\b.*?$",
        "",
        title,
        flags=re.I
    )

    # Remove restos de pontuação
    title = clean_spaces(title)

    # Evita títulos muito curtos ou lixo evidente
    invalid = [
        "is amanhã",
        "amanhã",
        "domingo",
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "dia",
        "oferta",
        "cupom",
    ]

    if title.lower() in invalid:
        return ""

    # Se o começo ficou claramente contaminado
    title = re.sub(
        r"^(?:is\s+)?(?:amanhã|hoje)\s+",
        "",
        title,
        flags=re.I
    )

    return title.strip(" -|•")


# ============================================================
# COUPON DETECTION
# ============================================================

def detect_coupon(text):
    """
    Detecta SOMENTE cupom verdadeiro.

    NÃO confunde:
        59% OFF
        62% OFF
        50% OFF

    com cupom.

    Só aceita quando a palavra Cupom está diretamente ligada
    à condição.
    """

    if not text:
        return None

    text = normalize_public_text(text)

    # Cupom R$ 15 OFF
    m = re.search(
        r"\bCupom\s+R\$\s*([\d\.,]+)\s*OFF\b",
        text,
        flags=re.I
    )

    if m:
        value = parse_money("R$ " + m.group(1))

        if value is not None:
            return {
                "type": "fixed",
                "value": value,
                "label": f"Cupom R$ {value:,.2f} OFF".replace(",", "X").replace(".", ",").replace("X", "."),
            }

    # R$ 15 OFF com Cupom
    m = re.search(
        r"\bR\$\s*([\d\.,]+)\s*OFF\s+com\s+Cupom\b",
        text,
        flags=re.I
    )

    if m:
        value = parse_money("R$ " + m.group(1))

        if value is not None:
            return {
                "type": "fixed",
                "value": value,
                "label": f"Cupom R$ {value:,.2f} OFF".replace(",", "X").replace(".", ",").replace("X", "."),
            }

    # Cupom 10% OFF
    m = re.search(
        r"\bCupom\s+(\d+(?:[.,]\d+)?)\s*%\s*OFF\b",
        text,
        flags=re.I
    )

    if m:
        try:
            value = float(m.group(1).replace(",", "."))

            return {
                "type": "percent",
                "value": value,
                "label": f"Cupom {str(m.group(1)).replace('.', ',')}% OFF",
            }
        except Exception:
            pass

    # 10% OFF com Cupom
    m = re.search(
        r"\b(\d+(?:[.,]\d+)?)\s*%\s*OFF\s+com\s+Cupom\b",
        text,
        flags=re.I
    )

    if m:
        try:
            value = float(m.group(1).replace(",", "."))

            return {
                "type": "percent",
                "value": value,
                "label": f"Cupom {str(m.group(1)).replace('.', ',')}% OFF",
            }
        except Exception:
            pass

    return None


# ============================================================
# MONEY EXTRACTION
# ============================================================

def all_money(text):
    if not text:
        return []

    text = normalize_public_text(text)

    result = []

    for m in re.finditer(
        r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)",
        text,
        flags=re.I
    ):
        value = parse_money(m.group(0))

        if value is not None:
            result.append({
                "value": value,
                "start": m.start(),
                "end": m.end(),
                "raw": m.group(0)
            })

    return result


# ============================================================
# CURRENT PRICE EXTRACTION
# ============================================================

def find_current_price(block):
    """
    Prioridade:

    1. preço imediatamente antes do percentual do vendedor:
       R$ 128,00 50% OFF

    2. preço depois do preço riscado/original

    3. último preço razoável.

    Isso evita pegar:
       R$ 72,99 em outros meios

    quando o preço real é:
       R$ 69,34 no Pix
    """

    block = normalize_public_text(block)

    # Preço + desconto direto do vendedor
    pattern = re.compile(
        r"(R\$\s*[0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|R\$\s*[0-9]+,[0-9]{2}|R\$\s*[0-9]+(?:\.[0-9]{2})?)"
        r"\s+"
        r"\d+(?:[.,]\d+)?\s*%\s*OFF",
        re.I
    )

    matches = list(pattern.finditer(block))

    if matches:
        # Normalmente o ÚLTIMO desses pares pertence ao produto
        m = matches[-1]
        value = parse_money(m.group(1))

        if value is not None:
            return value

    prices = all_money(block)

    if not prices:
        return None

    # Ignora valores absurdamente baixos
    valid = [
        p["value"]
        for p in prices
        if p["value"] >= 1
    ]

    if not valid:
        return None

    return valid[-1]


# ============================================================
# ORIGINAL PRICE
# ============================================================

def find_original_price(block, current_price):
    if not block or current_price is None:
        return None

    prices = all_money(block)

    if not prices:
        return None

    candidates = []

    for p in prices:
        value = p["value"]

        if value > current_price:
            candidates.append(value)

    if candidates:
        # O original mais próximo do preço atual
        return min(candidates)

    return None


# ============================================================
# COUPON CALCULATION
# ============================================================

def calculate_coupon(price, coupon):
    if price is None or coupon is None:
        return None

    if price < MIN_PRICE:
        return None

    if coupon["type"] == "fixed":
        discount = min(float(coupon["value"]), price)

    elif coupon["type"] == "percent":
        discount = price * float(coupon["value"]) / 100

    else:
        return None

    final_price = max(price - discount, 0)

    effective_percent = 0

    if price > 0:
        effective_percent = discount / price * 100

    return {
        "price": round(price, 2),
        "discount": round(discount, 2),
        "final": round(final_price, 2),
        "effective_percent": round(effective_percent, 1),
    }


# ============================================================
# TITLE EXTRACTION
# ============================================================

def extract_title_from_block(block):
    """
    NOVA EXTRAÇÃO DE TÍTULO.

    Em vez de pegar texto aleatório próximo do cupom,
    procuramos o trecho que vem ANTES do preço original.

    Exemplo:

    Tênis Kappa Pulse Supra
    R$ 259,99
    R$ 128,00
    50% OFF
    Cupom R$ 15 OFF

    O título será:
    Tênis Kappa Pulse Supra
    """

    if not block:
        return ""

    block = normalize_public_text(block)

    # Remove textos conhecidos que claramente não pertencem ao título
    block = re.sub(
        r"\bCupom\b.*$",
        "",
        block,
        flags=re.I
    )

    # Procuramos o preço original e pegamos o texto imediatamente antes
    money_pattern = re.compile(
        r"R\$\s*[0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|"
        r"R\$\s*[0-9]+,[0-9]{2}|"
        r"R\$\s*[0-9]+(?:\.[0-9]{2})?",
        re.I
    )

    prices = list(money_pattern.finditer(block))

    if prices:
        # Normalmente o primeiro preço é o original
        first_price = prices[0]

        before = block[:first_price.start()]

        # Remove desconto do produto anterior
        before = re.sub(
            r"\d+(?:[.,]\d+)?\s*%\s*OFF.*$",
            "",
            before,
            flags=re.I
        )

        # Remove lixo de navegação
        before = re.sub(
            r"\b(?:oferta|promoção|promocao|destaque)\b",
            "",
            before,
            flags=re.I
        )

        title = clean_product_title(before)

        if title:
            return title

    # Fallback:
    # procura uma sequência que tenha aparência de nome
    lines = re.split(r"[|•\n]", block)

    for line in lines:
        line = clean_product_title(line)

        if (
            line
            and len(line) >= 8
            and not re.search(r"R\$", line)
            and not re.search(r"%\s*OFF", line, re.I)
            and "cupom" not in line.lower()
        ):
            return line

    return ""


# ============================================================
# PUBLIC COUPON PARSER
# ============================================================

def parse_public_offers(html):
    """
    Converte a página pública de cupons em oportunidades.

    Importante:
    cada ocorrência de Cupom é tratada como pertencente a
    UM bloco de produto.

    Não misturamos o cupom de um produto com o preço/título
    do produto seguinte.
    """

    if not html:
        return []

    text = normalize_public_text(html)

    # Limpeza básica de HTML
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)

    text = normalize_public_text(text)

    # Localiza APENAS cupons verdadeiros
    coupon_pattern = re.compile(
        r"(?:"
        r"\bCupom\s+R\$\s*[\d\.,]+\s*OFF\b"
        r"|"
        r"\bR\$\s*[\d\.,]+\s*OFF\s+com\s+Cupom\b"
        r"|"
        r"\bCupom\s+\d+(?:[.,]\d+)?\s*%\s*OFF\b"
        r"|"
        r"\b\d+(?:[.,]\d+)?\s*%\s*OFF\s+com\s+Cupom\b"
        r")",
        re.I
    )

    coupon_matches = list(coupon_pattern.finditer(text))

    offers = []

    for index, coupon_match in enumerate(coupon_matches):

        coupon_text = coupon_match.group(0)
        coupon = detect_coupon(coupon_text)

        if not coupon:
            continue

        # ----------------------------------------------------
        # BLOCO DO PRODUTO
        # ----------------------------------------------------

        # Não vamos voltar demais.
        # O produto geralmente está dentro de algumas centenas
        # de caracteres antes do cupom.
        start = max(0, coupon_match.start() - 900)

        # Limite pelo cupom anterior
        if index > 0:
            previous_coupon_end = coupon_matches[index - 1].end()

            start = max(start, previous_coupon_end)

        end = coupon_match.end()

        # Inclui uma pequena parte depois do cupom, mas NÃO
        # permite invadir o próximo produto.
        if index + 1 < len(coupon_matches):
            next_start = coupon_matches[index + 1].start()

            # máximo 250 caracteres após o cupom
            end = min(next_start, coupon_match.end() + 250)
        else:
            end = min(len(text), coupon_match.end() + 250)

        block = text[start:end]

        # ----------------------------------------------------
        # PREÇO
        # ----------------------------------------------------

        current_price = find_current_price(block)

        if current_price is None:
            continue

        # Regra mínima
        if current_price < MIN_PRICE:
            continue

        # ----------------------------------------------------
        # ORIGINAL
        # ----------------------------------------------------

        original_price = find_original_price(
            block,
            current_price
        )

        # ----------------------------------------------------
        # TÍTULO
        # ----------------------------------------------------

        title = extract_title_from_block(block)

        # Se o título ficou ruim, tenta uma região mais
        # específica antes do primeiro preço.
        if not title:

            money_positions = list(
                re.finditer(
                    r"R\$\s*[\d\.,]+",
                    block,
                    flags=re.I
                )
            )

            if money_positions:
                title_region = block[:money_positions[0].start()]
                title = clean_product_title(title_region)

        # Ainda não achou?
        if not title:
            title = "Produto com cupom"

        # ----------------------------------------------------
        # CALCULA CUPOM
        # ----------------------------------------------------

        calculation = calculate_coupon(
            current_price,
            coupon
        )

        if not calculation:
            continue

        # ----------------------------------------------------
        # DESCONTO DIRETO DO VENDEDOR
        # ----------------------------------------------------

        seller_percent = None

        seller_match = re.search(
            r"R\$\s*[\d\.,]+\s+(\d+(?:[.,]\d+)?)\s*%\s*OFF",
            block,
            flags=re.I
        )

        if seller_match:
            try:
                seller_percent = float(
                    seller_match.group(1).replace(",", ".")
                )
            except Exception:
                seller_percent = None

        # ----------------------------------------------------
        # RESULTADO
        # ----------------------------------------------------

        offer = {
            "title": title,
            "original_price": original_price,
            "price": calculation["price"],
            "coupon": coupon["label"],
            "coupon_type": coupon["type"],
            "coupon_value": coupon["value"],
            "discount": calculation["discount"],
            "final": calculation["final"],
            "effective_percent": calculation["effective_percent"],
            "seller_discount_percent": seller_percent,
            "source": "Mercado Livre - Cupons",
            "coupon_confirmed": True,
        }

        offers.append(offer)

    return offers


# ============================================================
# DEDUPLICATION
# ============================================================

def normalize_title_key(title):
    if not title:
        return ""

    title = title.lower()

    title = re.sub(
        r"[^a-z0-9áàâãéêíóôõúçü\s]",
        " ",
        title
    )

    title = re.sub(r"\s+", " ", title)

    return title.strip()


def dedupe_offers(offers):
    """
    Uma oportunidade por produto.

    Se o mesmo produto aparecer mais de uma vez,
    fica a combinação com maior economia do cupom.
    """

    grouped = {}

    for offer in offers:

        key = normalize_title_key(
            offer.get("title", "")
        )

        if not key:
            continue

        if key not in grouped:
            grouped[key] = offer
            continue

        current = grouped[key]

        current_discount = float(
            current.get("discount", 0)
        )

        new_discount = float(
            offer.get("discount", 0)
        )

        if new_discount > current_discount:
            grouped[key] = offer

        elif (
            new_discount == current_discount
            and float(offer.get("final", 999999))
            < float(current.get("final", 999999))
        ):
            grouped[key] = offer

    return list(grouped.values())


# ============================================================
# RANKING
# ============================================================

def rank_offers(offers):
    return sorted(
        offers,
        key=lambda x: (
            -float(x.get("discount", 0)),
            -float(x.get("effective_percent", 0)),
            float(x.get("final", 999999)),
        )
    )


# ============================================================
# PUBLIC PAGE DOWNLOAD
# ============================================================

def fetch_public_coupon_page():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }

    try:
        response = requests.get(
            PUBLIC_COUPON_URL,
            headers=headers,
            timeout=25,
            allow_redirects=True
        )

        return {
            "status": response.status_code,
            "html": response.text,
            "url": response.url,
            "bytes": len(response.content),
        }

    except Exception as e:
        return {
            "status": 0,
            "html": "",
            "url": "",
            "bytes": 0,
            "error": str(e),
        }


# ============================================================
# MERCADO LIVRE OAUTH
# ============================================================

def get_oauth():
    conn = db()

    row = conn.execute(
        "SELECT * FROM oauth WHERE id = 1"
    ).fetchone()

    conn.close()

    return dict(row) if row else None


def save_oauth(
    access_token,
    refresh_token,
    expires_at,
    user_id=None,
    nickname=None
):
    conn = db()

    conn.execute("""
        INSERT INTO oauth (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname,
            updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            expires_at = excluded.expires_at,
            user_id = excluded.user_id,
            nickname = excluded.nickname,
            updated_at = excluded.updated_at
    """, (
        access_token,
        refresh_token,
        expires_at,
        user_id,
        nickname,
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()


def refresh_ml_token():
    data = get_oauth()

    if not data:
        return None

    refresh_token = data.get("refresh_token")

    if not refresh_token:
        return None

    payload = {
        "grant_type": "refresh_token",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }

    try:
        response = requests.post(
            ML_TOKEN_URL,
            data=payload,
            timeout=20
        )

        if response.status_code != 200:
            return None

        result = response.json()

        access_token = result.get("access_token")

        if not access_token:
            return None

        new_refresh = result.get(
            "refresh_token",
            refresh_token
        )

        expires_in = int(
            result.get("expires_in", 21600)
        )

        save_oauth(
            access_token,
            new_refresh,
            now_ts() + expires_in,
            data.get("user_id"),
            data.get("nickname")
        )

        return access_token

    except Exception:
        return None


def get_ml_token():
    data = get_oauth()

    if not data:
        return None

    token = data.get("access_token")
    expires_at = int(data.get("expires_at") or 0)

    if token and expires_at > now_ts() + 120:
        return token

    return refresh_ml_token()


def ml_get(endpoint, params=None):
    token = get_ml_token()

    if not token:
        return None, 401, {
            "error": "Mercado Livre não conectado"
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        response = requests.get(
            ML_API + endpoint,
            headers=headers,
            params=params,
            timeout=20
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "raw": response.text
            }

        return data, response.status_code, response.headers

    except Exception as e:
        return {
            "error": str(e)
        }, 500, {}


# ============================================================
# PKCE
# ============================================================

def make_pkce():
    verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(48)
    ).decode().rstrip("=")

    digest = hashlib.sha256(
        verifier.encode()
    ).digest()

    challenge = base64.urlsafe_b64encode(
        digest
    ).decode().rstrip("=")

    return verifier, challenge


# ============================================================
# HOME
# ============================================================

HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f4f5f7;
    font-family: Arial, Helvetica, sans-serif;
    color: #222;
}

.container {
    max-width: 1100px;
    margin: auto;
    padding: 20px;
}

.header {
    background: white;
    border-radius: 22px;
    padding: 25px;
    margin-bottom: 20px;
    box-shadow: 0 4px 18px rgba(0,0,0,.06);
}

h1 {
    margin: 0 0 8px;
    font-size: 30px;
}

.subtitle {
    color: #777;
    font-size: 16px;
}

.buttons {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 20px;
}

button,
.btn {
    border: 0;
    border-radius: 14px;
    padding: 15px 20px;
    font-weight: bold;
    font-size: 16px;
    cursor: pointer;
    text-decoration: none;
    display: inline-block;
}

.primary {
    background: #3483fa;
    color: white;
}

.secondary {
    background: #eee;
    color: #333;
}

.stats {
    display: grid;
    grid-template-columns: repeat(4,1fr);
    gap: 15px;
    margin-bottom: 20px;
}

.stat {
    background: white;
    border-radius: 20px;
    padding: 20px;
    box-shadow: 0 4px 18px rgba(0,0,0,.05);
}

.stat small {
    color: #777;
    font-size: 15px;
}

.stat strong {
    display: block;
    font-size: 28px;
    margin-top: 8px;
}

.card {
    background: white;
    border-radius: 22px;
    padding: 25px;
    margin-bottom: 20px;
    box-shadow: 0 4px 18px rgba(0,0,0,.06);
}

.badge {
    display: inline-block;
    background: #fff3c4;
    color: #765d00;
    border-radius: 12px;
    padding: 10px 14px;
    font-weight: bold;
    margin-bottom: 18px;
}

.title {
    font-size: 24px;
    font-weight: bold;
    margin-bottom: 18px;
}

.old {
    color: #999;
    text-decoration: line-through;
    font-size: 18px;
}

.label {
    color: #888;
    margin-top: 10px;
}

.price {
    font-size: 36px;
    font-weight: bold;
    margin: 5px 0 20px;
}

.coupon {
    border: 2px solid #f1d23a;
    background: #fffbdc;
    border-radius: 18px;
    padding: 18px;
    font-size: 22px;
    font-weight: bold;
}

.final {
    color: #31843f;
    font-size: 34px;
    font-weight: bold;
    margin-top: 22px;
}

.saving {
    color: #31843f;
    font-size: 22px;
    font-weight: bold;
    margin-top: 12px;
}

.warning {
    color: #777;
    margin-top: 20px;
    line-height: 1.45;
}

.offer {
    display: block;
    text-align: center;
    background: #3483fa;
    color: white;
    padding: 18px;
    border-radius: 18px;
    text-decoration: none;
    font-size: 20px;
    font-weight: bold;
    margin-top: 25px;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
}

@media(max-width:700px) {

    .stats {
        grid-template-columns: repeat(2,1fr);
    }

    .title {
        font-size: 21px;
    }

    .price {
        font-size: 32px;
    }

    .final {
        font-size: 30px;
    }

}

</style>
</head>

<body>

<div class="container">

<div class="header">

<h1>🕵️ Caçador de Ofertas</h1>

<div class="subtitle">
Produtos com cupom real identificado no Mercado Livre.
</div>

<div class="buttons">

<button class="primary"
        onclick="caçar()">
🔎 Caçar ofertas
</button>

<a class="btn secondary"
   href="/mercadolivre/login">
🔗 Conectar Mercado Livre
</a>

<a class="btn secondary"
   href="/api/debug-public"
   target="_blank">
🧪 Debug
</a>

</div>

</div>

<div id="resultado"></div>

</div>

<script>

async function caçar() {

    const resultado =
        document.getElementById("resultado");

    resultado.innerHTML = `
        <div class="card">
            🔎 Procurando cupons e ofertas...
        </div>
    `;

    try {

        const response =
            await fetch("/api/hunt");

        const data =
            await response.json();

        if (!data.ok) {

            resultado.innerHTML = `
                <div class="card">
                    ❌ ${data.error || "Erro"}
                </div>
            `;

            return;
        }

        let html = "";

        html += `
            <div class="stats">

                <div class="stat">
                    <small>Ofertas</small>
                    <strong>${data.count}</strong>
                </div>

                <div class="stat">
                    <small>Cupons</small>
                    <strong>${data.coupon_count}</strong>
                </div>

                <div class="stat">
                    <small>Desconto</small>
                    <strong>${data.total_discount}</strong>
                </div>

                <div class="stat">
                    <small>Valor final</small>
                    <strong>${data.total_final}</strong>
                </div>

            </div>
        `;

        html += `
            <div class="card">
                ✅ Caçada concluída: ${data.count}
                produto(s) único(s) com cupom.
            </div>
        `;

        for (const offer of data.offers) {

            html += `
                <div class="card">

                    <div class="badge">
                        🎟️ CUPOM CONFIRMADO
                    </div>

                    <div class="title">
                        ${escapeHtml(offer.title)}
                    </div>

                    ${
                        offer.original_price
                        ?
                        `<div class="old">
                            ${offer.original_price}
                        </div>`
                        :
                        ""
                    }

                    <div class="label">
                        Preço atual antes do cupom:
                    </div>

                    <div class="price">
                        ${offer.price}
                    </div>

                    <div class="coupon">
                        ${escapeHtml(offer.coupon)}
                    </div>

                    <div class="final">
                        Final estimado: ${offer.final}
                    </div>

                    <div class="saving">
                        Economia do cupom:
                        ${offer.discount}
                        (${offer.effective_percent}%)
                    </div>

                    <div class="warning">
                        ⚠️ O cálculo considera somente o cupom
                        identificado. Descontos diretos do vendedor
                        não foram somados. Confirme a aplicação
                        no checkout.
                    </div>

                    <a class="offer"
                       href="${offer.link || '#'}"
                       target="_blank">
                        🛒 VER OFERTA
                    </a>

                </div>
            `;
        }

        resultado.innerHTML = html;

    } catch (e) {

        resultado.innerHTML = `
            <div class="card">
                ❌ Erro ao consultar:
                ${escapeHtml(e.message)}
            </div>
        `;
    }
}


function escapeHtml(value) {

    if (value === null ||
        value === undefined) {
        return "";
    }

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

</script>

</body>
</html>
"""


# ============================================================
# HOME ROUTE
# ============================================================

@app.route("/")
def home():
    return render_template_string(HTML)


# ============================================================
# MERCADO LIVRE LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:
        return "ML_CLIENT_ID não configurado", 500

    verifier, challenge = make_pkce()

    state = secrets.token_urlsafe(32)

    session["oauth_state"] = state
    session["pkce_verifier"] = verifier

    params = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    url = ML_AUTH_URL + "?" + urllib.parse.urlencode(params)

    return redirect(url)


# ============================================================
# CALLBACK
# ============================================================

@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    error = request.args.get("error")

    if error:
        return jsonify({
            "ok": False,
            "error": error,
            "description": request.args.get("error_description")
        }), 400

    code = request.args.get("code")
    state = request.args.get("state")

    if not code:
        return "Código OAuth não recebido.", 400

    if state != session.get("oauth_state"):
        return "State OAuth inválido.", 400

    verifier = session.get("pkce_verifier")

    if not verifier:
        return "PKCE verifier não encontrado.", 400

    payload = {
        "grant_type": "authorization_code",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "code": code,
        "redirect_uri": ML_REDIRECT_URI,
        "code_verifier": verifier,
    }

    try:

        response = requests.post(
            ML_TOKEN_URL,
            data=payload,
            timeout=20
        )

        result = response.json()

        if response.status_code != 200:

            return jsonify({
                "ok": False,
                "status": response.status_code,
                "response": result
            }), response.status_code

        access_token = result.get("access_token")
        refresh_token = result.get("refresh_token")
        expires_in = int(
            result.get("expires_in", 21600)
        )

        if not access_token:
            return jsonify(result), 400

        user_id = None
        nickname = None

        try:

            me = requests.get(
                ML_API + "/users/me",
                headers={
                    "Authorization":
                    f"Bearer {access_token}"
                },
                timeout=20
            )

            if me.status_code == 200:

                me_data = me.json()

                user_id = str(
                    me_data.get("id")
                )

                nickname = me_data.get(
                    "nickname"
                )

        except Exception:
            pass

        save_oauth(
            access_token,
            refresh_token,
            now_ts() + expires_in,
            user_id,
            nickname
        )

        session.pop("oauth_state", None)
        session.pop("pkce_verifier", None)

        return redirect("/")

    except Exception as e:

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# ============================================================
# API STATUS
# ============================================================

@app.route("/api/status")
def api_status():

    data = get_oauth()

    if not data:

        return jsonify({
            "connected": False
        })

    return jsonify({
        "connected": bool(
            data.get("access_token")
        ),
        "user_id": data.get("user_id"),
        "nickname": data.get("nickname"),
        "expires_at": data.get("expires_at"),
    })


# ============================================================
# PRODUCT SEARCH
# ============================================================

def resolve_product_link(title):
    """
    Tenta encontrar o produto no catálogo.

    Se não conseguir, usa busca normal do Mercado Livre.
    """

    query = title.strip()

    if not query:
        return "https://lista.mercadolivre.com.br/"

    try:

        data, status, _ = ml_get(
            "/products/search",
            {
                "site_id": "MLB",
                "q": query,
                "limit": 5,
            }
        )

        if status == 200 and isinstance(data, dict):

            results = data.get(
                "results",
                []
            )

            if results:

                product_id = results[0].get(
                    "id"
                )

                if product_id:

                    return (
                        "https://www.mercadolivre.com.br/"
                        f"p/{product_id}"
                    )

    except Exception:
        pass

    return (
        "https://lista.mercadolivre.com.br/"
        + urllib.parse.quote(query)
    )


# ============================================================
# HUNT
# ============================================================

@app.route("/api/hunt")
def api_hunt():

    page = fetch_public_coupon_page()

    if page.get("status") != 200:

        return jsonify({
            "ok": False,
            "error": (
                "Não foi possível consultar a página "
                "pública de cupons."
            ),
            "status": page.get("status"),
        }), 502

    html = page.get("html", "")

    offers = parse_public_offers(html)

    offers = dedupe_offers(offers)

    offers = rank_offers(offers)

    # Limite para não sobrecarregar a API
    offers = offers[:30]

    total_discount = sum(
        float(o.get("discount", 0))
        for o in offers
    )

    total_final = sum(
        float(o.get("final", 0))
        for o in offers
    )

    # Links
    for offer in offers:

        offer["link"] = resolve_product_link(
            offer.get("title", "")
        )

        offer["price"] = money_br(
            offer.get("price")
        )

        if offer.get("original_price") is not None:
            offer["original_price"] = money_br(
                offer["original_price"]
            )

        offer["discount"] = money_br(
            offer.get("discount")
        )

        offer["final"] = money_br(
            offer.get("final")
        )

    return jsonify({
        "ok": True,
        "count": len(offers),
        "coupon_count": len(offers),
        "total_discount": money_br(
            total_discount
        ),
        "total_final": money_br(
            total_final
        ),
        "offers": offers,
        "source": page.get("url"),
    })


# ============================================================
# DEBUG PUBLIC
# ============================================================

@app.route("/api/debug-public")
def debug_public():

    page = fetch_public_coupon_page()

    html = page.get("html", "")

    offers = parse_public_offers(html)

    raw_coupon_regex = re.compile(
        r"(?:"
        r"\bCupom\s+R\$\s*[\d\.,]+\s*OFF\b"
        r"|"
        r"\bR\$\s*[\d\.,]+\s*OFF\s+com\s+Cupom\b"
        r"|"
        r"\bCupom\s+\d+(?:[.,]\d+)?\s*%\s*OFF\b"
        r"|"
        r"\b\d+(?:[.,]\d+)?\s*%\s*OFF\s+com\s+Cupom\b"
        r")",
        re.I
    )

    coupon_occurrences = len(
        raw_coupon_regex.findall(html)
    )

    samples = []

    for offer in offers[:10]:

        samples.append({
            "title": offer.get("title"),
            "original": offer.get(
                "original_price"
            ),
            "price": offer.get("price"),
            "coupon": offer.get("coupon"),
            "discount": offer.get("discount"),
            "final": offer.get("final"),
            "effective_percent": offer.get(
                "effective_percent"
            ),
        })

    return jsonify({
        "ok": True,
        "status": page.get("status"),
        "bytes": page.get("bytes"),
        "final_url": page.get("url"),
        "coupon_count": coupon_occurrences,
        "produto_count": len(offers),
        "offers_detected": len(offers),
        "sample_offers": samples,
    })


# ============================================================
# ML DIAGNOSTIC
# ============================================================

@app.route("/api/ml-diagnostic")
def ml_diagnostic():

    token = get_ml_token()

    result = {
        "token": bool(token),
        "client_id": bool(ML_CLIENT_ID),
        "redirect_uri": ML_REDIRECT_URI,
    }

    if not token:
        result["error"] = (
            "Mercado Livre não conectado."
        )
        return jsonify(result)

    # /users/me
    me, me_status, _ = ml_get(
        "/users/me"
    )

    result["users_me"] = {
        "status": me_status,
        "response": me,
    }

    # Product search
    products, product_status, _ = ml_get(
        "/products/search",
        {
            "site_id": "MLB",
            "q": "celular",
            "limit": 1,
        }
    )

    result["products_search"] = {
        "status": product_status,
        "response": products,
    }

    return jsonify(result)


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "app": "cacador-de-ofertas",
        "time": datetime.utcnow().isoformat()
    })


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False
    )