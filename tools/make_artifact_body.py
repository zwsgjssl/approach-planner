# claude.ai Artifact 公開用の本文(artifact_body.html)を生成する。
# <head>だけを消し、<header>は残す(build213〜215で<header>ごと消していた不具合の修正)。
# PWA用の manifest / apple-touch-icon のリンクは Artifact では使えないので外す。
import re, os, sys
root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) if os.path.basename(os.path.dirname(os.path.abspath(__file__)))=='tools' else os.path.dirname(os.path.abspath(__file__))
src = os.path.join(root, 'approach_planner.html')
out = sys.argv[1] if len(sys.argv)>1 else os.path.join(root, 'artifact_body.html')
h = open(src, encoding='utf-8').read()
h = re.sub(r'(?is)<!doctype[^>]*>|</?html(\s[^>]*)?>|<head(\s[^>]*)?>|</head>|<meta(\s[^>]*)?>|</?body(\s[^>]*)?>', '', h)
h = re.sub(r'(?i)<link rel="(manifest|apple-touch-icon)"[^>]*>\n?', '', h)
assert h.count('<header>')==1 and h.count('</header>')==1, 'header tag lost'
open(out, 'w', encoding='utf-8').write('<title>Approach Planner</title>\n\n'+h)
print('ok', out)
