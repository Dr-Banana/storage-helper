# Kitchen Agent Design Document

## 1. Overview
The "Kitchen Agent" is a specialized pivot of the Storage Helper project, designed to help users manage their kitchen inventory (ingredients, seasonings, condiments) through AI-powered image recognition and metadata extraction.

## 2. Core Functionality
- **Dual-mode Ingestion**:
    - **Bulk (Receipt Scanning)**: PRIMARY MODE. Take a photo of a shopping receipt. AI extracts multiple items at once.
    - **Single (Product Photography)**: FALLBACK MODE. Take a photo of a specific item for detailed metadata or when the receipt is missing.
- **Intelligent Extraction**: Uses OCR and Vision AI (Gemini 2.0/2.5) to identify items, brands, and date formats across different regions (CN/US/JP).
- **Proactive Inventory Management**: Focuses on expiry tracking and "active inventory" status rather than precise weight/quantity.

## 3. Metadata Schema (Kitchen Focus)
The following metadata fields will be prioritized for extraction:

| Field | Description | Example |
|-------|-------------|---------|
| `product_name` | Name of the product | 海天生抽 (Haitian Light Soy Sauce) |
| `manufacturer` | The brand or producer | 海天 (Haitian) |
| `expiry_date` | Expiration date extracted from the package | 2026-05-20 |
| `production_date` | Date of manufacture | 2024-05-20 |
| `shelf_life` | Duration of storage (if expiry date is missing) | 12 months, 60 days |
| `shelf_life_after_opening` | Duration after opening | 3 months |
| `status` | Current state of the item | `unopened`, `opened`, `finished` |
| `opened_at` | Timestamp when the item was opened | 2026-01-13T10:00:00Z |
| `source_type` | How the item was added | `receipt`, `manual_photo` |
| `category` | Item category | Seasoning |
| `sub_category` | More specific type | Soy Sauce |
| `state` | Physical state (Liquid/Powder/Solid/Paste) | Liquid |
| `quantity` | Net weight or volume | 500ml |
| `storage_requirement` | Storage conditions (Room Temp/Refrigerated/Frozen) | Refrigerated after opening |
| `tags` | Descriptive tags | Salty, Umami, Cooking |

## 4. Intelligent Expiration & Regional Logic
Kitchen OCR must handle diverse labeling standards:
1.  **China (CN)**: Usually labels `production_date` + `shelf_life` (e.g., 18 months). System must perform: `Production + Shelf Life = Expiry`.
2.  **USA/Global**: Labels `Best By` or `EXP`. Direct extraction.
3.  **Japan (JP)**: Labels `赏味期限` (Best taste) or `消费期限` (Safe to eat). AI should treat `赏味期限` as a "soft" expiry with a warning.
4.  **Fallback (Knowledge Base)**: If no date is found (e.g., Receipt item or fresh produce), AI provides estimates based on product category (e.g., Milk = 7 days).

## 5. Frictionless UX Strategy
To prevent the "toy effect," the system prioritizes automation over manual input:
- **Receipt First**: 30 items from a Costco trip should be processed in one photo. Items are initially marked as `unopened`.
- **Chat/Voice "Open" Trigger**: User says "I just opened the oyster sauce." System updates `status` to `opened` and sets `opened_at`.
- **Proactive Nudging**: Chatbot asks weekly: "You bought Milk 6 days ago, have you opened it?" or "This soy sauce has been open for 5 months, is it finished?"
- **Low-Maintenance Cleanup**: Focus on cleaning up "expired" or "likely consumed" items rather than precise inventory counts.

## 6. Data Model & Granularity
- **Individual Tracking**: Each physical item is a unique record. Buying two bottles of Soy Sauce results in two entries.
- **State Independence**: One bottle can be `opened` in the kitchen, while the other is `unopened` in the pantry.

## 7. System Adaptation

### 4.1 AI Orchestra Service Changes
- **`category_config.py`**: Update `ALLOWED_CATEGORY_TYPES` and `CATEGORY_METADATA_FIELDS` to include kitchen-specific categories (e.g., `SEASONING`, `INGREDIENT`, `CONDIMENT`, `SNACK`).
- **`vision.py`**: Enhance the Vision prompt to focus on identifying kitchen products, recognizing brands, and locating date stamps (often in hard-to-read places).
- **`recommendation.py`**: Adjust the recommendation logic to suggest kitchen storage locations (e.g., "Seasoning Rack", "Refrigerator Second Shelf", "Pantry").

### 4.2 Data Storage Service Changes
- Ensure the `metadata` JSON field in the `documents` or `pages` table can accommodate the new kitchen-specific fields.
- Update canonical categories and default locations for kitchen use.

### 4.3 Web Service Changes
- Update the UI to reflect the "Kitchen" theme.
- Enhance the capture/upload flow for quick product scanning.
- Add an "Expiry Tracker" dashboard view.

## 8. Implementation Roadmap
1.  **Phase 1: Receipt & Knowledge Base**: Implement bulk item extraction from receipts and category-based fallback shelf-life estimates.
2.  **Phase 2: Regional Date Logic**: Update AI prompts to handle CN/US/JP date formatting and calculation.
3.  **Phase 3: State Loop**: Add `status` (unopened/opened/finished) to schema and implement "Open" triggers via UI/Chat.
4.  **Phase 4: Proactive Nudging**: Implement the "Weekly Cleanup" and "Status Inquiry" chatbot logic.

