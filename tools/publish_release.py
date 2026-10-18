"""Tag-driven publication. Draft uploads, independent digest GET, then publish."""
from pathlib import Path
import hashlib,json,os,subprocess,urllib.request,urllib.parse
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from rmaa_version import VERSION
def api(path,method='GET',value=None,raw=None):
    url=path if path.startswith('https://') else 'https://api.github.com'+path
    data=raw if raw is not None else json.dumps(value).encode() if value is not None else None
    request=urllib.request.Request(url,data=data,method=method,headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'RMAA-Plus-release','Content-Type':'application/octet-stream' if raw is not None else 'application/json'})
    with urllib.request.urlopen(request,timeout=180) as response:return json.load(response)
def main():
    repo='/repos/'+os.environ['GITHUB_REPOSITORY'];tag='v'+VERSION
    if os.environ.get('GITHUB_REF')!='refs/tags/'+tag:raise ValueError('Tag/version mismatch')
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    release=api(repo+'/releases','POST',{'tag_name':tag,'target_commitish':sha,'name':'RMAA+ '+VERSION,'draft':True,'body':(ROOT/'RELEASE_NOTES.md').read_text(encoding='utf8')+'\n\nSource commit: '+sha})
    receipts=[]
    for p in sorted((ROOT/'release').iterdir()):
        if not p.is_file():continue
        digest='sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()
        asset=api(release['upload_url'].split('{')[0]+'?name='+urllib.parse.quote(p.name),'POST',raw=p.read_bytes())
        stored=api(repo+'/releases/assets/'+str(asset['id']))
        if stored.get('digest')!=digest or stored['size']!=p.stat().st_size:raise ValueError('Stored asset digest mismatch: '+p.name)
        receipts.append({'name':p.name,'digest':digest,'bytes':p.stat().st_size})
    api(repo+'/releases/'+str(release['id']),'PATCH',{'draft':False,'make_latest':'true'})
    print(json.dumps({'url':release['html_url'],'source_commit':sha,'verified_assets':receipts},indent=2))
if __name__=='__main__':main()
