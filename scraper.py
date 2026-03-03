"""
scraper.py – Web Scraping & Data Ingestion Module

Products are sourced from REAL public APIs and websites:
  • OpenFDA Drug NDC API     →  actual medicines (name, manufacturer, dosage form)
  • OpenFDA Device API       →  actual medical devices
  • Pharmeasy / Netmeds      →  scraped product listings (best-effort)

All scraped products are stored in the `products` table.
The `knowledge_base` table is populated with category-level RAG content.
"""

import requests
from bs4 import BeautifulSoup
import logging
import time
import re
from datetime import datetime
from database import get_db_connection, save_product

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SCRAPER] %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# ─────────────────────────────────────────────────────────────────────────────
# 1.  OpenFDA Drug NDC API  (real medicines with names, manufacturers, forms)
#     Docs: https://open.fda.gov/apis/drug/ndc/
# ─────────────────────────────────────────────────────────────────────────────

FDA_DRUG_URL = "https://api.fda.gov/drug/ndc.json"

# Map OpenFDA marketing_category / pharm_class → our category label
def _drug_category(record: dict) -> str:
    pharm = " ".join(record.get("pharm_class", [])).lower()
    mc    = (record.get("marketing_category") or "").lower()
    if any(k in pharm for k in ["analgesic", "anti-inflammatory", "antipyretic"]):
        return "Analgesics & Antipyretics"
    if any(k in pharm for k in ["antibiotic", "antibacterial", "antimicrobial"]):
        return "Antibiotics"
    if any(k in pharm for k in ["antidiabetic", "hypoglycemic", "insulin"]):
        return "Antidiabetics"
    if any(k in pharm for k in ["antihypertensive", "cardiac", "cardiovascular", "statin"]):
        return "Cardiac & Blood Pressure"
    if any(k in pharm for k in ["vitamin", "mineral", "supplement", "nutritional"]):
        return "Vitamins & Supplements"
    if any(k in pharm for k in ["antacid", "proton pump", "gastrointestinal", "laxative"]):
        return "Gastrointestinal"
    if any(k in pharm for k in ["antihistamine", "allergy", "bronchodilator", "respiratory"]):
        return "Respiratory & Allergy"
    if "otc" in mc or "monograph" in mc:
        return "OTC Medicine"
    return "Prescription Medicine"


