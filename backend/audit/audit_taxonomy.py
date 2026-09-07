"""Read-only deterministic audit of the Senseigigs taxonomy."""
import os, re, unicodedata
from collections import defaultdict
import mysql.connector

TABLES = ("services", "categories", "specialities", "tags", "speciality_tags")
ABBREVIATIONS = {"Mgmt": "Management", "Ops": "Operations", "DD": "Due Diligence", "GTM": "Go-to-Market"}
PLACEHOLDERS = {"arc", "dummy", "eee", "new", "new tag", "placeholder", "sample", "temp", "test"}
ACRONYMS = {"ai":"AI","alm":"ALM","aml":"AML","api":"API","crm":"CRM","erp":"ERP",
 "esg":"ESG","fmcg":"FMCG","fp&a":"FP&A","ifrs9":"IFRS9","ifrs17":"IFRS17",
 "it":"IT","kyb":"KYB","kyc":"KYC","m&a":"M&A","pmo":"PMO","qhse":"QHSE",
 "r&d":"R&D","rpa":"RPA","saas":"SaaS","s&op":"S&OP","ux":"UX"}

def connect():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST","db"), port=int(os.getenv("MYSQL_PORT","3306")),
        database=os.getenv("MYSQL_DATABASE","greygigz_beta"),
        user=os.getenv("MYSQL_USER","senseigigs"),
        password=os.getenv("MYSQL_PASSWORD","localpassword"))

def fetch(connection, table):
    cursor=connection.cursor(dictionary=True)
    cursor.execute("SELECT * FROM "+table)
    rows=cursor.fetchall(); cursor.close()
    return rows

def normalized(value):
    value=unicodedata.normalize("NFKC",value or "").strip().casefold()
    value=re.sub(r"[‐‑‒–—―]","-",value)
    value=re.sub(r"\s+"," ",value)
    return re.sub(r"[.,;:!?]+$","",value)

def smart_title(value):
    minor={"a","an","and","as","at","by","for","from","in","of","on","or","the","to","via","with"}
    output=[]; word_index=0
    for part in re.split(r"(\s+|[/()\-])",(value or "").strip()):
        if not part or re.fullmatch(r"\s+|[/()\-]",part):
            output.append(part); continue
        key=part.casefold()
        if key in ACRONYMS: word=ACRONYMS[key]
        elif key in minor and word_index: word=key
        elif part=="eCommerce": word=part
        else: word=part[:1].upper()+part[1:].lower()
        output.append(word); word_index+=1
    return "".join(output)

def finding(issue,table,row,details,proposed="",severity="Review"):
    return {"issue":issue,"table":table,"id":row.get("id",""),
            "original":row.get("name",""),"details":details,
            "proposed":proposed,"severity":severity}

def audit(data):
    out=defaultdict(list)
    for table in ("services","categories","specialities","tags"):
        for row in data[table]:
            name=row["name"] or ""; issues=[]
            if name!=name.strip(): issues.append("leading or trailing whitespace")
            if re.search(r"\s{2,}",name): issues.append("repeated whitespace")
            if re.search(r"[‐‑‒–—―]",name): issues.append("non-standard Unicode dash")
            if re.search(r"[.,;:!?]+$",name): issues.append("trailing punctuation")
            if issues:
                proposed=unicodedata.normalize("NFKC",name).strip()
                proposed=re.sub(r"\s+"," ",proposed)
                proposed=re.sub(r"[‐‑‒–—―]","-",proposed)
                proposed=re.sub(r"[.,;:!?]+$","",proposed)
                out["Whitespace and punctuation"].append(finding("Formatting",table,row,", ".join(issues),proposed,"High"))

    for table in ("services","categories","specialities"):
        for row in data[table]:
            name=row["name"] or ""
            first_letter=next((c for c in name if c.isalpha()),"")
            if first_letter and first_letter.islower() and not name.startswith("eCommerce"):
                position=name.index(first_letter)
                expected=name[:position]+first_letter.upper()+name[position+1:]
                out["Capitalisation"].append(finding("Capitalisation",table,row,"name begins with a lowercase letter",expected,"High"))
    for row in data["tags"]:
        expected=row["name"].lower()
        if row["name"]!=expected:
            out["Capitalisation"].append(finding("Capitalisation","tags",row,"tag is not lowercase",expected,"High"))

    for table in ("categories","specialities"):
        for row in data[table]:
            hits=[a for a in ABBREVIATIONS if re.search(rf"\b{re.escape(a)}\b",row["name"])]
            if hits:
                proposed=row["name"]
                for a in hits: proposed=re.sub(rf"\b{re.escape(a)}\b",ABBREVIATIONS[a],proposed)
                details=", ".join(f"{a} -> {ABBREVIATIONS[a]}" for a in hits)
                out["Abbreviations"].append(finding("Abbreviation",table,row,details,proposed))

    scopes={"services":lambda r:(normalized(r["name"]),),
      "categories":lambda r:(r["service_id"],normalized(r["name"])),
      "specialities":lambda r:(r["category_id"],normalized(r["name"])),
      "tags":lambda r:(normalized(r["name"]),),
      "speciality_tags":lambda r:(r["speciality_id"],r["tag_id"])}
    for table,key_fn in scopes.items():
        groups=defaultdict(list)
        for row in data[table]: groups[key_fn(row)].append(row)
        for key,rows in groups.items():
            if len(rows)>1:
                ids=", ".join(str(r["id"]) for r in rows)
                for row in rows: out["Exact duplicates"].append(finding("Exact duplicate",table,row,f"normalized key {key}; IDs {ids}",severity="High"))

    for table in ("categories","specialities","tags"):
        for row in data[table]:
            name=normalized(row["name"])
            markers=[]
            if name in PLACEHOLDERS:
                markers=[name]
            elif re.match(r"^(temp|dummy|sample|placeholder)(\b|\s)",name):
                markers=[re.match(r"^[a-z]+",name).group()]
            if markers: out["Placeholder data"].append(finding("Possible placeholder",table,row,"marker(s): "+", ".join(markers)))

    used_categories={r["category_id"] for r in data["specialities"]}
    used_specialities={r["speciality_id"] for r in data["speciality_tags"]}
    used_tags={r["tag_id"] for r in data["speciality_tags"]}
    for row in data["categories"]:
        if row["id"] not in used_categories: out["Unlinked records"].append(finding("Unlinked","categories",row,"category has no specialities"))
    for row in data["specialities"]:
        if row["id"] not in used_specialities: out["Unlinked records"].append(finding("Unlinked","specialities",row,"speciality has no tags"))
    for row in data["tags"]:
        if row["id"] not in used_tags: out["Unlinked records"].append(finding("Unlinked","tags",row,"tag is unused"))

    service_ids={r["id"] for r in data["services"]}; category_ids={r["id"] for r in data["categories"]}
    speciality_ids={r["id"] for r in data["specialities"]}; tag_ids={r["id"] for r in data["tags"]}
    for row in data["categories"]:
        if row["service_id"] not in service_ids: out["Parent integrity"].append(finding("Invalid parent","categories",row,f"missing service_id {row['service_id']}",severity="Critical"))
    for row in data["specialities"]:
        if row["category_id"] not in category_ids: out["Parent integrity"].append(finding("Invalid parent","specialities",row,f"missing category_id {row['category_id']}",severity="Critical"))
    for row in data["speciality_tags"]:
        if row["speciality_id"] not in speciality_ids: out["Parent integrity"].append(finding("Invalid parent","speciality_tags",row,f"missing speciality_id {row['speciality_id']}",severity="Critical"))
        if row["tag_id"] not in tag_ids: out["Parent integrity"].append(finding("Invalid parent","speciality_tags",row,f"missing tag_id {row['tag_id']}",severity="Critical"))
    return out

