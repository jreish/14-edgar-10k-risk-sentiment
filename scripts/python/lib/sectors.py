"""SIC code -> GICS-style sector classification.

Per pitfall #6 ("SIC -> sector mapping is manual and needs upkeep. Build
the initial map broadly... instead of reactively patching an
'Unclassified' bucket"), this covers SIC major-group ranges broadly
(not just the ~197 distinct codes actually observed in this dataset),
with explicit per-code overrides where a range doesn't cleanly map to one
sector (e.g. pharmaceuticals sit inside the broader chemicals range 2800-
2899 but belong in Health Care, not Materials; motor vehicles sit inside
transportation equipment but belong in Consumer Discretionary, not
Industrials).

Classification judgment calls follow GICS convention where there's a
reasonable analogue, documented inline where non-obvious.
"""

SECTORS = [
    "Energy", "Materials", "Industrials", "Consumer Discretionary",
    "Consumer Staples", "Health Care", "Financials", "Information Technology",
    "Communication Services", "Utilities", "Real Estate", "Unclassified",
]

# CIK-specific overrides: for a handful of well-known companies whose SIC
# code is too generic/legacy to imply one sector on its own. Found by
# inspecting SIC 7389 "Business Services, NEC" -- a legacy catch-all that,
# in this dataset, mixes payment networks (Visa, Mastercard), marketplaces
# (eBay, Etsy, Uber, DoorDash), and genuine IT/data-services companies
# (Accenture, Akamai, Fiserv, FIS, Fair Isaac, Broadridge, MSCI, CoStar) --
# no single sector fits all of them, so the ones with an unambiguous real
# business model get a direct override rather than being forced into
# whatever 7389's blanket default is. Checked before the SIC-based rules.
CIK_OVERRIDES: dict[int, str] = {
    1403161: "Financials",       # Visa
    1141391: "Financials",       # Mastercard
    1633917: "Financials",       # PayPal
    1365135: "Financials",       # Western Union
    1123360: "Financials",       # Global Payments
    1175454: "Financials",       # Corpay
    1065088: "Consumer Discretionary",  # eBay
    1370637: "Consumer Discretionary",  # Etsy
    1543151: "Consumer Discretionary",  # Uber
    1792789: "Consumer Discretionary",  # DoorDash
    1286681: "Consumer Discretionary",  # Domino's Pizza (SIC 5140 "Wholesale-Groceries" default is Consumer Staples, wrong for a restaurant chain)
}

# Explicit overrides: specific SIC codes (or code prefixes) that don't
# follow their surrounding range's general classification.
_OVERRIDES: dict[str, str] = {
    # Wholesale drug/medical distributors -> Health Care, not general wholesale/Industrials.
    "5122": "Health Care", "5047": "Health Care",
    # Wholesale groceries -> Consumer Staples (Food Distribution), not Industrials.
    "5140": "Consumer Staples",
    # SIC 7389 "Business Services, NEC" default -> Information Technology
    # (the majority pattern in this dataset: Accenture, Akamai, Fiserv, FIS,
    # Fair Isaac, Broadridge, MSCI, CoStar); named exceptions above.
    "7389": "Information Technology",
    # Pharma/biotech sit inside the broader chemicals range but are Health Care.
    "2833": "Health Care", "2834": "Health Care", "2835": "Health Care", "2836": "Health Care",
    # Agricultural chemicals (fertilizer) -> Materials, not Health Care/Consumer Staples.
    "2870": "Materials", "2879": "Materials",
    # Soap/cosmetics -> Consumer Staples (household/personal products), not Materials.
    "2840": "Consumer Staples", "2841": "Consumer Staples", "2842": "Consumer Staples",
    "2843": "Consumer Staples", "2844": "Consumer Staples",
    # Motor vehicles -> Consumer Discretionary (GICS Automobiles), not Industrials.
    "3711": "Consumer Discretionary", "3713": "Consumer Discretionary",
    "3714": "Consumer Discretionary", "3716": "Consumer Discretionary",
    "3751": "Consumer Discretionary",  # motorcycles/bicycles
    # Computers/semiconductors/software -> Information Technology.
    "3570": "Information Technology", "3571": "Information Technology",
    "3572": "Information Technology", "3575": "Information Technology",
    "3576": "Information Technology", "3577": "Information Technology",
    "3578": "Information Technology", "3579": "Information Technology",
    "3661": "Information Technology", "3663": "Communication Services",  # broadcast equip -> comm infra
    "3669": "Information Technology", "3670": "Information Technology",
    "3672": "Information Technology", "3674": "Information Technology",
    "3675": "Information Technology", "3676": "Information Technology",
    "3677": "Information Technology", "3678": "Information Technology",
    "3679": "Information Technology", "3812": "Industrials",  # aero/defense electronics
    "7370": "Information Technology", "7371": "Information Technology",
    "7372": "Information Technology", "7373": "Information Technology",
    "7374": "Information Technology", "7375": "Information Technology",
    "7379": "Information Technology",
    # Medical/surgical instruments -> Health Care, not general instruments/IT.
    "3826": "Information Technology",  # lab analytical instruments (broad-use, not medical-specific)
    "3841": "Health Care", "3842": "Health Care", "3843": "Health Care",
    "3844": "Health Care", "3845": "Health Care", "3851": "Health Care",
    # REITs and real-estate-adjacent finance -> Real Estate, not Financials.
    "6500": "Real Estate", "6510": "Real Estate", "6512": "Real Estate",
    "6552": "Real Estate", "6792": "Real Estate", "6798": "Real Estate",
    # Grocery/drug retail -> Consumer Staples (GICS Food & Staples Retailing).
    "5411": "Consumer Staples", "5412": "Consumer Staples",
    "5912": "Consumer Staples",
    # Restaurants -> Consumer Discretionary.
    "5810": "Consumer Discretionary", "5812": "Consumer Discretionary",
    # Media/broadcasting/publishing -> Communication Services.
    "2711": "Communication Services", "2721": "Communication Services",
    "2731": "Communication Services", "2741": "Communication Services",
    "4833": "Communication Services", "4841": "Communication Services",
    "7812": "Communication Services", "7822": "Communication Services",
    "7829": "Communication Services", "7841": "Communication Services",
    "4812": "Communication Services", "4813": "Communication Services",
    "4822": "Communication Services", "4899": "Communication Services",
    # Pipelines -> Energy, not Industrials/Utilities.
    "4922": "Energy", "4923": "Energy", "4924": "Energy", "1311": "Energy",
    "1381": "Energy", "1382": "Energy", "1389": "Energy", "2911": "Energy",
    "1300": "Energy",
    # Waste management -> Industrials (GICS Commercial Services & Supplies).
    "4953": "Industrials", "4959": "Industrials",
}

