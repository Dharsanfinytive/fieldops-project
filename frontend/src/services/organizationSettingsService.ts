import api from "./api";

export interface FieldOperationsSettings {
  retention_days: number;
  location_freshness_seconds: number;
  max_location_accuracy_meters: number;
  tracking_scope: string;
}

export interface CommunicationChannelSetting {
  channel: "EMAIL" | "SMS";
  state: "ENABLED" | "DISABLED";
  revision: number;
  updated_at: string | null;
  updated_by: string;
}

export const getFieldOperationsSettings = async () =>
  (await api.get<FieldOperationsSettings>("/organization-settings/field-operations")).data;

export const updateFieldOperationsSettings = async (payload: {
  retention_days: number;
  location_freshness_seconds: number;
  reason: string;
}) => (await api.put<FieldOperationsSettings>("/organization-settings/field-operations", payload)).data;

export const getCommunicationSettings = async () =>
  (await api.get<{ channels: CommunicationChannelSetting[] }>("/organization-settings/communications")).data;

export const updateCommunicationChannel = async (
  channel: "EMAIL" | "SMS",
  payload: { state: "ENABLED" | "DISABLED"; reason: string },
) => (await api.put<CommunicationChannelSetting>(`/organization-settings/communications/${channel}`, payload)).data;

export const sendCommunicationTest = async (payload: {
  channel: "EMAIL" | "SMS";
  destination: string;
}) => (await api.post<{ sent: boolean; channel: string; destination: string }>("/organization-settings/communications/test", payload)).data;
