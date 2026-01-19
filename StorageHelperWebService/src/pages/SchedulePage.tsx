import React, { useState, useEffect, useCallback } from 'react';
import { Calendar, Plus, Clock, MapPin, Edit2, Trash2, CheckCircle2, Circle, ChevronLeft, ChevronRight } from 'lucide-react';
import ScheduleService, { Schedule, CreateScheduleRequest } from '../api/scheduleService';
import clsx from 'clsx';


const SchedulePage: React.FC = () => {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [filteredSchedules, setFilteredSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedMonth, setSelectedMonth] = useState(new Date());
  const [showModal, setShowModal] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>('all');

  // Fetch schedules for the selected month
  const fetchSchedules = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const year = selectedMonth.getFullYear();
      const month = selectedMonth.getMonth();
      const startDate = new Date(year, month, 1);
      const endDate = new Date(year, month + 1, 0, 23, 59, 59);

      const data = await ScheduleService.getSchedulesByRange(
        startDate.toISOString(),
        endDate.toISOString()
      );
      setSchedules(data);
    } catch (err) {
      setError('Failed to fetch schedules');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [selectedMonth]);

  useEffect(() => {
    fetchSchedules();
  }, [fetchSchedules]);

  // Filter schedules by status
  useEffect(() => {
    if (filterStatus === 'all') {
      setFilteredSchedules(schedules);
    } else {
      setFilteredSchedules(schedules.filter(s => s.status === filterStatus));
    }
  }, [schedules, filterStatus]);

  const handleCreateSchedule = async (formData: CreateScheduleRequest) => {
    try {
      await ScheduleService.createSchedule(formData);
      await fetchSchedules();
      setShowModal(false);
      setEditingSchedule(null);
    } catch (err) {
      setError('Failed to create schedule');
      console.error(err);
    }
  };

  const handleUpdateSchedule = async (id: number, formData: CreateScheduleRequest) => {
    try {
      await ScheduleService.updateSchedule(id, formData);
      await fetchSchedules();
      setShowModal(false);
      setEditingSchedule(null);
    } catch (err) {
      setError('Failed to update schedule');
      console.error(err);
    }
  };

  const handleDeleteSchedule = async (id: number) => {
    if (!window.confirm('Are you sure you want to delete this schedule?')) return;
    try {
      await ScheduleService.deleteSchedule(id);
      await fetchSchedules();
    } catch (err) {
      setError('Failed to delete schedule');
      console.error(err);
    }
  };

  const handleStatusToggle = async (schedule: Schedule) => {
    const newStatus = schedule.status === 'completed' ? 'pending' : 'completed';
    try {
      await ScheduleService.updateScheduleStatus(schedule.id, newStatus);
      await fetchSchedules();
    } catch (err) {
      setError('Failed to update status');
      console.error(err);
    }
  };

  const handlePreviousMonth = () => {
    setSelectedMonth(new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() - 1));
  };

  const handleNextMonth = () => {
    setSelectedMonth(new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() + 1));
  };

  const monthYear = selectedMonth.toLocaleDateString('en-US', {
    month: 'long',
    year: 'numeric',
  });

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 p-6 md:p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-4xl font-bold text-gray-900 mb-2">Schedule</h1>
            <p className="text-gray-600">Manage your tasks and events effortlessly</p>
          </div>
          <button
            onClick={() => {
              setEditingSchedule(null);
              setShowModal(true);
            }}
            className="flex items-center gap-2 bg-gradient-to-r from-indigo-600 to-indigo-700 text-white px-6 py-3 rounded-lg hover:shadow-lg transition-all font-medium"
          >
            <Plus size={20} />
            New Schedule
          </button>
        </div>

        {error && (
          <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded-lg mb-6">
            {error}
          </div>
        )}

        {/* Month Navigation */}
        <div className="bg-white rounded-xl shadow-md border border-gray-100 p-8 mb-8">
          <div className="flex items-center justify-between mb-8">
            <button
              onClick={handlePreviousMonth}
              className="p-3 hover:bg-indigo-50 rounded-lg transition-all text-gray-700 hover:text-indigo-600"
            >
              <ChevronLeft size={28} />
            </button>
            <h2 className="text-3xl font-bold text-gray-900">{monthYear}</h2>
            <button
              onClick={handleNextMonth}
              className="p-3 hover:bg-indigo-50 rounded-lg transition-all text-gray-700 hover:text-indigo-600"
            >
              <ChevronRight size={28} />
            </button>
          </div>

          {/* Filter Buttons */}
          <div className="flex gap-3 flex-wrap">
            {['all', 'pending', 'completed'].map(status => (
              <button
                key={status}
                onClick={() => setFilterStatus(status)}
                className={clsx(
                  'px-5 py-2 rounded-full font-medium transition-all text-sm',
                  filterStatus === status
                    ? 'bg-indigo-600 text-white shadow-md'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                )}
              >
                {status.charAt(0).toUpperCase() + status.slice(1)}
              </button>
            ))}
          </div>
        </div>

        {/* Schedules List */}
        {loading ? (
          <div className="bg-white rounded-xl shadow-md border border-gray-100 p-12 text-center">
            <div className="animate-spin text-indigo-600 mb-4 flex justify-center">
              <Calendar size={48} />
            </div>
            <p className="text-gray-600 font-medium">Loading schedules...</p>
          </div>
        ) : (
          <div>
            {filteredSchedules.length === 0 ? (
              <div className="bg-white rounded-xl shadow-md border border-gray-100 p-12 text-center">
                <Calendar size={48} className="mx-auto text-gray-300 mb-4" />
                <p className="text-gray-600 text-lg font-medium">No schedules found</p>
                <p className="text-gray-500 text-sm mt-2">
                  {filterStatus !== 'all'
                    ? `No ${filterStatus} schedules in ${monthYear}`
                    : 'Create your first schedule to get started'}
                </p>
              </div>
            ) : (
              <div className="space-y-8">
                {/* Group schedules by date */}
                {(() => {
                  const groupedByDate = new Map<string, typeof filteredSchedules>();
                  filteredSchedules.forEach(schedule => {
                    const date = new Date(schedule.scheduled_time).toLocaleDateString();
                    if (!groupedByDate.has(date)) {
                      groupedByDate.set(date, []);
                    }
                    groupedByDate.get(date)!.push(schedule);
                  });

                  // Sort dates
                  const sortedDates = Array.from(groupedByDate.keys()).sort(
                    (a, b) => new Date(a).getTime() - new Date(b).getTime()
                  );

                  return sortedDates.map(date => {
                    const schedules = groupedByDate.get(date)!;
                    const dateObj = new Date(date);
                    const dateLabel = dateObj.toLocaleDateString('en-US', {
                      weekday: 'long',
                      month: 'long',
                      day: 'numeric',
                      year: 'numeric'
                    });

                    return (
                      <div key={date}>
                        <div className="flex items-center gap-4 mb-5">
                          <div className="flex-1 h-1 bg-gradient-to-r from-indigo-500 to-transparent rounded-full"></div>
                          <h3 className="text-lg font-bold text-gray-900 whitespace-nowrap px-4 py-2 bg-white rounded-full border-2 border-indigo-200">
                            📆 {dateLabel}
                          </h3>
                          <div className="flex-1 h-1 bg-gradient-to-l from-indigo-500 to-transparent rounded-full"></div>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                          {schedules
                            .sort((a, b) => new Date(a.scheduled_time).getTime() - new Date(b.scheduled_time).getTime())
                            .map(schedule => (
                              <ScheduleCard
                                key={schedule.id}
                                schedule={schedule}
                                onEdit={() => {
                                  setEditingSchedule(schedule);
                                  setShowModal(true);
                                }}
                                onDelete={() => handleDeleteSchedule(schedule.id)}
                                onStatusToggle={() => handleStatusToggle(schedule)}
                              />
                            ))}
                        </div>
                      </div>
                    );
                  });
                })()}
              </div>
            )}
          </div>
        )}

        {/* Modal */}
        {showModal && (
          <ScheduleModal
            schedule={editingSchedule}
            onClose={() => {
              setShowModal(false);
              setEditingSchedule(null);
            }}
            onSubmit={
              editingSchedule
                ? (data) => handleUpdateSchedule(editingSchedule.id, data)
                : handleCreateSchedule
            }
          />
        )}
      </div>
    </div>
  );
};

