from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Set
from enum import Enum

# ============================================================================
# Kitchen Categories and Locations (Migrated from category_config.py)
# ============================================================================

# Allowed Category Types for Kitchen items
ALLOWED_CATEGORY_TYPES: List[str] = [
    "Fruit", "Vegetable", "Meat", "Seafood", "Dairy", "Egg", 
    "Pantry", "Seasoning", "Snack", "Beverage", "Frozen", "Household", "Receipt"
]

# Mapping category codes to potential location keywords
CATEGORY_LOCATION_KEYWORDS: Dict[str, List[str]] = {
    "Fruit": ["fridge", "counter", "basket"],
    "Vegetable": ["fridge", "crisper", "pantry"],
    "Meat": ["fridge", "freezer"],
    "Seafood": ["fridge", "freezer"],
    "Dairy": ["fridge"],
    "Egg": ["fridge"],
    "Pantry": ["pantry", "shelf", "cabinet"],
    "Seasoning": ["pantry", "shelf", "spice rack"],
    "Snack": ["pantry", "shelf"],
    "Beverage": ["fridge", "pantry"],
    "Frozen": ["freezer"],
    "Household": ["pantry", "cabinet", "cleaning"],
}

# Metadata fields required for each kitchen category
COMMON_KITCHEN_FIELDS = [
    "product_name", "manufacturer", "expiry_date", "production_date", 
    "shelf_life", "shelf_life_after_opening", "quantity", "unit", "storage_requirement"
]

CATEGORY_METADATA_FIELDS: Dict[str, List[str]] = {
    cat: COMMON_KITCHEN_FIELDS for cat in ALLOWED_CATEGORY_TYPES
}

# Common category suggestions for AI
COMMON_CATEGORY_SUGGESTIONS: Dict[str, Dict[str, str]] = {
    "Fruit": {"name": "Fruits", "description": "Fresh or dried fruits"},
    "Vegetable": {"name": "Vegetables", "description": "Fresh, leafy, or hardy vegetables"},
    "Meat": {"name": "Meat", "description": "Fresh or processed meat products"},
    "Seafood": {"name": "Seafood", "description": "Fish, shellfish, and other seafood"},
    "Dairy": {"name": "Dairy", "description": "Milk, cheese, yogurt, and other dairy products"},
    "Egg": {"name": "Eggs", "description": "Chicken, duck, or other eggs"},
    "Pantry": {"name": "Pantry", "description": "Dry goods, canned food, and staples"},
    "Seasoning": {"name": "Seasonings", "description": "Spices, sauces, and condiments"},
    "Snack": {"name": "Snacks", "description": "Chips, cookies, and other snacks"},
    "Beverage": {"name": "Beverages", "description": "Drinks, juices, and alcohol"},
    "Frozen": {"name": "Frozen Food", "description": "Items stored in the freezer"},
    "Household": {"name": "Household", "description": "Non-food kitchen items, cleaning supplies"},
    "Receipt": {"name": "Receipt", "description": "Shopping receipts containing multiple kitchen items"},
}

# ============================================================================
# Pydantic Models for Kitchen Inventory
# ============================================================================

class StorageType(str, Enum):
    FRIDGE = "Fridge"
    FREEZER = "Freezer"
    PANTRY = "Pantry"
    OTHER = "Other"

# Standard sub-category tags used for fine-grained location matching.
# These are surfaced to users as quick-pick tags on Location cards.
LOCATION_SUB_TAGS: List[str] = [
    "Meat",
    "Dairy",
    "Produce",
    "Frozen",
    "Drinks",
    "Snacks",
    "Condiments",
    "Spices",
    "Grains",
    "Baking",
    "Pantry",
    "Supplements",
    "Cleaning",
    "Other",
]