def scrape_fda_drugs(limit: int = 200) -> int:
    """
    Fetch drug products from OpenFDA Drug NDC endpoint.
    Searches across several therapeutic queries to get variety.
    Returns count of products saved.
    """
    search_terms = [
        "analgesic",
        "antibiotic",
        "antidiabetic",
        "antihypertensive",
        "vitamin",
        "antacid",
        "antihistamine",
    ]
    total_saved = 0

    for term in search_terms:
        try:
            params = {
                "search": f"pharm_class:\"{term}\"",
                "limit": 30,
            }
            resp = requests.get(FDA_DRUG_URL, params=params, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                logger.warning(f"FDA drug API returned {resp.status_code} for term='{term}'")
                continue

            data = resp.json()
            results = data.get("results", [])
            logger.info(f"FDA drugs [{term}]: {len(results)} records received")

            for r in results:
                brand   = (r.get("brand_name") or r.get("generic_name") or "").strip().title()
                generic = (r.get("generic_name") or "").strip().title()
                mfr     = (r.get("labeler_name") or "").strip()
                form    = (r.get("dosage_form") or "").strip().title()
                route   = ", ".join(r.get("route", [])).title()
                ndc     = r.get("product_ndc", "")

                if not brand:
                    continue

                name = brand if brand != generic else f"{brand} ({form})"
                desc = f"{generic} – {form}"
                if route:
                    desc += f", {route} administration"
                strengths = r.get("active_ingredients", [])
                if strengths:
                    strength_str = "; ".join(
                        f"{s.get('name','').title()} {s.get('strength','')}"
                        for s in strengths[:3]
                    )
                    desc += f". Active: {strength_str}"

                product = {
                    "product_name": name[:200],
                    "category": _drug_category(r),
                    "description": desc[:500],
                    "manufacturer": mfr[:200],
                    "source_url": f"https://api.fda.gov/drug/ndc.json?search=product_ndc:{ndc}",
                }
                if save_product(product):
                    total_saved += 1

            time.sleep(0.5)  # polite rate limiting

        except Exception as exc:
            logger.warning(f"FDA drug scrape error [{term}]: {exc}")

    logger.info(f"FDA drugs total saved: {total_saved}")
    return total_saved


# ─────────────────────────────────────────────────────────────────────────────
# 2.  OpenFDA Device Classification API  (real medical devices)
#     Docs: https://open.fda.gov/apis/device/classification/
# ─────────────────────────────────────────────────────────────────────────────

FDA_DEVICE_URL = "https://api.fda.gov/device/classification.json"

DEVICE_CATEGORY_MAP = {
    "cardiovascular": "Diagnostic Equipment",
    "radiology":      "Diagnostic Equipment",
    "general & plastic surgery": "Surgical Equipment",
    "orthopedic":     "Rehabilitation Equipment",
    "physical medicine": "Rehabilitation Equipment",
    "ophthalmic":     "Diagnostic Equipment",
    "ear, nose and throat": "Diagnostic Equipment",
    "clinical chemistry": "Diagnostic Equipment",
    "microbiology":   "Diagnostic Equipment",
    "immunology":     "Diagnostic Equipment",
    "anesthesiology": "Surgical Equipment",
    "neurology":      "Diagnostic Equipment",
    "obstetrics/gynecology": "Hospital Equipment",
    "hematology":     "Diagnostic Equipment",
    "gastroenterology": "Hospital Equipment",
    "urology":        "Hospital Equipment",
}


def _device_category(panel: str) -> str:
    panel_lc = (panel or "").lower()
    for key, cat in DEVICE_CATEGORY_MAP.items():
        if key in panel_lc:
            return cat
    return "Medical Device"


def scrape_fda_devices(limit: int = 100) -> int:
    """Fetch medical device classifications from OpenFDA Device API."""
    total_saved = 0
    try:
        params = {"limit": limit, "skip": 0}
        resp = requests.get(FDA_DEVICE_URL, params=params, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            logger.warning(f"FDA device API returned {resp.status_code}")
            return 0

        data = resp.json()
        results = data.get("results", [])
        logger.info(f"FDA devices: {len(results)} records received")

        seen = set()
        for r in results:
            name  = (r.get("device_name") or "").strip().title()
            panel = (r.get("medical_specialty_description") or "").strip()
            code  = r.get("product_code", "")
            cls   = r.get("device_class", "")
            definition = (r.get("definition") or "")[:400]

            if not name or name in seen:
                continue
            seen.add(name)

            product = {
                "product_name": name[:200],
                "category": _device_category(panel),
                "description": definition or f"FDA Class {cls} medical device. Specialty: {panel}.",
                "manufacturer": f"FDA Product Code: {code}",
                "source_url": f"https://api.fda.gov/device/classification.json?search=product_code:{code}",
            }
            if save_product(product):
                total_saved += 1

    except Exception as exc:
        logger.warning(f"FDA device scrape error: {exc}")

    logger.info(f"FDA devices total saved: {total_saved}")
    return total_saved


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Netmeds.com – Scrape OTC/non-prescription product listings
#     (best-effort HTML scraping; silently skipped if blocked)
# ─────────────────────────────────────────────────────────────────────────────

NETMEDS_CATEGORIES = [
    ("https://www.netmeds.com/non-prescriptions/category/pain-fever",           "Analgesics & Antipyretics"),
    ("https://www.netmeds.com/non-prescriptions/category/cold-cough-flu",       "Respiratory & Allergy"),
    ("https://www.netmeds.com/non-prescriptions/category/vitamins-supplements", "Vitamins & Supplements"),
    ("https://www.netmeds.com/non-prescriptions/category/diabetes-care",        "Antidiabetics"),
    ("https://www.netmeds.com/non-prescriptions/category/digestive-care",       "Gastrointestinal"),
]


def scrape_netmeds(category_url: str, category_label: str) -> int:
    """Scrape product cards from a Netmeds category page."""
    saved = 0
    try:
        page_headers = dict(HEADERS)
        page_headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        resp = requests.get(category_url, headers=page_headers, timeout=20)
        if resp.status_code != 200:
            logger.warning(f"Netmeds {category_url}: HTTP {resp.status_code}")
            return 0

        soup = BeautifulSoup(resp.text, "html.parser")

        # Netmeds product cards use class "product-list-item" or "cat_product_card"
        cards = (
            soup.find_all("div", class_="product-list-item") or
            soup.find_all("div", class_="cat_product_card") or
            soup.find_all("div", attrs={"data-product-id": True})
        )

        logger.info(f"Netmeds [{category_label}]: {len(cards)} cards found")

        for card in cards[:50]:
            # Product name
            name_el = (
                card.find("p", class_="clsgetname") or
                card.find("h3") or
                card.find("p", class_="product-name") or
                card.find(attrs={"class": lambda c: c and "name" in c.lower()})
            )
            name = name_el.get_text(strip=True) if name_el else ""

            # Manufacturer
            mfr_el = card.find("p", class_="mfr-name") or card.find("span", class_="mfr")
            mfr = mfr_el.get_text(strip=True) if mfr_el else ""

            # Price (for description)
            price_el = card.find("span", class_="final-price") or card.find(attrs={"class": lambda c: c and "price" in str(c).lower()})
            price = price_el.get_text(strip=True) if price_el else ""

            if not name or len(name) < 3:
                continue

            desc = f"{category_label} product"
            if price:
                desc += f". Price: {price}"

            product = {
                "product_name": re.sub(r"\s+", " ", name)[:200],
                "category": category_label,
                "description": desc[:500],
                "manufacturer": re.sub(r"\s+", " ", mfr)[:200],
                "source_url": category_url,
            }
            if save_product(product):
                saved += 1

    except Exception as exc:
        logger.warning(f"Netmeds scrape error [{category_url}]: {exc}")

    return saved


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Knowledge Base (category-level RAG content for the chatbot)
#     This is NOT the products table — this is used for chatbot answers.
# ─────────────────────────────────────────────────────────────────────────────

DEMO_KB = [
    {"title": "Surgical & Disposable Consumables",    "category": "Surgical Consumables",    "content": "We supply sterile gloves, surgical masks, gowns, drapes, syringes, IV cannulas, wound dressings, and sutures. ISO 13485 and CE certified. Available in bulk for hospitals, clinics, and wholesale distributors.", "source_url": "https://example-healthcare.com/surgical"},
    {"title": "Diagnostic Equipment & Instruments",   "category": "Diagnostic Equipment",    "content": "Pulse oximeters, glucometers, ECG machines, BP monitors, stethoscopes, POCT kits. OEM and white-label distribution agreements available for regional dealers.", "source_url": "https://example-healthcare.com/diagnostics"},
    {"title": "Hospital Furniture & Patient Care",    "category": "Hospital Furniture",      "content": "Hospital beds, examination tables, IV stands, wheelchairs, stretchers, and ICU furniture available for direct procurement and through authorised dealer networks.", "source_url": "https://example-healthcare.com/furniture"},
    {"title": "Pharmaceutical Distribution",          "category": "Pharmaceuticals",         "content": "WHO-listed essential medicines, OTC pharmaceuticals, nutraceuticals, and specialty drugs. Cold-chain logistics available. Distribution partnerships open to registered distributors and pharmacy chains.", "source_url": "https://example-healthcare.com/pharma"},
    {"title": "Personal Protective Equipment (PPE)",  "category": "PPE",                     "content": "N95/KN95 respirators, face shields, safety goggles, isolation gowns, shoe covers, and hand sanitizers. Bulk supply for government tenders, corporate clients, and regional distributors.", "source_url": "https://example-healthcare.com/ppe"},
    {"title": "Dealer & Distributor Partnership",     "category": "Distribution Opportunities","content": "Three tiers: Authorised Reseller (50 units/month), Regional Distributor (exclusive territory, 500 units/month), National Distributor (dedicated account manager, co-marketing, preferential pricing).", "source_url": "https://example-healthcare.com/partners"},
    {"title": "Bulk Purchase & Tender Support",       "category": "Bulk Purchase",           "content": "Quantity discounts start at 10% for orders above 1,000 units. Government hospital tenders, NGO procurement drives, and large-scale bulk purchases fully supported.", "source_url": "https://example-healthcare.com/bulk"},
    {"title": "Sterilisation & Infection Control",    "category": "Infection Control",       "content": "Autoclave pouches, sterilisation indicators, disinfectant solutions, UV sterilisation cabinets, and hospital-grade antiseptics. For clinics, dental practices, and surgical centres.", "source_url": "https://example-healthcare.com/sterilisation"},
    {"title": "Rehabilitation & Mobility Aids",       "category": "Rehabilitation Equipment","content": "Crutches, walkers, orthopaedic supports, compression stockings, TENS units, CPM machines. Partnerships with physiotherapy clinics and home-care distributors.", "source_url": "https://example-healthcare.com/rehab"},
    {"title": "Cold Chain & Vaccine Storage",         "category": "Cold Chain Equipment",    "content": "Medical-grade refrigerators, vaccine cold chain equipment, temperature monitoring data loggers. WHO PQS and ISO compliant. For government immunisation programmes.", "source_url": "https://example-healthcare.com/coldchain"},
]


def _clean(raw: str, maxlen: int = 500) -> str:
    return re.sub(r"\s+", " ", raw or "").strip()[:maxlen]


def load_demo_kb():
    conn = get_db_connection()
    cur = conn.cursor()
    loaded = 0
    for item in DEMO_KB:
        cur.execute(
            "INSERT OR IGNORE INTO knowledge_base (title, content, category, source_url, scraped_at) VALUES (?,?,?,?,?)",
            (item["title"], item["content"], item["category"], item["source_url"], datetime.utcnow().isoformat()),
        )
        loaded += cur.rowcount
    conn.commit()
    conn.close()
    logger.info(f"Seeded {loaded} KB records.")
    return loaded


# ─────────────────────────────────────────────────────────────────────────────
# 5.  MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def run_scraper() -> int:
    """
    Called by app.py on startup and via /api/scrape.
    Scrapes real product data from OpenFDA APIs and Netmeds.
    Always seeds the chatbot KB as well.
    Returns total records touched.
    """
    total = 0

    # ── Real Products from OpenFDA ──────────────────────────────────────────
    logger.info("Scraping OpenFDA drug products …")
    total += scrape_fda_drugs(limit=200)

    logger.info("Scraping OpenFDA medical device classifications …")
    total += scrape_fda_devices(limit=100)

    # ── Real Product Listings from Netmeds (best-effort) ───────────────────
    for cat_url, cat_label in NETMEDS_CATEGORIES:
        logger.info(f"Scraping Netmeds [{cat_label}] …")
        total += scrape_netmeds(cat_url, cat_label)
        time.sleep(1)

    # ── Chatbot KB (category-level content for RAG) ─────────────────────────
    total += load_demo_kb()

    logger.info(f"Scraper complete. Total records: {total}")
    return total


if __name__ == "__main__":
    run_scraper()
