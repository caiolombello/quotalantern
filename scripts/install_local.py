"""Source-only installer: no downloads, provider calls or credential copies."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys,time

parser=argparse.ArgumentParser()
parser.add_argument('--dry-run',action='store_true')
parser.add_argument('--enable-autostart',action='store_true')
args=parser.parse_args()
source=Path(__file__).resolve().parent.parent
home=Path.home()
base=home/'.local/share/codexbar-linux'
build=json.loads((source/'BUILD.json').read_text())
assert sys.version_info>=(3,12),'Python 3.12 or newer is required'
assert shutil.which('flock'),'Install util-linux flock from your distribution'
python=base/'venv/bin/python'
if not python.exists():python=Path(sys.executable)
probe="import gi\nfor n,v in [('Gtk','3.0'),('AyatanaAppIndicator3','0.1'),('Notify','0.7')]: gi.require_version(n,v); getattr(__import__('gi.repository',fromlist=[n]),n)"
subprocess.run([str(python),'-c',probe],check=True)
assert all(hashlib.sha256((source/p).read_bytes()).hexdigest()==h for p,h in build['file_sha256'].items()),'Source hash differs from BUILD.json'
release=base/'releases'/(build['version']+'-'+build['content_sha256'][:12])
print('QuotaLantern',build['version'])
print('Runtime:',python)
print('Version directory:',release)
print('Data and credentials stay in their existing storage. Providers are not started.')
if args.dry_run:sys.exit(0)
listing=subprocess.run(['ps','-u',str(os.getuid()),'-o','comm=,args='],capture_output=True,text=True,check=True)
assert not any(x.lstrip().startswith('python') and '-m codexbar_linux' in x for x in listing.stdout.splitlines()),'Close CodexBar/QuotaLantern before installing'
base.mkdir(parents=True,exist_ok=True,mode=0o700)
release.parent.mkdir(exist_ok=True)
if not release.exists():
    release.mkdir()
    for directory in ('codexbar_linux','assets'):
        shutil.copytree(source/directory,release/directory,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(source/'BUILD.json',release/'BUILD.json')
assert all(hashlib.sha256((release/p).read_bytes()).hexdigest()==h for p,h in build['file_sha256'].items()),'Existing version directory has changed'
backup=base/'migration-backups'/('install-'+str(time.time_ns()))
backup.mkdir(parents=True,mode=0o700)
binary=home/'.local/bin/codexbar-linux'
alias=home/'.local/bin/quotalantern'
desktop=home/'.local/share/applications/codexbar-linux.desktop'
autostart=home/'.config/autostart/codexbar-linux.desktop'
icon=home/'.local/share/icons/hicolor/scalable/apps/codexbar-linux.svg'
paths=[binary,alias,desktop,icon]+([autostart] if args.enable_autostart or autostart.exists() else [])
record=[]
for n,p in enumerate(paths):
    row={'path':str(p),'existed':p.exists(),'backup':str(backup/f'file-{n}')}
    if p.exists():shutil.copy2(p,row['backup'])
    record.append(row)
(backup/'restore.json').write_text(json.dumps(record,indent=2))
rollback='''from pathlib import Path
import json,shutil,os,subprocess
root=Path(__file__).resolve().parent
listing=subprocess.run(['ps','-u',str(os.getuid()),'-o','comm=,args='],capture_output=True,text=True,check=True)
assert not any(x.lstrip().startswith('python') and '-m codexbar_linux' in x for x in listing.stdout.splitlines()),'Close the app before rollback'
for row in json.loads((root/'restore.json').read_text()):
 p=Path(row['path'])
 if row['existed']:shutil.copy2(row['backup'],p)
 elif p.exists():p.rename(root/(p.name+'.retired'))
print('Entry points restored; data and versions remain recoverable.')
'''
(backup/'rollback.py').write_text(rollback)
def write(path,text,mode):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.quotalantern-tmp')
    tmp.write_text(text);tmp.chmod(mode);tmp.replace(path)
wrapper='''#!/usr/bin/env bash
set -euo pipefail
umask 077
RELEASE_DIR=RELEASE_LITERAL
PYTHON=PYTHON_LITERAL
export PYTHONPATH="${RELEASE_DIR}:${PYTHONPATH:-}"
export PYTHONNOUSERSITE=1
cd "$RELEASE_DIR"
case "${1:-}" in --check|--help|-h) exec "$PYTHON" -m codexbar_linux "$@" ;; esac
exec /usr/bin/flock -n "$HOME/.local/share/codexbar-linux/.instance.lock" "$PYTHON" -m codexbar_linux "$@"
'''
import shlex
wrapper=wrapper.replace('RELEASE_LITERAL',shlex.quote(str(release))).replace('PYTHON_LITERAL',shlex.quote(str(python)))
write(binary,wrapper,0o755)
write(alias,'#!/usr/bin/env bash\nexec "$HOME/.local/bin/codexbar-linux" "$@"\n',0o755)
entry=f'''[Desktop Entry]
Name=QuotaLantern
Comment=AI usage, quota and costs for Linux
Exec={binary}
TryExec={binary}
Icon=codexbar-linux
Type=Application
Categories=Utility;
Terminal=false
StartupNotify=false
X-KDE-StartupNotify=false
'''
if desktop.exists():
    text=desktop.read_text()
    text='\n'.join('Name=QuotaLantern' if line.startswith('Name=') else line for line in text.splitlines())+'\n'
    write(desktop,text,desktop.stat().st_mode&0o777)
else:write(desktop,entry,0o644)
icon.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile(release/'assets/icon.svg',icon);icon.chmod(0o644)
if autostart.exists():
    text='\n'.join('Name=QuotaLantern' if line.startswith('Name=') else line for line in autostart.read_text().splitlines())+'\n'
    write(autostart,text,autostart.stat().st_mode&0o777)
elif args.enable_autostart:write(autostart,entry,0o600)
config=home/'.config/codexbar-linux/config.json'
if not config.exists():
    config.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    write(config,json.dumps({'enabled_providers':['codex'],'hide_offline':True,'open_dashboard_on_start':False},indent=2)+'\n',0o600)
print('Check offline: quotalantern --check')
print('Open: quotalantern')
print('Rollback after closing the app: python3',backup/'rollback.py')
