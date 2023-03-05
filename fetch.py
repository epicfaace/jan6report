import requests
import json
import os
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import pypdf
from lunr import lunr
import gzip
import shutil
from collections import Counter

ORIGINALS_DIR = "originals"
CONTENT_DIR = "content"
INDEX_FILE = "idx.json"
INDEX_FILE_COMPRESSED = "idx.json.gz"
CONTENT_FILE = "content.json"
CONTENT_FILE_COMPRESSED = "content.json.gz"


os.makedirs(ORIGINALS_DIR, exist_ok=True)

def get_file_id_to_file():
    with open("data.json", "r") as f:
        files = json.load(f)["childNodes"]
    return {f["nodeValue"]["packageid"] + ".pdf.txt": f["nodeValue"] for f in files}

def get_file_id_to_file_with_content():
    file_id_to_file = get_file_id_to_file()
    for file_id, file in file_id_to_file.items():
        path = os.path.join(CONTENT_DIR, file_id)
        if not os.path.exists(path):
            # File wasn't transcribed / isn't transcribable
            continue
        with open(path, 'r') as f:
            file["content"] = f.read()
        file["url"] = get_url(file)
    return file_id_to_file

def get_url(item):
    assert "pdffile" in item, item
    link = item.get("pdffile", item.get("other1file"))
    long_filename = item.get("pdf", item.get("other1")).split("/")[-1]
    # extension = "." + long_filename.split(".")[-1]
    url = f"https://www.govinfo.gov/content/pkg/{link}"
    return url

def fetch(i, ctr):
    item = i["nodeValue"]
    if "pdffile" not in item: return
    assert "pdffile" in item
    url = get_url(item)
    filename = item["packageid"] + ".pdf"
    output_path = os.path.join(ORIGINALS_DIR, filename)
    if os.path.exists(output_path): return
    print(ctr, filename)
    r = requests.get(url)
    with open(output_path, "wb+") as f:
        f.write(r.content)

def do_fetch():
    items = {}
    for url in [
        "https://www.govinfo.gov/wssearch/browsecommittee/chamber/house/committee/january6th/collection/CPRT/congress/117?fetchChildrenOnly=1",
        "https://www.govinfo.gov/wssearch/rb//gpo/January%206th%20Committee%20Final%20Report%20and%20Supporting%20Materials%20Collection/Supporting%20Materials%20-%20Court%20Documents?fetchChildrenOnly=1",
        "https://www.govinfo.gov/wssearch/rb//gpo/January%206th%20Committee%20Final%20Report%20and%20Supporting%20Materials%20Collection/Supporting%20Materials%20-%20Transcribed%20Interviews%20and%20Depositions?fetchChildrenOnly=1",
        "https://www.govinfo.gov/wssearch/rb//gpo/January%206th%20Committee%20Final%20Report%20and%20Supporting%20Materials%20Collection/Supporting%20Materials%20-%20Documents%20on%20File%20with%20the%20Select%20Committee?fetchChildrenOnly=1"
    ]:
        data = requests.get(url).json()
        for node in data["childNodes"]:
            items[node["nodeValue"]["packageid"]] = node
        with open("data.json", "w+") as f:
            json.dump({"childNodes": list(items.values())}, f, indent=2)
    # print(list(items.keys()))
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch, i, ctr) for ctr, i in enumerate(list(items.values()))]
        for fut in as_completed(futures):
            fut.result()

def extract_text_from_pdf(file_path, output_file_path):
    if os.path.exists(output_file_path): return
    with open(file_path, "rb") as file:
        pdf = pypdf.PdfReader(file)
        text = " ".join([page.extract_text() for page in pdf.pages])
    with open(output_file_path, "w+") as out:
        out.write(text)

def save_text_to_file(text, file_path):
    with open(file_path, "w") as file:
        file.write(text)

def build_lunr_index(text_files_directory):
    file_id_to_file = get_file_id_to_file()
    documents = []
    for i, file_name in enumerate(os.listdir(text_files_directory)):
        file_path = os.path.join(text_files_directory, file_name)
        with open(file_path, "r") as file:
            content = file.read()
            file_info = file_id_to_file[file_name]
            documents.append({
                'id': file_name,
                'title': file_info['title'],
                'body': content,
                'url': get_url(file_info)
            })
    print("Building index...")
    index = lunr(ref='id', fields=('title', 'body'), documents=documents)
    print("Index built.")
    print(index.search("test"))
    serialized_idx = index.serialize()
    with open(INDEX_FILE, 'w') as fd:
        json.dump(serialized_idx, fd)

def extract_text_from_pdfs(pdf_files_directory, text_files_directory):
    if not os.path.exists(text_files_directory):
        os.makedirs(text_files_directory)

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_file = {executor.submit(extract_text_from_pdf, os.path.join(pdf_files_directory, file_name), os.path.join(text_files_directory, file_name + ".txt")): file_name for file_name in os.listdir(pdf_files_directory) if file_name.endswith(".pdf")}
        for future in as_completed(future_to_file):
            file_name = future_to_file[future]

    return build_lunr_index(text_files_directory)

def build_content_file():
    file_id_to_file = get_file_id_to_file_with_content()
    with open(os.path.join(CONTENT_FILE), 'w') as f:
        json.dump(file_id_to_file, f, indent=2)

def compress_files():
    with open(CONTENT_FILE, "rb") as f_in, gzip.open(CONTENT_FILE_COMPRESSED, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)
    with open(INDEX_FILE, "rb") as f_in, gzip.open(INDEX_FILE_COMPRESSED, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)

def calc_stats():
    file_id_to_file = get_file_id_to_file_with_content()
    c = Counter()
    for file in file_id_to_file.values():
        if "content" not in file: continue
        for word in file["content"].split(" "):
            w = word.strip()
            if not w: break
            c[w] += 1
    with open("most-common.txt", "w+") as f:
        for (word, count) in c.most_common():
            if word[0].isupper() or len(word) > 4:
                if count >= 3:
                    f.write(f"{word}\t")


do_fetch()
extract_text_from_pdfs(ORIGINALS_DIR, CONTENT_DIR)
build_content_file()
build_lunr_index(CONTENT_DIR)
compress_files()

calc_stats()