def markdown(rows):
    if not rows: return "No findings."
    cols=("issue","table","id","original","details","proposed","severity")
    clean=lambda v:str(v if v is not None else "").replace("|","\\|").replace("\n"," ")
    lines=["| "+" | ".join(cols)+" |","|"+"|".join("---" for _ in cols)+"|"]
    lines+=["| "+" | ".join(clean(row.get(col,"")) for col in cols)+" |" for row in rows]
    return "\n".join(lines)

def consolidated_markdown(findings, sections):
    rows=[]
    record_types={"services":"Service","categories":"Category",
                  "specialities":"Speciality","tags":"Tag",
                  "speciality_tags":"Speciality tag link"}
    for section in sections:
        for row in findings.get(section,[]):
            proposed=row["proposed"]
            if not proposed and section=="Placeholder data":
                proposed="Review for deactivation or removal"
            elif not proposed and section=="Unlinked records":
                proposed="Review link or deactivate"
            elif not proposed and section=="Exact duplicates":
                proposed="Review duplicate records"
            confidence={"Critical":"High","High":"High","Review":"Medium"}.get(row["severity"],row["severity"])
            issue=row["details"] if section=="Whitespace and punctuation" else row["issue"]
            if section=="Abbreviations":
                issue="Abbreviation: "+row["details"]
            elif section=="Unlinked records":
                issue=row["details"]
            elif section=="Parent integrity":
                issue=row["details"]
            rows.append({
                "Record type":record_types.get(row["table"],row["table"]),
                "ID":row["id"],"Original name":row["original"],
                "Issue":issue,"Proposed value":proposed,
                "Confidence":confidence,
            })
    if not rows:
        return "No findings."
    columns=("Record type","ID","Original name","Issue","Proposed value","Confidence")
    clean=lambda value:str(value if value is not None else "").replace("|","\\|").replace("\n"," ")
    lines=["| "+" | ".join(columns)+" |","|"+"|".join("---" for _ in columns)+"|"]
    lines+=["| "+" | ".join(clean(row[column]) for column in columns)+" |" for row in rows]
    return "\n".join(lines)

def main():
    connection=connect()
    try: data={table:fetch(connection,table) for table in TABLES}
    finally: connection.close()
    findings=audit(data)
    sections=("Whitespace and punctuation","Capitalisation","Abbreviations","Exact duplicates","Placeholder data","Unlinked records","Parent integrity")
    print("# Senseigigs Taxonomy Quality Audit\n\nRead-only audit. No database records were changed.\n")
    print("## Source row counts\n\n| Table | Rows |\n|---|---:|")
    for table in TABLES: print(f"| {table} | {len(data[table])} |")
    print("\n## Findings summary\n\n| Check | Findings |\n|---|---:|")
    for section in sections: print(f"| {section} | {len(findings.get(section,[]))} |")
    print("\n## Specific errors and review items\n")
    print(consolidated_markdown(findings,sections))
    for section in sections: print(f"\n## {section}\n\n{markdown(findings.get(section,[]))}")

if __name__=="__main__": main()
