from __future__ import annotations
import base64, hashlib, io, json, os, re, shutil, sys, time, zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'index.html'
OUT_PARENT = ROOT / 'delivery-build'
PKG = OUT_PARENT / 'client-presentation'
ZIP_PATH = OUT_PARENT / 'TightLinesFL-Client-Presentation.zip'
REPORT_PATH = OUT_PARENT / 'validation-report.json'

THEMES = [
    ('theme-01','pursuit','Backcountry'),
    ('theme-02','logbook',"Captain's Log"),
    ('theme-03','strike','Strike Zone'),
    ('theme-04','openwater','Driftline'),
]

WIX_RE = re.compile(r'https://static\.wixstatic\.com/[^\"\'\s)<>}]+')
DATA_RE = re.compile(r'data:image/([a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=]+)')

EXTRA_BASENAMES = {
    'pursuit': set(),
    'logbook': {
        'ebc51e_294d0ffd6a53461292cbbde8e6bcb96f~mv2.jpg',
        'ebc51e_a56a61ada71e47d0910da63f52d323ab~mv2.jpg',
        'ebc51e_e360074edcca4dc0a3fd62577baa98ae~mv2.jpg',
        'ebc51e_f9ec1e92d04e4de3b69619928b991f2c~mv2.jpg',
        'ebc51e_d58ffe1bf07d4ec7a4e1be5df7f95669f000.jpg',
        'ebc51e_690b6059d3d8403384db4c6a4cb6e814~mv2.jpg',
        'ebc51e_7358ca04d8bd42aabf34528fce89092d~mv2.jpg',
    },
    'strike': {
        'ebc51e_690b6059d3d8403384db4c6a4cb6e814~mv2.jpg',
    },
    'openwater': {
        'ebc51e_80d490384b5e48ceabcca46364c5a5d9~mv2.jpg',
        'ebc51e_9a83a7da51a74148b1aabb568f5cf95d~mv2.jpg',
        'ebc51e_592d8a9213e84681b997b6519c347e14~mv2.jpg',
        'ebc51e_ceba9510338f48d1a8a4b49a3b13faed~mv2.jpg',
    },
}

def die(msg: str):
    raise RuntimeError(msg)

def human(n: int) -> str:
    units=['B','KB','MB','GB']
    v=float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f'{v:.2f} {u}'
        v/=1024

def strip_logo_runtime(text: str) -> str:
    start = text.find('const logoBoards=')
    if start != -1:
        end = text.find('initLogoBoards();', start)
        if end != -1:
            end += len('initLogoBoards();')
            text = text[:start] + '\n// Logo-board runtime removed for standalone website delivery.\n' + text[end:]
    return text

def image_ext(raw: bytes, declared: str='') -> str:
    if raw.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return 'webp'
    d=declared.lower()
    if 'jpeg' in d or 'jpg' in d: return 'jpg'
    if 'png' in d: return 'png'
    if 'webp' in d: return 'webp'
    return 'bin'

def extract_brand_boards(original: str):
    board_dir = PKG / 'brand-boards'
    board_dir.mkdir(parents=True, exist_ok=True)
    marker = original.find('const logoBoards=')
    if marker == -1:
        die('Could not locate embedded logoBoards array.')
    end = original.find('];', marker)
    if end == -1:
        die('Could not locate end of logoBoards array.')
    block = original[marker:end+2]
    matches = list(DATA_RE.finditer(block))
    if not matches:
        die('No embedded brand boards were found.')
    out=[]
    for i,m in enumerate(matches,1):
        raw=base64.b64decode(m.group(2))
        ext=image_ext(raw,m.group(1))
        if ext not in {'jpg','png','webp'}:
            die(f'Unsupported brand board format #{i}: {ext}')
        path=board_dir / f'brand-board-{i:02d}.{ext}'
        path.write_bytes(raw)
        try:
            with Image.open(io.BytesIO(raw)) as im:
                dims=im.size
        except Exception:
            dims=None
        out.append({'file':path.name,'bytes':len(raw),'dimensions':dims})
    return out

