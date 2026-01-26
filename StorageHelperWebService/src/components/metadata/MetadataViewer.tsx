import React, { useState, useEffect } from 'react';
import { Trash2, Sparkles } from 'lucide-react';

interface MetadataViewerProps {
  metadata: Record<string, any>;
  categoryCode?: string;
  isEditing?: boolean;
  onMetadataChange?: (newMetadata: Record<string, any>) => void;
}

const MetadataViewer: React.FC<MetadataViewerProps> = ({ 
  metadata, 
  categoryCode, 
  isEditing = false,
  onMetadataChange 
}) => {
  if (!metadata || Object.keys(metadata).length === 0) return null;

  // Dispatch based on category or content
  if (categoryCode === 'RECEIPT' || categoryCode === 'REC' || metadata.items) {
    return (
      <ReceiptMetadataViewer 
        metadata={metadata} 
        isEditing={isEditing} 
        onMetadataChange={onMetadataChange} 
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
}> = ({ metadata, isEditing, onMetadataChange }) => {
  const items = metadata.items || [];
  const [highlightedItems, setHighlightedItems] = useState<{ [index: number]: string[] }>({});
  
  // Listen for AI correction events from ChatInterface
  useEffect(() => {
    const handleCorrection = (e: any) => {
        // Handle "preview" event to show pending changes
        if (e.detail?.previewItems) {
            const previewItems = e.detail.previewItems;
            const highlights: { [index: number]: string[] } = {};
            
            previewItems.forEach((newItem: any, index: number) => {
                const oldItem = items[index];
                if (!oldItem) return;
                
                const changedKeys: string[] = [];
                Object.keys(newItem).forEach(key => {
                    if (JSON.stringify(newItem[key]) !== JSON.stringify(oldItem[key])) {
                        changedKeys.push(key);
                    }
                });
                
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
            return;
        }

        // Handle "apply" event (existing logic)
        if (onMetadataChange && e.detail?.correctedItems) {
            const newItems = e.detail.correctedItems;
            
            // Re-calculate diffs to ensure highlights persist during application
            const highlights: { [index: number]: string[] } = {};
            newItems.forEach((newItem: any, index: number) => {
                const oldItem = items[index];
                if (!oldItem) return;
                const changedKeys: string[] = [];
                Object.keys(newItem).forEach(key => {
                    if (JSON.stringify(newItem[key]) !== JSON.stringify(oldItem[key])) {
                        changedKeys.push(key);
                    }
                });
                if (changedKeys.length > 0) highlights[index] = changedKeys;
            });
            
            setHighlightedItems(highlights);
            onMetadataChange({ ...metadata, items: newItems });
            
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

  const openAiChat = () => {
    window.dispatchEvent(new CustomEvent('open-chat', { 
        detail: { 
            context: {
                type: 'correction',
                data: items
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
        <h3 className="text-sm font-bold text-home-text-dark">Items ({items.length})</h3>
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
      <div className="overflow-x-auto rounded-home border border-home-primary-200">
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
            {items.map((item: any, idx: number) => (
              <tr key={idx} className="hover:bg-home-primary-50/50 transition-colors">
                <td className="px-4 py-3">
                  {isEditing ? (
                    <>
                      <input 
                        type="text" 
                        value={item.product_name || ''} 
                        onChange={(e) => handleItemChange(idx, 'product_name', e.target.value)}
                        className={`w-full text-sm font-medium border-b border-dashed border-home-primary-200 p-0 focus:ring-0 mb-1 ${getHighlightClass(idx, 'product_name')}`}
                      />
                      <input 
                        type="text" 
                        value={item.original_text || ''} 
                        onChange={(e) => handleItemChange(idx, 'original_text', e.target.value)}
                        className="w-full text-[10px] text-home-text-light border-none p-0 focus:ring-0 bg-transparent"
                      />
                    </>
                  ) : (
                    <>
                      <p className="text-sm font-medium text-home-text-dark">{item.product_name}</p>
                      <p className="text-[10px] text-home-text-light truncate max-w-[150px]">{item.original_text}</p>
                    </>
                  )}
                </td>
                <td className="px-4 py-3">
                  {isEditing ? (
                    <input 
                      type="text" 
                      value={item.category || ''} 
                      onChange={(e) => handleItemChange(idx, 'category', e.target.value)}
                      className={`w-full text-xs font-medium border border-home-primary-200 rounded px-1 focus:ring-0 ${getHighlightClass(idx, 'category')}`}
                    />
                  ) : (
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-home-secondary-100 text-home-secondary-800">
                      {item.category}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-center">
                  {isEditing ? (
                    <input 
                      type="text" 
                      value={item.quantity || ''} 
                      onChange={(e) => handleItemChange(idx, 'quantity', e.target.value)}
                      className={`w-12 text-center text-sm border border-home-primary-200 rounded focus:ring-0 ${getHighlightClass(idx, 'quantity')}`}
                    />
                  ) : (
                    <span className="text-sm text-home-text-dark">{item.quantity}</span>
                  )}
                </td>
                <td className="px-4 py-3 text-center">
                  {isEditing ? (
                    <input 
                      type="text" 
                      value={item.unit || ''} 
                      onChange={(e) => handleItemChange(idx, 'unit', e.target.value)}
                      className={`w-12 text-center text-sm border border-home-primary-200 rounded focus:ring-0 ${getHighlightClass(idx, 'unit')}`}
                      placeholder="pcs"
                    />
                  ) : (
                    <span className="text-sm text-home-text-dark text-opacity-70">{item.unit || '-'}</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {isEditing ? (
                    <input 
                      type="text" 
                      value={item.storage_suggestion || ''} 
                      onChange={(e) => handleItemChange(idx, 'storage_suggestion', e.target.value)}
                      className={`w-full text-sm font-medium border border-home-primary-200 rounded px-1 focus:ring-0 ${getHighlightClass(idx, 'storage_suggestion')}`}
                    />
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
                    <div className="flex items-center justify-center">
                      <input 
                        type="number" 
                        value={item.estimated_shelf_life_days || 0} 
                        onChange={(e) => handleItemChange(idx, 'estimated_shelf_life_days', parseInt(e.target.value))}
                        className={`w-12 text-center text-xs border border-home-primary-200 rounded focus:ring-0 ${getHighlightClass(idx, 'estimated_shelf_life_days')}`}
                      />
                      <span className="text-[10px] ml-0.5">d</span>
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
                    >
                      <Trash2 size={16} />
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
  const filteredEntries = Object.entries(metadata).filter(([key]) => key !== 'items');

  const handleChange = (key: string, value: any) => {
    if (onMetadataChange) {
      onMetadataChange({ ...metadata, [key]: value });
    }
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {filteredEntries.map(([key, value]) => (
        <div key={key} className="flex flex-col p-2 bg-white rounded border border-home-primary-100 shadow-sm">
          <span className="text-[10px] text-home-text-light font-medium uppercase tracking-tight">{key.replace(/_/g, ' ')}</span>
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
  );
};

export default MetadataViewer;
