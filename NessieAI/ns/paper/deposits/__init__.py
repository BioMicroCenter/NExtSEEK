"""One adapter per repository: fetch(accession, fetcher) -> list[DepositRecord]."""
from NessieAI.ns.paper.deposits import ena, geo, pride, repos

ADAPTERS = {
    "geo": geo.fetch,
    "ena": ena.fetch,
    "pride": pride.fetch,
    "zenodo": repos.fetch_zenodo,
    "figshare": repos.fetch_figshare,
    "sdr": repos.fetch_sdr,
}
