import React from 'react';
import { 
  Apple, 
  Carrot, 
  Beef, 
  Fish, 
  Milk, 
  Egg, 
  Package, 
  ChefHat, 
  Cookie, 
  Coffee, 
  Snowflake, 
  Home, 
  FileText,
  HelpCircle
} from 'lucide-react';

interface CategoryIconProps {
  categoryCode: string;
  className?: string;
  size?: number;
}

const CategoryIcon: React.FC<CategoryIconProps> = ({ categoryCode, className = "", size = 24 }) => {
  const code = categoryCode?.toUpperCase() || 'UNKNOWN';

  const iconMap: Record<string, { icon: any, color: string, bgColor: string }> = {
    'FRUIT': { icon: Apple, color: 'text-orange-600', bgColor: 'bg-orange-100' },
    'VEGETABLE': { icon: Carrot, color: 'text-green-600', bgColor: 'bg-green-100' },
    'MEAT': { icon: Beef, color: 'text-red-600', bgColor: 'bg-red-100' },
    'SEAFOOD': { icon: Fish, color: 'text-blue-600', bgColor: 'bg-blue-100' },
    'DAIRY': { icon: Milk, color: 'text-sky-600', bgColor: 'bg-sky-100' },
    'EGG': { icon: Egg, color: 'text-yellow-600', bgColor: 'bg-yellow-100' },
    'PANTRY': { icon: Package, color: 'text-amber-700', bgColor: 'bg-amber-100' },
    'SEASONING': { icon: ChefHat, color: 'text-purple-600', bgColor: 'bg-purple-100' },
    'SNACK': { icon: Cookie, color: 'text-rose-500', bgColor: 'bg-rose-100' },
    'BEVERAGE': { icon: Coffee, color: 'text-indigo-600', bgColor: 'bg-indigo-100' },
    'FROZEN': { icon: Snowflake, color: 'text-cyan-500', bgColor: 'bg-cyan-100' },
    'HOUSEHOLD': { icon: Home, color: 'text-slate-600', bgColor: 'bg-slate-100' },
    'RECEIPT': { icon: FileText, color: 'text-home-primary-600', bgColor: 'bg-home-primary-50' },
    'REC': { icon: FileText, color: 'text-home-primary-600', bgColor: 'bg-home-primary-50' },
  };

  const config = iconMap[code] || { icon: HelpCircle, color: 'text-gray-400', bgColor: 'bg-gray-50' };
  const IconComponent = config.icon;

  return (
    <div className={`flex items-center justify-center rounded-full ${config.bgColor} ${className}`} style={{ width: size * 1.8, height: size * 1.8 }}>
      <IconComponent size={size} className={config.color} />
    </div>
  );
};

export default CategoryIcon;

