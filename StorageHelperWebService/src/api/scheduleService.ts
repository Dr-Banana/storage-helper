import apiClient from './client';


export interface Schedule {
  id: number;
  user_id: number;
  title: string;
  event_type?: string;
  description?: string;
  scheduled_time: string;
  end_time?: string;
  location?: string;
  status: string;
  priority: number;
  metadata?: Record<string, any>;
  created_at: string;
}

export interface CreateScheduleRequest {
  title: string;
  event_type?: string;
  description?: string;
  scheduled_time: string;
  end_time?: string;
  location?: string;
  priority?: number;
  metadata?: Record<string, any>;
}

export interface UpdateScheduleRequest extends Partial<CreateScheduleRequest> {}

export interface UpdateScheduleStatusRequest {
  status: string;
}

class ScheduleService {
  /**
   * Create a new schedule
   */
  static async createSchedule(data: CreateScheduleRequest): Promise<Schedule> {
    const response = await apiClient.post('/schedule', data);
    return response.data;
  }

  /**
   * Get all schedules for current user
   */
  static async getSchedules(): Promise<Schedule[]> {
    const response = await apiClient.get('/schedule');
    return response.data;
  }

  /**
   * Get schedules within a date range
   */
  static async getSchedulesByRange(startTime: string, endTime: string): Promise<Schedule[]> {
    const response = await apiClient.get('/schedule/range', {
      params: {
        start_time: startTime,
        end_time: endTime,
      },
    });
    return response.data;
  }

  /**
   * Get a specific schedule
   */
  static async getSchedule(id: number): Promise<Schedule> {
    const response = await apiClient.get(`/schedule/${id}`);
    return response.data;
  }

  /**
   * Update a schedule
   */
  static async updateSchedule(id: number, data: UpdateScheduleRequest): Promise<Schedule> {
    const response = await apiClient.put(`/schedule/${id}`, data);
    return response.data;
  }

  /**
   * Update schedule status
   */
  static async updateScheduleStatus(id: number, status: string): Promise<Schedule> {
    const response = await apiClient.patch(`/schedule/${id}/status`, {
      status,
    });
    return response.data;
  }

  /**
   * Delete a schedule
   */
  static async deleteSchedule(id: number): Promise<void> {
    await apiClient.delete(`/schedule/${id}`);
  }

  /**
   * Overwrite cooking steps and ingredients for a specific dish in a schedule.
   * Used by the RecipeDiffCard "Save this version" button.
   */
  static async updateCookingSteps(
    scheduleId: number,
    payload: {
      dish_name: string;
      steps: string[];
      ingredients?: { name: string; quantity: string }[];
      date?: string;
      meal_time?: string;
    },
  ): Promise<Schedule> {
    const response = await apiClient.patch(`/schedule/${scheduleId}/cooking-steps`, payload);
    return response.data;
  }
}

export default ScheduleService;
