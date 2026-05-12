"""Async OAI-PMH client for harvesting arXiv metadata."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from xml.etree import ElementTree as ET

import httpx
from lxml import etree

from ..config import get_settings

logger = logging.getLogger(__name__)

OAI_NS = "http://www.openarchives.org/OAI/2.0/"
ARXIV_RAW_NS = "http://arxiv.org/OAI/arXivRaw/"

NS = {
    "oai": OAI_NS,
    "raw": ARXIV_RAW_NS,
}


class OaiPmhError(Exception):
    """Raised when the OAI-PMH endpoint returns an error."""


class OaiPmhClient:
    """Async client for arXiv OAI-PMH metadata harvesting."""

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._base_url = settings.oaipmh_base_url
        self._metadata_prefix = settings.oaipmh_metadata_prefix
        self._polite_delay = settings.oaipmh_polite_delay_seconds
        self._page_size = settings.oaipmh_page_size
        self._timeout = settings.oaipmh_request_timeout
        self._proxy = settings.http_proxy

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            proxy=self._proxy,
            follow_redirects=True,
            headers={"User-Agent": "arxiv-mirror-oaipmh/1.0"},
        )

    async def list_records(
        self,
        from_date: datetime | None = None,
        until: datetime | None = None,
        resumption_token: str | None = None,
    ) -> tuple[list[dict], str | None]:
        """Fetch one page of records from the OAI-PMH endpoint.

        Returns:
            (list_of_record_dicts, next_resumption_token_or_None)
        """
        params: dict[str, str] = {}
        if resumption_token:
            params["verb"] = "ListRecords"
            params["resumptionToken"] = resumption_token
        else:
            params["verb"] = "ListRecords"
            params["metadataPrefix"] = self._metadata_prefix
            if from_date is not None:
                params["from"] = from_date.strftime("%Y-%m-%d")
            if until is not None:
                params["until"] = until.strftime("%Y-%m-%d")

        logger.info(
            "OAI-PMH request: %s params=%s",
            self._base_url,
            params,
        )

        response = await self.client.get(self._base_url, params=params)
        response.raise_for_status()

        root = etree.fromstring(response.content)

        # Check for OAI-PMH errors
        error_el = root.find(f"{{{OAI_NS}}}error")
        if error_el is not None:
            code = error_el.get("code", "unknown")
            text = error_el.text or ""
            raise OaiPmhError(f"OAI-PMH error {code}: {text}")

        list_records_el = root.find(f"{{{OAI_NS}}}ListRecords")
        if list_records_el is None:
            return [], None

        records = []
        for record_el in list_records_el.findall(f"{{{OAI_NS}}}record"):
            rec = self._parse_record(record_el)
            if rec is not None:
                records.append(rec)

        # Extract resumption token
        token_el = list_records_el.find(f"{{{OAI_NS}}}resumptionToken")
        next_token: str | None = None
        if token_el is not None and token_el.text and token_el.text.strip():
            next_token = token_el.text.strip()

        return records, next_token

    def _parse_record(self, record_el: etree._Element) -> dict | None:
        """Parse a single <record> element into a dict."""
        header_el = record_el.find(f"{{{OAI_NS}}}header")
        if header_el is None:
            return None

        # Check if deleted
        status = header_el.get("status", "")
        if status == "deleted":
            identifier_el = header_el.find(f"{{{OAI_NS}}}identifier")
            raw_id = (
                identifier_el.text.strip() if identifier_el is not None and identifier_el.text else ""
            )
            return {
                "id": _strip_oai_prefix(raw_id),
                "deleted": True,
            }

        # Identifier
        identifier_el = header_el.find(f"{{{OAI_NS}}}identifier")
        raw_id = (
            identifier_el.text.strip() if identifier_el is not None and identifier_el.text else ""
        )
        arxiv_id = _strip_oai_prefix(raw_id)

        # Datestamp
        datestamp_el = header_el.find(f"{{{OAI_NS}}}datestamp")
        datestamp_str = (
            datestamp_el.text.strip() if datestamp_el is not None and datestamp_el.text else ""
        )
        oai_datestamp = _parse_oai_datetime(datestamp_str)

        # Metadata
        metadata_el = record_el.find(f"{{{OAI_NS}}}metadata")
        if metadata_el is None:
            return {
                "id": arxiv_id,
                "oai_datestamp": oai_datestamp,
                "deleted": False,
            }

        arxiv_el = metadata_el.find(f"{{{ARXIV_RAW_NS}}}arXivRaw")
        if arxiv_el is None:
            return {
                "id": arxiv_id,
                "oai_datestamp": oai_datestamp,
                "deleted": False,
            }

        title = _get_text(arxiv_el, f"{{{ARXIV_RAW_NS}}}title")
        abstract = _get_text(arxiv_el, f"{{{ARXIV_RAW_NS}}}abstract")
        doi = _get_text(arxiv_el, f"{{{ARXIV_RAW_NS}}}doi")
        categories_str = _get_text(arxiv_el, f"{{{ARXIV_RAW_NS}}}categories")
        comments = _get_text(arxiv_el, f"{{{ARXIV_RAW_NS}}}comments")
        journal_ref = _get_text(arxiv_el, f"{{{ARXIV_RAW_NS}}}journal-ref")
        license_str = _get_text(arxiv_el, f"{{{ARXIV_RAW_NS}}}license")

        categories = [c.strip() for c in categories_str.split() if c.strip()] if categories_str else []
        primary_category = categories[0] if categories else None

        # Authors
        authors: list[str] = []
        authors_el = arxiv_el.find(f"{{{ARXIV_RAW_NS}}}authors")
        if authors_el is not None:
            for author_el in authors_el.findall(f"{{{ARXIV_RAW_NS}}}author"):
                name = _build_author_name(author_el)
                if name:
                    authors.append(name)

        # Versions
        versions: list[dict] = []
        for version_el in arxiv_el.findall(f"{{{ARXIV_RAW_NS}}}version"):
            ver = _parse_version(version_el)
            versions.append(ver)

        # Raw XML for storage
        raw_xml = etree.tostring(record_el, encoding="unicode")

        return {
            "id": arxiv_id,
            "title": title or "",
            "authors": authors or None,
            "abstract": abstract,
            "doi": doi,
            "categories": categories or None,
            "primary_category": primary_category,
            "comments": comments,
            "journal_ref": journal_ref,
            "license": license_str,
            "oai_datestamp": oai_datestamp,
            "versions": versions,
            "raw_xml": raw_xml,
            "deleted": False,
        }

    async def harvest_all(
        self,
        from_date: datetime | None = None,
        until: datetime | None = None,
    ):
        """Async generator that yields pages of records with polite delays."""
        resumption_token: str | None = None
        page = 0

        while True:
            records, resumption_token = await self.list_records(
                from_date=from_date if page == 0 else None,
                until=until if page == 0 else None,
                resumption_token=resumption_token,
            )

            if not records:
                break

            yield records, resumption_token
            page += 1

            if resumption_token is None:
                break

            logger.info(
                "Harvest page %d done, sleeping %.1fs before next page",
                page,
                self._polite_delay,
            )
            await asyncio.sleep(self._polite_delay)

    async def close(self) -> None:
        await self.client.aclose()


def _strip_oai_prefix(identifier: str) -> str:
    """Strip 'oai:arXiv.org:' prefix from OAI identifier."""
    prefix = "oai:arXiv.org:"
    if identifier.startswith(prefix):
        return identifier[len(prefix):]
    return identifier


def _parse_oai_datetime(s: str) -> datetime | None:
    """Parse OAI-PMH datestamp string to datetime."""
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")) if "T" in s else datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _get_text(el: etree._Element, tag: str) -> str | None:
    """Get text content of a child element, or None."""
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _build_author_name(author_el: etree._Element) -> str | None:
    """Build author name string from <author> element.

    arXivRaw uses <keyname> and <forenames> sub-elements.
    """
    keyname = _get_text(author_el, f"{{{ARXIV_RAW_NS}}}keyname")
    forenames = _get_text(author_el, f"{{{ARXIV_RAW_NS}}}forenames")

    if keyname and forenames:
        return f"{forenames} {keyname}"
    if keyname:
        return keyname
    if forenames:
        return forenames
    return None


def _parse_version(version_el: etree._Element) -> dict:
    """Parse a <version> element."""
    version_str = version_el.get("version", "")
    version_number = 1
    if version_str.startswith("v"):
        try:
            version_number = int(version_str[1:])
        except ValueError:
            pass

    date_str = _get_text(version_el, f"{{{ARXIV_RAW_NS}}}date")
    version_date = None
    if date_str:
        # arXiv version dates are like "Tue, 1 Mar 2025 12:34:56 GMT"
        # or simplified ISO format
        for fmt in (
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%SZ",
        ):
            try:
                version_date = datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
                break
            except (ValueError, TypeError):
                continue
        if version_date is None:
            version_date = date_str

    size = _get_text(version_el, f"{{{ARXIV_RAW_NS}}}size")
    file_type = _get_text(version_el, f"{{{ARXIV_RAW_NS}}}source_type")

    return {
        "version_number": version_number,
        "date": version_date,
        "size": size,
        "source_type": file_type,
    }