# Fallback ranges by SIC major group (first 2 digits, or a narrower prefix
# where the 2-digit group is too broad to have one sector).
_RANGE_RULES: list[tuple[str, str]] = [
    ("01", "Materials"), ("02", "Materials"), ("07", "Materials"),  # agriculture
    ("08", "Materials"), ("09", "Materials"),
    ("10", "Materials"),  # metal mining
    ("12", "Energy"), ("13", "Energy"),  # coal, oil & gas extraction
    ("14", "Materials"),  # nonmetallic minerals
    ("15", "Industrials"), ("16", "Industrials"), ("17", "Industrials"),  # construction
    ("20", "Consumer Staples"),  # food
    ("21", "Consumer Staples"),  # tobacco
    ("22", "Consumer Discretionary"),  # textiles
    ("23", "Consumer Discretionary"),  # apparel
    ("24", "Consumer Discretionary"),  # lumber/wood
    ("25", "Consumer Discretionary"),  # furniture
    ("26", "Materials"),  # paper
    ("27", "Communication Services"),  # printing/publishing (see overrides)
    ("28", "Materials"),  # chemicals (see overrides for pharma/cosmetics/ag)
    ("29", "Energy"),  # petroleum refining
    ("30", "Materials"),  # rubber/plastics
    ("31", "Consumer Discretionary"),  # leather
    ("32", "Materials"),  # stone/glass/concrete
    ("33", "Materials"),  # primary metals
    ("34", "Industrials"),  # fabricated metal products
    ("35", "Industrials"),  # industrial/commercial machinery (see overrides for computers)
    ("36", "Industrials"),  # electronic equipment (see overrides for semis/IT)
    ("37", "Industrials"),  # transportation equipment (see overrides for autos)
    ("38", "Industrials"),  # instruments (see overrides for medical/lab)
    ("39", "Consumer Discretionary"),  # misc manufacturing
    ("40", "Industrials"), ("41", "Industrials"), ("42", "Industrials"),
    ("43", "Industrials"), ("44", "Industrials"), ("45", "Industrials"),
    ("47", "Industrials"),  # transportation
    ("48", "Communication Services"),  # communications
    ("49", "Utilities"),  # electric/gas/sanitary services
    ("50", "Industrials"), ("51", "Industrials"),  # wholesale trade
    ("52", "Consumer Discretionary"), ("53", "Consumer Discretionary"),
    ("54", "Consumer Staples"),  # food stores
    ("55", "Consumer Discretionary"), ("56", "Consumer Discretionary"),
    ("57", "Consumer Discretionary"), ("58", "Consumer Discretionary"),
    ("59", "Consumer Discretionary"),  # retail trade
    ("60", "Financials"),  # depository institutions
    ("61", "Financials"),  # non-depository credit
    ("62", "Financials"),  # security/commodity brokers
    ("63", "Financials"), ("64", "Financials"),  # insurance
    ("65", "Real Estate"), ("66", "Real Estate"),  # real estate
    ("67", "Financials"),  # holding/investment offices (REITs overridden above)
    ("70", "Consumer Discretionary"),  # hotels
    ("72", "Consumer Discretionary"),  # personal services
    ("73", "Industrials"),  # business services (see overrides for software)
    ("75", "Consumer Discretionary"),  # auto repair
    ("76", "Industrials"),  # misc repair services
    ("78", "Communication Services"),  # motion pictures
    ("79", "Consumer Discretionary"),  # amusement/recreation
    ("80", "Health Care"),  # health services
    ("81", "Industrials"),  # legal services
    ("82", "Consumer Discretionary"),  # educational services
    ("83", "Health Care"),  # social services (research labs land here too)
    ("86", "Industrials"),  # membership organizations
    ("87", "Industrials"),  # engineering/management/research services
    ("99", "Unclassified"),
]


def classify_company(cik: int | None, sic: str | None) -> str:
    if cik is not None and cik in CIK_OVERRIDES:
        return CIK_OVERRIDES[cik]
    if not sic:
        return "Unclassified"
    sic = sic.strip().zfill(4)
    if sic in _OVERRIDES:
        return _OVERRIDES[sic]
    for prefix, sector in _RANGE_RULES:
        if sic.startswith(prefix):
            return sector
    return "Unclassified"
