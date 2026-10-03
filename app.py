import os, sqlite3, secrets, hashlib, base64, time, re, html as html_lib, threading, uuid
from urllib.parse import urlencode
import requests
from flask import Flask, request, redirect, session, jsonify, render_template_string

app=Flask(__name__)
app.secret_key=os.getenv('FLASK_SECRET_KEY','chave-cacador-ofertas')
ML_CLIENT_ID=os.getenv('ML_CLIENT_ID','').strip()
ML_CLIENT_SECRET=os.getenv('ML_CLIENT_SECRET','').strip()
ML_REDIRECT_URI=os.getenv('ML_REDIRECT_URI','https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback').strip()
ML_API='https://api.mercadolibre.com'; ML_AUTH='https://auth.mercadolivre.com.br/authorization'; ML_TOKEN=ML_API+'/oauth/token'; SITE_ID='MLB'
DB_FILE='ofertas.db'; MIN_PRODUCT_PRICE=69.90
COUPON_SOURCE_URLS=['https://www.mercadolivre.com.br/l/promocoes','https://www.mercadolivre.com.br/l/descontaco-cupons','https://www.mercadolivre.com.br/ofertas/cupons']

CATALOG={
'📱 Celulares':['smartphone','iphone','samsung galaxy','motorola moto','xiaomi redmi','poco smartphone','realme smartphone'],
'🌸 Perfumes':['perfume masculino','perfume feminino','perfume importado','perfume nacional','perfume eau de parfum'],
'🏋️ Academia':['roupa academia masculina','roupa academia feminina','camiseta academia','short academia','legging academia','tenis academia','tenis corrida','tenis treino','whey protein','creatina','pre treino','suplementos'],
'🔧 Ferramentas':['furadeira','parafusadeira','esmerilhadeira','kit ferramentas','maleta ferramentas','serra','chave de impacto'],
'🎧 Eletrônicos':['fone bluetooth','headset','smartwatch','tablet','caixa de som bluetooth','camera digital','power bank'],
'🏠 Casa':['aspirador de pó','liquidificador','cafeteira','air fryer','ventilador','ferro de passar'],
'🍳 Cozinha':['air fryer','panela elétrica','jogo de panelas','cafeteira','liquidificador','sandwichera'],
'🚗 Automotivo':['compressor automotivo','aspirador automotivo','suporte celular carro','carregador automotivo','ferramentas automotivas','tapete automotivo'],
'👕 Moda':['tenis masculino','tenis feminino','mochila','relogio masculino','bolsa feminina','oculos de sol']}

# ---------------- DB ----------------
def db():
 c=sqlite3.connect(DB_FILE); c.row_factory=sqlite3.Row; return c

