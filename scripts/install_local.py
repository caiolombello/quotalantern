"""Source-only installer: no downloads, provider calls or credential copies."""
from pathlib import Path
import argparse,hashlib,json,os,platform,shutil,subprocess,sys,time

parser=argparse.ArgumentParser()
parser.add_argument('--dry-run',action='store_true')
parser.add_argument('--enable-autostart',action='store_true')
args=parser.parse_args()
source=Path(__file__).resolve().parent.parent
home=Path.home()
base=home/'.local/share/codexbar-linux'
build=json.loads((source/'BUILD.json').read_text())
assert sys.version_info>=(3,12),'Python 3.12 or newer is required'
python=base/'venv/bin/python'
if not python.exists():python=Path(sys.executable)
assert all(hashlib.sha256((source/p).read_bytes()).hexdigest()==h for p,h in build['file_sha256'].items()),'Source hash differs from BUILD.json'
probe="""import json,sys
from codexbar_linux.gtk_runtime import runtime_status
status=runtime_status()
print(json.dumps(status))
sys.exit(0 if all(status[k] for k in ('gtk3','appindicator','notify')) else 1)
"""
checked=subprocess.run([str(python),'-c',probe],cwd=source,
                       env={**os.environ,'PYTHONNOUSERSITE':'1'},capture_output=True,text=True)
if checked.returncode:
    try: distro=platform.freedesktop_os_release()
    except OSError: distro={}
    family={distro.get('ID',''),*distro.get('ID_LIKE','').split()}
    fedora='sudo dnf install python3-gobject gtk3 libayatana-appindicator-gtk3 libnotify'
    debian='sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7'
    hint=fedora if 'fedora' in family else debian if family & {'debian','ubuntu'} else f'Fedora: {fedora}\nDebian/Ubuntu: {debian}'
    sys.exit(f'Desktop dependencies unavailable for {python}.\n{checked.stdout.strip()}\n{checked.stderr.strip()}\nInstall system packages, then rerun install.sh:\n{hint}\nThe selected Python must have access to system GI bindings.')
runtime=json.loads(checked.stdout)
release=base/'releases'/(build['version']+'-'+build['content_sha256'][:12])
print('QuotaLantern',build['version'])
print('Runtime:',python)
print('Tray backend:',runtime['appindicator_backend'])
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
exec "$PYTHON" -m codexbar_linux.launcher "$@"
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
    replacements={'Name':'QuotaLantern'}
    if args.enable_autostart:replacements.update({'Hidden':'false','X-GNOME-Autostart-enabled':'true'})
    lines=[]
    for line in autostart.read_text().splitlines():
        key,separator,value=line.partition('=')
        lines.append(key.strip()+'='+replacements[key.strip()] if separator and key.strip() in replacements else line)
    text='\n'.join(lines)+'\n'
    write(autostart,text,autostart.stat().st_mode&0o777)
elif args.enable_autostart:write(autostart,entry,0o600)
config=home/'.config/codexbar-linux/config.json'
if not config.exists():
    config.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    write(config,json.dumps({'enabled_providers':['codex'],'hide_offline':True,'open_dashboard_on_start':False},indent=2)+'\n',0o600)
command='quotalantern' if str(alias.parent) in os.get_exec_path() else str(alias)
print('Check offline:',command,'--check')
print('Open:',command)
if command != 'quotalantern':print('For the short command, add',alias.parent,'to PATH or open a new login session.')
print('Rollback after closing the app: python3',backup/'rollback.py')
