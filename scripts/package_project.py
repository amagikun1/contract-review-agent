"""白名单打包；不包含本机密钥、运行数据和依赖目录。"""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root=Path(__file__).resolve().parents[1]
target=root.parent/'contract-review-delivery.zip'
roots=['backend','frontend','scripts','samples','docs','README.md','ARCHITECTURE.md','pytest.ini','.env.example','.gitignore']
excluded={'.env','node_modules','.venv','venv','__pycache__','.pytest_cache','dist','.git','data'}
files=[]
for name in roots:
    path=root/name
    for file in ([path] if path.is_file() else path.rglob('*')):
        if file.is_file() and not excluded.intersection(file.relative_to(root).parts) and file.suffix not in {'.pyc','.log','.tsbuildinfo'}:
            files.append(file)
with ZipFile(target,'w',ZIP_DEFLATED) as archive:
    for file in sorted(files): archive.write(file,file.relative_to(root).as_posix())
with ZipFile(target) as archive:
    assert not any(set(p.split('/')) & (excluded|{'.env'}) for p in archive.namelist())
print(f'Created {target} ({len(files)} files, {target.stat().st_size:,} bytes)')
