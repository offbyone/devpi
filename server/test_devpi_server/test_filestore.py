import os
import pytest
import py
from devpi_server.filestore import *
from devpi_common.url import URL

pytestmark = [pytest.mark.writetransaction]

zip_types = ("application/zip", "application/x-zip-compressed")

BytesIO = py.io.BytesIO

def getdigest(content, hash_type):
    return getattr(hashlib, hash_type)(content).hexdigest()


class TestFileStore:
    def test_maplink_deterministic(self, filestore, gen):
        link = gen.pypi_package_link("pytest-1.2.zip")
        entry1 = filestore.maplink(link)
        entry2 = filestore.maplink(link)
        assert entry1.relpath == entry2.relpath
        assert entry1.basename == "pytest-1.2.zip"
        assert py.builtin._istext(entry1.hash_spec)

    def test_maplink_splitmd5_issue78(self, filestore, gen):
        link = gen.pypi_package_link("pytest-1.2.zip")
        entry1 = filestore.maplink(link)
        # check md5 directory structure (issue78)
        parts = entry1.relpath.split("/")
        parent2 = parts[-2]
        parent1 = parts[-3]
        assert parent1 == link.md5[:3]
        assert parent2 == link.md5[3:16]

    def test_maplink_splithash_value_sha256(self, filestore, gen):
        link = URL("http://package.org/pytest-1.2.zip#sha256=12301238900980123")
        entry1 = filestore.maplink(link)
        parts = entry1.relpath.split("/")
        parent2 = parts[-2]
        parent1 = parts[-3]
        assert link.hash_algo == hashlib.sha256
        assert parent1 == link.hash_value[:3]
        assert parent2 == link.hash_value[3:16]

    def test_maplink(self, filestore, gen):
        link = gen.pypi_package_link("pytest-1.2.zip")
        entry1 = filestore.maplink(link)
        entry2 = filestore.maplink(link)
        assert not entry1.file_exists() and not entry2.file_exists()
        assert entry1 == entry2
        assert entry1.relpath.endswith("/pytest-1.2.zip")
        assert entry1.hash_spec == link.hash_spec

    def test_maplink_replaced_release_not_cached_yet(self, filestore, gen):
        link = gen.pypi_package_link("pytest-1.2.zip")
        entry1 = filestore.maplink(link)
        assert not entry1.file_exists()
        assert entry1.hash_spec and entry1.hash_spec == link.hash_spec
        newlink = gen.pypi_package_link("pytest-1.2.zip")
        entry2 = filestore.maplink(newlink)
        assert entry2.hash_spec and entry2.hash_spec == newlink.hash_spec

    def test_maplink_replaced_release_already_cached(self, filestore, gen):
        content = b'somedata'
        md5 = hashlib.md5(content).hexdigest()
        link = gen.pypi_package_link("pytest-1.2.zip", md5=md5)
        entry1 = filestore.maplink(link)
        # pseudo-write a release file
        entry1.file_set_content(content)
        assert entry1.file_exists()
        newlink = gen.pypi_package_link("pytest-1.2.zip")
        entry2 = filestore.maplink(newlink)
        assert entry2.hash_spec and entry2.hash_spec == newlink.hash_spec
        assert not entry2.file_exists()

    def test_file_delete(self, filestore, gen):
        link = gen.pypi_package_link("pytest-1.2.zip", md5=False)
        entry1 = filestore.maplink(link)
        entry1.file_set_content(b"")
        assert entry1.file_exists()
        entry1.file_delete()
        assert not entry1.file_exists()

    def test_maplink_egg(self, filestore, gen):
        link = gen.pypi_package_link("master#egg=pytest-dev", md5=False)
        entry1 = filestore.maplink(link)
        entry2 = filestore.maplink(link)
        assert entry1 == entry2
        assert not entry1 != entry2
        assert entry1.relpath.endswith("/master")
        assert entry1.eggfragment == "pytest-dev"
        assert not entry1.hash_spec
        assert entry1.url == link.url_nofrag
        assert entry1.eggfragment == "pytest-dev"

    def test_relpathentry(self, filestore, gen):
        link = gen.pypi_package_link("pytest-1.7.zip", md5=False)
        entry = filestore.maplink(link)
        assert entry.url == link.url
        assert not entry.file_exists()
        hashtype = "sha256"
        hash_value = getattr(hashlib, hashtype)(b"").hexdigest()
        entry.hash_spec = hash_spec = "%s=%s" %(hashtype, hash_value)
        assert not entry.file_exists()
        entry.file_set_content(b"")
        assert entry.file_exists()
        assert entry.url == link.url
        assert entry.hash_spec.endswith(hash_value)

        # reget
        entry = filestore.get_file_entry(entry.relpath)
        assert entry.file_exists()
        assert entry.url == link.url
        assert entry.hash_spec == hash_spec
        entry.delete()
        assert not entry.file_exists()

    def test_cache_remote_file(self, filestore, httpget, gen):
        link = gen.pypi_package_link("pytest-1.8.zip", md5=False)
        entry = filestore.maplink(link)
        assert not entry.hash_spec and not entry.file_exists()
        filestore.keyfs.restart_as_write_transaction()
        headers={"content-length": "3",
                 "last-modified": "Thu, 25 Nov 2010 20:00:27 GMT",
        }
        httpget.url2response[link.url] = dict(status_code=200,
                headers=headers, raw = BytesIO(b"123"))
        entry.cache_remote_file()
        rheaders = entry.gethttpheaders()
        assert rheaders["content-length"] == "3"
        assert rheaders["content-type"] in zip_types
        assert rheaders["last-modified"] == headers["last-modified"]
        bytes = entry.file_get_content()
        assert bytes == b"123"

        # reget entry and check about content
        filestore.keyfs.restart_as_write_transaction()
        entry = filestore.get_file_entry(entry.relpath)
        assert entry.file_exists()
        assert entry.hash_value == getdigest(bytes, entry.hashtype)
        assert entry.file_size() == 3
        rheaders = entry.gethttpheaders()
        assert entry.file_get_content() == b"123"

    @pytest.mark.parametrize("mode", ("commit", "rollback"))
    def test_file_tx(self, filestore, gen, mode):
        assert filestore.keyfs.tx
        link = gen.pypi_package_link("pytest-1.8.zip", md5=False)
        entry = filestore.maplink(link)
        assert not entry.file_exists()
        entry.file_set_content(b'123')
        assert entry.file_exists()
        assert not os.path.exists(entry._filepath)
        assert entry.file_get_content() == b'123'
        if mode == "commit":
            filestore.keyfs.restart_as_write_transaction()
            assert os.path.exists(entry._filepath)
            entry.file_delete()
            assert os.path.exists(entry._filepath)
            assert not entry.file_exists()
            filestore.keyfs.commit_transaction_in_thread()
            assert not os.path.exists(entry._filepath)
        elif mode == "rollback":
            filestore.keyfs.rollback_transaction_in_thread()
            assert not os.path.exists(entry._filepath)

    def test_iterfile_remote_no_headers(self, filestore, httpget, gen):
        link = gen.pypi_package_link("pytest-1.8.zip", md5=False)
        entry = filestore.maplink(link)
        assert not entry.hash_spec
        headers={}
        httpget.url2response[link.url] = dict(status_code=200,
                headers=headers, raw = BytesIO(b"123"))
        entry.cache_remote_file()
        rheaders = entry.gethttpheaders()
        assert rheaders["content-length"] == "3"
        assert rheaders["content-type"] in zip_types
        assert entry.file_get_content() == b"123"

    def test_iterfile_remote_error_size_mismatch(self, filestore, httpget, gen):
        link = gen.pypi_package_link("pytest-3.0.zip", md5=False)
        entry = filestore.maplink(link)
        assert not entry.hash_spec
        headers={"content-length": "3",
                 "last-modified": "Thu, 25 Nov 2010 20:00:27 GMT",
                 "content-type": "application/zip"}
        httpget.url2response[link.url] = dict(status_code=200,
                headers=headers, raw = BytesIO(b"1"))
        with pytest.raises(ValueError):
            entry.cache_remote_file()

    def test_iterfile_remote_nosize(self, filestore, httpget, gen):
        link = gen.pypi_package_link("pytest-3.0.zip", md5=False)
        entry = filestore.maplink(link)
        assert not entry.hash_spec
        headers={"last-modified": "Thu, 25 Nov 2010 20:00:27 GMT",
                 "content-length": None,}
        assert entry.file_size() is None
        httpget.url2response[link.url] = dict(status_code=200,
                headers=headers, raw=BytesIO(b"1"))
        entry.cache_remote_file()
        assert entry.file_get_content() == b"1"
        entry2 = filestore.get_file_entry(entry.relpath)
        assert entry2.file_size() == 1
        rheaders = entry.gethttpheaders()
        assert rheaders["last-modified"] == headers["last-modified"]
        assert rheaders["content-type"] in zip_types

    def test_iterfile_remote_error_md5(self, filestore, httpget, gen):
        link = gen.pypi_package_link("pytest-3.0.zip")
        entry = filestore.maplink(link)
        assert entry.hash_spec and entry.hash_spec == link.hash_spec
        headers={"content-length": "3",
                 "last-modified": "Thu, 25 Nov 2010 20:00:27 GMT",
                 "content-type": "application/zip"}
        httpget.url2response[link.url_nofrag] = dict(status_code=200,
                headers=headers, raw=BytesIO(b"123"))
        with pytest.raises(ValueError) as excinfo:
            entry.cache_remote_file()
        assert link.md5 in str(excinfo.value)
        assert not entry.file_exists()

    def test_iterfile_eggfragment(self, filestore, httpget, gen):
        link = gen.pypi_package_link("master#egg=pytest-dev", md5=False)
        entry = filestore.maplink(link)
        assert entry.eggfragment
        assert entry.url
        headers={"content-length": "4",
                 "last-modified": "Thu, 25 Nov 2010 20:00:27 GMT",
                 "content-type": "application/zip"}

        httpget.mockresponse(link.url_nofrag, headers=headers,
                             raw=BytesIO(b"1234"))
        entry.cache_remote_file()
        assert entry.file_get_content() == b"1234"
        httpget.mockresponse(entry.url, headers=headers, raw=BytesIO(b"3333"))
        entry.cache_remote_file()
        assert entry.file_get_content() == b"3333"

    def test_store_and_iter(self, filestore):
        content = b"hello"
        entry = filestore.store("user", "index", "something-1.0.zip", content)
        assert entry.hash_spec.endswith("="+getdigest(content, entry.hashtype))
        assert entry.file_exists()
        filestore.keyfs.restart_as_write_transaction()
        entry2 = filestore.get_file_entry(entry.relpath)
        assert entry2.basename == "something-1.0.zip"
        assert entry2.file_exists()
        assert entry2.hash_spec == entry.hash_spec
        assert entry2.last_modified
        assert entry2.file_get_content() == content
