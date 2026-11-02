"""Create deterministic source distribution; never labels it a Windows binary."""
import hashlib
import json
from pathlib import Path
import zipfile

root=Path(__file__).resolve().parents[1]
out=root/'artifacts'; out.mkdir(exist_ok=True)
excluded={'.git','.transfer','incoming','recovered','artifacts','__pycache__','.build-venv','.build-work','.build-spec'}
files=[]
for p in sorted(root.rglob('*')):
    rel=p.relative_to(root)
    if not p.is_file() or any(part in excluded for part in rel.parts) or p.name.endswith(('.pyc','.exe')):
        continue
    if len(rel.parts)==1 and p.name.endswith('-tests.txt'):
        continue
    files.append((rel.as_posix(),p.read_bytes()))
manifest={'release':'0.4-stage1-source-candidate','windows_exe_included':False,
          'files':{name:hashlib.sha256(data).hexdigest() for name,data in files}}
files.append(('MANIFEST.json',(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n').encode()))
target=out/'PortableAI_v0.4_Stage1_Source_Tested.zip'
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
    for name,data in files:
        info=zipfile.ZipInfo('PortableAI/'+name,date_time=(2026,11,2,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED
        info.external_attr=0o644<<16
        archive.writestr(info,data)
    for folder in ['workspace','memory','config','logs','backups','data']:
        info=zipfile.ZipInfo('PortableAI/'+folder+'/',date_time=(2026,11,2,0,0,0))
        info.external_attr=(0o755<<16)|0x10
        archive.writestr(info,b'')
digest=hashlib.sha256(target.read_bytes()).hexdigest()
(target.with_suffix('.zip.sha256')).write_text(digest+'  '+target.name+'\n')
print(json.dumps({'file':str(target),'bytes':target.stat().st_size,'sha256':digest,'files':len(files)},indent=2))
