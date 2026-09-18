import hashlib,io,json,stat,zipfile
from pathlib import Path
import pytest
from release_unpack import unpack_release,RELEASE_ROOT


def payload(extra=None):
    files={'VERSION.txt':b'9.7.5-NEXUS','Nexus_Update.sh':b'#!/bin/bash\nexit 0\n'}
    manifest={k:hashlib.sha256(v).hexdigest() for k,v in files.items()}
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        for k,v in files.items():z.writestr(RELEASE_ROOT+'/'+k,v)
        z.writestr(RELEASE_ROOT+'/MANIFEST_SHA256.json',json.dumps(manifest))
        if extra:extra(z)
    data=b.getvalue();return data,hashlib.sha256(data).hexdigest()


def test_extract_and_repeat_leave_existing_states_untouched(tmp_path):
    data,h=payload();root=unpack_release(data,tmp_path,h)
    (root/'crypto_positions.json').write_text('existing latest state')
    assert unpack_release(data,tmp_path,h)==root
    assert (root/'crypto_positions.json').read_text()=='existing latest state'
    assert (root/'Nexus_Update.sh').stat().st_mode & stat.S_IXUSR


def test_wrong_download_checksum_never_extracts(tmp_path):
    data,h=payload()
    with pytest.raises(ValueError,match='pruefsumme'):unpack_release(data,tmp_path,'0'*64)
    assert not (tmp_path/RELEASE_ROOT).exists()
    assert not list(tmp_path.glob('.nexus_entpacken_*'))


@pytest.mark.parametrize('bad',['../outside',RELEASE_ROOT+'/../outside','/absolute','other/root.py',RELEASE_ROOT+'/sub\\evil.py'])
def test_unsafe_archive_names_rejected_before_any_write(tmp_path,bad):
    data,h=payload(lambda z:z.writestr(bad,'bad'))
    with pytest.raises(ValueError):unpack_release(data,tmp_path,h)
    assert not (tmp_path/RELEASE_ROOT).exists()
    assert not list(tmp_path.glob('.nexus_entpacken_*'))


def test_modified_existing_code_is_not_overwritten(tmp_path):
    data,h=payload();root=unpack_release(data,tmp_path,h);(root/'VERSION.txt').write_text('modified')
    with pytest.raises(ValueError,match='veraendert'):unpack_release(data,tmp_path,h)
    assert (root/'VERSION.txt').read_text()=='modified'


def test_symlink_payload_is_rejected(tmp_path):
    def extra(z):
        info=zipfile.ZipInfo(RELEASE_ROOT+'/link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
        z.writestr(info,'/etc')
    data,h=payload(extra)
    with pytest.raises(ValueError):unpack_release(data,tmp_path,h)
    assert not (tmp_path/RELEASE_ROOT).exists()
    assert not list(tmp_path.glob('.nexus_entpacken_*'))


def test_unmanifested_file_is_not_extracted(tmp_path):
    data,h=payload(lambda z:z.writestr(RELEASE_ROOT+'/extra.py','malicious'))
    with pytest.raises(ValueError,match='widersprechen'):unpack_release(data,tmp_path,h)