class ReceiptItem(BaseModel):
    original_text: str = Field(..., description="Raw text from receipt line, e.g., 'KS ORG SPIN'")
    product_name: str = Field(..., description="Full expanded name, e.g., 'Kirkland Signature Organic Spinach'")
    category: str = Field(..., description="General category, e.g., 'Vegetable', 'Meat', 'Household'")
    quantity: str = Field("1", description="Numeric quantity only, e.g., '1', '2.5'. Extract unit separately.")
    unit: Optional[str] = Field(None, description="Measurement unit if detected, e.g., 'lb', 'kg', 'oz', 'pack'.")
    is_food: bool = Field(..., description="True if edible/cooking ingredient, False for cleaning/household")
    estimated_shelf_life_days: int = Field(..., description="Estimated days until expiry based on food type. 365 for non-perishables.")
    storage_suggestion: StorageType = Field(..., description="Best place to store this item")
    sub_category: Optional[str] = Field(
        None,
        description=(
            "Fine-grained sub-category tag used for smart location routing. "
            "Choose exactly one from: Meat, Dairy, Produce, Frozen, Drinks, Snacks, "
            "Condiments, Spices, Grains, Baking, Pantry, Supplements, Cleaning, Other. "
            "Map from item category: "
            "Meat/Seafood -> Meat; Dairy/Egg -> Dairy; Fruit/Vegetable (raw/fresh) -> Produce; "
            "Frozen -> Frozen; Beverage -> Drinks; Snack -> Snacks; "
            "Seasoning (sauce/vinegar/ketchup) -> Condiments; "
            "Seasoning (pepper/salt/spice/herb) -> Spices; "
            "Pantry (rice/pasta/oats/quinoa) -> Grains; "
            "Pantry (flour/yeast/baking soda) -> Baking; "
            "Pantry (canned/dry/packaged) -> Pantry; "
            "Household (cleaning/soap/paper) -> Cleaning; "
            "Household (vitamins/protein powder) -> Supplements. "
            "CRITICAL: Packaged baked goods/desserts that happen to contain fruit in the name "
            "(mango cake, strawberry mochi, lemon tart) -> Snacks, NOT Produce. "
            "Produce is ONLY for raw/unprocessed fruits and vegetables."
        )
    )

class ReceiptResult(BaseModel):
    merchant: Optional[str] = Field(None, description="Store name detected from header")
    purchase_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    total_payment: Optional[float] = Field(None, description="Total amount paid as shown on the receipt")
    currency: str = Field("USD")
    items: List[ReceiptItem] = Field(default_factory=list, description="List of valid purchased items")

# ============================================================================
# Categories that require secure storage (e.g., alcohol for safety, or high-value items)
SECURE_CATEGORIES: Set[str] = set()

# Categories that are frequently accessed
FREQUENT_ACCESS_CATEGORIES: Set[str] = {
    "Seasoning", "Dairy", "Vegetable", "Fruit", "Snack"
}

# Merchant Specific Rules (Extensible Interface)
# ============================================================================

MERCHANT_RULES: Dict[str, Dict[str, str]] = {
    "COSTCO": {
        "abbreviations": "KS = Kirkland Signature, ORG = Organic, APL = Apple, SPIN = Spinach, STK = Steak",
        "notes": "Costco receipts often use item numbers on the left and price on the right."
    },
    # Future merchants can be added here
    # "WHOLE_FOODS": { ... },
    # "ASIAN_MARKET": { ... }
}

def get_merchant_rules_context(merchant_name: Optional[str] = None) -> str:
    """Get formatting rules for the prompt based on the merchant name."""
    if not merchant_name:
        return "General rules: Expand common abbreviations and identify food items."
    
    # Try case-insensitive lookup
    merchant_key = merchant_name.upper()
    if merchant_key in MERCHANT_RULES:
        rules = MERCHANT_RULES[merchant_key]
        return f"Store Specific Rules for {merchant_key}:\n- {rules['abbreviations']}\n- {rules['notes']}"
    
    return f"General rules for {merchant_name}: Identify products and estimate shelf life."

# ============================================================================
# Kitchen Agent specialized Prompts
# ============================================================================

