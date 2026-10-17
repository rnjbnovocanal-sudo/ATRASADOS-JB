"""ATRASADOS JB - servidor local de teste. Dependencias: requests, beautifulsoup4."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urljoin
from pathlib import Path
from datetime import datetime
import json, re, time, threading, os
import requests
from bs4 import BeautifulSoup

BASE = 'https://bichoatrasado.com/'
SLUGS = {'rio','look-goias','federal','nacional','loteria-dos-sonhos','paraiba','bahia','sao-paulo','lbr-brasilia','minas-gerais','pernambuco','rio-grande-do-sul','sergipe'}
ROOT = Path(__file__).resolve().parent
CACHE = {}
LOCK = threading.Lock()
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36', 'Accept-Language':'pt-BR,pt;q=0.9'}


def number(text):
    match = re.search(r'\d+', text or '')
    return int(match.group()) if match else None


def cell_value(cell, label):
    text = cell.get_text(' ', strip=True)
    match = re.search(r'\b'+label+r'\s*[:\-]?\s*(\d+)', text, re.I)
    return int(match.group(1)) if match else number(text)


def parse_row(tr):
    cells = tr.find_all('td', recursive=False)
    if len(cells) < 4:
        return None
    pos = number(cells[0].get_text(' ',strip=True))
    if pos is None or not 1 <= pos <= 25:
        return None
    animalcell = cells[1]
    content = animalcell.get_text(' ', strip=True)
    match = re.search(r'Grupo\s*0?(\d{1,2})\b', content, re.I)
    if not match:
        return None
    group = int(match.group(1))
    if not 1 <= group <= 25:
        return None
    animal = re.sub(r'^\s*\d+[º°.]?\s*', '', content[:match.start()]).strip(' ·-–')
    draws = cell_value(cells[2], 'Atual')
    days = cell_value(cells[3], 'Dias')
    if not animal or draws is None or days is None:
        return None
    img = animalcell.find('img')
    photo = None
    if img:
        raw = img.get('data-src') or img.get('src') or ''
        if raw and not raw.startswith('data:'):
            photo = urljoin(BASE, raw)
    return {'animal':animal,'group':f'{group:02d}','days':days,'draws':draws,'photo':photo,'position':pos}


def parse_cards(soup):
    """Lê o ranking em cartões/divs, usado quando a fonte não usa <table>."""
    title = soup.find(string=re.compile(r'Ranking dos 25 bichos', re.I))
    if title is None:
        return []
    # Não confundir o resumo 'Mais atrasados' com o ranking principal.
    anchor = title.parent
    candidates = []
    seen = set()
    for node in anchor.next_elements:
        if getattr(node, 'name', None) in ('h1','h2') and node is not anchor:
            break
        if getattr(node, 'name', None) not in ('div','li','tr','article'):
            continue
        txt = node.get_text(' ',strip=True)
        matches = re.findall(r'Grupo\s*0?(\d{1,2})\b',txt,re.I)
        if len(matches) != 1 or not re.search(r'\bAtual\s*\d+',txt,re.I) or not re.search(r'\bDias\s*\d+',txt,re.I):
            continue
        group = int(matches[0])
        if group not in range(1,26) or group in seen:
            continue
        # Um cartão válido precisa ter um nome de animal e os dois valores.
        m = re.search(r'([A-Za-zÀ-ÿ]+)\s+Grupo\s*0?'+str(group)+r'\b',txt,re.I)
        draw = re.search(r'\bAtual\s*(\d+)',txt,re.I)
        days = re.search(r'\bDias\s*(\d+)',txt,re.I)
        if not (m and draw and days):
            continue
        img = node.find('img')
        raw = (img.get('data-src') or img.get('src') or '') if img else ''
        photo = urljoin(BASE,raw) if raw and not raw.startswith('data:') else None
        candidates.append({'animal':m.group(1).title(),'group':f'{group:02d}',
                           'days':int(days.group(1)),'draws':int(draw.group(1)),
                           'photo':photo})
        seen.add(group)
        if len(candidates)==6:
            break
    return candidates


def parse_ranking(soup):
    # Tenta primeiro tabelas HTML e depois cartões; nunca mistura as duas abas.
    for table in soup.find_all('table'):
        headings=' '.join(th.get_text(' ',strip=True) for th in table.find_all('th'))
        if all(re.search(r'\b'+x+r'\b',headings,re.I) for x in ('Bicho','Atual','Dias')):
            rows=[]
            for tr in table.find_all('tr'):
                row=parse_row(tr)
                if row and row['position']==len(rows)+1:
                    row.pop('position',None)
                    rows.append(row)
                    if len(rows)==6:
                        return rows
            break
    return parse_cards(soup)


def fetch(slug):
    if slug not in SLUGS:
        raise ValueError('Loteria nao autorizada: '+slug)
    with LOCK:
        cached = CACHE.get(slug)
        if cached and time.time()-cached[0] < 90:
            return cached[1]
    response = requests.get(urljoin(BASE,slug), headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    rows = parse_ranking(soup)
    if len(rows) != 6:
        raise ValueError(f'Ranking incompleto: {len(rows)} de 6 linhas válidas. A fonte usa estrutura diferente; envie o diagnóstico do HTML.')
    extraction = 'Extracao nao informada'
    for text in soup.stripped_strings:
        if re.search(r'ultima extracao', text, re.I) or re.search(r'última extração', text, re.I):
            extraction = text[:240]
            break
    result = {'items':rows,'source':urljoin(BASE,slug),'source_extraction':extraction,'checked_at':datetime.now().astimezone().isoformat(timespec='seconds')}
    with LOCK:
        CACHE[slug]=(time.time(),result)
    return result


class Handler(BaseHTTPRequestHandler):
    def send(self,status,data,ctype):
        self.send_response(status)
        self.send_header('Content-Type',ctype)
        self.send_header('Cache-Control','no-store')
        self.send_header('Access-Control-Allow-Origin','*')
        self.end_headers()
        self.wfile.write(data)

    def json(self,status,data):
        self.send(status,json.dumps(data,ensure_ascii=False).encode('utf-8'),'application/json; charset=utf-8')

    def do_GET(self):
        u = urlparse(self.path)
        query = parse_qs(u.query)
        if u.path == '/api/atrasados' or u.path.startswith('/api/atrasados/'):
            slug = query.get('l',query.get('loteria',['']))[0] or u.path.removeprefix('/api/atrasados/').strip('/')
            try:
                self.json(200,fetch(slug))
            except ValueError as e:
                self.json(422,{'error':str(e),'slug':slug})
            except Exception as e:
                self.json(502,{'error':'Falha ao consultar a fonte: '+str(e),'slug':slug})
        elif u.path in ('/','/index.html'):
            file = ROOT/'ATRASADOS_JB_INTEGRADO.html'
            if not file.exists():
                file = ROOT/'index.html'
            if file.exists():
                self.send(200,file.read_bytes(),'text/html; charset=utf-8')
            else:
                self.send(404,b'HTML nao encontrado na pasta do servidor','text/plain; charset=utf-8')
        elif u.path == '/api/diagnostico':
            slug=query.get('l',['rio'])[0]
            if slug not in SLUGS:
                self.json(422,{'error':'Loteria não autorizada'})
                return
            try:
                response=requests.get(urljoin(BASE,slug),headers=HEADERS,timeout=20)
                response.raise_for_status()
                soup=BeautifulSoup(response.text,'html.parser')
                heading=soup.find(string=re.compile('Ranking dos 25 bichos',re.I))
                raw=heading.parent.parent.get_text(' ',strip=True)[:1800] if heading else soup.get_text(' ',strip=True)[:1800]
                self.json(200,{'status':response.status_code,'titulo_encontrado':bool(heading),'amostra':raw,'tabelas':len(soup.find_all('table'))})
            except Exception as e:
                self.json(502,{'error':str(e)})
        elif u.path == '/api/foto':
            slug = query.get('l',[''])[0]
            group = query.get('g',[''])[0].zfill(2)
            try:
                item = next(x for x in fetch(slug)['items'] if x['group']==group)
                url = item['photo']
                parsed = urlparse(url or '')
                if parsed.scheme != 'https' or not parsed.hostname or not (parsed.hostname == 'bichoatrasado.com' or parsed.hostname.endswith('.bichoatrasado.com')):
                    raise ValueError('Foto ausente ou fora da fonte autorizada')
                r = requests.get(url,headers=HEADERS,timeout=15)
                r.raise_for_status()
                ctype = r.headers.get('Content-Type','')
                if not ctype.lower().startswith('image/'):
                    raise ValueError('A URL nao retornou uma imagem')
                self.send(200,r.content,ctype)
            except Exception:
                self.send(404,b'Foto nao disponivel','text/plain; charset=utf-8')
        else:
            self.send(404,b'Nao encontrado','text/plain; charset=utf-8')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8766'))
    print(f'ATRASADOS JB iniciado na porta {port}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