interface ScheduleCardProps {
  schedule: Schedule;
  onEdit: () => void;
  onDelete: () => void;
  onStatusToggle: () => void;
}

const ScheduleCard: React.FC<ScheduleCardProps> = ({
  schedule,
  onEdit,
  onDelete,
  onStatusToggle,
}) => {
  const scheduledDate = new Date(schedule.scheduled_time);
  const endDate = schedule.end_time ? new Date(schedule.end_time) : null;

  // Format date and time separately for better visibility
  const dateString = scheduledDate.toLocaleDateString('en-US', { 
    weekday: 'short',
    month: 'short', 
    day: 'numeric' 
  });
  
  const startTime = scheduledDate.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
  
  const endTime = endDate ? endDate.toLocaleTimeString([], { 
    hour: '2-digit', 
    minute: '2-digit' 
  }) : null;

  const isCompleted = schedule.status === 'completed';
  
  // Color coding based on priority
  const priorityColors = {
    0: 'bg-blue-50 border-l-blue-500',
    1: 'bg-amber-50 border-l-amber-500',
    2: 'bg-red-50 border-l-red-500',
  };
  
  const priorityBadgeColors = {
    0: 'bg-blue-100 text-blue-800',
    1: 'bg-amber-100 text-amber-800',
    2: 'bg-red-100 text-red-800',
  };
  
  const priorityEmojis = {
    0: '🔵',
    1: '🟡',
    2: '🔴',
  };
  
  const priorityLabels = {
    0: 'Normal',
    1: 'High',
    2: 'Urgent',
  };

  const bgColor = priorityColors[Math.min(schedule.priority, 2) as keyof typeof priorityColors] || 'bg-white border-l-gray-500';

  return (
    <div
      className={clsx(
        'bg-white rounded-xl shadow-md hover:shadow-xl transition-all border-l-4 overflow-hidden',
        isCompleted ? 'border-l-green-500 opacity-70' : bgColor
      )}
    >
      <div className="p-5">
        {/* Date Badge */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <div className="px-3 py-1.5 bg-indigo-100 text-indigo-700 rounded-lg font-semibold text-sm">
              📅 {dateString}
            </div>
            {isCompleted && (
              <span className="px-2 py-1 bg-green-100 text-green-700 rounded-lg font-semibold text-xs">
                ✓ Done
              </span>
            )}
          </div>
          
          {/* Action buttons */}
          <div className="flex gap-1">
            <button
              onClick={onEdit}
              className="p-2 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
              title="Edit"
            >
              <Edit2 size={18} />
            </button>
            <button
              onClick={onDelete}
              className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
              title="Delete"
            >
              <Trash2 size={18} />
            </button>
          </div>
        </div>

        {/* Title with checkbox */}
        <div className="flex items-start gap-3 mb-4">
          <button
            onClick={onStatusToggle}
            className={clsx(
              'flex-shrink-0 mt-1 transition-all',
              isCompleted ? 'text-green-500' : 'text-gray-300 hover:text-indigo-500'
            )}
          >
            {isCompleted ? <CheckCircle2 size={24} /> : <Circle size={24} />}
          </button>
          <div className="flex-1 min-w-0">
            <h3 className={clsx(
              'text-lg font-bold text-gray-900 break-words',
              isCompleted && 'line-through text-gray-500'
            )}>
              {schedule.title}
            </h3>
            {schedule.event_type && (
              <p className="text-xs font-medium text-indigo-600 mt-1 uppercase tracking-wider">
                🏷️ {schedule.event_type}
              </p>
            )}
          </div>
        </div>

        {/* Description */}
        {schedule.description && (
          <p className="text-gray-700 text-sm mb-4 p-3 bg-gray-50 rounded-lg border border-gray-100 line-clamp-3">
            {schedule.description}
          </p>
        )}

        {/* Time and location info - enhanced display */}
        <div className="space-y-3 mb-4 p-4 bg-gradient-to-r from-slate-50 to-slate-100 rounded-lg border border-slate-200">
          <div className="flex items-center gap-3 text-sm">
            <Clock size={18} className="text-indigo-500 flex-shrink-0" />
            <div>
              <span className="font-semibold text-gray-900">{startTime}</span>
              {endTime && <span className="text-gray-600"> → {endTime}</span>}
            </div>
          </div>
          {schedule.location && (
            <div className="flex items-center gap-3 text-sm">
              <MapPin size={18} className="text-indigo-500 flex-shrink-0" />
              <span className="font-semibold text-gray-900">{schedule.location}</span>
            </div>
          )}
        </div>

        {/* Priority badge */}
        {schedule.priority >= 0 && (
          <div className="flex items-center gap-2">
            <span className={clsx(
              'px-4 py-2 rounded-lg text-xs font-bold inline-block',
              priorityBadgeColors[Math.min(schedule.priority, 2) as keyof typeof priorityBadgeColors]
            )}>
              {priorityEmojis[Math.min(schedule.priority, 2) as keyof typeof priorityEmojis]} {priorityLabels[Math.min(schedule.priority, 2) as keyof typeof priorityLabels]}
            </span>
          </div>
        )}
      </div>
    </div>
  );
};

interface ScheduleModalProps {
  schedule?: Schedule | null;
  onClose: () => void;
  onSubmit: (data: CreateScheduleRequest) => Promise<void>;
}

const ScheduleModal: React.FC<ScheduleModalProps> = ({ schedule, onClose, onSubmit }) => {
  const getLocalDateTime = (dateString?: string) => {
    if (!dateString) {
      const now = new Date();
      return now.toISOString().slice(0, 16);
    }
    const date = new Date(dateString);
    return date.toISOString().slice(0, 16);
  };

  const [formData, setFormData] = useState<CreateScheduleRequest>({
    title: schedule?.title || '',
    event_type: schedule?.event_type || '',
    description: schedule?.description || '',
    scheduled_time: getLocalDateTime(schedule?.scheduled_time),
    end_time: schedule?.end_time ? getLocalDateTime(schedule.end_time) : '',
    location: schedule?.location || '',
    priority: schedule?.priority || 0,
  });

  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await onSubmit({
        ...formData,
        scheduled_time: new Date(formData.scheduled_time).toISOString(),
        end_time: formData.end_time ? new Date(formData.end_time).toISOString() : undefined,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-40 backdrop-blur-sm flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-8 border-b border-gray-200 sticky top-0 bg-gradient-to-r from-indigo-50 to-white">
          <h2 className="text-2xl font-bold text-gray-900">
            {schedule ? '✏️ Edit Schedule' : '✨ Create New Schedule'}
          </h2>
          <button 
            onClick={onClose} 
            className="text-gray-400 hover:text-gray-600 hover:bg-gray-100 p-2 rounded-lg transition-all text-2xl leading-none"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-8 space-y-6">
          {/* Title */}
          <div>
            <label className="block text-sm font-semibold text-gray-900 mb-2">Title *</label>
            <input
              type="text"
              required
              value={formData.title}
              onChange={(e) => setFormData({ ...formData, title: e.target.value })}
              className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all bg-white"
              placeholder="Enter schedule title"
            />
          </div>

          {/* Event Type and Priority - two columns */}
          <div className="grid grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-2">Event Type</label>
              <input
                type="text"
                value={formData.event_type || ''}
                onChange={(e) => setFormData({ ...formData, event_type: e.target.value })}
                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all bg-white"
                placeholder="e.g., Work, Personal"
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-2">Priority</label>
              <select
                value={formData.priority || 0}
                onChange={(e) => setFormData({ ...formData, priority: parseInt(e.target.value) })}
                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all bg-white"
              >
                <option value="0">🔵 Normal</option>
                <option value="1">🟡 High</option>
                <option value="2">🔴 Very High</option>
              </select>
            </div>
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-semibold text-gray-900 mb-2">Description</label>
            <textarea
              value={formData.description || ''}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all bg-white resize-none"
              placeholder="Add any notes or details..."
              rows={3}
            />
          </div>

          {/* Date and Time fields */}
          <div className="grid grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-2">Start Time *</label>
              <input
                type="datetime-local"
                required
                value={formData.scheduled_time}
                onChange={(e) => setFormData({ ...formData, scheduled_time: e.target.value })}
                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all bg-white"
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-2">End Time</label>
              <input
                type="datetime-local"
                value={formData.end_time || ''}
                onChange={(e) => setFormData({ ...formData, end_time: e.target.value })}
                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all bg-white"
              />
            </div>
          </div>

          {/* Location */}
          <div>
            <label className="block text-sm font-semibold text-gray-900 mb-2">Location</label>
            <input
              type="text"
              value={formData.location || ''}
              onChange={(e) => setFormData({ ...formData, location: e.target.value })}
              className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all bg-white"
              placeholder="e.g., Meeting Room A, Coffee Shop"
            />
          </div>

          {/* Action Buttons */}
          <div className="flex gap-3 pt-6 border-t border-gray-200">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-6 py-2.5 border-2 border-gray-300 text-gray-700 font-medium rounded-lg hover:bg-gray-50 transition-all"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="flex-1 px-6 py-2.5 bg-gradient-to-r from-indigo-600 to-indigo-700 text-white font-medium rounded-lg hover:shadow-lg transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? '⏳ Saving...' : schedule ? '💾 Update' : '✨ Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default SchedulePage;