def init_db():
 c=db()
 c.execute('''CREATE TABLE IF NOT EXISTS oauth_tokens(
 id INTEGER PRIMARY KEY CHECK(id=1),
 access_token TEXT,
 refresh_token TEXT,
 expires_at INTEGER,
 user_id TEXT,
 nickname TEXT)''')

 c.execute('''CREATE TABLE IF NOT EXISTS cupons(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 code TEXT UNIQUE,
 description TEXT,
 discount_percent REAL,
 fixed_discount REAL DEFAULT 0,
 min_purchase REAL,
 max_discount REAL,
 source_url TEXT,
 conditions TEXT,
 active INTEGER DEFAULT 1,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

 c.commit()
 c.close()

init_db()

# ---------------- UTILS ----------------
def brl(v):
 try:
  return f'R$ {float(v):,.2f}'.replace(',','X').replace('.',',').replace('X','.')
 except:
  return 'R$ 0,00'

def norm(s):
 if not s:
  return ''

 s=str(s).lower().translate(
  str.maketrans(
   'áàãâäéèêëíìîïóòõôöúùûüç',
   'aaaaaeeeeiiiiooooouuuuc'
  )
 )

 return re.sub(
  r'\s+',
  ' ',
  re.sub(
   r'[^a-z0-9\s]+',
   ' ',
   s
  )
 ).strip()

def json_safe(x):
 if isinstance(x,dict):
  return {
   str(k):json_safe(v)
   for k,v in x.items()
  }

 if isinstance(x,list):
  return [json_safe(v) for v in x]

 if isinstance(x,(str,int,float,bool)) or x is None:
  return x

 return str(x)

def pct_discount(price,original):
 try:
  return round(
   (1-float(price)/float(original))*100,
   2
  ) if float(original)>float(price)>0 else 0
 except:
  return 0

def total(price,shipping):
 try:
  return round(
   float(price)+float(shipping or 0),
   2
  )
 except:
  return float(price or 0)

def model_name(title):
 t=re.sub(
  r'\b(novo|original|oficial|promoção|frete grátis)\b',
  '',
  str(title or 'Produto'),
  flags=re.I
 )
 return re.sub(r'\s+',' ',t).strip()

def specs(title):
 t=str(title or '')
 out=[]

 for p in [
  r'\b\d+(?:GB|TB)\b',
  r'\b(?:2G|3G|4G|5G)\b'
 ]:
  for x in re.findall(p,t,re.I):
   x=x.upper()

   if x not in out:
    out.append(x)

 for x,label in [
  (r'dual\s*sim','Dual SIM'),
  (r'\bnfc\b','NFC')
 ]:
  if re.search(x,t,re.I) and label not in out:
   out.append(label)

 return out

# ---------------- OAUTH ----------------
def pkce():
 v=secrets.token_urlsafe(64)

 ch=base64.urlsafe_b64encode(
  hashlib.sha256(v.encode()).digest()
 ).decode().rstrip('=')

 return v,ch

def tokens():
 c=db()

 r=c.execute(
  'SELECT * FROM oauth_tokens WHERE id=1'
 ).fetchone()

 c.close()

 return dict(r) if r else None

def save_tokens(data,user=None):
 old=tokens() or {}

 c=db()

 c.execute('''
 INSERT INTO oauth_tokens(
 id,
 access_token,
 refresh_token,
 expires_at,
 user_id,
 nickname
 )
 VALUES(1,?,?,?,?,?)

 ON CONFLICT(id) DO UPDATE SET
 access_token=excluded.access_token,
 refresh_token=COALESCE(
  excluded.refresh_token,
  oauth_tokens.refresh_token
 ),
 expires_at=excluded.expires_at,
 user_id=COALESCE(
  excluded.user_id,
  oauth_tokens.user_id
 ),
 nickname=COALESCE(
  excluded.nickname,
  oauth_tokens.nickname
 )
 ''',(
  data.get('access_token'),
  data.get('refresh_token'),
  int(time.time())+
  int(data.get('expires_in',21600)),
  str(user.get('id'))
  if user and user.get('id')
  else old.get('user_id'),
  user.get('nickname')
  if user
  else old.get('nickname')
 ))

 c.commit()
 c.close()

def refresh_token():
 t=tokens()

 if not t or not t.get('refresh_token'):
  return None

 try:
  r=requests.post(
   ML_TOKEN,
   data={
    'grant_type':'refresh_token',
    'client_id':ML_CLIENT_ID,
    'client_secret':ML_CLIENT_SECRET,
    'refresh_token':t['refresh_token']
   },
   timeout=30
  )

  if r.status_code!=200:
   return None

  d=r.json()

  save_tokens(
   d,
   {
    'id':t.get('user_id'),
    'nickname':t.get('nickname')
   }
  )

  return d.get('access_token')

 except:
  return None

def access_token():
 t=tokens()

 if not t:
  return None

 if (
  t.get('access_token')
  and time.time() <
  (t.get('expires_at') or 0)-120
 ):
  return t['access_token']

 return refresh_token() or t.get('access_token')

def ml_get(path,params=None,token=None):
 token=token or access_token()

 if not token:
  return {},401,{}

 try:
  r=requests.get(
   path
   if path.startswith('http')
   else ML_API+path,
   headers={
    'Authorization':'Bearer '+token,
    'Accept':'application/json'
   },
   params=params,
   timeout=30
  )

  try:
   d=r.json()
  except:
   d={'message':r.text}

  return d,r.status_code,dict(r.headers)

 except requests.RequestException as e:
  return {'error':str(e)},500,{}

# ---------------- LOGIN ----------------
@app.route('/mercadolivre/login')
def login():
 if not ML_CLIENT_ID:
  return jsonify({
   'erro':'ML_CLIENT_ID não configurado.'
  }),500

 v,ch=pkce()

 state=secrets.token_urlsafe(32)

 session['ml_state']=state
 session['ml_code_verifier']=v

 q=urlencode({
  'response_type':'code',
  'client_id':ML_CLIENT_ID,
  'redirect_uri':ML_REDIRECT_URI,
  'state':state,
  'code_challenge':ch,
  'code_challenge_method':'S256'
 })

 return redirect(
  ML_AUTH+'?'+q
 )

@app.route('/mercadolivre/callback')
def callback():

 if request.args.get('error'):
  return jsonify({
   'erro':request.args.get('error'),
   'descricao':
    request.args.get(
     'error_description'
    )
  }),400

 code=request.args.get('code')
 state=request.args.get('state')

 if (
  not code
  or state!=session.get('ml_state')
 ):
  return jsonify({
   'erro':'Código ou state inválido.'
  }),400

 try:

  r=requests.post(
   ML_TOKEN,
   data={
    'grant_type':'authorization_code',
    'client_id':ML_CLIENT_ID,
    'client_secret':ML_CLIENT_SECRET,
    'code':code,
    'redirect_uri':ML_REDIRECT_URI,
    'code_verifier':
     session.get(
      'ml_code_verifier'
     )
   },
   timeout=30
  )

  if r.status_code!=200:
   return jsonify({
    'erro':'Falha ao obter token',
    'status':r.status_code,
    'resposta':r.text
   }),r.status_code

  d=r.json()
  user=None

  if d.get('access_token'):

   me=requests.get(
    ML_API+'/users/me',
    headers={
     'Authorization':
      'Bearer '+d['access_token']
    },
    timeout=30
   )

   if me.status_code==200:
    user=me.json()

  save_tokens(d,user)

  session.pop(
   'ml_state',
   None
  )

  session.pop(
   'ml_code_verifier',
   None
  )

  return redirect(
   '/?conectado=1'
  )

 except Exception as e:
  return jsonify({
   'erro':str(e)
  }),500

@app.route('/mercadolivre/logout')
def logout():

 c=db()

 c.execute(
  'DELETE FROM oauth_tokens WHERE id=1'
 )

 c.commit()
 c.close()

 session.clear()

 return redirect('/')

# ---------------- ML PRODUCTS ----------------
def discover_categories(q,token=None):

 d,s,_=ml_get(
  f'/sites/{SITE_ID}/domain_discovery/search',
  {'q':q},
  token
 )

 if (
  s!=200
  or not isinstance(d,list)
 ):
  return []

 return [
  {
   'category_id':
    x.get('category_id')
    or x.get('id'),

   'category_name':
    x.get('category_name')
    or x.get('name')
    or x.get('category_id')
    or x.get('id')
  }

  for x in d

  if (
   x.get('category_id')
   or x.get('id')
  )
 ]

def highlights(cid,token=None):

 d,s,_=ml_get(
  f'/highlights/{SITE_ID}/category/{cid}',
  token=token
 )

 if s!=200:
  return []

 if isinstance(d,list):
  return d

 if isinstance(d,dict):
  return d.get(
   'content',
   d.get(
    'results',
    []
   )
  )

 return []

def product(pid,token=None):

 d,s,_=ml_get(
  f'/products/{pid}',
  token=token
 )

 if (
  s==200
  and isinstance(d,dict)
 ):
  return d

 return None

def product_items(pid,token=None):

 d,s,_=ml_get(
  f'/products/{pid}/items',
  token=token
 )

 if s!=200:
  return []

 if isinstance(d,list):
  return d

 if isinstance(d,dict):
  return d.get(
   'results',
   []
  )

 return []

def normalize_item(x):

 if (
  not isinstance(x,dict)
  or not x.get('item_id')
 ):
  return None

 sh=x.get('shipping') or {}

 free=bool(
  sh.get('free_shipping')
 )

 cost=(
  0
  if free
  else sh.get('cost')
 )

 return {
  'item_id':
   x['item_id'],

  'seller_id':
   x.get('seller_id'),

  'price':
   x.get('price'),

  'original_price':
   x.get('original_price'),

  'condition':
   x.get('condition'),

  'listing_type_id':
   x.get('listing_type_id'),

  'free_shipping':
   free,

  'shipping_cost':
   cost,

  'permalink':
   x.get('permalink'),

  'user_product_id':
   x.get('user_product_id')
 }

# ---------------- COUPONS ----------------
def num(s):

 m=re.search(
  r'(\d+(?:[.,]\d+)?)',
  str(s or '')
 )

 return (
  float(
   m.group(1).replace(',','.')
  )
  if m
  else None
 )

def clean_coupon_html(raw):

 t=html_lib.unescape(
  raw or ''
 )

 t=re.sub(
  r'<script.*?</script>|'
  r'<style.*?</style>|'
  r'<noscript.*?</noscript>',
  ' ',
  t,
  flags=re.I|re.S
 )

 t=re.sub(
  r'</(?:div|p|li|h1|h2|h3|h4|h5|h6|section|article|br|tr)>',
  '\n',
  t,
  flags=re.I
 )

 t=re.sub(
  r'<[^>]+>',
  ' ',
  t
 )

 return re.sub(
  r'\s+',
  ' ',
  t.replace('\r','\n')
 ).strip()

def valid_code(c):

 return bool(
  re.fullmatch(
   r'[A-Z0-9][A-Z0-9_-]{5,29}',
   c or '',
   re.I
  )
  and re.search(
   r'[A-Z]',
   c or '',
   re.I
  )
  and re.search(
   r'\d',
   c or ''
  )
 )

def coupon_blocks(text):

 pattern=re.compile(
  r'\bCupom\s+'
  r'([A-Z0-9][A-Z0-9_-]{5,29})\b'
  r'(?=\s+Cupom\s+v[aá]lido)',
  re.I
 )

 matches=[]

 for m in pattern.finditer(text):

  code=m.group(1).upper()

  if valid_code(code):
   matches.append(
    (
     m.start(),
     m.end(),
     code
    )
   )

 blocks=[]

 for i,(_,end,code) in enumerate(
  matches
 ):

  next_start=(
   matches[i+1][0]
   if i+1<len(matches)
   else len(text)
  )

  blocks.append(
   (
    code,
    text[end:next_start][:5000]
   )
  )

 return blocks

def parse_coupon(
 code,
 block,
 source_url
):

 percent=None
 fixed=None
 minimum=None
 maximum=None

 for pattern in [
  r'(?:até\s+)?(\d+(?:[.,]\d+)?)\s*%\s*(?:off|de desconto)?',
  r'(?:desconto de|desconto)\s+'
  r'(\d+(?:[.,]\d+)?)\s*%'
 ]:

  m=re.search(
   pattern,
   block,
   re.I
  )

  if m:
   percent=num(m.group(1))
   break

 for pattern in [
  r'R\$\s*(\d+(?:[.,]\d+)?)'
  r'\s*(?:OFF|de desconto)',

  r'(?:desconto de|ganhe)\s*'
  r'R\$\s*(\d+(?:[.,]\d+)?)'
 ]:

  m=re.search(
   pattern,
   block,
   re.I
  )

  if m:
   fixed=num(m.group(1))
   break

 for pattern in [
  r'(?:a partir de|partir de|compra mínima de|'
  r'mínimo de|valor mínimo de)\s*R?\$?\s*'
  r'(\d+(?:[.,]\d+)?)',

  r'R\$\s*(\d+(?:[.,]\d+)?)'
  r'\s*(?:ou mais|em compras)'
 ]:

  m=re.search(
   pattern,
   block,
   re.I
  )

  if m:
   minimum=num(m.group(1))
   break

 for pattern in [
  r'(?:desconto\s+)?'
  r'(?:máximo de|maximo de|limitado a)'
  r'\s*R?\$?\s*(\d+(?:[.,]\d+)?)',

  r'(?:máximo|limite).*?'
  r'R\$\s*(\d+(?:[.,]\d+)?)'
 ]:

  m=re.search(
   pattern,
   block,
   re.I
  )

  if m:
   maximum=num(m.group(1))
   break

 return {
  'code':code,
  'description':block[:2500],
  'discount_percent':percent,
  'fixed_discount':fixed or 0,
  'min_purchase':minimum,
  'max_discount':maximum,
  'source_url':source_url,
  'conditions':block[:2500]
 }

def sync_coupons():

 headers={
  'User-Agent':
   'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
   'AppleWebKit/537.36 Chrome/140 Safari/537.36',

  'Accept-Language':
   'pt-BR,pt;q=0.9,en;q=0.8'
 }

 found={}
 errors=[]

 for source_url in COUPON_SOURCE_URLS:

  try:

   r=requests.get(
    source_url,
    headers=headers,
    timeout=30
   )

   if r.status_code!=200:

    errors.append(
     f'{source_url}: HTTP {r.status_code}'
    )

    continue

   text=clean_coupon_html(
    r.text
   )

   for code,block in coupon_blocks(text):

    coupon=parse_coupon(
     code,
     block,
     source_url
    )

    if (
     coupon['discount_percent']
     or coupon['fixed_discount']
    ):

     old=found.get(code)

     if (
      not old
      or len(coupon['conditions'])
      > len(old['conditions'])
     ):
      found[code]=coupon

  except Exception as e:

   errors.append(
    f'{source_url}: {e}'
   )

 if not found:

  return {
   'ok':False,
   'cupons_encontrados':0,
   'erros':errors
  }

 c=db()

 c.execute(
  'UPDATE cupons SET active=0'
 )

 for coupon in found.values():

  c.execute('''
  INSERT INTO cupons(
   code,
   description,
   discount_percent,
   fixed_discount,
   min_purchase,
   max_discount,
   source_url,
   conditions,
   active,
   updated_at
  )
  VALUES(
   ?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP
  )

  ON CONFLICT(code) DO UPDATE SET
   description=excluded.description,
   discount_percent=excluded.discount_percent,
   fixed_discount=excluded.fixed_discount,
   min_purchase=excluded.min_purchase,
   max_discount=excluded.max_discount,
   source_url=excluded.source_url,
   conditions=excluded.conditions,
   active=1,
   updated_at=CURRENT_TIMESTAMP
  ''',(
   coupon['code'],
   coupon['description'],
   coupon['discount_percent'],
   coupon['fixed_discount'],
   coupon['min_purchase'],
   coupon['max_discount'],
   coupon['source_url'],
   coupon['conditions']
  ))

 c.commit()
 c.close()

 print(
  '[CUPONS]',
  len(found)
 )

 return {
  'ok':True,
  'cupons_encontrados':len(found),
  'erros':errors
 }

def coupons():

 c=db()

 rows=c.execute('''
 SELECT *
 FROM cupons
 WHERE active=1
 ORDER BY
  updated_at DESC,
  discount_percent DESC,
  max_discount DESC
 ''').fetchall()

 c.close()

 return [
  dict(row)
  for row in rows
 ]

def coupon_discount(coupon,price):

 try:

  price=float(price)

  minimum=coupon.get(
   'min_purchase'
  )

  if (
   minimum
   and price<float(minimum)
  ):
   return 0

  discounts=[]

  percent=float(
   coupon.get(
    'discount_percent'
   ) or 0
  )

  fixed=float(
   coupon.get(
    'fixed_discount'
   ) or 0
  )

  maximum=coupon.get(
   'max_discount'
  )

  if percent>0:

   value=price*percent/100

   if maximum:
    value=min(
     value,
     float(maximum)
    )

   discounts.append(
    value
   )

  if fixed>0:

   value=fixed

   if maximum:
    value=min(
     value,
     float(maximum)
    )

   discounts.append(
    value
   )

  return round(
   max(discounts or [0]),
   2
  )

 except:
  return 0

def best_coupon(price):

 options=[]

 for coupon in coupons():

  saving=coupon_discount(
   coupon,
   price
  )

  if saving<=0:
   continue

  item=dict(coupon)

  item['desconto_estimado']=saving

  item['percentual_efetivo']=round(
   saving/float(price)*100,
   2
  )

  options.append(item)

 return max(
  options,
  key=lambda x:(
   x['desconto_estimado'],
   x['percentual_efetivo'],
   -float(
    x.get('min_purchase') or 0
   )
  ),
  default=None
 )

# ---------------- PIX ----------------
def explicit_cash_discount(raw,price):

 if not isinstance(raw,dict):
  return 0,None

 price=float(price or 0)

 values=[]

 for key in [
  'pix_discount',
  'cash_discount',
  'discount_pix',
  'payment_discount'
 ]:

  value=raw.get(key)

  if (
   isinstance(value,(int,float))
   and value>0
  ):
   values.append(
    float(value)
   )

 for key in [
  'pix_price',
  'cash_price',
  'price_pix',
  'price_cash'
 ]:

  value=raw.get(key)

  if (
   isinstance(value,(int,float))
   and 0<value<price
  ):
   values.append(
    price-float(value)
   )

 saving=round(
  max(values or [0]),
  2
 )

 return (
  saving,
  'Pix/à vista'
  if saving
  else None
 )

# ---------------- RELEVANCE ----------------
BAD={
 'celular':[
  'capa','capinha','pelicula','suporte',
  'carregador','cabo','adaptador','case'
 ],

 'perfume':[
  'decant','amostra','porta perfume','refil vazio'
 ],

 'academia':[
  'halter','anilha','barra','caneleira',
  'elastico','suporte'
 ],

 'ferramenta':[
  'broca avulsa','peca','carvao',
  'bateria avulsa','capa'
 ]
}

def profile(query):

 q=norm(query)

 if any(
  x in q
  for x in [
   'iphone',
   'samsung',
   'galaxy',
   'motorola',
   'xiaomi',
   'redmi',
   'poco',
   'smartphone',
   'celular'
  ]
 ):
  return 'celular'

 if 'perfume' in q:
  return 'perfume'

 if any(
  x in q
  for x in [
   'academia',
   'tenis corrida',
   'tenis treino',
   'whey',
   'creatina',
   'suplemento'
  ]
 ):
  return 'academia'

 if any(
  x in q
  for x in [
   'furadeira',
   'parafusadeira',
   'ferramenta',
   'esmerilhadeira',
   'serra'
  ]
 ):
  return 'ferramenta'

 return None

def relevance(title,query):

 title=norm(title)
 query=norm(query)

 profile_name=profile(query)

 score=sum(
  10
  for word in query.split()
  if len(word)>=3
  and word in title
 )

 if profile_name:

  strong={
   'celular':[
    'smartphone',
    'iphone',
    'galaxy',
    'samsung',
    'motorola',
    'xiaomi',
    'redmi',
    'poco'
   ],

   'perfume':[
    'perfume',
    'parfum',
    'eau de parfum'
   ],

   'academia':[
    'roupa',
    'camiseta',
    'short',
    'legging',
    'tenis',
    'whey',
    'creatina',
    'suplemento'
   ],

   'ferramenta':[
    'furadeira',
    'parafusadeira',
    'esmerilhadeira',
    'ferramenta',
    'serra',
    'impacto'
   ]
  }

  score+=sum(
   35
   for word in strong[profile_name]
   if word in title
  )

  score-=sum(
   90
   for word in BAD[profile_name]
   if word in title
  )

 return score

# ---------------- CAÇADOR ----------------
def scan_queries(
 queries,
 min_discount=0,
 job=None
):

 products={}

 token=access_token()

 queries=list(
  dict.fromkeys(
   queries
  )
 )

 total_queries=max(
  1,
  len(queries)
 )

 for index,query in enumerate(
  queries,
  1
 ):

  if job:
   update_job(
    job,
    progress=
     15+
     int(
      index/total_queries*25
     ),
    message=
     f'🔎 Buscando: {query}'
   )

  categories=discover_categories(
   query,
   token
  )[:3]

  for category in categories:

   for highlight in highlights(
    category['category_id'],
    token
   ):

    if highlight.get('type')!='PRODUCT':
     continue

    product_id=(
     highlight.get('id')
     or highlight.get('product_id')
    )

    if product_id:

     products.setdefault(
      product_id,
      {
       'category_id':
        category['category_id'],

       'category_name':
        category['category_name'],

       'query':
        query
      }
     )

  if len(products)>=80:
   break

 offers=[]
 seen_items=set()

 product_list=list(
  products.items()
 )[:80]

 for index,(product_id,base) in enumerate(
  product_list,
  1
 ):

  if job:

   update_job(
    job,
    progress=
     40+
     int(
      index/
      max(
       1,
       len(product_list)
      )*50
     ),
    message=
     f'🛒 Analisando produto '
     f'{index}/'
     f'{len(product_list)}'
   )

  product_data=product(
   product_id,
   token
  )

  if not product_data:
   continue

  title=(
   product_data.get('name')
   or product_data.get('title')
   or product_id
  )

  score=relevance(
   title,
   base['query']
  )

  if score<15:
   continue

  pictures=(
   product_data.get('pictures')
   or []
  )

  image=(
   pictures[0].get('url')
   if pictures
   and isinstance(
    pictures[0],
    dict
   )
   else None
  )

  raw_items=product_items(
   product_id,
   token
  )

  for raw in raw_items:

   item=normalize_item(
    raw
   )

   if not item:
    continue

   if item['item_id'] in seen_items:
    continue

   seen_items.add(
    item['item_id']
   )

   try:
    price=float(
     item['price']
    )
   except:
    continue

   if price<MIN_PRODUCT_PRICE:
    continue

   try:

    original=(
     float(
      item['original_price']
     )
     if item.get(
      'original_price'
     ) not in (
      None,
      ''
     )
     else None
    )

   except:
    original=None

   seller_discount=pct_discount(
    price,
    original
   )

   if (
    seller_discount
    <float(min_discount or 0)
   ):
    continue

   shipping=item.get(
    'shipping_cost'
   )

   total_price=total(
    price,
    shipping
   )

   coupon=best_coupon(
    price
   )

   coupon_saving=(
    coupon['desconto_estimado']
    if coupon
    else 0
   )

   coupon_final=(
    round(
     total_price-
     coupon_saving,
     2
    )
    if coupon
    else None
   )

   cash_discount,cash_label=(
    explicit_cash_discount(
     raw,
     price
    )
   )

   cash_final=(
    round(
     total_price-
     cash_discount,
     2
    )
    if cash_discount
    else None
   )

   best_final=coupon_final

   best_mode=(
    'cupom'
    if coupon
    else None
   )

   best_saving=coupon_saving

   if (
    cash_final is not None
    and (
     best_final is None
     or cash_final<best_final
    )
   ):

    best_final=cash_final
    best_mode='pix'
    best_saving=cash_discount

   offers.append({
    'product_id':
     product_id,

    'item_id':
     item['item_id'],

    'title':
     title,

    'modelo_nome':
     model_name(title),

    'especificacoes':
     specs(title),

    'image':
     image,

    'category_name':
     base['category_name'],

    'permalink':
     item.get('permalink')
     or product_data.get(
      'permalink'
     )
     or (
      'https://www.mercadolivre.com.br/p/'
      +product_id
     ),

    'price':
     price,

    'original_price':
     original,

    'discount':
     seller_discount,

    'seller_id':
     item.get('seller_id'),

    'condition':
     item.get('condition'),

    'free_shipping':
     item.get(
      'free_shipping'
     ),

    'shipping_cost':
     shipping,

    'shipping_known':
     shipping is not None,

    'total_price':
     total_price,

    'relevance_score':
     score,

    'cupom':
     coupon,

    'desconto_cupom':
     coupon_saving,

    'percentual_cupom_efetivo':
     round(
      coupon_saving/price*100,
      2
     )
     if coupon_saving
     else 0,

    'cash_discount':
     cash_discount,

    'cash_label':
     cash_label,

    'cash_final':
     cash_final,

    'melhor_forma':
     best_mode,

    'maior_desconto':
     best_saving,

    'preco_com_cupom':
     coupon_final,

    'preco_final_melhor':
     best_final
   })

 # ----------------
 # 1 PRODUTO = 1 OPORTUNIDADE
 # ----------------

 groups={}

 for offer in offers:

  groups.setdefault(
   offer['product_id'],
   []
  ).append(offer)

 models=[]

 for product_id,items in groups.items():

  items.sort(
   key=lambda item:(
    -(item.get(
     'maior_desconto'
    ) or 0),

    -(
     item.get(
      'percentual_cupom_efetivo'
     ) or 0
    ),

    item.get(
     'preco_final_melhor'
    )
    if item.get(
     'preco_final_melhor'
    ) is not None
    else 999999,

    0
    if item.get(
     'free_shipping'
    )
    else 1,

    item.get(
     'price'
    ) or 999999
   )
  )

  best=items[0]

  best['menor_preco_modelo']=True

  models.append({
   'product_id':
    product_id,

   'title':
    best['title'],

   'modelo_nome':
    best['modelo_nome'],

   'especificacoes':
    best['especificacoes'],

   'image':
    best['image'],

   'category_name':
    best['category_name'],

   'ofertas':
    [best]
  })

 models.sort(
  key=lambda group:(
   -(
    group['ofertas'][0].get(
     'maior_desconto'
    ) or 0
   ),

   -(
    group['ofertas'][0].get(
     'percentual_cupom_efetivo'
    ) or 0
   ),

   group['ofertas'][0].get(
    'price'
   ) or 999999
  )
 )

 unique=[]
 signatures=set()

 for group in models:

  signature=norm(
   group['modelo_nome']
  )

  if signature in signatures:
   continue

  signatures.add(signature)
  unique.append(group)

 models=unique[:30]

 flat=[
  group['ofertas'][0]
  for group in models
 ]

 values=[
  item['price']
  for item in flat
 ]

 finals=[
  item['preco_final_melhor']
  for item in flat
  if item.get(
   'preco_final_melhor'
  ) is not None
 ]

 stats={
  'ofertas':
   len(flat),

  'cupom aplicável':
   sum(
    bool(
     item.get('cupom')
    )
    for item in flat
   ),

  'valor':
   brl(
    min(
     values or [0]
    )
   ),

  'valor com o desconto':
   brl(
    min(
     finals
     or values
     or [0]
    )
   )
 }

 return {
  'stats':
   stats,

  'modelos':
   models,

  'ofertas':
   flat,

  'produtos_unicos':
   len(models)
 }

def auto_queries(category=None):

 if category:
  return CATALOG.get(
   category,
   []
  )[:8]

 queries=[
  query
  for category_queries
  in CATALOG.values()
  for query in category_queries
 ]

 return queries[:24]

# ---------------- ANÚNCIO ----------------
def ad_text(
 offer,
 affiliate=''
):

 lines=[
  '🔥 OFERTA IMPERDÍVEL!',
  '',
  f"🛍️ {offer.get('title','Produto')}"
 ]

 if offer.get(
  'original_price'
 ):
  lines.append(
   f"💸 De: "
   f"{brl(offer['original_price'])}"
  )

 final_price=(
  offer.get(
   'preco_com_cupom'
  )
  or offer.get(
   'preco_final_melhor'
  )
  or offer.get(
   'price'
  )
 )

 lines.append(
  f"🔥 Por: {brl(final_price)}"
 )

 if offer.get(
  'discount',
  0
 )>0:

  lines.append(
   f"🏷️ "
   f"{offer['discount']}% OFF"
  )

 if offer.get(
  'free_shipping'
 ):
  lines.append(
   '🚚 Frete grátis'
  )

 if offer.get(
  'cupom'
 ):

  coupon=offer['cupom']

  lines.extend([
   '',
   f"🎟️ Cupom: "
   f"{coupon['code']}"
  ])

  if coupon.get(
   'discount_percent'
  ):
   lines.append(
    f"🔥 Até "
    f"{coupon['discount_percent']}% OFF"
   )

  if coupon.get(
   'fixed_discount'
  ):
   lines.append(
    f"💰 "
    f"{brl(coupon['fixed_discount'])}"
    f" OFF"
   )

  if coupon.get(
   'min_purchase'
  ):
   lines.append(
    f"🛒 Compra mínima: "
    f"{brl(coupon['min_purchase'])}"
   )

  if coupon.get(
   'max_discount'
  ):
   lines.append(
    f"💰 Máximo: "
    f"{brl(coupon['max_discount'])}"
   )

  lines.append(
   f"💵 Economia estimada: "
   f"{brl(offer.get('desconto_cupom',0))}"
  )

 lines.extend([
  '',
  '👉 Pegar promoção:',
  affiliate
  or offer.get(
   'permalink',
   ''
  ),
  '',
  '🏪 Loja oficial no MELI!',
  '',
  '⚠️ Confirme a aplicação do cupom no checkout.'
 ])

 return '\n'.join(lines)

# ---------------- JOBS ----------------
JOBS={}
JOB_LOCK=threading.Lock()

def create_job():

 job_id=uuid.uuid4().hex

 with JOB_LOCK:

  JOBS[job_id]={
   'status':'queued',
   'progress':0,
   'message':'Aguardando início...',
   'result':None,
   'error':None
  }

 return job_id

def update_job(
 job_id,
 **kwargs
):

 with JOB_LOCK:

  if job_id in JOBS:
   JOBS[job_id].update(
    kwargs
   )

def get_job(job_id):

 with JOB_LOCK:

  return dict(
   JOBS.get(
    job_id,
    {
     'status':'not_found',
     'progress':0,
     'message':
      'Caça não encontrada.',
     'result':None,
     'error':'not_found'
    }
   )
  )

def run_job(
 job_id,
 category=None
):

 try:

  update_job(
   job_id,
   status='running',
   progress=5,
   message=
    '🎟️ Procurando os melhores cupons...'
  )

  coupon_sync=sync_coupons()

  update_job(
   job_id,
   progress=12,
   message=
    '🛒 Começando a caça...'
  )

  result=scan_queries(
   auto_queries(category),
   job=job_id
  )

  result['coupon_sync']=coupon_sync

  update_job(
   job_id,
   status='done',
   progress=100,
   message=(
    '✅ Caça finalizada: '
    f"{result['produtos_unicos']} "
    'produtos únicos.'
   ),
   result=json_safe(result)
  )

 except Exception as e:

  print(
   '[ERRO JOB]',
   repr(e)
  )

  update_job(
   job_id,
   status='error',
   progress=100,
   message='❌ Erro durante a caça.',
   error=str(e)
  )

# ---------------- API ----------------
@app.route('/api/cacar/start')
def start_cacar():

 if not access_token():
  return jsonify({
   'erro':
    'Conecte sua conta do Mercado Livre primeiro.'
  }),401

 category=(
  request.args.get(
   'categoria',
   ''
  ).strip()
  or None
 )

 job_id=create_job()

 threading.Thread(
  target=run_job,
  args=(job_id,category),
  daemon=True
 ).start()

 return jsonify({
  'jobId':job_id
 })

@app.route('/api/cacar/status/<job_id>')
def status_cacar(job_id):
 return jsonify(
  json_safe(
   get_job(job_id)
  )
 )

@app.route('/api/cacar')
def api_cacar():
 return start_cacar()

@app.route('/api/buscar')
def api_buscar():

 query=request.args.get(
  'q',
  ''
 ).strip()

 if not query:
  return jsonify({
   'erro':'Informe uma busca.'
  }),400

 if not access_token():
  return jsonify({
   'erro':
    'Conecte sua conta do Mercado Livre primeiro.'
  }),401

 sync_coupons()

 return jsonify(
  json_safe(
   scan_queries([query])
  )
 )

@app.route('/api/cupons')
def api_cupons():

 s=(
  sync_coupons()
  if request.args.get(
   'atualizar'
  )=='1'
  else None
 )

 return jsonify({
  'cupons':
   json_safe(
    coupons()
   ),

  'fontes':
   COUPON_SOURCE_URLS,

  'sincronizacao':
   s
 })

@app.route('/api/gerar-anuncio')
def gerar_anuncio():

 o={
  'title':
   request.args.get(
    'title',
    'Produto'
   ),

  'price':
   float(
    request.args.get(
     'price',
     0
    ) or 0
   ),

  'original_price':
   request.args.get(
    'original_price'
   ),

  'discount':
   float(
    request.args.get(
     'discount',
     0
    ) or 0
   ),

  'free_shipping':
   request.args.get(
    'shipping_free'
   )=='1',

  'cupom':
   None,

  'preco_com_cupom':
   None,

  'permalink':
   request.args.get(
    'permalink',
    ''
   )
 }

 code=request.args.get(
  'cupom',
  ''
 ).strip().upper()

 if code:

  c=db()

  r=c.execute(
   'SELECT * FROM cupons WHERE code=? AND active=1',
   (code,)
  ).fetchone()

  c.close()

  if r:

   cup=dict(r)

   cup['desconto_estimado']=coupon_discount(
    cup,
    o['price']
   )

   o['cupom']=cup

   o['desconto_cupom']=\
    cup['desconto_estimado']

   o['preco_com_cupom']=max(
    0,
    o['price']-
    cup['desconto_estimado']
   )

 return jsonify({
  'anuncio':
   ad_text(
    o,
    request.args.get(
     'affiliate_link',
     ''
    ).strip()
   )
 })

# ---------------- DIAGNOSTIC ----------------
@app.route('/mercadolivre/diagnostico')
def diagnostico():

 t=tokens()

 out={
  'configurado':
   bool(ML_CLIENT_ID),

  'conectado':
   bool(access_token())
 }

 if access_token():

  d,s,_=ml_get(
   '/users/me'
  )

  out['users_me']={
   'status_http':s,
   'resposta':d
  }

 if t:

  out['token_local']={
   'user_id':
    t.get('user_id'),

   'nickname':
    t.get('nickname'),

   'expires_at':
    t.get('expires_at')
  }

 return jsonify(out)

@app.route('/mercadolivre/teste-produto-itens')
def teste_items():

 pid=request.args.get(
  'product_id',
  'MLB58793248'
 )

 d,s,_=ml_get(
  f'/products/{pid}/items'
 )

 return jsonify({
  'product_id':pid,
  'status_http':s,
  'resposta':d
 }),s

@app.route('/health')
def health():

 return jsonify({
  'status':'ok',
  'app':'Cacador de Ofertas',
  'mercado_livre_conectado':
   bool(access_token()),
  'produto_minimo':
   MIN_PRODUCT_PRICE,
  'cupom_primeiro':
   'ativo',
  'gerador_anuncio':
   'ativo',
  'caca_automatica':
   'ativo'
 })

HTML=r'''<!doctype html>
<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
 name="viewport"
 content="width=device-width,initial-scale=1"
>

<title>Caçador de Ofertas</title>

<style>

*{
 box-sizing:border-box
}

body{
 margin:0;
 background:#f3f4f6;
 font-family:Arial,sans-serif;
 color:#222
}

.container{
 max-width:1000px;
 margin:auto;
 padding:15px
}

.card{
 background:#fff;
 border-radius:16px;
 padding:16px;
 margin-bottom:15px;
 box-shadow:0 4px 18px #0001
}

button,input{
 width:100%;
 padding:13px;
 border:1px solid #ddd;
 border-radius:10px;
 font-size:15px
}

button{
 background:#3483fa;
 color:#fff;
 border:0;
 margin-top:7px;
 font-weight:bold;
 cursor:pointer
}

.login{
 background:#ffe600;
 color:#222
}

.grid{
 display:grid;
 grid-template-columns:
  repeat(auto-fit,minmax(150px,1fr));
 gap:8px
}

.cat{
 background:#fff;
 color:#222;
 border:1px solid #ddd;
 text-align:left
}

.stats{
 display:grid;
 grid-template-columns:
  repeat(4,1fr);
 gap:8px
}

.stat{
 background:#f2f3f5;
 border-radius:10px;
 padding:10px;
 font-size:12px
}

.stat b{
 display:block;
 font-size:21px;
 margin-top:5px
}

.progress{
 height:10px;
 background:#e5e7eb;
 border-radius:20px;
 overflow:hidden;
 margin-top:10px
}

.bar{
 height:100%;
 width:0;
 background:#3483fa;
 transition:.3s
}

.modelo{
 border:1px solid #ddd;
 border-radius:15px;
 padding:12px;
 margin-top:12px;
 background:#fff
}

.mh{
 display:flex;
 gap:12px;
 align-items:center
}

.mh img{
 width:80px;
 height:80px;
 object-fit:contain;
 border-radius:10px;
 background:#f7f7f7
}

.title{
 font-weight:bold;
 font-size:17px
}

.price{
 font-size:23px;
 font-weight:bold;
 margin-top:9px
}

.old{
 text-decoration:line-through;
 color:#777
}

.green{
 color:#008a3e;
 font-weight:bold
}

.coupon{
 background:#fff8d7;
 border:1px dashed #d0a500;
 border-radius:10px;
 padding:10px;
 margin-top:9px
}

.final{
 background:#e9f8ef;
 color:#008a3e;
 font-weight:bold;
 padding:10px;
 border-radius:9px;
 margin-top:7px
}

.small{
 font-size:12px;
 color:#666
}

.ad{
 display:none;
 white-space:pre-wrap;
 background:#f6f6f6;
 border:1px solid #ddd;
 border-radius:10px;
 padding:12px;
 margin-top:8px;
 font-size:13px
}

.status{
 background:#ecf8ef;
 padding:10px;
 border-radius:10px
}

@media(max-width:600px){

 .stats{
  grid-template-columns:
   repeat(2,1fr)
 }

}

</style>

<script>

async function jfetch(url){

 const r=await fetch(url);

 let d={};

 try{
  d=await r.json()
 }
 catch(e){
  throw new Error(
   'Resposta inválida do servidor'
  )
 }

 if(!r.ok){

  throw new Error(
   d.erro||
   d.error||
   'Erro HTTP '+r.status
  )
 }

 return d
}


async function cacar(cat){

 const st=
  document.getElementById(
   'status'
  );

 const bar=
  document.getElementById(
   'bar'
  );

 st.textContent=
  '🔄 Iniciando caça...';

 bar.style.width='3%';

 try{

  const d=await jfetch(
   '/api/cacar/start'+
   (
    cat
    ? '?categoria='+
      encodeURIComponent(cat)
    : ''
   )
  );

  const id=d.jobId;

  const poll=setInterval(
   async()=>{

    try{

     const x=await jfetch(
      '/api/cacar/status/'+id
     );

     st.textContent=
      x.message||
      'Caçando...';

     bar.style.width=
      (x.progress||0)+'%';

     if(
      x.status==='done'
     ){

      clearInterval(
       poll
      );

      render(
       x.result||{}
      );

      st.textContent=
       x.message;

      bar.style.width=
       '100%';
     }

     if(
      x.status==='error'
     ){

      clearInterval(
       poll
      );

      st.textContent=
       '❌ '+x.error;

      bar.style.width=
       '100%';
     }

    }
    catch(e){

     clearInterval(
      poll
     );

     st.textContent=
      '❌ '+e.message;
    }

   },
   1200
  );

 }
 catch(e){

  st.textContent=
   '❌ '+e.message;

  bar.style.width=
   '0%';
 }
}


async function buscar(){

 const q=
  document
   .getElementById('q')
   .value
   .trim();

 if(!q)
  return;

 document.getElementById(
  'status'
 ).textContent=
  '🔎 Procurando...';

 try{

  render(
   await jfetch(
    '/api/buscar?q='+
    encodeURIComponent(q)
   )
  );

  document.getElementById(
   'status'
  ).textContent=
   '✅ Busca finalizada.';

 }
 catch(e){

  document.getElementById(
   'status'
  ).textContent=
   '❌ '+e.message;
 }
}


function render(data){

 document.getElementById(
  'stats'
 ).innerHTML=
  Object.entries(
   data.stats||{}
  )
  .map(
   ([k,v])=>
    `<div class="stat">
      ${esc(k)}
      <b>${esc(v)}</b>
     </div>`
  )
  .join('');


 document.getElementById(
  'results'
 ).innerHTML=
  (data.modelos||[])
  .map(
   (m,i)=>
    `<div class="modelo">

      <div class="mh">

       ${
        m.image
        ? `<img src="${esc(m.image)}">`
        : ''
       }

       <div>

        <div class="title">
         🔥
         ${esc(m.modelo_nome)}
        </div>

        <div>
         ${
          (m.especificacoes||[])
          .map(
           s=>
            `<span>
             ${esc(s)}
             </span>`
          )
          .join('')
         }
        </div>

       </div>

      </div>

      ${
       (m.ofertas||[])
       .map(
        (o,j)=>
         offer(
          o,
          i,
          j
         )
       )
       .join('')
      }

     </div>`
  )
  .join('')

  ||

  '<p>Nenhuma oportunidade encontrada.</p>';
}


function offer(o,i,j){

 const id=
  'x'+i+'_'+j;

 const c=o.cupom;

 return `

 <div
  style="
   border-top:1px solid #eee;
   margin-top:10px;
   padding-top:10px
  "
 >

  <div class="price">
   ${brl(
    o.preco_final_melhor||
    o.price
   )}
   🔥
  </div>


  ${
   o.preco_final_melhor&&
   o.preco_final_melhor<
   o.price

   ? `
    <div class="old">
     De ${brl(o.price)}
    </div>
   `
   : ''
  }


  ${
   o.original_price

   ? `
    <div class="old">
     Preço anterior:
     ${brl(o.original_price)}
    </div>
   `
   : ''
  }


  ${
   o.free_shipping

   ? `
    <div class="green">
     🚚 Frete grátis
    </div>
   `
   : ''
  }


  ${
   c

   ? `

    <div class="coupon">

     <b>
      🎟️ Cupom:
      ${esc(c.code)}
     </b>

     <div>
      💰 Economia estimada:
      ${brl(
       o.desconto_cupom
      )}
     </div>

     ${
      c.discount_percent

      ? `
       <div>
        🔥 Até
        ${c.discount_percent}%
        OFF
       </div>
      `
      : ''
     }

     ${
      c.fixed_discount

      ? `
       <div>
        💵
        ${brl(
         c.fixed_discount
        )}
        OFF
       </div>
      `
      : ''
     }

     ${
      c.min_purchase

      ? `
       <div class="small">
        Compra mínima:
        ${brl(
         c.min_purchase
        )}
       </div>
      `
      : ''
     }

     ${
      c.max_discount

      ? `
       <div class="small">
        Máximo:
        ${brl(
         c.max_discount
        )}
       </div>
      `
      : ''
     }

     <div class="small">
      ⚠️ Cupom estimado —
      confirme no checkout.
     </div>

    </div>

   `

   : `

    <div class="coupon">
     🎟️ Nenhum cupom
     identificado para este resultado.
    </div>

   `
  }


  <br>


  <a
   href="${esc(o.permalink)}"
   target="_blank"
  >
   🛒 Ver produto
  </a>


  <input
   id="link_${id}"
   placeholder="Cole seu link de afiliado"
  >


  <button
   onclick='anuncio(
    "${id}",
    ${JSON.stringify(o)}
   )'
  >
   📢 Gerar anúncio
  </button>


  <div
   id="ad_${id}"
   class="ad"
  ></div>

 </div>
 `;
}


async function anuncio(id,o){

 const link=
  document.getElementById(
   'link_'+id
  ).value;

 const p=
  new URLSearchParams({
   title:o.title,
   price:o.price,
   discount:o.discount||0,
   shipping_free:
    o.free_shipping
    ? '1'
    : '0',

   cupom:
    o.cupom
    ? o.cupom.code
    : '',

   affiliate_link:
    link,

   permalink:
    o.permalink||''
  });

 if(o.original_price){

  p.set(
   'original_price',
   o.original_price
  );
 }

 const d=await jfetch(
  '/api/gerar-anuncio?'+p
 );

 document.getElementById(
  'ad_'+id
 ).textContent=
  d.anuncio;

 document.getElementById(
  'ad_'+id
 ).style.display=
  'block';
}


function brl(v){

 return 'R$ '+
  Number(
   v||0
  ).toLocaleString(
   'pt-BR',
   {
    minimumFractionDigits:2,
    maximumFractionDigits:2
   }
  );
}


function esc(s){

 return String(
  s??''
 ).replace(
  /[&<>"']/g,
  c=>({
   '&':'&amp;',
   '<':'&lt;',
   '>':'&gt;',
   '"':'&quot;',
   "'":'&#039;'
  }[c])
 );
}

</script>

</head>

<body>

<div class="container">


<div class="card">

 <h1>
  🛒 Caçador de Ofertas
 </h1>

 <p>
  Produtos a partir de R$ 69,90,
  com busca de cupons e escolha
  da maior economia estimada.
 </p>


 {% if conectado %}

  <div class="status">

   🟢 Mercado Livre conectado

   {% if nickname %}
    <br>
    <b>{{nickname}}</b>
   {% endif %}

  </div>


  <a
   href="/mercadolivre/logout"
  >
   <button>
    Desconectar
   </button>
  </a>

 {% else %}

  <a
   href="/mercadolivre/login"
  >
   <button class="login">
    🔗 Conectar Mercado Livre
   </button>
  </a>

 {% endif %}

</div>


<div class="card">

 <h2>
  🔥 Caçar promoções
 </h2>


 <button
  onclick="cacar('')"
 >
  🚀 CAÇAR TODAS AS CATEGORIAS
 </button>


 <div class="grid">

  {% for c in categorias %}

   <button
    class="cat"
    onclick="cacar({{c|tojson}})"
   >
    {{c}}
   </button>

  {% endfor %}

 </div>


 <p
  id="status"
  class="small"
 >
  Escolha uma categoria
  ou cace tudo.
 </p>


 <div class="progress">

  <div
   id="bar"
   class="bar"
  ></div>

 </div>

</div>


<div class="card">

 <h2>
  🔎 Busca manual
 </h2>


 <input
  id="q"
  placeholder="Ex: celular, perfume, furadeira..."
 >


 <button
  onclick="buscar()"
 >
  Procurar
 </button>

</div>


<div class="card">

 <h2>
  📊 Resultado
 </h2>


 <div
  id="stats"
  class="stats"
 ></div>

</div>


<div class="card">

 <h2>
  🏆 Melhores oportunidades
 </h2>


 <p class="small">
  1 produto = 1 oportunidade.
  Gere o anúncio pronto para
  WhatsApp ou Telegram.
 </p>


 <div id="results">

  <p>
   Faça uma busca para começar.
  </p>

 </div>

</div>


<div class="card">

 <a
  href="/api/cupons?atualizar=1"
  target="_blank"
 >
  🎟️ Atualizar/consultar cupons
 </a>

 <br>
 <br>

 <a
  href="/mercadolivre/diagnostico"
  target="_blank"
 >
  🧪 Diagnóstico Mercado Livre
 </a>

</div>


</div>

</body>

</html>
'''

@app.route('/')
def index():

 t=tokens()

 return render_template_string(
  HTML,
  conectado=bool(
   access_token()
  ),
  nickname=
   t.get('nickname')
   if t
   else None,
  categorias=
   list(CATALOG)
 )

@app.route('/cupons')
def coupons_page():

 return jsonify({
  'cupons':
   json_safe(
    coupons()
   ),

  'fontes':
   COUPON_SOURCE_URLS
 })

@app.errorhandler(404)
def e404(e):

 return jsonify({
  'erro':
   'Rota não encontrada.',
  'rota':
   request.path
 }),404

@app.errorhandler(500)
def e500(e):

 return jsonify({
  'erro':
   'Erro interno no servidor.',
  'detalhes':
   str(e)
 }),500

if __name__=='__main__':

 app.run(
  host='0.0.0.0',
  port=int(
   os.getenv(
    'PORT',
    '8080'
   )
  ),
  debug=False
 )