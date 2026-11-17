"""Acquire the official Ubuntu image locally, never create an ESXi VM."""
import hashlib,json,os,subprocess,urllib.request
from pathlib import Path

def main():
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args()
    if not Path("/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg").exists():
        raise RuntimeError("Ubuntu cloud-image GPG keyring required before downloading")
    root=Path(__file__).resolve().parents[1]
    folder=root/'data/os-images/ubuntu-24.04';folder.mkdir(parents=True,mode=0o700,exist_ok=True)
    base='https://cloud-images.ubuntu.com/releases/noble/release/'
    name='ubuntu-24.04-server-cloudimg-amd64.ova'
    def fetch(url):
        with urllib.request.urlopen(url,timeout=60) as source:return source.read()
    sums=fetch(base+'SHA256SUMS')
    lines=[line.split() for line in sums.decode().splitlines() if line.split()[-1].lstrip('*')==name]
    assert len(lines)==1
    expected=lines[0][0];assert len(expected)==64
    image=folder/name
    if not image.exists():
        partial=folder/(name+'.partial-'+str(os.getpid()))
        digest=hashlib.sha256()
        try:
            with urllib.request.urlopen(base+name,timeout=60) as response,partial.open('xb') as dest:
                while chunk:=response.read(1024*1024):dest.write(chunk);digest.update(chunk)
            assert digest.hexdigest()==expected,'Official checksum mismatch'
            partial.chmod(0o600);os.replace(partial,image)
        finally:
            if partial.exists():partial.unlink()
    else:
        assert hashlib.file_digest(image.open('rb'),'sha256').hexdigest()==expected
    print('Official Ubuntu OVA checksum verified',flush=True)
    verified=False
    keyring=Path('/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg')
    if keyring.exists():
        (folder/'SHA256SUMS').write_bytes(sums)
        (folder/'SHA256SUMS.gpg').write_bytes(fetch(base+'SHA256SUMS.gpg'))
        p=subprocess.run(['gpgv','--keyring',str(keyring),str(folder/'SHA256SUMS.gpg'),str(folder/'SHA256SUMS')],capture_output=True)
        assert p.returncode==0,'Official checksum signature failed'
        verified=True
    verify=subprocess.run(['/usr/bin/ovftool','--verifyOnly',str(image)],capture_output=True,text=True,timeout=180)
    assert verify.returncode==0,verify.stdout[-3000:]+verify.stderr[-1000:]
    metadata={'image_id':'ubuntu-24.04','path':str(image),'sha256':expected,'source_url':base+name,'checksums_url':base+'SHA256SUMS','signature_verified':verified,'ovftool_verified':True}
    (folder/'image.json').write_text(json.dumps(metadata,indent=2)+'\n');(folder/'image.json').chmod(0o600)
    print(json.dumps(metadata,indent=2),flush=True)

if __name__ == "__main__":
    main()
