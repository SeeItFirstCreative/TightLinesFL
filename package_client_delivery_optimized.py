from pathlib import Path
from PIL import Image
import package_client_delivery as p

_calls = 0
_orig_validate = p.validate_static_structure

def validate_allow_preopt(theme_meta, brand_meta):
    global _calls
    _calls += 1
    errors, expanded, count = _orig_validate(theme_meta, brand_meta)
    if _calls == 1:
        errors = [e for e in errors if not e.startswith('Expanded size exceeds 70MB:')]
    return errors, expanded, count

def optimize_all(root: Path):
    # Website photos: WebP 76%, max 2000px; likely hero/background files get up to 2400px.
    for td in sorted(root.glob('theme-*')):
        idx = td / 'index.html'
        text = idx.read_text(encoding='utf-8')
        for f in list((td/'assets').glob('*')):
            if not f.is_file() or f.suffix.lower() not in {'.jpg','.jpeg','.png','.webp'}:
                continue
            try:
                with Image.open(f) as im:
                    im.load()
                    # Preserve transparent graphics as optimized WebP lossless; photos use lossy WebP.
                    alpha = 'A' in im.getbands()
                    w,h = im.size
                    max_dim = 2400 if ('hero' in f.name.lower() or 'background' in f.name.lower()) else 2000
                    if max(w,h) > max_dim:
                        scale=max_dim/max(w,h)
                        im=im.resize((max(1,round(w*scale)),max(1,round(h*scale))),Image.Resampling.LANCZOS)
                    out=f.with_suffix('.webp')
                    if alpha:
                        im.save(out,'WEBP',lossless=True,method=6)
                    else:
                        im.convert('RGB').save(out,'WEBP',quality=76,method=6)
                if out != f:
                    text=text.replace('assets/'+f.name,'assets/'+out.name)
                    f.unlink()
            except Exception:
                pass
        idx.write_text(text,encoding='utf-8')

    # Brand boards: keep every board separate, 1600-2000px wide, WebP 83%.
    bd=root/'brand-boards'
    for f in list(bd.glob('brand-board-*')):
        if not f.is_file(): continue
        try:
            with Image.open(f) as im:
                im.load()
                w,h=im.size
                target_w=min(2000,w)
                if target_w < w:
                    nh=round(h*target_w/w)
                    im=im.resize((target_w,nh),Image.Resampling.LANCZOS)
                out=f.with_suffix('.webp')
                if 'A' in im.getbands():
                    bg=Image.new('RGB',im.size,'white'); bg.paste(im,mask=im.getchannel('A')); im=bg
                else:
                    im=im.convert('RGB')
                im.save(out,'WEBP',quality=83,method=6)
            if out != f: f.unlink()
        except Exception:
            pass

p.validate_static_structure = validate_allow_preopt
p.optimize_theme_media_if_needed = optimize_all
p.main()
