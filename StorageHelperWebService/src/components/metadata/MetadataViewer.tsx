import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Trash2, Sparkles, Plus, ChevronRight } from 'lucide-react';

interface MetadataViewerProps {
  metadata: Record<string, any>;
  categoryCode?: string;
  isEditing?: boolean;
  onMetadataChange?: (newMetadata: Record<string, any>) => void;
  ingestionMetadata?: {
    ocr_text?: string | null;
    vision_understanding?: any;
    cleaned_text?: string | null;
    page_results?: Array<{ ocr_text?: string | null }>;
  };
}

const MetadataViewer: React.FC<MetadataViewerProps> = ({ 
  metadata, 
  categoryCode, 
  isEditing = false,
  onMetadataChange,
  ingestionMetadata
}) => {
  if (!metadata || Object.keys(metadata).length === 0) return null;

  // Dispatch based on category or content
  if (categoryCode === 'RECEIPT' || categoryCode === 'REC' || metadata.items) {
    return (
      <ReceiptMetadataViewer 
        metadata={metadata} 
        isEditing={isEditing} 
        onMetadataChange={onMetadataChange}
        ingestionMetadata={ingestionMetadata}
      />
    );
  }

  return (
    <GenericMetadataViewer 
      metadata={metadata} 
      isEditing={isEditing} 
      onMetadataChange={onMetadataChange} 
    />
  );
};

/**
 * Receipt Specific Viewer
 */