KITCHEN_RECEIPT_PROMPT = f"""
# Role
You are an expert Kitchen Inventory Agent and Receipt Decoder. Your goal is to digitize shopping receipts into a structured, human-readable kitchen inventory list. You specialize in decoding obscure abbreviations (e.g., Costco, Asian market receipts) and estimating shelf life for food safety.

# Context
The user will provide an image or text of a shopping receipt.
Store Context: Use the store name (usually at the top) to infer brands and product types.

# Core Tasks
1. **EXTRACT & DECODE**: Identify every purchased item. Convert cryptic receipt abbreviations into full product names. Extract the `total_payment` amount paid and the `merchant` name.
   - Example: "KS ORG APL" -> "Kirkland Signature Organic Apples"
   - Example: "HKU YUZU" -> "Hakutsuru Yuzu Liqueur"
   - **QUANTITY & UNIT SPLITTING**: If a line says "1.25 lb Bananas", set quantity="1.25" and unit="lb". Do NOT put "1.25 lb" in quantity.

2. **FILTER NOISE (Crucial)**:
   - IGNORE tax lines (e.g., "CA REDEMP VAL", "CRV", "TAX").
   - IGNORE subtotal line, "Total" text label lines, change due, and cashier info. (Note: Still extract the numeric value into `total_payment`).
   - IGNORE discount/rebate lines that appear below an item (e.g., "3.00-", "SAVINGS").
   - IGNORE long strings of numeric codes, item numbers, or transaction IDs (e.g., "4323118 1897217...").
   - SEPARATE non-kitchen items (e.g., clothes, electronics) into a separate list or exclude them based on configuration.

3. **CATEGORIZE & LOCATE**:
   - Assign a strict category: Fruit, Vegetable, Meat, Seafood, Dairy, Egg, Pantry, Seasoning, Snack, Beverage, Frozen, or Household.
   - Suggest a storage location: Fridge, Freezer, Pantry, or Other.
     Storage guidance:
     • Pantry → dry goods, grains, canned, packaged snacks, cookies, cakes, pastries,
                candy, chips, non-perishables, beverages (sealed/shelf-stable).
     • Fridge → fresh meat, dairy, eggs, cut fruit/vegetables, leftovers,
                cream-based desserts (cheesecake, mousse cake), opened beverages.
     • Freezer → frozen meals, ice cream, raw meat for long storage.
     • Other  → non-food household items.
     IMPORTANT: A "mango cake", "strawberry cake", or similar packaged baked good
     → storage_suggestion = Pantry (it is shelf-stable, NOT a fresh dairy-cream cake).
   - Assign a `sub_category` tag (exactly one) from this fixed list:
     Meat, Dairy, Produce, Frozen, Drinks, Snacks, Condiments, Spices, Grains, Baking, Pantry, Supplements, Cleaning, Other.
     This tag drives smart shelf routing — pick the MOST SPECIFIC match:
     Meat     → any meat, poultry, seafood, fish, shellfish.
     Dairy    → milk, cheese, yogurt, butter, eggs, cream.
     Produce  → fresh or pantry vegetables and fruits (apple, banana, carrot, onion, potato, garlic).
     Frozen   → anything stored in a freezer (frozen meals, ice cream, frozen vegetables).
     Drinks   → soda, juice, coffee, tea, water, alcohol, any beverage.
     Snacks   → chips, nuts, chocolate, crackers, cookies, candy, cakes, mochi, pastries,
                packaged desserts, energy bars. (Any packaged sweet treat = Snacks, NOT Produce.)
     Condiments → soy sauce, ketchup, mayo, vinegar, hot sauce, salad dressing, oyster sauce.
     Spices   → pepper, salt, cumin, cinnamon, any dry spice or herb.
     Grains   → rice, pasta, oats, quinoa, noodles, cereal.
     Baking   → flour, yeast, cocoa powder, baking soda, sugar, baking mixes.
     Pantry   → canned goods, dry staples, packaged/processed foods at room temperature.
     Supplements → protein powder, vitamins, health supplements, medicine.
     Cleaning → detergent, soap, paper towels, cleaning sprays, household supplies.
     Other → anything that doesn't fit above.

     CRITICAL sub_category rules:
     - "Mango cake", "strawberry mochi", "lemon tart" → Snacks (it is a PACKAGED SNACK, not Produce).
     - Only assign Produce for raw, unprocessed fruits and vegetables.
     - A product name containing a fruit/vegetable word (mango, strawberry, lemon) does NOT
       make it Produce if it is a processed/packaged food (cake, juice drink, candy, jam).

4. **ESTIMATE EXPIRY (The "Smart" Part)**:
   - Receipts do not have expiry dates. You MUST estimate `estimated_shelf_life_days` based on the product type and common food safety standards.
   - Fresh Leafy Veg: ~5-7 days
   - Berries/Soft Fruit: ~3-5 days
   - Hardy Veg (Carrots/Potatoes): ~14-30 days
   - Fresh Meat/Seafood: ~3 days
   - Dairy/Eggs: ~14 days
   - Frozen Items: ~90 days
   - Pantry/Canned/Dry Goods: ~365 days
   - Alcohol: ~365 days

# Merchant Specific Knowledge (Expandable)
Current rules for common stores:
- {MERCHANT_RULES['COSTCO']['abbreviations']}
- {MERCHANT_RULES['COSTCO']['notes']}

# Output Format
Return the result strictly as a JSON object following the schema provided. Do not output markdown code blocks, just the raw JSON.
"""