def collect_needed_urls(original: str, concept_id: str) -> set[str]:
    soup=BeautifulSoup(original,'html.parser')
    sec=soup.find(id=concept_id)
    if not sec:
        die(f'Missing concept section: {concept_id}')
    urls=set(WIX_RE.findall(str(sec)))
    extras=EXTRA_BASENAMES.get(concept_id,set())
    if extras:
        all_urls=set(WIX_RE.findall(original))
        by_base={urlparse(u).path.rsplit('/',1)[-1]:u for u in all_urls}
        missing=[]
        for base in extras:
            if base in by_base: urls.add(by_base[base])
            else: missing.append(base)
        if missing:
            die(f'Could not resolve expected {concept_id} assets: {missing}')
    return urls

def download_cached(url: str, cache: Path) -> bytes:
    key=hashlib.sha256(url.encode()).hexdigest()[:16]
    suffix=Path(urlparse(url).path).suffix.lower()
    if suffix not in {'.jpg','.jpeg','.png','.webp'}: suffix='.jpg'
    p=cache/f'{key}{suffix}'
    if p.exists() and p.stat().st_size > 0:
        return p.read_bytes()
    last=None
    for attempt in range(4):
        try:
            r=requests.get(url,timeout=45,headers={'User-Agent':'Mozilla/5.0 TightLinesFL client packaging'})
            r.raise_for_status()
            raw=r.content
            if len(raw) < 100:
                raise RuntimeError(f'Download too small ({len(raw)} bytes)')
            p.write_bytes(raw)
            return raw
        except Exception as e:
            last=e; time.sleep(2*(attempt+1))
    die(f'Failed to download asset {url}: {last}')

def make_placeholder(path: Path):
    im=Image.new('RGB',(4,4),(245,245,240))
    im.save(path,'JPEG',quality=80,optimize=True)

def standalone_html(original: str, concept_id: str, title: str, theme_dir: Path, cache: Path) -> tuple[str, list[dict]]:
    needed=collect_needed_urls(original,concept_id)
    trimmed=strip_logo_runtime(original)
    soup=BeautifulSoup(trimmed,'html.parser')
    switcher=soup.select_one('.review-switcher')
    if switcher: switcher.decompose()
    for sec in list(soup.select('section.concept')):
        if sec.get('id') != concept_id:
            sec.decompose()
    target=soup.find('section',id=concept_id)
    if not target:
        die(f'{concept_id}: standalone target missing after extraction')
    classes=list(target.get('class',[]))
    if 'active' not in classes:
        classes.append('active')
        target['class']=classes
    if soup.title:
        soup.title.string=f'TightLinesFL — {title}'
    first_script=soup.find('script')
    if first_script:
        txt=first_script.string if first_script.string is not None else first_script.get_text()
        txt=re.sub(
            r"const initial=location\.hash\.slice\(1\)\|\|'pursuit';if\(\['pursuit','logbook','strike','openwater','logos'\]\.includes\(initial\)\)setConcept\(initial,false\);",
            f"const initial='{concept_id}';setConcept(initial,false);",
            txt,
            count=1
        )
        first_script.string=txt
    html='<!doctype html>\n'+str(soup)
    assets=theme_dir/'assets'
    assets.mkdir(parents=True,exist_ok=True)
    placeholder=assets/'placeholder.jpg'
    make_placeholder(placeholder)
    mapping={}
    manifest=[]
    for url in sorted(needed):
        raw=download_cached(url,cache)
        ext=image_ext(raw,Path(urlparse(url).path).suffix)
        if ext == 'bin': ext='jpg'
        fn=f'image-{hashlib.sha256(url.encode()).hexdigest()[:12]}.{ext}'
        (assets/fn).write_bytes(raw)
        mapping[url]=f'assets/{fn}'
        manifest.append({'url':url,'file':fn,'bytes':len(raw)})
    for url in sorted(set(WIX_RE.findall(html)),key=len,reverse=True):
        html=html.replace(url,mapping.get(url,'assets/placeholder.jpg'))
    data_map={}
    def repl_data(m):
        full=m.group(0)
        if full in data_map: return data_map[full]
        raw=base64.b64decode(m.group(2))
        ext=image_ext(raw,m.group(1))
        h=hashlib.sha256(raw).hexdigest()[:12]
        fn=f'inline-{h}.{ext}'
        p=assets/fn
        if not p.exists(): p.write_bytes(raw)
        rel=f'assets/{fn}'
        data_map[full]=rel
        return rel
    html=DATA_RE.sub(repl_data,html)
    if 'assets/placeholder.jpg' not in html:
        placeholder.unlink(missing_ok=True)
    (theme_dir/'index.html').write_text(html,encoding='utf-8')
    return html,manifest

