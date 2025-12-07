# Storage Data Files

This directory contains JSON files representing storage-related data for testing and development.

## Files

### `locations.json`
Contains all storage location definitions following the `storage_location` table schema.

Each location includes:
- `id`: Unique location identifier
- `name`: Location name (e.g., "Box under the bed")
- `description`: Detailed description
- `photo_url`: Optional photo URL
- `parent_id`: Optional parent location ID for hierarchy
- `created_at`: Creation timestamp
- `updated_at`: Last update timestamp

### `document_categories.json`
Defines all available document categories following the `document_category` table schema.

Each category includes:
- `id`: Unique category identifier
- `code`: Category code (e.g., "TAX", "VISA", "MED", "INS")
- `name`: Display name (e.g., "Tax Documents", "Immigration Documents")
- `description`: Category description
- `created_at`: Creation timestamp
- `updated_at`: Last update timestamp

### `location_document_types.json`
Defines which document types/categories can be stored in each location, following the `location_document_type` table schema.

Each entry includes:
- `id`: Unique identifier
- `location_id`: Reference to location
- `document_type_id`: Optional specific document type ID (null = applies to all types in category)
- `category_id`: Document category ID (references document_categories.json)
- `priority`: Recommendation priority (higher = preferred location)
- `is_allowed`: Whether this type is allowed in this location
- `created_at`: Creation timestamp
- `updated_at`: Last update timestamp

## Usage

These JSON files can be used for:
- Testing the ingestion pipeline
- Testing location recommendations
- Simulating storage rules and constraints
- Development and testing purposes

## Relationships

- `document_categories.json` defines all available categories (TAX, VISA, MED, INS)
- `locations.json` defines all storage locations
- `location_document_types.json` links locations to categories/types, defining storage rules
