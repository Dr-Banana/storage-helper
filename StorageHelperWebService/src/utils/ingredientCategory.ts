/**
 * Ingredient category metadata and mapper.
 */

export type GroceryCategory = 'produce' | 'meat' | 'dairy' | 'grain' | 'spice' | 'other';

export interface CategoryMeta {
  label: string;
  emoji: string;
  order: number;
}

export const CATEGORY_META: Record<GroceryCategory, CategoryMeta> = {
  produce: { label: 'Produce',             emoji: '🥬', order: 1 },
  meat:    { label: 'Meat & Seafood',      emoji: '🥩', order: 2 },
  dairy:   { label: 'Dairy & Eggs',        emoji: '🥛', order: 3 },
  grain:   { label: 'Grains & Pantry',     emoji: '🌾', order: 4 },
  spice:   { label: 'Spices & Condiments', emoji: '🧂', order: 5 },
  other:   { label: 'Other',               emoji: '📦', order: 6 },
};

export function resolveIngredientCategory(backendCategory?: string): GroceryCategory {
  const map: Record<string, GroceryCategory> = {
    vegetable: 'produce',
    protein:   'meat',
    dairy:     'dairy',
    grain:     'grain',
    spice:     'spice',
    other:     'other',
  };
  return map[backendCategory?.toLowerCase() ?? ''] ?? 'other';
}