const ReceiptMetadataViewer: React.FC<{ 
  metadata: any; 
  isEditing: boolean;
  onMetadataChange?: (newMetadata: any) => void;
  ingestionMetadata?: {
    ocr_text?: string | null;
    vision_understanding?: any;
    cleaned_text?: string | null;
    page_results?: Array<{ ocr_text?: string | null }>;
  };
}> = ({ metadata, isEditing, onMetadataChange, ingestionMetadata }) => {
  const items = metadata.items || [];
  const [highlightedItems, setHighlightedItems] = useState<{ [index: number]: string[] }>({});
  const [previewItems, setPreviewItems] = useState<any[] | null>(null);

  const displayItems = previewItems || items;

  // Scroll hint: track whether the table can still scroll right
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const syncScrollHint = useCallback(() => {
    const el = tableScrollRef.current;
    if (!el) return;
    setCanScrollRight(el.scrollLeft < el.scrollWidth - el.clientWidth - 2);
  }, []);

  useEffect(() => {
    // Re-check whenever items change (initial render or data update)
    const id = requestAnimationFrame(syncScrollHint);
    return () => cancelAnimationFrame(id);
  }, [displayItems, syncScrollHint]);
  
  // Listen for AI correction events from ChatInterface
  useEffect(() => {
    const handleCorrection = (e: any) => {
        // Handle "preview" event to show pending changes
        if (e.detail?.previewItems) {
            const previewItemsData = e.detail.previewItems;
            setPreviewItems(previewItemsData); // Store preview items for comparison display
            const highlights: { [index: number]: string[] } = {};
            
            previewItemsData.forEach((newItem: any, index: number) => {
                const oldItem = items[index];
                const changedKeys: string[] = [];
                
                // Normalize values for comparison (handle enum objects, null, undefined, strings)
                const normalizeValue = (val: any): any => {
                    if (val === null || val === undefined) return null;
                    if (typeof val === 'string') {
                        // Normalize strings: trim and handle empty strings
                        return val.trim() || null;
                    }
                    if (typeof val === 'object' && val !== null) {
                        // Handle enum-like objects (e.g., StorageType.PANTRY)
                        if ('value' in val) {
                            const enumVal = val.value;
                            return typeof enumVal === 'string' ? enumVal.trim() : enumVal;
                        }
                        // Handle objects with single property that might be the value
                        const keys = Object.keys(val);
                        if (keys.length === 1) {
                            const singleVal = val[keys[0]];
                            return typeof singleVal === 'string' ? singleVal.trim() : singleVal;
                        }
                        return JSON.stringify(val);
                    }
                    return val;
                };

                if (!oldItem) {
                    // This is a NEW item. Highlight all its relevant display keys
                    Object.keys(newItem).forEach(key => {
                        const val = normalizeValue(newItem[key]);
                        if (val !== null && val !== undefined && val !== '') {
                            changedKeys.push(key);
                        }
                    });
                    if (changedKeys.length > 0) highlights[index] = changedKeys;
                    return;
                }
                
                // Check all keys in newItem
                Object.keys(newItem).forEach(key => {
                    const newVal = newItem[key];
                    const oldVal = oldItem[key];
                    
                    const normalizedNew = normalizeValue(newVal);
                    const normalizedOld = normalizeValue(oldVal);
                    
                    if (JSON.stringify(normalizedNew) !== JSON.stringify(normalizedOld)) {
                        changedKeys.push(key);
                    }
                });
                
                // Also check keys in oldItem that might not be in newItem (important for storage_suggestion)
                Object.keys(oldItem).forEach(key => {
                    if (!(key in newItem)) {
                        const oldVal = oldItem[key];
                        const normalizedOld = normalizeValue(oldVal);
                        // Only mark as changed if old value is not null/undefined/empty
                        if (normalizedOld !== null && normalizedOld !== undefined && normalizedOld !== '') {
                            changedKeys.push(key);
                        }
                    }
                });
                
                // Debug: log storage_suggestion comparison if it exists
                if ('storage_suggestion' in oldItem || 'storage_suggestion' in newItem) {
                    const oldStorage = normalizeValue(oldItem.storage_suggestion);
                    const newStorage = normalizeValue(newItem.storage_suggestion);
                    if (JSON.stringify(oldStorage) !== JSON.stringify(newStorage)) {
                        if (!changedKeys.includes('storage_suggestion')) {
                            changedKeys.push('storage_suggestion');
                        }
                    }
                }
                
                if (changedKeys.length > 0) {
                    highlights[index] = changedKeys;
                }
            });
            setHighlightedItems(highlights);
            return;
        }

        // Handle "cancel" event to clear highlights
        if (e.detail?.action === 'cancel') {
            setHighlightedItems({});
            setPreviewItems(null);
            return;
        }

        // Handle "apply" event (existing logic)
        if (onMetadataChange && e.detail?.correctedItems) {
            const newItems = e.detail.correctedItems;
            
            // Re-calculate diffs to ensure highlights persist during application
            const highlights: { [index: number]: string[] } = {};
            newItems.forEach((newItem: any, index: number) => {
                const oldItem = items[index];
                const changedKeys: string[] = [];
                
                // Normalize function for comparison
                const normalizeValue = (val: any): any => {
                    if (val === null || val === undefined) return null;
                    if (typeof val === 'string') {
                        // Normalize strings: trim and handle empty strings
                        return val.trim() || null;
                    }
                    if (typeof val === 'object' && val !== null) {
                        // Handle enum-like objects (e.g., StorageType.PANTRY)
                        if ('value' in val) {
                            const enumVal = val.value;
                            return typeof enumVal === 'string' ? enumVal.trim() : enumVal;
                        }
                        // Handle objects with single property that might be the value
                        const keys = Object.keys(val);
                        if (keys.length === 1) {
                            const singleVal = val[keys[0]];
                            return typeof singleVal === 'string' ? singleVal.trim() : singleVal;
                        }
                        return JSON.stringify(val);
                    }
                    return val;
                };

                if (!oldItem) {
                    // New item added via AI
                    Object.keys(newItem).forEach(key => {
                        const val = normalizeValue(newItem[key]);
                        if (val !== null && val !== undefined && val !== '') {
                            changedKeys.push(key);
                        }
                    });
                    if (changedKeys.length > 0) highlights[index] = changedKeys;
                    return;
                }
                
                Object.keys(newItem).forEach(key => {
                    const normalizedNew = normalizeValue(newItem[key]);
                    const normalizedOld = normalizeValue(oldItem[key]);
                    if (JSON.stringify(normalizedNew) !== JSON.stringify(normalizedOld)) {
                        changedKeys.push(key);
                    }
                });
                
                // Also check keys in oldItem that might not be in newItem
                Object.keys(oldItem).forEach(key => {
                    if (!(key in newItem)) {
                        const normalizedOld = normalizeValue(oldItem[key]);
                        if (normalizedOld !== null && normalizedOld !== undefined && normalizedOld !== '') {
                            changedKeys.push(key);
                        }
                    }
                });
                
                // Consistency fix: Include the storage_suggestion check in apply logic too
                if ('storage_suggestion' in oldItem || 'storage_suggestion' in newItem) {
                    const oldStorage = normalizeValue(oldItem.storage_suggestion);
                    const newStorage = normalizeValue(newItem.storage_suggestion);
                    if (JSON.stringify(oldStorage) !== JSON.stringify(newStorage)) {
                        if (!changedKeys.includes('storage_suggestion')) {
                            changedKeys.push('storage_suggestion');
                        }
                    }
                }
                
                if (changedKeys.length > 0) highlights[index] = changedKeys;
            });
            
            setHighlightedItems(highlights);
            setPreviewItems(null); // Clear preview items after applying
            
            // Update metadata with new items
            const updatedMetadata = { ...metadata, items: newItems };
            onMetadataChange(updatedMetadata);
            
            // Update ChatInterface's activeContext with the new items
            // This ensures that subsequent corrections use the updated items
            const ocrText = ingestionMetadata?.ocr_text || 
                           ingestionMetadata?.page_results?.[0]?.ocr_text || 
                           null;
            
            window.dispatchEvent(new CustomEvent('update-correction-context', {
                detail: {
                    context: {
                        type: 'correction',
                        data: newItems, // Use updated items
                        metadata: {
                            ocr_text: ocrText,
                            vision_understanding: ingestionMetadata?.vision_understanding,
                            cleaned_text: ingestionMetadata?.cleaned_text,
                            items: newItems // Use updated items
                        }
                    }
                }
            }));
            
            setTimeout(() => {
                setHighlightedItems({});
            }, 3000);
        }
    };
    
    window.addEventListener('apply-correction', handleCorrection);
    return () => window.removeEventListener('apply-correction', handleCorrection);
  }, [metadata, onMetadataChange, items]);

  const handleHeaderChange = (key: string, value: any) => {
    if (onMetadataChange) {
      onMetadataChange({ ...metadata, [key]: value });
    }
  };

  const handleItemChange = (index: number, key: string, value: any) => {
    if (onMetadataChange) {
      const newItems = [...items];
      newItems[index] = { ...newItems[index], [key]: value };
      onMetadataChange({ ...metadata, items: newItems });
    }
  };

  const handleItemDelete = (index: number) => {
    if (onMetadataChange && window.confirm(`Are you sure you want to delete "${items[index]?.product_name || 'this item'}"?`)) {
      const newItems = items.filter((_: any, i: number) => i !== index);
      onMetadataChange({ ...metadata, items: newItems });
    }
  };

  const handleAddItem = () => {
    if (onMetadataChange) {
      const newItem = {
        product_name: '',
        category: '',
        quantity: 1,
        unit: 'pcs',
        storage_suggestion: '',
        estimated_shelf_life_days: 7,
        original_text: 'Manually added'
      };
      const newItems = [...items, newItem];
      const newIndex = newItems.length - 1;
      
      onMetadataChange({ ...metadata, items: newItems });
      
      // Trigger highlight for the new item
      setHighlightedItems(prev => ({
        ...prev,
        [newIndex]: Object.keys(newItem)
      }));
      
      // Clear highlight after 3s
      setTimeout(() => {
        setHighlightedItems(prev => {
          const next = { ...prev };
          delete next[newIndex];
          return next;
        });
      }, 3000);
    }
  };

  const openAiChat = () => {
    // Always use the latest items from metadata, not the closure value
    const currentItems = metadata.items || [];
    
    // Collect OCR text from page_results or ingestionMetadata
    const ocrText = ingestionMetadata?.ocr_text || 
                     ingestionMetadata?.page_results?.[0]?.ocr_text || 
                     null;
    
    window.dispatchEvent(new CustomEvent('open-chat', { 
        detail: { 
            context: {
                type: 'correction',
                data: currentItems, // Use current items from metadata
                metadata: {
                    ocr_text: ocrText,
                    vision_understanding: ingestionMetadata?.vision_understanding,
                    cleaned_text: ingestionMetadata?.cleaned_text,
                    items: currentItems // Use current items from metadata
                }
            }
        } 
    }));
  };

  const getHighlightClass = (index: number, key: string) => {
    if (highlightedItems[index]?.includes(key)) {
        return "ring-2 ring-home-success-500 bg-home-success-50 transition-all duration-500 animate-pulse";
    }
    return "";
  };

  // Format value for display (truncate long values)
  const formatValue = (val: any, maxLength: number = 20): string => {
    if (val === null || val === undefined) return '';
    if (typeof val === 'object' && val !== null) {
      if ('value' in val) return String(val.value).substring(0, maxLength);
      return JSON.stringify(val).substring(0, maxLength);
    }
    const str = String(val);
    return str.length > maxLength ? str.substring(0, maxLength) + '...' : str;
  };

  // Get change preview text for a field
  const getChangePreview = (index: number, key: string): string | null => {
    if (!previewItems || !previewItems[index] || !highlightedItems[index]?.includes(key)) {
      return null;
    }
    const oldItem = items[index];
    const newItem = previewItems[index];
    if (!newItem) return null;
    
    if (!oldItem) {
      return `New item added via AI`;
    }
    
    const oldVal = formatValue(oldItem[key], 15);
    const newVal = formatValue(newItem[key], 15);
    
    if (oldVal === newVal) return null;
    
    return `${oldVal} → ${newVal}`;
  };

  return (
    <div className="space-y-4">
      {/* Receipt Header Info */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
        { (metadata.merchant !== undefined || isEditing) && (
          <div className="p-2 bg-white rounded border border-home-primary-100">
            <span className="text-[10px] text-home-text-light font-medium uppercase">Merchant</span>
            {isEditing ? (
              <input 
                type="text" 
                value={metadata.merchant || ''} 
                onChange={(e) => handleHeaderChange('merchant', e.target.value)}
                className="w-full text-sm font-bold border-none p-0 focus:ring-0 bg-transparent"
              />
            ) : (
              <p className="text-sm text-home-text-dark font-bold">{metadata.merchant}</p>
            )}
          </div>
        )}
        {(metadata.purchase_date !== undefined || isEditing) && (
          <div className="p-2 bg-white rounded border border-home-primary-100">
            <span className="text-[10px] text-home-text-light font-medium uppercase">Date</span>
            {isEditing ? (
              <input 
                type="date" 
                value={metadata.purchase_date || ''} 
                onChange={(e) => handleHeaderChange('purchase_date', e.target.value)}
                className="w-full text-sm font-medium border-none p-0 focus:ring-0 bg-transparent"
              />
            ) : (
              <p className="text-sm text-home-text-dark font-medium">{metadata.purchase_date}</p>
            )}
          </div>
        )}
        {(metadata.total_payment !== undefined || isEditing) && (
          <div className="p-2 bg-white rounded border border-home-primary-100">
            <span className="text-[10px] text-home-text-light font-medium uppercase">Total</span>
            {isEditing ? (
              <div className="flex items-center">
                <span className="text-sm font-bold text-home-primary-600 mr-1">{metadata.currency === 'USD' ? '$' : ''}</span>
                <input 
                  type="number" 
                  step="0.01"
                  value={metadata.total_payment || 0} 
                  onChange={(e) => handleHeaderChange('total_payment', parseFloat(e.target.value))}
                  className="w-full text-sm font-bold border-none p-0 focus:ring-0 bg-transparent text-home-primary-600"
                />
              </div>
            ) : (
              <p className="text-sm text-home-primary-600 font-bold">
                {metadata.currency === 'USD' ? '$' : ''}{metadata.total_payment} {metadata.currency !== 'USD' ? metadata.currency : ''}
              </p>
            )}
          </div>
        )}
      </div>

      {/* Items Table Header & Actions */}
      <div className="flex justify-between items-center mb-2">
        <h3 className="text-sm font-bold text-home-text-dark">Items ({displayItems.length})</h3>
        {isEditing && (
          <div className="flex items-center gap-3 bg-purple-50/50 p-1.5 rounded-xl border border-purple-100">
             <span className="text-[10px] text-purple-600/70 font-medium italic hidden md:block px-2">
                "Change milk to 1 gallon..."
             </span>
             <button 
                onClick={openAiChat}
                className="text-xs font-bold text-white bg-gradient-to-r from-purple-500 to-indigo-600 hover:from-purple-600 hover:to-indigo-700 px-3 py-1.5 rounded-lg shadow-sm hover:shadow-md transition-all flex items-center gap-1.5"
             >
                <Sparkles size={14} className="animate-pulse" />
                Chat to Fix
             </button>
          </div>
        )}
      </div>

      {/* Items Table */}
      <div className="relative">
        {/* Right-edge fade gradient — visible when there is still content to scroll to */}
        {canScrollRight && (
          <div
            className="pointer-events-none absolute right-0 top-0 bottom-0 w-12 z-10 flex items-center justify-end pr-1"
            style={{ background: 'linear-gradient(to right, transparent 0%, rgba(255,255,255,0.95) 100%)' }}
          >
            <ChevronRight size={16} className="text-stone-400 animate-pulse" />
          </div>
        )}
        <div
          ref={tableScrollRef}
          onScroll={syncScrollHint}
          className="overflow-x-auto rounded-home border border-home-primary-200"
        >
        <table className="min-w-full divide-y divide-home-primary-200">
          <thead className="bg-home-primary-50">
            <tr>
              <th className="px-4 py-2 text-left text-xs font-semibold text-home-text-dark uppercase tracking-wider">Product</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-home-text-dark uppercase tracking-wider">Category</th>
              <th className="px-4 py-2 text-center text-xs font-semibold text-home-text-dark uppercase tracking-wider">Qty</th>
              <th className="px-4 py-2 text-center text-xs font-semibold text-home-text-dark uppercase tracking-wider">Unit</th>
              <th className="px-4 py-2 text-left text-xs font-semibold text-home-text-dark uppercase tracking-wider">Storage</th>
              <th className="px-4 py-2 text-center text-xs font-semibold text-home-text-dark uppercase tracking-wider">Life</th>
              {isEditing && (
                <th className="px-4 py-2 text-center text-xs font-semibold text-home-text-dark uppercase tracking-wider w-16">Action</th>
              )}
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-home-primary-100">
            {displayItems.map((item: any, idx: number) => (
              <tr key={idx} className="hover:bg-home-primary-50/50 transition-colors">
                <td className="px-4 py-3">
                  {isEditing ? (
                    <div className="flex flex-col gap-1">
                      <input 
                        type="text" 
                        value={item.product_name || ''} 
                        onChange={(e) => handleItemChange(idx, 'product_name', e.target.value)}
                        className={`w-full text-sm font-medium border-b border-dashed border-home-primary-200 p-0 focus:ring-0 ${getHighlightClass(idx, 'product_name')}`}
                        disabled={!!previewItems}
                      />
                      {getChangePreview(idx, 'product_name') && (
                        <span className="text-[10px] text-home-success-600 font-medium italic">
                          {getChangePreview(idx, 'product_name')}
                        </span>
                      )}
                      <input 
                        type="text" 
                        value={item.original_text || ''} 
                        onChange={(e) => handleItemChange(idx, 'original_text', e.target.value)}
                        className="w-full text-[10px] text-home-text-light border-none p-0 focus:ring-0 bg-transparent"
                        disabled={!!previewItems}
                      />
                    </div>
                  ) : (
                    <>
                      <p className="text-sm font-medium text-home-text-dark">{item.product_name}</p>
                      <p className="text-[10px] text-home-text-light truncate max-w-[150px]">{item.original_text}</p>
                    </>
                  )}
                </td>
                <td className="px-4 py-3">
                  {isEditing ? (
                    <div className="flex flex-col gap-1">
                      <input 
                        type="text" 
                        value={item.category || ''} 
                        onChange={(e) => handleItemChange(idx, 'category', e.target.value)}
                        className={`w-full text-xs font-medium border border-home-primary-200 rounded px-1 focus:ring-0 ${getHighlightClass(idx, 'category')}`}
                        disabled={!!previewItems}
                      />
                      {getChangePreview(idx, 'category') && (
                        <span className="text-[10px] text-home-success-600 font-medium italic">
                          {getChangePreview(idx, 'category')}
                        </span>
                      )}
                    </div>
                  ) : (
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-home-secondary-100 text-home-secondary-800">
                      {item.category}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-center">
                  {isEditing ? (
                    <div className="flex flex-col items-center gap-1">
                      <input 
                        type="text" 
                        value={item.quantity || ''} 
                        onChange={(e) => handleItemChange(idx, 'quantity', e.target.value)}
                        className={`w-12 text-center text-sm border border-home-primary-200 rounded focus:ring-0 ${getHighlightClass(idx, 'quantity')}`}
                        disabled={!!previewItems}
                      />
                      {getChangePreview(idx, 'quantity') && (
                        <span className="text-[10px] text-home-success-600 font-medium italic">
                          {getChangePreview(idx, 'quantity')}
                        </span>
                      )}
                    </div>
                  ) : (
                    <span className="text-sm text-home-text-dark">{item.quantity}</span>
                  )}
                </td>
                <td className="px-4 py-3 text-center">
                  {isEditing ? (
                    <div className="flex flex-col items-center gap-1">
                      <input 
                        type="text" 
                        value={item.unit || ''} 
                        onChange={(e) => handleItemChange(idx, 'unit', e.target.value)}
                        className={`w-12 text-center text-sm border border-home-primary-200 rounded focus:ring-0 ${getHighlightClass(idx, 'unit')}`}
                        placeholder="pcs"
                        disabled={!!previewItems}
                      />
                      {getChangePreview(idx, 'unit') && (
                        <span className="text-[10px] text-home-success-600 font-medium italic">
                          {getChangePreview(idx, 'unit')}
                        </span>
                      )}
                    </div>
                  ) : (
                    <span className="text-sm text-home-text-dark text-opacity-70">{item.unit || '-'}</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {isEditing ? (
                    <div className="flex flex-col gap-1">
                      <input 
                        type="text" 
                        value={item.storage_suggestion || ''} 
                        onChange={(e) => handleItemChange(idx, 'storage_suggestion', e.target.value)}
                        className={`w-full text-sm font-medium border border-home-primary-200 rounded px-1 focus:ring-0 ${getHighlightClass(idx, 'storage_suggestion')}`}
                        disabled={!!previewItems}
                      />
                      {getChangePreview(idx, 'storage_suggestion') && (
                        <span className="text-[10px] text-home-success-600 font-medium italic">
                          {getChangePreview(idx, 'storage_suggestion')}
                        </span>
                      )}
                    </div>
                  ) : (
                    <div className="flex flex-col">
                      {item.location_name ? (
                        <span className="text-sm text-green-600 font-bold">{item.location_name}</span>
                      ) : (
                        <span className="text-sm text-home-text-dark font-medium">{item.storage_suggestion}</span>
                      )}
                      {!item.location_id && item.storage_suggestion && (
                        <span className="text-[10px] text-amber-600 font-medium italic">AI Suggested</span>
                      )}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 text-center">
                  {isEditing ? (
                    <div className="flex flex-col items-center gap-1">
                      <div className="flex items-center justify-center">
                        <input 
                          type="number" 
                          value={item.estimated_shelf_life_days || 0} 
                          onChange={(e) => handleItemChange(idx, 'estimated_shelf_life_days', parseInt(e.target.value))}
                          className={`w-12 text-center text-xs border border-home-primary-200 rounded focus:ring-0 ${getHighlightClass(idx, 'estimated_shelf_life_days')}`}
                          disabled={!!previewItems}
                        />
                        <span className="text-[10px] ml-0.5">d</span>
                      </div>
                      {getChangePreview(idx, 'estimated_shelf_life_days') && (
                        <span className="text-[10px] text-home-success-600 font-medium italic">
                          {getChangePreview(idx, 'estimated_shelf_life_days')}
                        </span>
                      )}
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center">
                      <span className={`text-xs font-bold ${item.estimated_shelf_life_days <= 7 ? 'text-home-error-600' : 'text-home-success-600'}`}>
                        {item.estimated_shelf_life_days !== undefined ? `${item.estimated_shelf_life_days}d` : '-'}
                      </span>
                      {item.expiry_date && (
                        <span className="text-[9px] text-home-text-light/70 whitespace-nowrap">
                          {new Date(item.expiry_date).toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' })}
                        </span>
                      )}
                    </div>
                  )}
                </td>
                {isEditing && (
                  <td className="px-4 py-3 text-center">
                    <button
                      onClick={() => handleItemDelete(idx)}
                      className="p-1.5 hover:bg-red-50 text-red-500 hover:text-red-700 rounded transition-colors"
                      title="Delete item"
                      disabled={!!previewItems}
                    >
                      <Trash2 size={16} />
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {isEditing && !previewItems && (
          <div className="p-3 bg-home-primary-50/50 flex justify-center border-t border-home-primary-100">
            <button
              onClick={handleAddItem}
              className="flex items-center gap-2 px-4 py-2 bg-white border border-home-primary-300 rounded-xl text-sm font-bold text-home-primary-600 hover:bg-home-primary-50 hover:border-home-primary-400 transition-all shadow-sm active:scale-95"
            >
              <Plus size={16} />
              Add New Item
            </button>
          </div>
        )}
        </div>{/* end overflow-x-auto */}
      </div>{/* end relative wrapper */}
    </div>
  );
};

/**
 * Generic Key-Value Viewer
 */
const GenericMetadataViewer: React.FC<{ 
  metadata: Record<string, any>;
  isEditing: boolean;
  onMetadataChange?: (newMetadata: Record<string, any>) => void;
}> = ({ metadata, isEditing, onMetadataChange }) => {
  const [highlightedKeys, setHighlightedKeys] = useState<string[]>([]);
  const filteredEntries = Object.entries(metadata).filter(([key]) => key !== 'items');

  const handleChange = (key: string, value: any) => {
    if (onMetadataChange) {
      onMetadataChange({ ...metadata, [key]: value });
    }
  };

  const handleAddField = () => {
    if (onMetadataChange) {
      const baseName = `new_field_${filteredEntries.length + 1}`;
      let newKey = baseName;
      let suffix = 1;
      
      // Prevent collision with existing keys
      while (newKey in metadata) {
        newKey = `${baseName}_${suffix}`;
        suffix++;
      }

      onMetadataChange({ ...metadata, [newKey]: '' });
      
      // Trigger highlight for the new field
      setHighlightedKeys(prev => [...prev, newKey]);
      setTimeout(() => {
        setHighlightedKeys(prev => prev.filter(k => k !== newKey));
      }, 3000);
    }
  };

  const getHighlightClass = (key: string) => {
    if (highlightedKeys.includes(key)) {
      return "ring-2 ring-home-success-500 bg-home-success-50 transition-all duration-500 animate-pulse";
    }
    return "";
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {filteredEntries.map(([key, value], idx) => (
          <div key={idx} className={`flex flex-col p-2 bg-white rounded border border-home-primary-100 shadow-sm ${getHighlightClass(key)}`}>
            {isEditing ? (
              <input 
                type="text"
                value={key.replace(/_/g, ' ')}
                onChange={(e) => {
                  // Use symmetric 1-to-1 replacement to preserve consecutive underscores
                  let newKey = e.target.value.replace(/ /g, '_');
                  
                  // Prevent using reserved 'items' key which crashes ReceiptMetadataViewer
                  if (newKey === 'items') newKey = 'items_metadata';
                  
                  if (newKey === key) return;
                  
                  const newMetadata: Record<string, any> = {};
                  // Preserve key order and prevent data loss on collision
                  Object.keys(metadata).forEach(k => {
                    if (k === key) {
                      newMetadata[newKey] = metadata[k];
                    } else if (k === newKey) {
                      // Collision! Rename existing key to avoid loss
                      let suffix = 1;
                      while (`${k}_${suffix}` in metadata || `${k}_${suffix}` in newMetadata) {
                        suffix++;
                      }
                      newMetadata[`${k}_${suffix}`] = metadata[k];
                    } else {
                      newMetadata[k] = metadata[k];
                    }
                  });
                  onMetadataChange?.(newMetadata);
                }}
                className="text-[10px] text-home-text-light font-medium uppercase tracking-tight border-none p-0 focus:ring-0 bg-transparent"
              />
            ) : (
              <span className="text-[10px] text-home-text-light font-medium uppercase tracking-tight">{key.replace(/_/g, ' ')}</span>
            )}
            {isEditing ? (
              <input 
                type="text" 
                value={typeof value === 'object' ? JSON.stringify(value) : String(value)} 
                onChange={(e) => handleChange(key, e.target.value)}
                className="w-full text-sm text-home-text-dark font-semibold border-none p-0 focus:ring-0 bg-transparent"
              />
            ) : (
              <span className="text-sm text-home-text-dark font-semibold">
                {typeof value === 'object' ? JSON.stringify(value) : String(value)}
              </span>
            )}
          </div>
        ))}
      </div>
      {isEditing && (
        <div className="flex justify-center">
          <button
            onClick={handleAddField}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-home-primary-300 rounded-xl text-sm font-bold text-home-primary-600 hover:bg-home-primary-50 hover:border-home-primary-400 transition-all shadow-sm active:scale-95"
          >
            <Plus size={16} />
            Add New Field
          </button>
        </div>
      )}
    </div>
  );
};

export default MetadataViewer;
