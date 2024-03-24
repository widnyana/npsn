#!/usr/bin/env python
import json
import os.path

import urllib3
from pathlib import Path
from typing import List
import requests
from lxml.html import HtmlElement
from pydantic import BaseModel, RootModel
from pydantic.deprecated.json import pydantic_encoder
from lxml import html

urllib3.disable_warnings()

default_headers = {
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}

sess = requests.session()
sess.verify = False

BASEURL = "https://referensi.data.kemdikbud.go.id/pendidikan/dikdas"
LINES = {"formal": "jf", "non-formal": "jn"}
KINDS = {"SD": "5", "SMP": "6", "MI": "9", "MTs": "10", "SMPTK": "36", "SDTK": "38", "SPK SD": "53", "SPK SMP": "54",
         "Adi W P": "58", "Madyama W P": "59", "Mula Dhammasekha": "62", "Muda Dhammasekha": "63", "PDF Ula": "67",
         "PDF Wustha": "68", "SPM Ula": "70", "SPM Wustha": "71", }

out_provinces = "out/provinces.json"
out_cities = "out/cities.json"
out_districs = "out/districts.json"
out_npsns = "out/npsns.json"


class Province(BaseModel):
    id: str
    name: str
    url: str


class City(BaseModel):
    id: str
    name: str
    url: str
    province: Province


class District(BaseModel):
    id: str
    name: str
    url: str
    city: City


class SatuanPendidikan(BaseModel):
    npsn: str
    url: str
    source_url: str
    alamat: str
    kelurahan: str
    jalur: str
    bentuk: str
    status: str
    parent: District


class URLPattern(BaseModel):
    kind: str
    kind_id: str
    line: str
    line_code: str
    pattern: str


Provinces = RootModel[List[Province]]
Cities = RootModel[List[City]]
Districs = RootModel[List[District]]


def _xpath_all(element: HtmlElement, rule: str) -> list[str]:
    raw = element.xpath(rule)
    if raw:
        return [f"{r}".strip() for r in raw]
    return []


def _xpath_first_entry(element: HtmlElement, rule: str) -> str:
    raw = _xpath_all(element, rule)
    if raw:
        return f"{raw[0]}".strip()

    return ""


def _get_id_from_url(url: str):
    """sample: https://referensi.data.kemdikbud.go.id/pendidikan/dikdas/010000/1"""

    raw = url.strip().split("/")
    if not raw:
        return None
    return raw[-2]


def npsn_url_pattern_builder() -> list[URLPattern]:
    stacks = []
    for _l, _lc in LINES.items():
        for _k, _ki in KINDS.items():
            stacks.append(URLPattern(kind=_k, kind_id=_ki, line=_l, line_code=_lc, pattern=f"{_lc}/{_ki}/all"))

    return stacks


def process_province():
    res = sess.get(BASEURL, headers=default_headers, verify=False)
    if res.status_code != 200:
        return None

    stacks = []
    provinces: list[HtmlElement] = html.fromstring(res.text).xpath("//table/tbody/tr")
    for p in provinces:
        _url: str = _xpath_first_entry(p, "td/a/@href")
        if not _url:
            continue

        province_name = _xpath_first_entry(p, "td/a//text()")
        province = Province(id=_get_id_from_url(_url), url=_url, name=province_name)
        stacks.append(province)

    with open(out_provinces, 'w') as f:
        f.write(json.dumps(stacks, default=pydantic_encoder, indent=2))


def process_cities():
    provinces = Provinces.parse_file(out_provinces)

    stacks = []
    for p in provinces.root:
        resp = sess.get(p.url)

        if resp.status_code != 200:
            continue

        cities = html.fromstring(resp.text).xpath("//table/tbody/tr")
        for c in cities:
            _url: str = _xpath_first_entry(c, "td/a/@href")
            if not _url:
                continue

            name = _xpath_first_entry(c, "td/a//text()")
            city = City(id=_get_id_from_url(_url), url=_url, name=name, province=p)
            stacks.append(city)

    with open(out_cities, 'w') as f:
        f.write(json.dumps(stacks, default=pydantic_encoder, indent=2))


def process_districts():
    cities = Cities.parse_file(out_cities)

    stacks = []
    for c in cities.root:
        print(f"processing districs in url: {c.url}")
        resp = sess.get(c.url)

        if resp.status_code != 200:
            continue

        districs = html.fromstring(resp.text).xpath("//table/tbody/tr")
        for d in districs:
            _url: str = _xpath_first_entry(d, "td/a/@href")
            if not _url:
                continue

            name = _xpath_first_entry(d, "td/a//text()")
            district = District(id=_get_id_from_url(_url), url=_url, name=name, city=c)
            stacks.append(district)

    with open(out_districs, 'w') as f:
        f.write(json.dumps(stacks, default=pydantic_encoder, indent=2))


def process_npsns():
    # pattern: https://referensi.data.kemdikbud.go.id/pendidikan/dikdas/016002/3/<LINE_CODE>/<KIND_ID>/<STATUS_CODE>
    districs = Districs.parse_file(out_districs)
    patterns = npsn_url_pattern_builder()
    stacks = []

    # FIXME: part belows still broken, and causing duplicated data being dumped into the output file.
    #        can be solved by examining the output from xhr request, and decide another request should
    #        be made or not. perhaps `npsn_url_pattern_builder()` need to be refactored too, not sure.
    for d in districs.root:
        for patt in patterns:
            _url = os.path.join(d.url, patt.pattern)

            resp = sess.get(_url)
            if resp.status_code != 200:
                continue

            raws = html.fromstring(resp.text).xpath("//table/tbody/tr")
            for r in raws:
                _, npsn, name, alamat, kelurahan, status = [x.strip() for x in _xpath_all(r, "td//text()")]
                sp = SatuanPendidikan(
                    npsn=npsn,
                    url=f"https://referensi.data.kemdikbud.go.id/pendidikan/npsn/{npsn}",
                    source_url=_url,
                    alamat=alamat,
                    kelurahan=kelurahan,
                    jalur=patt.line,
                    bentuk=patt.kind,
                    status=status,
                    parent=d
                )

                stacks.append(sp)

        # TODO: remove this debugging line
        print(json.dumps(stacks, default=pydantic_encoder, indent=2))
        break

    with open(out_npsns, 'w') as f:
        f.write(json.dumps(stacks, default=pydantic_encoder, indent=2))


def fetch():
    outdir = Path("out")
    if not outdir.exists():
        outdir.mkdir(parents=True)

    if not Path(out_provinces).exists():
        process_province()

    if not Path(out_cities).exists():
        process_cities()

    if not Path(out_districs).exists():
        process_districts()

    if not Path(out_npsns).exists():
        process_npsns()


if __name__ == "__main__":
    fetch()
