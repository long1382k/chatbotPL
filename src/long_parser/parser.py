import json
import os
import re
import subprocess
import csv
from docx import Document

from long_parser.paths import ARTIFACTS_DIR, CONVERTED_DIR, DATA_INPUT_DIR


def convert_doc_to_docx(doc_path, out_dir):
    """Converts a .doc file to .docx using LibreOffice."""
    try:
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)

        cmd = [
            "libreoffice",
            "--headless",
            "--convert-to",
            "docx",
            "--outdir",
            out_dir,
            doc_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"LibreOffice conversion failed: {result.stderr}")

        basename = os.path.basename(doc_path)
        docx_name = os.path.splitext(basename)[0] + ".docx"
        expected_docx = os.path.join(out_dir, docx_name)
        if not os.path.exists(expected_docx):
            matches = [
                f
                for f in os.listdir(out_dir)
                if f.startswith(os.path.splitext(basename)[0]) and f.endswith(".docx")
            ]
            if matches:
                expected_docx = os.path.join(out_dir, matches[0])
            else:
                raise Exception(f"Converted file {docx_name} not found in {out_dir}")
        return expected_docx
    except Exception as e:
        raise Exception(f"Conversion error: {str(e)}")


def load_metadata(csv_path):
    metadata = {}
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name_docx = row.get("name_docx", "").strip()
            if name_docx:
                metadata[name_docx] = row
    return metadata


def parse_docx(docx_path, doc_name):
    doc = Document(docx_path)

    re_chuong = re.compile(
        r"^(CHƯƠNG|Chương)\s+([IVXLCDM]+)(?:[\.\s:](.*))?$", re.IGNORECASE
    )
    re_dieu = re.compile(r"^Điều\s+(\d+)(?:[\.\s:](.*))?$", re.IGNORECASE)
    re_khoan = re.compile(r"^(\d+)\.\s+(.*)$")
    re_diem = re.compile(r"^([a-z])\)\s+(.*)$")

    root = {"document_name": doc_name, "title": "", "children": [], "content": []}

    current_chuong = None
    current_dieu = None
    current_khoan = None

    first_level_found = False

    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    i = 0
    while i < len(paragraphs):
        text = paragraphs[i]

        m_chuong = re_chuong.match(text)
        m_dieu = re_dieu.match(text)
        m_khoan = re_khoan.match(text)
        m_diem = re_diem.match(text)

        if m_chuong:
            first_level_found = True
            num = m_chuong.group(2).upper()
            title = (m_chuong.group(3) or "").strip()

            if not title and i + 1 < len(paragraphs):
                next_text = paragraphs[i + 1]
                if not re_chuong.match(next_text) and not re_dieu.match(next_text):
                    title = next_text
                    i += 1

            full_text = f"Chương {num} {title}".strip()
            current_chuong = {
                "level": "CHƯƠNG",
                "number": num,
                "title": title,
                "content": [full_text],
                "children": [],
            }
            root["children"].append(current_chuong)
            current_dieu = None
            current_khoan = None
        elif m_dieu:
            first_level_found = True
            num = m_dieu.group(1)
            title = (m_dieu.group(2) or "").strip()

            full_text = f"Điều {num}. {title}".strip() if title else f"Điều {num}."
            current_dieu = {
                "level": "ĐIỀU",
                "number": num,
                "title": title,
                "content": [full_text],
                "children": [],
            }
            if current_chuong:
                current_chuong["children"].append(current_dieu)
            else:
                root["children"].append(current_dieu)
            current_khoan = None
        elif m_khoan:
            if current_dieu:
                current_khoan = {
                    "level": "KHOẢN",
                    "number": m_khoan.group(1),
                    "title": "",
                    "content": [text],
                    "children": [],
                }
                current_dieu["children"].append(current_khoan)
            else:
                if not first_level_found:
                    root["content"].append(text)
                elif current_chuong:
                    current_chuong["content"].append(text)
        elif m_diem:
            if current_khoan:
                diem = {
                    "level": "ĐIỂM",
                    "number": m_diem.group(1),
                    "title": "",
                    "content": [text],
                    "children": [],
                }
                current_khoan["children"].append(diem)
            else:
                if not first_level_found:
                    root["content"].append(text)
                elif current_dieu:
                    current_dieu["content"].append(text)
                elif current_chuong:
                    current_chuong["content"].append(text)
        else:
            if not first_level_found:
                root["content"].append(text)
            elif current_khoan:
                current_khoan["content"].append(text)
            elif current_dieu:
                current_dieu["content"].append(text)
            elif current_chuong:
                current_chuong["content"].append(text)

        i += 1

    return root


def main():
    input_dir = str(DATA_INPUT_DIR / "type1")
    output_dir = str(ARTIFACTS_DIR / "type1")
    converted_dir = str(CONVERTED_DIR)
    csv_path = str(DATA_INPUT_DIR / "full_metadata.csv")

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    metadata = load_metadata(csv_path)
    if not os.path.isdir(input_dir):
        os.makedirs(input_dir, exist_ok=True)
    files = [f for f in os.listdir(input_dir) if f.endswith(".doc")]

    report = []

    for filename in files:
        doc_path = os.path.join(input_dir, filename)
        name_no_ext = os.path.splitext(filename)[0]
        output_file = os.path.join(output_dir, name_no_ext + ".json")

        print(f"Processing {filename}...")
        try:
            docx_path = convert_doc_to_docx(doc_path, converted_dir)
            result = parse_docx(docx_path, filename)

            meta = metadata.get(name_no_ext, {})
            result["document_type"] = meta.get("Loại văn bản", "")
            result["effective_date"] = meta.get("Ngày có hiệu lực", "")
            result["industry"] = meta.get("Ngành", "")
            result["field"] = meta.get("Lĩnh vực", "")
            result["issuing_agency"] = meta.get("Cơ quan ban hành", "")
            result["signer"] = meta.get("Người ký", "")

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=4)

            report.append({"filename": filename, "status": "Success"})
            print(f"Done: {output_file}")

        except Exception as e:
            error_msg = str(e)
            print(f"Failed {filename}: {error_msg}")
            report.append({"filename": filename, "status": "Failed", "error": error_msg})

    report_path = str(ARTIFACTS_DIR / "parser_report.json")
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=4)

    print(f"\nProcessing complete. Report saved to {report_path}")


if __name__ == "__main__":
    main()
