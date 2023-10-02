from typing import Literal
import requests
from bs4 import BeautifulSoup, Tag
import pandas as pd
import re

from . import schemas


class ETL:
    BASE_EURLEX_URL = "https://eur-lex.europa.eu/legal-content/PT/TXT/HTML/?uri=CELEX:"
    DOC_TYPES_DESCRIPTORS = {
        "L": ["directive", "diretiva", "directiva"],
        "R": ["regulation", "regulamento"],
    }
    CELEX_DIGIT_COUNT = 4
    CSS_CLASSES_TO_IGNORE = ["signatory", "note"]

    # Patterns to identify an isolated marker, i.e., no sentence follows it ============= #
    ISOLATED_MARKER_PATTERNS = [
        "([0-9]+\.)+$",  # 1.1.
        "([0-9]+\.)+\-[a-z]\.$",  # 1.1.-a.
        "([0-9]+\.)+\-[a-z]\)$",  # 1.1.-a)
        "\([0-9]+\)$",  # (12)
        "[0-9]+\)$",  # 12)
        "\([a-z]+\)$",  # (a) OR (A)
        "[a-z]+\)$",  # a) OR A)
        "[0-9]+\.$",  # 1.
        "[a-z]+\.$",  # a.
        "—$",  # —
        "[0-9]+\-[a-z]\)$",  # 1-a)
        "[0-9]+\-[a-z]\.$",  # 1-a.
        "[a-z]+\-[a-z]\)$",  # a-a)
    ]

    # Patterns to identify a sentence's starting marker ================================= #
    STARTING_MARKER_PATTERNS = [
        "[0-9]+\.[0-9]+\-[a-z]\.",  # 1.2-a.  <sentence>
        "[0-9]+\.[0-9]+\-[a-z]\)",  # 1.2-a)  <sentence>
        "[0-9]+\-[a-z]\.",  # 4-a.  <sentence>
        "[0-9]+\-[a-z]\)",  # 4-a)  <sentence>
        "[0-9]+\-[a-z]\s+",  # 4-A  <sentence>
        "[0-9]+[a-z]\.\s+",  # 4A.  <sentence>
        "([0-9]+\.)+",  # 1.2.  <sentence>
        "\([0-9]+\)",  # (12)  <sentence>
        "[0-9]+\)",  # 12)  <sentence>
        "\([a-z]+\)",  # (a)  <sentence>
        "[a-z]+\)",  # a)  <sentence>
        "[0-9]+\.",  # 12.  <sentence>
        "[0-9]+\s+",  # 12  <sentence>
        "[a-z]+\.",  # a.  <sentence>
    ]

    @property
    def isolated_marker_pattern(self) -> re.Pattern:
        pattern_ = self._build_patterns_list(ETL.ISOLATED_MARKER_PATTERNS)
        return re.compile(pattern_, flags=re.IGNORECASE)

    @property
    def starting_marker_pattern(self) -> re.Pattern:
        pattern_ = self._build_patterns_list(ETL.STARTING_MARKER_PATTERNS)
        return re.compile(pattern_, flags=re.IGNORECASE)

    def _build_patterns_list(self, patterns_list: list[str]) -> str:
        pattern_ = ""
        for sub_pattern_ in patterns_list:
            pattern_ += f"|^{sub_pattern_}"
            pattern_ += (
                f"|^«{sub_pattern_}"  # Duplicate pattern, adding a leading "«" symbol
            )
        pattern_ = pattern_[1:]  # Remove leading "|"
        return pattern_

    @property
    def css_classes_to_ignore(self) -> list[str]:
        return [
            *ETL.CSS_CLASSES_TO_IGNORE,
            *[f"oj-{name}" for name in ETL.CSS_CLASSES_TO_IGNORE],
        ]

    def run_routine(self, documents_names: list[str]) -> pd.DataFrame:
        docs_params = self.build_docs_params(documents_names)

        dfs_list = []
        for i, doc_params in enumerate(docs_params):
            url = self.build_url(**doc_params)
            print(url)

            response = requests.get(url)
            assert response.status_code == 200, "The HTTP request failed."

            html = BeautifulSoup(response.content.decode(), "html.parser")
            html_passages = html.find_all("p")
            records = self.parse_html_passages(documents_names[i], html_passages)

            df_ = pd.DataFrame(data=records)
            df_ = df_.drop(
                index=df_[(df_.text == "") & (df_.ref == "")].index
            ).reset_index(drop=True)
            dfs_list.append(df_)

        df = pd.concat(dfs_list)
        df = df.reset_index(names="doc_id")
        df = df.reset_index(names="source_id")
        return df

    def build_docs_params(self, documents: list[str]) -> list[schemas.DocParams]:
        docs_params = []
        for doc in documents:
            split_name = doc.split()
            doc_nums = split_name[-1].split("/")

            doc_type = None
            doc_type_alias = split_name[0].strip().lower()
            for doc_type_key, doc_type_aliases in ETL.DOC_TYPES_DESCRIPTORS.items():
                if doc_type_alias in doc_type_aliases:
                    doc_type = doc_type_key
                    break

            assert doc_type, "Doc type's alias not found in existing mapping."

            docs_params.append(
                dict(
                    doc_sector=3,
                    doc_year=doc_nums[0],
                    doc_number=doc_nums[1],
                    doc_type=doc_type,
                )
            )
        return docs_params

    def build_url(
        self,
        doc_year: int,
        doc_number: int,
        doc_sector: int = 3,
        doc_type: Literal["L", "R"] = "L",
    ) -> str:
        doc_number_proxy = str(doc_number)
        prefix_zeros = ""
        for _ in range(ETL.CELEX_DIGIT_COUNT - len(doc_number_proxy)):
            prefix_zeros += "0"

        doc_number_proxy = f"{prefix_zeros}{doc_number_proxy}"
        return (
            f"{ETL.BASE_EURLEX_URL}{doc_sector}{doc_year}{doc_type}{doc_number_proxy}"
        )

    def parse_html_passages(
        self, document_name: str, html_passages: list[Tag]
    ) -> list[schemas.Record]:
        # recover_heading = False
        heading = ""
        section = ""
        article = ""
        article_subtitle = ""
        ref = ""
        prev_marker = False
        records = []

        # Loop to retrieve the first 'normal' passage
        for i, tag in enumerate(html_passages):
            if tag["class"][0] in ["normal", "oj-normal"]:
                break

        for tag in html_passages[i:]:
            assert type(tag["class"]) == list, "The CSS class attr is not a list"

            # For now, simply notify a passage has more than one CSS class.
            # Nonetheless, the conditional ignores additional classes.
            if len(tag["class"]) != 1:
                print(tag["class"])

            if tag["class"][0] in self.css_classes_to_ignore:
                continue

            text = tag.text.strip()
            text = re.sub(pattern="(\xa0)+", repl=" ", string=text)

            # if recover_heading and (tag["class"][0].startswith("doc-ti") or tag["class"][0].startswith("oj-doc-ti")):
            if tag["class"][0].startswith("doc-ti") or tag["class"][0].startswith(
                "oj-doc-ti"
            ):
                heading = text
                section = ""
                article = ""
                article_subtitle = ""
                ref = ""

            # if tag["class"][0] in "oj-ti-section":
            if tag["class"][0].startswith("ti-section") or tag["class"][0].startswith(
                "oj-ti-section"
            ):
                section = text
                article = ""
                article_subtitle = ""
                ref = ""

            elif tag["class"][0] in ["ti-art", "oj-ti-art"]:
                article = text
                article_subtitle = ""
                ref = ""

            elif tag["class"][0] in ["sti-art", "oj-sti-art"]:
                article_subtitle = text

            elif tag["class"][0] in ["normal", "oj-normal"]:
                if self.isolated_marker_pattern.match(text):
                    ref = text
                    prev_marker = True
                    continue

                elif marker_match := self.starting_marker_pattern.match(text):
                    ref = marker_match.group()
                    text = text.replace(ref, "").strip()

                elif not prev_marker:
                    ref = ""

                records.append(
                    {
                        "document": document_name,
                        "heading": heading,
                        "section": section,
                        "article": article,
                        "article_subtitle": article_subtitle,
                        "text": text,
                        "ref": ref,
                    }
                )
                prev_marker = False

        return records


if __name__ == "__main__":
    documents_names = [
        "Diretiva (UE) 2015/1535",
        "Diretiva (UE) 2018/645",
        "Diretiva (UE) 2019/2161",
        "Diretiva (UE) 2019/713",
        "Diretiva (UE) 2019/770",
        "Diretiva (UE) 2019/771",
        "Diretiva Delegada (UE) 2020/1687",
        "Diretiva Delegada (UE) 2021/1206",
        "Diretiva Delegada (UE) 2019/369",
        "Diretiva de Execução (UE) 2019/68",
        "Diretiva de Execução (UE) 2019/69",
        "Regulamento (UE) 2016/679",
        "Diretiva (UE) 2019/1937",
        "Diretiva (UE) 2018/1673",
        "Diretiva (UE) 2018/2002",
        "Diretiva (UE) 2019/1258",
        "Diretiva (UE) 2019/2177",
        "Diretiva (UE) 2019/692",
        "Diretiva (UE) 2018/645",
    ]

    etl = ETL()
    df = etl.run_routine(documents_names)
    print(df.sample(n=5))