def optimize_theme_media_if_needed(root: Path):
    for p in root.rglob('*'):
        if not p.is_file() or p.suffix.lower() not in {'.jpg','.jpeg','.png','.webp'}:
            continue
        if 'brand-boards' in p.parts:
            continue
        if p.stat().st_size < 450_000:
            continue
        try:
            with Image.open(p) as im:
                im.load()
                w,h=im.size
                max_dim=2200
                if max(w,h)>max_dim:
                    scale=max_dim/max(w,h)
                    im=im.resize((max(1,int(w*scale)),max(1,int(h*scale))),Image.Resampling.LANCZOS)
                if im.mode not in ('RGB','L'):
                    bg=Image.new('RGB',im.size,'white')
                    if 'A' in im.getbands(): bg.paste(im,mask=im.getchannel('A'))
                    else: bg.paste(im.convert('RGB'))
                    im=bg
                elif im.mode!='RGB': im=im.convert('RGB')
                im.save(p,'JPEG',quality=86,optimize=True,progressive=True)
                if p.suffix.lower() not in {'.jpg','.jpeg'}:
                    new=p.with_suffix('.jpg')
                    if new != p:
                        p.replace(new)
                        idx=p.parents[1]/'index.html' if p.parent.name=='assets' else None
                        if idx and idx.exists():
                            t=idx.read_text(encoding='utf-8').replace(f'assets/{p.name}',f'assets/{new.name}')
                            idx.write_text(t,encoding='utf-8')
        except Exception:
            pass

def validate_static_structure(theme_meta, brand_meta):
    errors=[]
    if len(theme_meta)!=4: errors.append(f'Expected 4 themes, found {len(theme_meta)}')
    if len(brand_meta)!=6: errors.append(f'Expected 6 brand boards, found {len(brand_meta)}')
    expected_dirs={f'theme-{i:02d}' for i in range(1,5)}|{'brand-boards'}
    got={p.name for p in PKG.iterdir() if p.is_dir()}
    if got!=expected_dirs: errors.append(f'Unexpected package dirs: {sorted(got)}')
    forbidden_fragments=['file://','blob:','/mnt/','C:\\','__MACOSX','.DS_Store','node_modules']
    concept_ids=[]
    html_hashes=[]
    for folder,cid,title in THEMES:
        td=PKG/folder; idx=td/'index.html'; ad=td/'assets'
        if not idx.exists(): errors.append(f'{folder}: index.html missing'); continue
        if not ad.is_dir(): errors.append(f'{folder}: assets/ missing')
        text=idx.read_text(encoding='utf-8')
        for frag in forbidden_fragments:
            if frag in text: errors.append(f'{folder}: forbidden reference {frag}')
        if 'https://static.wixstatic.com' in text: errors.append(f'{folder}: external Wix dependency remains')
        if 'data:image/' in text: errors.append(f'{folder}: embedded image dependency remains')
        soup=BeautifulSoup(text,'html.parser')
        concepts=soup.select('section.concept')
        if len(concepts)!=1: errors.append(f'{folder}: expected exactly one website concept, found {len(concepts)}')
        elif concepts[0].get('id')!=cid: errors.append(f'{folder}: wrong concept {concepts[0].get("id")}')
        else: concept_ids.append(cid)
        if soup.select_one('.review-switcher'): errors.append(f'{folder}: internal theme switcher remains')
        for tag in soup.find_all(['img','script','source','video']):
            for attr in ('src','poster'):
                v=tag.get(attr)
                if not v: continue
                if v.startswith(('http://','https://','//','file:','blob:','/')):
                    errors.append(f'{folder}: non-relative asset {v[:120]}')
                elif v.startswith('data:'):
                    errors.append(f'{folder}: data asset remains')
                elif not v.startswith('#'):
                    fp=td/v
                    if not fp.exists(): errors.append(f'{folder}: missing local asset {v}')
        for m in re.finditer(r'url\(["\']?([^"\')]+)',text):
            v=m.group(1)
            if v.startswith(('http://','https://','//','file:','blob:','/','data:')):
                errors.append(f'{folder}: non-relative CSS asset {v[:120]}')
            elif v and not v.startswith('#'):
                fp=td/v
                if not fp.exists(): errors.append(f'{folder}: missing CSS asset {v}')
        html_hashes.append(hashlib.sha256(re.sub(r'\s+',' ',str(concepts[0]) if concepts else text).encode()).hexdigest())
    if len(set(concept_ids))!=4: errors.append('Theme concept IDs are not unique')
    if len(set(html_hashes))!=4: errors.append('Theme HTML structures are not distinct')
    all_files=[p for p in PKG.rglob('*') if p.is_file()]
    if len(all_files)>500: errors.append(f'File count exceeds 500: {len(all_files)}')
    for p in all_files:
        if p.stat().st_size>20*1024*1024: errors.append(f'Individual file >20MB: {p.relative_to(PKG)}')
        if p.name.startswith('.') or any(part.startswith('.') for part in p.relative_to(PKG).parts): errors.append(f'Hidden file: {p.relative_to(PKG)}')
        if p.suffix.lower() in {'.psd','.ai','.fig'}: errors.append(f'Source-design file included: {p.relative_to(PKG)}')
    expanded=sum(p.stat().st_size for p in all_files)
    if expanded>70*1024*1024: errors.append(f'Expanded size exceeds 70MB: {human(expanded)}')
    return errors,expanded,len(all_files)

