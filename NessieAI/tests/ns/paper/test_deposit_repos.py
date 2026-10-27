from NessieAI.ns.paper.deposits import ADAPTERS, repos
from NessieAI.tests.ns.paper.fakes import FakeResponse, fetcher, js, key

SDR_XML = b"""<publicObject><mods xmlns="http://www.loc.gov/mods/v3"><titleInfo><title>Slide-seq maps</title></titleInfo></mods>
<contentMetadata><resource><file id="puck 1.csv" size="42"><checksum type="sha1">x</checksum><checksum type="md5">abc</checksum></file></resource>
<resource><file id="readme.txt" size="7"/></resource></contentMetadata></publicObject>"""


def test_registry_covers_every_repo():
    assert set(ADAPTERS) == {"geo", "ena", "pride", "zenodo", "figshare", "sdr"}


def test_zenodo():
    f, _ = fetcher({key("https://zenodo.org/api/records/7654321"): js({
        "metadata": {"title": "Images", "publication_date": "2023-05-06", "description": "<p>tiffs</p>"},
        "files": [{"key": "a.tif", "size": 5, "checksum": "md5:123", "links": {"self": "https://zenodo.org/api/records/7654321/files/a.tif/content"}}]})})
    project, file = repos.fetch_zenodo("7654321", f)
    assert (project.kind, project.title, str(project.submitted)) == ("project", "Images", "2023-05-06")
    assert (file.record_id, file.files[0].checksum, file.files[0].checksum_type) == ("7654321/a.tif", "123", "md5")


def test_figshare():
    f, _ = fetcher({key("https://api.figshare.com/v2/articles/23456789"): js({
        "title": "Code", "published_date": "2024-01-02T00:00:00Z", "doi": "10.6084/m9.figshare.23456789",
        "files": [{"name": "x.zip", "size": 9, "computed_md5": "dd", "download_url": "https://ndownloader.figshare.com/files/1"}]})})
    project, file = repos.fetch_figshare("23456789", f)
    assert project.fields["doi"] == "10.6084/m9.figshare.23456789"
    assert (file.title, file.files[0].url, file.files[0].checksum) == ("x.zip", "https://ndownloader.figshare.com/files/1", "dd")


def test_sdr():
    f, _ = fetcher({key("https://purl.stanford.edu/bb123cd4567.xml"): FakeResponse(200, SDR_XML)})
    project, first, second = repos.fetch_sdr("bb123cd4567", f)
    assert project.title == "Slide-seq maps"
    assert first.files[0].url == "https://stacks.stanford.edu/file/druid:bb123cd4567/puck%201.csv"
    assert (first.files[0].size, first.files[0].checksum, first.files[0].checksum_type) == (42, "abc", "md5")
    assert second.files[0].checksum is None


def test_sdr_leaves_unpublished_files_out_and_names_them_on_the_project():
    xml = (b'<publicObject><mods xmlns="http://www.loc.gov/mods/v3"><titleInfo><title>Pucks</title></titleInfo></mods>'
           b'<contentMetadata><resource><file id="open.csv" size="3" publish="yes"/></resource>'
           b'<resource><file id="dark.csv" size="4" publish="no" shelve="yes"/></resource></contentMetadata></publicObject>')
    f, _ = fetcher({key("https://purl.stanford.edu/bb123cd4567.xml"): FakeResponse(200, xml)})
    records = repos.fetch_sdr("bb123cd4567", f)
    assert [r.record_id for r in records] == ["bb123cd4567", "bb123cd4567/open.csv"]
    assert records[0].fields["unpublished_files"] == "dark.csv"


def _zenodo_with(checksum):
    return {key("https://zenodo.org/api/records/7654321"): js({
        "metadata": {"title": "Images"},
        "files": [{"key": "a.tif", "size": 5, "checksum": checksum}]})}


def test_zenodo_never_labels_a_non_md5_digest_as_md5_and_keeps_the_algorithm():
    f, _ = fetcher(_zenodo_with("sha256:" + "e" * 64))
    _, file = repos.fetch_zenodo("7654321", f)
    assert (file.files[0].checksum, file.files[0].checksum_type) == (None, None)
    assert file.fields["checksum"] == "sha256:" + "e" * 64


def test_zenodo_types_a_sha1_digest_as_sha1():
    f, _ = fetcher(_zenodo_with("SHA1:" + "a" * 40))
    _, file = repos.fetch_zenodo("7654321", f)
    assert (file.files[0].checksum, file.files[0].checksum_type) == ("a" * 40, "sha1")
    assert file.fields == {}
