from dataclasses import dataclass


@dataclass
class DocParams:
    doc_sector: int
    doc_year: str
    doc_number: str
    doc_type: str


@dataclass
class Record:
    document: str
    heading: str
    section: str
    article: str
    article_subtitle: str
    text: str
    ref: str