def make_zip():
    ZIP_PATH.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP_PATH,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(PKG.rglob('*')):
            if p.is_file():
                arc=Path('client-presentation')/p.relative_to(PKG)
                z.write(p,arc.as_posix())

def validate_zip_extract():
    tmp=OUT_PARENT/'extract-check'
    shutil.rmtree(tmp,ignore_errors=True); tmp.mkdir(parents=True)
    with zipfile.ZipFile(ZIP_PATH) as z:
        z.extractall(tmp)
        names=z.namelist()
    top=tmp/'client-presentation'
    if not top.is_dir(): die('ZIP did not extract to one client-presentation/ root folder')
    if (top/'client-presentation').exists(): die('Extra nested wrapper folder detected')
    for folder,_,_ in THEMES:
        if not (top/folder/'index.html').exists(): die(f'Extract validation missing {folder}/index.html')
    return len(names)

def main():
    if not SOURCE.exists(): die(f'Missing source {SOURCE}')
    original=SOURCE.read_text(encoding='utf-8',errors='ignore')
    if 'id="pursuit"' not in original or 'id="strike"' not in original:
        die('Source does not look like the approved TightLinesFL combined review file.')
    shutil.rmtree(OUT_PARENT,ignore_errors=True)
    PKG.mkdir(parents=True)
    cache=OUT_PARENT/'download-cache'; cache.mkdir()
    brand_meta=extract_brand_boards(original)
    theme_meta=[]
    for folder,cid,title in THEMES:
        td=PKG/folder; td.mkdir(parents=True)
        html,manifest=standalone_html(original,cid,title,td,cache)
        theme_meta.append({'folder':folder,'concept':cid,'title':title,'assets_downloaded':len(manifest),'html_bytes':len(html.encode())})
    shutil.rmtree(cache,ignore_errors=True)
    errors,expanded,file_count=validate_static_structure(theme_meta,brand_meta)
    if errors:
        die('Static validation failed:\n- '+'\n- '.join(errors))
    make_zip()
    if ZIP_PATH.stat().st_size>35*1024*1024 or expanded>70*1024*1024:
        optimize_theme_media_if_needed(PKG)
        errors,expanded,file_count=validate_static_structure(theme_meta,brand_meta)
        if errors: die('Post-optimization validation failed:\n- '+'\n- '.join(errors))
        make_zip()
    zip_size=ZIP_PATH.stat().st_size
    if zip_size>35*1024*1024: die(f'ZIP exceeds 35MB: {human(zip_size)}')
    if expanded>70*1024*1024: die(f'Expanded package exceeds 70MB: {human(expanded)}')
    zip_entries=validate_zip_extract()
    report={
        'status':'STATIC_VALIDATION_PASSED',
        'website_themes':len(theme_meta),
        'brand_boards':len(brand_meta),
        'zip_bytes':zip_size,
        'zip_size':human(zip_size),
        'expanded_bytes':expanded,
        'expanded_size':human(expanded),
        'file_count':file_count,
        'zip_entries':zip_entries,
        'themes':theme_meta,
        'brand_board_details':brand_meta,
        'zip_file':ZIP_PATH.name,
    }
    REPORT_PATH.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    main()
